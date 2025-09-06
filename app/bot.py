# app/bot.py
import asyncio
import json
from typing import List, Mapping, Any

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultPhoto, InputMediaPhoto
)

from .config import settings
from . import db
from .payments import cryptopay

bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

AMOUNTS_USDT = [1, 2, 4, 8, 16, 32, 64, 128, 256]

# ===================== keyboards =====================
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 События", callback_data="events")],
        [InlineKeyboardButton(text="🎟 Текущие ставки", callback_data="mybets")],
        [InlineKeyboardButton(text="📤 Поделиться ставкой", callback_data="share")],
    ])

def kb_fights_list(items: List[Mapping[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for f in items[:]:
        t = f"{f['participant1_name']} vs {f['participant2_name']}"
        rows.append([InlineKeyboardButton(text=t, callback_data=f"fight:{f['id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_reply_link(deal_id: int, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🤝 Ответить на ставку",
            url=f"https://t.me/{bot_username}?start=reply_{deal_id}"
        )]
    ])

def kb_fight(f: Mapping[str, Any]) -> InlineKeyboardMarkup:
    p1 = f["participant1_name"]; p2 = f["participant2_name"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Поставить на {p1}", callback_data=f"bet_side:{f['id']}:1")],
        [InlineKeyboardButton(text=f"Поставить на {p2}", callback_data=f"bet_side:{f['id']}:2")],
        [InlineKeyboardButton(text="📜 Открытые ставки", callback_data=f"open:{f['id']}")],
        [InlineKeyboardButton(text="⬅️ К событиям", callback_data="events")],
    ])

def kb_amounts(fid: int, side: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for i, amt in enumerate(AMOUNTS_USDT, start=1):
        row.append(InlineKeyboardButton(text=f"{amt} USDT", callback_data=f"bet_amt:{fid}:{side}:{amt}"))
        if i % 3 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"fight:{fid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_open_deals(fight_id: int, deals: List[Mapping[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for d in deals[:20]:
        side = d["participant1"]; amt = d["amount1_cents"] / 100
        rows.append([InlineKeyboardButton(
            text=f"Ответить: {amt:.2f} USDT (на {'P2' if side==1 else 'P1'})",
            callback_data=f"reply:{d['id']}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"fight:{fight_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_pay(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить в Mini App", url=url)],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_main")],
    ])

def kb_reply_one(deal_id: int, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"reply:{deal_id}")],
    ])

def kb_share_pick_chat(deal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить другу", switch_inline_query=f"reply_{deal_id}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_main")],
    ])

# ===================== helpers =====================
async def ensure_user(tg_user) -> Mapping[str, Any]:
    return await db.ensure_user_by_tg(tg_user.id, tg_user.username or tg_user.full_name)

def fight_caption(f: Mapping[str, Any]) -> str:
    lines = [f"<b>{f['title']}</b>", f"{f['participant1_name']} vs {f['participant2_name']}"]
    if f.get("starts_at"): lines.append(f"Старт: {f['starts_at']}")
    if f.get("description"): lines += ["", f"{f['description']}"]
    return "\n".join(lines)

async def replace(cq: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.message.answer(text, reply_markup=reply_markup)
# === helpers (добавь рядом с show_main/replace) ===
async def send_with_photo(target_msg_or_chat, photo_url: str, caption: str, reply_markup: InlineKeyboardMarkup):
    """
    Универсально отправляет фото+подпись.
    target_msg_or_chat: Message (для .answer_*) или сам Bot/ChatId — мы используем Message.
    """
    try:
        await target_msg_or_chat.answer_photo(photo=photo_url, caption=caption, reply_markup=reply_markup)
    except Exception:
        # если вдруг фото битое — просто текстом
        await target_msg_or_chat.answer(caption, reply_markup=reply_markup)

from aiogram.types import InputMediaPhoto, InlineKeyboardMarkup

async def replace_with_photo(
    cq: CallbackQuery,
    photo_url: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None
):
    # безопасная замена: удаляем старое сообщение и отправляем новое фото
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.message.answer_photo(photo=photo_url, caption=caption, reply_markup=reply_markup)

async def show_main(target_msg: Message):
    photo = getattr(settings, "MAIN_MENU_PHOTO_URL", None)
    if photo:
        await send_with_photo(target_msg, photo, "Главное меню:", kb_main())
    else:
        await target_msg.answer("Главное меню:", reply_markup=kb_main())

async def auto_check_and_finalize(cq: CallbackQuery, invoice_id: int):
    """
    30 сек, шаг 2 сек, опрашиваем CryptoPay. На paid — проводим NEW/MATCH и правим то же сообщение.
    Если не успели — показываем кнопки ручной проверки.
    """
    for _ in range(15):  # ~30 сек
        try:
            invs = await cryptopay.get_invoices([invoice_id])
            inv = next((x for x in invs if int(x.get("invoice_id", 0)) == invoice_id), None)
        except Exception:
            inv = None

        if inv and inv.get("status") == "paid":
            iw = await db.get_invoice_wait(invoice_id)
            text = "Оплата уже обработана ✅."
            if iw:
                payload = json.loads(iw["payload"])
                payer_tg = int(payload.get("tg_user_id"))
                user = await db.ensure_user_by_tg(payer_tg, None)

                if iw["kind"] == "NEW":
                    await db.create_deal_after_paid(payload, invoice_id, user["id"])
                    text = "✅ Оплата получена. Ставка активна и ждёт соперника."
                elif iw["kind"] == "MATCH":
                    await db.match_deal_after_paid(payload, invoice_id, user["id"])
                    text = "✅ Оплата получена. Ставка сматчена!"

                await db.del_invoice_wait(invoice_id)

            try:
                if cq.message:
                    await cq.message.edit_text(text, reply_markup=kb_main())
                else:
                    await bot.edit_message_caption(
                        inline_message_id=cq.inline_message_id,
                        caption=text,
                        reply_markup=kb_main(),
                        parse_mode=ParseMode.HTML
                    )
            except Exception:
                pass
            return

        await asyncio.sleep(2)

    # таймаут — оставить кнопки «Проверить оплату»
    rm = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"checkpay:{invoice_id}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_main")],
    ])
    try:
        if cq.message:
            await cq.message.edit_reply_markup(reply_markup=rm)
        else:
            await bot.edit_message_reply_markup(inline_message_id=cq.inline_message_id, reply_markup=rm)
    except Exception:
        pass

# ===================== handlers =====================
# вверху файла
import re
REPLY_RE = re.compile(r"(?:^|[\s?=&])reply_(\d+)\b", re.IGNORECASE)

@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user)

    raw = m.text or ""
    compact = " ".join(raw.split())                  # убираем \n и лишние пробелы
    mobj = REPLY_RE.search(compact)                  # ловим reply_<id> в ЛЮБОЙ форме

    if not mobj:
        await show_main(m)                           # твоя функция главного меню
        return

    try:
        deal_id = int(mobj.group(1))
    except Exception:
        await show_main(m)
        return

    u = await ensure_user(m.from_user)
    d = await db.fetchrow("""
        SELECT d.*, f.title, f.participant1_name p1, f.participant2_name p2, f.photo_url
        FROM deal d JOIN fight f ON f.id=d.fight_id
        WHERE d.id=$1
    """, deal_id)

    if (not d) or (not d["paid1"]) or d["status"] != "awaiting_match":
        await m.answer("Эта ставка уже недоступна.", reply_markup=kb_main()); return
    if d["user1_id"] == u["id"]:
        await m.answer("Нельзя отвечать на свою ставку. Отправь её другу через «📤 Поделиться ставкой».",
                       reply_markup=kb_main()); return

    need_side = 2 if d["participant1"] == 1 else 1
    amt = (d["amount1_cents"] or 0) / 100
    text = (f"<b>{d['title']}</b>\n{d['p1']} vs {d['p2']}\n\n"
            f"Ставка друга: <b>{amt:.2f} USDT</b>\n"
            f"Нужно поставить на: <b>{'P1' if need_side == 1 else 'P2'}</b>")

    if d.get("photo_url"):
        await m.answer_photo(d["photo_url"], caption=text,
                             reply_markup=kb_reply_one(deal_id, "🤝 Ответить на ставку"))
    else:
        await m.answer(text, reply_markup=kb_reply_one(deal_id, "🤝 Ответить на ставку"))

@dp.callback_query(F.data == "back_main")
async def back_main(cq: CallbackQuery):
    try:
        await cq.message.delete()
    except Exception:
        pass
    await show_main(cq.message)   # покажет главное меню с обложкой

@dp.callback_query(F.data == "events")
async def cb_events(cq: CallbackQuery):
    items = await db.list_upcoming()
    events_photo = getattr(settings, "EVENTS_MENU_PHOTO_URL", None)

    if not items:
        caption = "Пока нет событий."
        if events_photo:
            await replace_with_photo(cq, events_photo, caption, kb_main())
        else:
            await replace(cq, caption, kb_main())
        return

    caption = "Выбери событие:"
    markup = kb_fights_list(items)
    if events_photo:
        await replace_with_photo(cq, events_photo, caption, markup)
    else:
        await replace(cq, caption, markup)



@dp.callback_query(F.data.startswith("open:"))
async def cb_open(cq: CallbackQuery):
    fight_id = int(cq.data.split(":")[1])
    u = await ensure_user(cq.from_user)
    deals = await db.list_open_deals(fight_id, exclude_user_id=u["id"])
    if not deals:
        f = await db.get_fight(fight_id)
        return await replace(cq, "Открытых ставок нет.\nСоздай свою:", InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Поставить на {f['participant1_name']}", callback_data=f"bet_side:{fight_id}:1")],
            [InlineKeyboardButton(text=f"Поставить на {f['participant2_name']}", callback_data=f"bet_side:{fight_id}:2")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"fight:{fight_id}")],
        ]))
    await replace(cq, "Открытые ставки:", kb_open_deals(fight_id, deals))

@dp.callback_query(F.data.startswith("bet_side:"))
async def cb_side(cq: CallbackQuery):
    _, fid, side = cq.data.split(":")
    await replace(cq, "Выбери сумму:", kb_amounts(int(fid), int(side)))

@dp.callback_query(F.data.startswith("bet_amt:"))
async def cb_amount(cq: CallbackQuery):
    _, fid, side, amt = cq.data.split(":")
    fight_id, participant, amount = int(fid), int(side), int(amt)
    await ensure_user(cq.from_user)

    payload = {
        "kind": "NEW",
        "fight_id": fight_id,
        "participant": participant,
        "amount_cents": amount * 100,
        "tg_user_id": cq.from_user.id,
    }

    inv = await cryptopay.create_invoice(
        amount_cents=amount * 100,
        asset=settings.CRYPTO_DEFAULT_ASSET,
        payload=json.dumps(payload),
    )
    invoice_id = int(inv["invoice_id"])
    await db.add_invoice_wait(invoice_id, "NEW", payload)

    pay_url = inv.get("bot_invoice_url") or inv.get("pay_url") or inv.get("url")
    await cq.message.edit_text(
        f"Создан счёт на оплату: <b>{amount} USDT</b>\n"
        "После оплаты ставка активируется и будет ждать оппонента до окончания боя.",
        reply_markup=kb_pay(pay_url)
    )

    # авто-проверка этой же карточки
    asyncio.create_task(auto_check_and_finalize(cq, invoice_id))

@dp.callback_query(F.data.startswith("fight:"))
async def cb_fight(cq: CallbackQuery):
    fid = int(cq.data.split(":")[1])
    f = await db.get_fight(fid)
    if not f:
        await cq.answer("Событие не найдено", show_alert=True)
        return

    # пробуем отредактировать фото, если исходное сообщение — фото
    if f.get("photo_url"):
        try:
            await cq.message.edit_media(
                InputMediaPhoto(media=f["photo_url"], caption=fight_caption(f)),
                reply_markup=kb_fight(f)
            )
            return
        except Exception:
            # не получилось редактировать (или исходное сообщение не фото) — просто заменим
            pass

    await replace(cq, fight_caption(f), kb_fight(f))

@dp.callback_query(F.data.startswith("reply:"))
async def cb_reply(cq: CallbackQuery):
    deal_id = int(cq.data.split(":")[1])
    u = await ensure_user(cq.from_user)
    d = await db.fetchrow("""SELECT d.*, f.participant1_name p1, f.participant2_name p2
                               FROM deal d JOIN fight f ON f.id=d.fight_id
                              WHERE d.id=$1""", deal_id)
    if not d or not d["paid1"] or d["status"] != "awaiting_match":
        return await cq.answer("Эта ставка уже недоступна.", show_alert=True)
    if d["user1_id"] == u["id"]:
        return await cq.answer("Нельзя отвечать на свою ставку.", show_alert=True)

    resp_side = 2 if d["participant1"] == 1 else 1
    amt_cents = int(d["amount1_cents"])
    payload = {"kind": "MATCH", "deal_id": deal_id, "participant": resp_side,
               "amount_cents": amt_cents, "tg_user_id": cq.from_user.id}

    inv = await cryptopay.create_invoice(
        amount_cents=amt_cents,
        asset=settings.CRYPTO_DEFAULT_ASSET,
        payload=json.dumps(payload)
    )
    invoice_id = int(inv["invoice_id"])
    await db.add_invoice_wait(invoice_id, "MATCH", payload)
    pay_url = inv.get("bot_invoice_url") or inv.get("pay_url") or inv.get("url")

    # ВАЖНО: редактируем текущее сообщение (не delete+answer), чтобы авто-проверка могла его обновить
    await cq.message.edit_text(
        f"Счёт на <b>{amt_cents / 100:.2f} USDT</b> создан. После оплаты ставка будет сматчена.",
        reply_markup=kb_pay(pay_url),
    )

    asyncio.create_task(auto_check_and_finalize(cq, invoice_id))

@dp.callback_query(F.data == "mybets")
async def cb_mybets(cq: CallbackQuery):
    u = await ensure_user(cq.from_user)

    rows = await db.fetch("""
        SELECT d.*,
               f.title,
               f.participant1_name AS p1,
               f.participant2_name AS p2,
               f.status AS fight_status
        FROM deal d
        JOIN fight f ON f.id = d.fight_id
        WHERE (d.user1_id = $1 OR d.user2_id = $1)
          AND f.status IN ('upcoming','today','live')
          AND d.status IN ('awaiting_match','matched')
        ORDER BY d.id DESC
        LIMIT 100
    """, u["id"])

    if not rows:
        await replace(cq, "Сейчас у тебя нет актуальных ставок.", kb_main())
        return

    lines = []
    for b in rows:
        who = "P1" if b["user1_id"] == u["id"] else ("P2" if b["user2_id"] == u["id"] else "?")
        side = b["participant1"] if who == "P1" else b.get("participant2")
        side_txt = "на 1-го" if side == 1 else "на 2-го"
        amt = (b["amount1_cents"] if who == "P1" else (b["amount2_cents"] or 0)) / 100
        status_human = "ждёт оппонента" if b["status"] == "awaiting_match" else "сматчена"
        lines.append(f"• <b>{b['title']}</b> — {side_txt} — {amt:.2f} {settings.CRYPTO_DEFAULT_ASSET} — {status_human}")

    await replace(cq, "\n".join(lines), kb_main())

@dp.callback_query(F.data == "share")
async def cb_share(cq: CallbackQuery):
    u = await ensure_user(cq.from_user)

    sql = """
    SELECT
        d.id,
        d.amount1_cents,
        d.participant1,
        f.title,
        f.participant1_name AS p1,
        f.participant2_name AS p2
    FROM deal AS d
    JOIN fight AS f ON f.id = d.fight_id
    WHERE d.user1_id = $1
      AND d.status = 'awaiting_match'
      AND d.paid1 = TRUE
      AND d.user2_id IS NULL
    ORDER BY d.id DESC
    LIMIT 20
    """

    try:
        rows = await db.fetch(sql, u["id"])
    except Exception as e:
        # временный лог — если вдруг опять что-то с SQL
        await cq.answer(f"Ошибка выборки: {type(e).__name__}", show_alert=True)
        return

    if not rows:
        await replace(cq, "Нет открытых ставок для шаринга. Создай новую ставку.", kb_main())
        return

    kb_rows = []
    for r in rows:
        amt = (r["amount1_cents"] or 0) / 100
        fighter = r["p1"] if int(r["participant1"]) == 1 else r["p2"]
        kb_rows.append([
            InlineKeyboardButton(
                text=f"📤 {fighter} • {amt:.2f} USDT",
                callback_data=f"sharedeal:{r['id']}"
            )
        ])

    kb_rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_main")])
    await replace(cq, "Выбери ставку для отправки другу:", InlineKeyboardMarkup(inline_keyboard=kb_rows))

@dp.callback_query(F.data.startswith("sharedeal:"))
async def cb_sharedeal(cq: CallbackQuery):
    deal_id = int(cq.data.split(":")[1])

    # нормальный SQL без "..."
    sql = """
    SELECT
        d.*,
        f.title,
        f.photo_url,
        f.participant1_name AS p1,
        f.participant2_name AS p2
    FROM deal AS d
    JOIN fight AS f ON f.id = d.fight_id
    WHERE d.id = $1
    """
    d = await db.fetchrow(sql, deal_id)

    if (not d) or (not d["paid1"]) or d["status"] != "awaiting_match":
        await cq.answer("Эта ставка уже недоступна к пересылке.", show_alert=True)
        return

    # показываем экран «Отправить другу»
    photo = getattr(settings, "MAIN_MENU_PHOTO_URL", None)
    caption = "Нажми «Отправить другу», выбери чат и отправь карточку:"
    markup = kb_share_pick_chat(deal_id)

    if photo:
        await replace_with_photo(cq, photo, caption, markup)
    else:
        await replace(cq, caption, markup)

@dp.inline_query()
async def inline_share(iq: InlineQuery):
    q = (iq.query or "").strip()
    if not q.startswith("reply_"):
        return await bot.answer_inline_query(iq.id, [], cache_time=1, is_personal=True)

    try:
        deal_id = int(q.split("_", 1)[1])
    except Exception:
        return await bot.answer_inline_query(iq.id, [], cache_time=1, is_personal=True)

    d = await db.fetchrow("""
        SELECT d.*,
               f.title,
               f.participant1_name AS p1,
               f.participant2_name AS p2,
               f.photo_url
        FROM deal d
        JOIN fight f ON f.id = d.fight_id
        WHERE d.id = $1
    """, deal_id)
    if not d or not d["paid1"] or d["status"] != "awaiting_match":
        return await bot.answer_inline_query(iq.id, [], cache_time=1, is_personal=True)

    need_side   = 2 if d["participant1"] == 1 else 1
    amt         = (d["amount1_cents"] or 0) / 100
    picked_name = d["p1"] if d["participant1"] == 1 else d["p2"]

    caption = (
        f"<b>Ставка на {picked_name}</b>\n"
        f"{d['p1']} vs {d['p2']}\n\n"
        f"Сумма: <b>{amt:.2f} USDT</b>\n"
        f"Нужно поставить на: <b>{'P1' if need_side == 1 else 'P2'}</b>"
    )
    photo_url = d.get("photo_url") or "https://via.placeholder.com/800x500.png?text=Fight"

    # Важно: у InlineQueryResultPhoto НЕ используем поля 'description' (его нет).
    result = InlineQueryResultPhoto(
        id=str(deal_id),
        photo_url=photo_url,
        thumbnail_url=photo_url,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_reply_link(deal_id, (await bot.me()).username),
    )
    await bot.answer_inline_query(iq.id, [result], cache_time=0, is_personal=True)

# ===================== payments poller =====================
async def payments_loop():
    while True:
        try:
            ids = await db.pending_invoice_ids()
            if ids:
                invs = await cryptopay.get_invoices(ids)
                inv_map = {}
                for x in invs:
                    if isinstance(x, dict):
                        try:
                            inv_map[int(x.get("invoice_id", 0))] = x
                        except Exception:
                            continue
                for inv_id in ids:
                    inv = inv_map.get(int(inv_id))
                    if inv and inv.get("status") == "paid":
                        iw = await db.get_invoice_wait(int(inv_id))
                        if iw:
                            payload = json.loads(iw["payload"])
                            payer_tg = int(payload.get("tg_user_id"))
                            user = await db.ensure_user_by_tg(payer_tg, None)
                            if iw["kind"] == "NEW":
                                await db.create_deal_after_paid(payload, int(inv_id), user["id"])
                            elif iw["kind"] == "MATCH":
                                await db.match_deal_after_paid(payload, int(inv_id), user["id"])
                            await db.del_invoice_wait(int(inv_id))
            await asyncio.sleep(6)
        except Exception as e:
            print(f"[payments_loop] tick error: {e!r}")
            await asyncio.sleep(6)

from aiogram.types import BotCommand

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Главное меню"),
    ]
    await bot.set_my_commands(commands)

async def main():
    asyncio.create_task(payments_loop())
    await set_bot_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())