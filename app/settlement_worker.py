# app/settlement_worker.py
import asyncio
from typing import Mapping, Any, List, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import settings
from . import db
from .payments import cryptopay


# ===== helpers =====

async def _get_tg_user_id(app_user_id: Optional[int]) -> Optional[int]:
    if not app_user_id:
        return None
    row = await db.fetchrow("SELECT tg_user_id FROM app_user WHERE id=$1", app_user_id)
    return int(row["tg_user_id"]) if row else None


def _fmt_usdt(cents: int) -> str:
    return f"{cents/100:.2f} USDT"


# ===== queries (всё внутри файла, чтобы не править db.py) =====

SQL_DEALS_TO_PAYOUT = """
SELECT
  d.*,
  f.title,
  f.participant1_name AS p1_name,
  f.participant2_name AS p2_name,
  f.winner_participant
FROM deal d
JOIN fight f ON f.id = d.fight_id
WHERE f.status = 'done'
  AND d.status = 'matched'
ORDER BY d.id
LIMIT $1
"""

SQL_DEALS_TO_REFUND = """
SELECT
  d.*,
  f.title,
  f.participant1_name AS p1_name,
  f.participant2_name AS p2_name
FROM deal d
JOIN fight f ON f.id = d.fight_id
WHERE f.status = 'done'
  AND d.status = 'awaiting_match'
  AND d.paid1 = TRUE
  AND d.user2_id IS NULL
ORDER BY d.id
LIMIT $1
"""

# Флаг «закрыта»
SQL_MARK_SETTLED = "UPDATE deal SET status='settled' WHERE id=$1"


# ===== notifications =====

async def _notify(bot: Bot, tg_id: Optional[int], text: str) -> None:
    if not tg_id:
        return
    try:
        await bot.send_message(tg_id, text)
    except Exception as e:
        # Не валим процесс из-за ошибок Телеграма — просто логнем в stdout
        print(f"[SETTLE] notify fail to {tg_id}: {e!r}")


async def _notify_payout(
    bot: Bot,
    deal: Mapping[str, Any],
    winner_tg: Optional[int],
    loser_tg: Optional[int],
    payout_cents: int,
    fee_cents: int,
) -> None:
    title = deal["title"]
    win_side = int(deal["winner_participant"])
    p1 = deal["p1_name"]
    p2 = deal["p2_name"]

    if winner_tg:
        await _notify(
            bot,
            winner_tg,
            (
                f"✅ <b>Выплата по событию:</b> {title}\n"
                f"Победил: <b>{'1-й ('+p1+')' if win_side==1 else '2-й ('+p2+')'}</b>\n"
                f"Начислено: <b>{_fmt_usdt(payout_cents)}</b>\n"
                f"Комиссия: {_fmt_usdt(fee_cents)}"
            ),
        )
    if loser_tg:
        await _notify(
            bot,
            loser_tg,
            (
                f"❌ <b>Проигрыш по событию:</b> {title}\n"
                f"Победил: <b>{'1-й ('+p1+')' if win_side==1 else '2-й ('+p2+')'}</b>"
            ),
        )


async def _notify_refund(bot: Bot, tg_id: Optional[int], deal: Mapping[str, Any], amount_cents: int) -> None:
    if not tg_id:
        return
    title = deal["title"]
    await _notify(
        bot,
        tg_id,
        (
            f"↩️ <b>Возврат ставки</b>\n"
            f"Событие: {title}\n"
            f"Вернули: <b>{_fmt_usdt(amount_cents)}</b>\n"
            f"Причина: ставка не нашла оппонента до окончания боя."
        ),
    )


# ===== settlements =====

async def _process_payout(bot: Bot, d: Mapping[str, Any]) -> None:
    """
    Выплата победителю по matched-сделке.
    Комиссия удерживается от общей суммы (ставки обеих сторон).
    """
    try:
        win = int(d["winner_participant"])
        if win not in (1, 2):
            # Корректность данных — без победителя платить нельзя
            print(f"[SETTLE] skip deal {d['id']}: winner_participant={win!r}")
            return

        user1_tg = await _get_tg_user_id(d.get("user1_id"))
        user2_tg = await _get_tg_user_id(d.get("user2_id"))

        a1 = int(d.get("amount1_cents") or 0)
        a2 = int(d.get("amount2_cents") or 0)
        total = a1 + a2

        fee_cents = int(total * settings.FEE_PCT)
        payout_cents = total - fee_cents

        # Кому платим
        if win == 1:
            pay_tg = user1_tg
        else:
            pay_tg = user2_tg

        if not pay_tg:
            print(f"[SETTLE] deal {d['id']} winner has no tg_user_id -> skip")
            return

        # Выплата (через transfer)
        await cryptopay.refund(
            invoice_id=int(d.get("invoice2_id") or d.get("invoice1_id") or 0),  # любой для idempotency
            amount_cents=payout_cents,
            tg_user_id=int(pay_tg),
            asset=settings.CRYPTO_DEFAULT_ASSET,
        )

        # Пометили закрытой
        await db.execute(SQL_MARK_SETTLED, d["id"])

        # Уведомления
        await _notify_payout(
            bot,
            winner_tg=pay_tg,
            loser_tg=user1_tg if pay_tg == user2_tg else user2_tg,
            deal=d,
            payout_cents=payout_cents,
            fee_cents=fee_cents,
        )
    except Exception as e:
        print(f"[SETTLE] payout fail deal={d.get('id')}: {e!r}")


async def _process_refund(bot: Bot, d: Mapping[str, Any]) -> None:
    """
    Возврат автору одиночной ставки (awaiting_match), когда бой уже done.
    """
    try:
        user1_tg = await _get_tg_user_id(d.get("user1_id"))
        a1 = int(d.get("amount1_cents") or 0)
        inv1 = int(d.get("invoice1_id") or 0)

        if not user1_tg or a1 <= 0:
            print(f"[SETTLE] refund skip deal={d.get('id')} (tg={user1_tg}, amount={a1})")
            await db.execute(SQL_MARK_SETTLED, d["id"])
            return

        await cryptopay.refund(
            invoice_id=inv1 or int(d["id"]),
            amount_cents=a1,
            tg_user_id=user1_tg,
            asset=settings.CRYPTO_DEFAULT_ASSET,
        )

        await db.execute(SQL_MARK_SETTLED, d["id"])
        await _notify_refund(bot, user1_tg, d, a1)
    except Exception as e:
        print(f"[SETTLE] refund fail deal={d.get('id')}: {e!r}")


# ===== main loop =====

async def loop(bot: Bot, tick_seconds: int = 5, batch: int = 100) -> None:
    while True:
        try:
            # 1) Выплаты победителям
            to_pay: List[Mapping[str, Any]] = await db.fetch(SQL_DEALS_TO_PAYOUT, batch)
            print(f"[SETTLE] tick: {len(to_pay)} deal(s) to payout")
            for d in to_pay:
                await _process_payout(bot, d)

            # 2) Возвраты за одиночные
            to_refund: List[Mapping[str, Any]] = await db.fetch(SQL_DEALS_TO_REFUND, batch)
            print(f"[SETTLE] tick: {len(to_refund)} deal(s) to refund")
            for d in to_refund:
                await _process_refund(bot, d)

        except Exception as e:
            print(f"[SETTLE] loop FAIL: {e!r}")

        await asyncio.sleep(tick_seconds)


async def main() -> None:
    bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await loop(bot)


if __name__ == "__main__":
    asyncio.run(main())