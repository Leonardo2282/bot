# app/bot.py
import asyncio
from typing import Dict, List, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings
from . import db
from .payments.cryptopay import create_deposit_invoice, rub_to_usdt, CryptoPayError

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Один «экран» на чат: редактируем/заменяем одно сообщение
SCREEN_MSG: Dict[int, int] = {}     # chat_id -> message_id
STATE: Dict[int, str] = {}          # user_id -> "idle" | "await_bet_amount:<fight_id>:<side>" | "await_deposit_custom"

# ================= helpers =================

async def ensure_user(x: Message | CallbackQuery) -> dict:
    tg_id = x.from_user.id
    username = x.from_user.username
    row = await db.fetchrow("SELECT * FROM app_user WHERE tg_user_id=$1", tg_id)
    if row:
        return row
    await db.execute(
        "INSERT INTO app_user (tg_user_id, username) VALUES ($1,$2) ON CONFLICT (tg_user_id) DO NOTHING",
        tg_id, username
    )
    return await db.fetchrow("SELECT * FROM app_user WHERE tg_user_id=$1", tg_id)

async def safe_edit_text(chat_id: int, text: str, reply_markup=None) -> None:
    mid = SCREEN_MSG.get(chat_id)
    if mid:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=mid, text=text, reply_markup=reply_markup)
            return
        except Exception:
            pass
    m = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    SCREEN_MSG[chat_id] = m.message_id

async def safe_edit_photo(chat_id: int, photo_url: str, caption: str, reply_markup=None) -> None:
    mid = SCREEN_MSG.get(chat_id)
    if mid:
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=mid,
                media=InputMediaPhoto(media=photo_url, caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=reply_markup
            )
            return
        except Exception:
            pass
    m = await bot.send_photo(chat_id, photo=photo_url, caption=caption, reply_markup=reply_markup)
    SCREEN_MSG[chat_id] = m.message_id

def parse_money_to_cents(s: str) -> Tuple[bool, int | str]:
    """
    Возвращает (ok, cents|error_msg). Разрешаем '10', '10.5', '10,50'
    Ограничиваем до 2 знаков после запятой.
    """
    txt = (s or "").strip().replace(" ", "").replace(",", ".")
    try:
        val = float(txt)
    except Exception:
        return False, "Некорректный формат. Пример: 10 или 10.5"
    if val <= 0:
        return False, "Сумма должна быть больше нуля."
    # максимум 2 знака
    if "." in txt:
        frac = txt.split(".", 1)[1]
        if len(frac) > 2:
            return False, "Не больше 2 знаков после запятой."
    cents = int(round(val * 100))
    if cents == 0:
        return False, "Слишком маленькая сумма."
    if cents > 10_000_000_00:  # 10 млн USDT (защита от опечаток)
        return False, "Слишком большая сумма."
    return True, cents

# ================= keyboards =================

def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 События", callback_data="menu:events")
    kb.button(text="💼 Мои ставки", callback_data="menu:mybets")
    kb.button(text="💰 Баланс", callback_data="menu:balance")
    kb.button(text="➕ Пополнить", callback_data="menu:deposit")
    kb.button(text="⬅️ Вывести", callback_data="menu:withdraw")
    kb.adjust(2, 2, 1)
    return kb.as_markup()

def events_kb(rows: List[dict]):
    kb = InlineKeyboardBuilder()
    for r in rows:
        title = f"{r['participant1_name']} vs {r['participant2_name']} — {r['starts_at'].strftime('%d.%m %H:%M') if r.get('starts_at') else ''}"
        kb.button(text=title, callback_data=f"fight:{r['id']}")
    kb.button(text="◀️ Назад", callback_data="menu:root")
    kb.adjust(1)
    return kb.as_markup()

def fight_card_kb(fight_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="Поставить на 1-го", callback_data=f"side:{fight_id}:1")
    kb.button(text="Поставить на 2-го", callback_data=f"side:{fight_id}:2")
    kb.button(text="◀️ К событиям", callback_data="menu:events")
    kb.adjust(1, 1, 1)
    return kb.as_markup()

def open_bets_kb(bets: List[dict], fight_id: int, side: int):
    kb = InlineKeyboardBuilder()
    for b in bets:
        amount = b["amount1_cents"] / 100
        kb.button(text=f"Принять {amount:.2f} USDT (id={b['id']})", callback_data=f"accept:{b['id']}")
    kb.button(text="💸 Создать свою ставку", callback_data=f"create_bet:{fight_id}:{side}")
    kb.button(text="◀️ Назад", callback_data=f"fight:{fight_id}")
    kb.adjust(1)
    return kb.as_markup()

def bet_enter_amount_kb(fight_id: int, side: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data=f"bet:cancel:{fight_id}:{side}")
    return kb.as_markup()

def deposit_rub_menu_kb():
    amounts = [25, 50, 100, 300, 500, 1000, 3000, 5000, 10000]
    kb = InlineKeyboardBuilder()
    for a in amounts:
        kb.button(text=f"{a} ₽", callback_data=f"deposit_rub:{a}")
    kb.button(text="◀️ Назад", callback_data="menu:root")
    kb.adjust(3, 3, 3, 1)
    return kb.as_markup()

def pay_usdt_kb(invoice_url: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="Оплатить в USDT (CryptoBot)", url=invoice_url)
    kb.button(text="◀️ В меню", callback_data="menu:root")
    kb.adjust(1, 1)
    return kb.as_markup()

# ================= texts =================

def fight_caption(f: dict) -> str:
    return (
        f"<b>{f['participant1_name']} vs {f['participant2_name']}</b>\n"
        f"Старт: {f['starts_at'].strftime('%d.%m %H:%M') if f.get('starts_at') else '—'}\n"
        f"Статус: {f.get('status','upcoming')}"
    )

# ================= screens =================

async def screen_root(chat_id: int):
    await safe_edit_text(chat_id, "Главное меню:", reply_markup=main_menu_kb())

async def screen_events(chat_id: int):
    rows = await db.fetch("""SELECT * FROM fight
                             WHERE status IN ('upcoming','today','live')
                             ORDER BY starts_at NULLS LAST, id""")
    if not rows:
        await safe_edit_text(chat_id, "Пока нет событий (таблица пустая или синк не прошёл).",
                             reply_markup=main_menu_kb())
        return
    await safe_edit_text(chat_id, "Выбери событие:", reply_markup=events_kb(rows))

async def screen_balance(chat_id: int, user: dict):
    await safe_edit_text(
        chat_id,
        f"Баланс: <b>{user['balance_cents']/100:.2f}</b> USDT, заморожено: <b>{user['held_cents']/100:.2f}</b>",
        reply_markup=main_menu_kb()
    )

async def screen_mybets(chat_id: int, user: dict):
    rows = await db.fetch(
        """SELECT d.*, f.participant1_name, f.participant2_name
           FROM deal d
           JOIN fight f ON f.id=d.fight_id
           WHERE d.user1_id=$1 OR d.user2_id=$1
           ORDER BY d.id DESC LIMIT 10""",
        user["id"]
    )
    if not rows:
        await safe_edit_text(chat_id, "Пока нет ставок.", reply_markup=main_menu_kb())
        return
    lines = []
    for r in rows:
        a = r["amount1_cents"]/100
        status = "ожидает оппонента" if r["user2_id"] is None else "схлопнута"
        lines.append(f"{r['participant1_name']} vs {r['participant2_name']}: {a:.2f} USDT ({status})")
    await safe_edit_text(chat_id, "Мои ставки:\n\n" + "\n".join(lines), reply_markup=main_menu_kb())

async def screen_fight(chat_id: int, fight_id: int):
    f = await db.fetchrow("SELECT * FROM fight WHERE id=$1", fight_id)
    if not f:
        await safe_edit_text(chat_id, "Событие не найдено.", reply_markup=main_menu_kb()); return
    photo = f.get("photo_url") or "https://picsum.photos/seed/placeholder/1200/700"
    await safe_edit_photo(chat_id, photo, fight_caption(f), reply_markup=fight_card_kb(fight_id))

async def screen_side(chat_id: int, fight_id: int, side: int):
    f = await db.fetchrow("SELECT * FROM fight WHERE id=$1", fight_id)
    if not f:
        await safe_edit_text(chat_id, "Событие не найдено.", reply_markup=main_menu_kb()); return
    bets = await db.fetch(
        """SELECT d.* FROM deal d
           WHERE d.fight_id=$1 AND d.user2_id IS NULL AND d.participant1=$2
           ORDER BY d.amount1_cents ASC, d.id""",
        fight_id, side
    )
    if not bets:
        txt = (f"{fight_caption(f)}\n\n"
               f"Открытых ставок на {'1-го' if side==1 else '2-го'} пока нет.\n"
               f"Нажми «Создать свою ставку».")
        await safe_edit_text(chat_id, txt, reply_markup=open_bets_kb([], fight_id, side))
        return
    txt = (f"{fight_caption(f)}\n\n"
           f"Выбери открытую ставку на {'1-го' if side==1 else '2-го'} или создай свою:")
    await safe_edit_text(chat_id, txt, reply_markup=open_bets_kb(bets, fight_id, side))

# ================= commands =================

@dp.message(Command("start"))
async def start_cmd(m: Message):
    STATE[m.from_user.id] = "idle"
    await ensure_user(m)
    await screen_root(m.chat.id)

# ================= callbacks =================

@dp.callback_query(F.data == "menu:root")
async def cb_root(cq: CallbackQuery):
    STATE[cq.from_user.id] = "idle"
    await cq.answer()
    await screen_root(cq.message.chat.id)

@dp.callback_query(F.data == "menu:events")
async def cb_events(cq: CallbackQuery):
    await cq.answer()
    await screen_events(cq.message.chat.id)

@dp.callback_query(F.data == "menu:balance")
async def cb_balance(cq: CallbackQuery):
    await cq.answer()
    user = await ensure_user(cq)
    await screen_balance(cq.message.chat.id, user)

@dp.callback_query(F.data == "menu:mybets")
async def cb_mybets(cq: CallbackQuery):
    await cq.answer()
    user = await ensure_user(cq)
    await screen_mybets(cq.message.chat.id, user)

@dp.callback_query(F.data == "menu:withdraw")
async def cb_withdraw(cq: CallbackQuery):
    await cq.answer("Вывод — пока заглушка.", show_alert=True)

# --- Пополнение (RUB -> USDT по курсу CryptoPay) ---

@dp.callback_query(F.data == "menu:deposit")
async def cb_deposit(cq: CallbackQuery):
    await cq.answer()
    await safe_edit_text(
        cq.message.chat.id,
        "Выбери сумму пополнения в ₽:",
        reply_markup=deposit_rub_menu_kb()
    )

@dp.callback_query(F.data.startswith("deposit_rub:"))
async def cb_deposit_rub(cq: CallbackQuery):
    await cq.answer()
    rub = float(cq.data.split(":")[1])
    user = await ensure_user(cq)
    try:
        usdt = await rub_to_usdt(rub)
    except CryptoPayError as e:
        await safe_edit_text(
            cq.message.chat.id,
            f"Не удалось получить курс у Crypto Pay. Попробуй позже.\n\n{e}",
            reply_markup=main_menu_kb()
        )
        return
    try:
        invoice = await create_deposit_invoice(user_id=user["id"], amount_usdt=usdt)
    except CryptoPayError as e:
        await safe_edit_text(
            cq.message.chat.id,
            f"Не удалось создать счёт на оплату. Попробуй позже.\n\n{e}",
            reply_markup=main_menu_kb()
        )
        return

    txt = (
        f"Пополнение баланса на <b>{int(rub)} ₽</b>\n"
        f"≈ <b>{usdt:.2f} USDT</b> по текущему курсу Crypto Pay.\n\n"
        f"Нажми кнопку ниже, чтобы оплатить через мини-приложение CryptoBot."
    )
    await safe_edit_text(
        cq.message.chat.id,
        txt,
        reply_markup=pay_usdt_kb(invoice["bot_invoice_url"])
    )

# --- Бои / ставки ---

@dp.callback_query(F.data.startswith("fight:"))
async def cb_fight(cq: CallbackQuery):
    await cq.answer()
    fight_id = int(cq.data.split(":")[1])
    STATE[cq.from_user.id] = "idle"
    await screen_fight(cq.message.chat.id, fight_id)

@dp.callback_query(F.data.startswith("side:"))
async def cb_side(cq: CallbackQuery):
    await cq.answer()
    fight_id, side = map(int, cq.data.split(":")[1:])
    STATE[cq.from_user.id] = "idle"
    await screen_side(cq.message.chat.id, fight_id, side)

@dp.callback_query(F.data.startswith("create_bet:"))
async def cb_create_bet(cq: CallbackQuery):
    await cq.answer()
    fight_id, side = map(int, cq.data.split(":")[1:])
    STATE[cq.from_user.id] = f"await_bet_amount:{fight_id}:{side}"
    await safe_edit_text(
        cq.message.chat.id,
        "Введи сумму ставки (USDT), например: <b>10</b> или <b>10.5</b>",
        reply_markup=bet_enter_amount_kb(fight_id, side)
    )

@dp.callback_query(F.data.startswith("bet:cancel:"))
async def cb_bet_cancel(cq: CallbackQuery):
    # Возврат к экрану выбора ставок на сторону
    await cq.answer()
    st = STATE.get(cq.from_user.id, "idle")
    _, _, fight_id, side = cq.data.split(":")
    fight_id = int(fight_id); side = int(side)
    STATE[cq.from_user.id] = "idle"
    await screen_side(cq.message.chat.id, fight_id, side)

@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(cq: CallbackQuery):
    await cq.answer()
    deal_id = int(cq.data.split(":")[1])
    deal = await db.fetchrow("SELECT * FROM deal WHERE id=$1", deal_id)
    if not deal or deal["user2_id"] is not None:
        await safe_edit_text(cq.message.chat.id, "Ставка уже недоступна.", reply_markup=main_menu_kb()); return
    me = await ensure_user(cq)
    if deal["user1_id"] == me["id"]:
        await safe_edit_text(cq.message.chat.id, "Нельзя принять собственную ставку.", reply_markup=main_menu_kb()); return

    need = deal["amount1_cents"]
    if me["balance_cents"] < need:
        await safe_edit_text(cq.message.chat.id, f"Недостаточно средств. Нужно {need/100:.2f} USDT. Пополни баланс.",
                             reply_markup=main_menu_kb()); return

    async with (await db.get_pool()).acquire() as conn:
        tr = conn.transaction(); await tr.start()
        try:
            fresh = await conn.fetchrow("SELECT * FROM deal WHERE id=$1 FOR UPDATE", deal_id)
            if not fresh or fresh["user2_id"] is not None:
                await tr.rollback()
                await safe_edit_text(cq.message.chat.id, "Ставку только что забрали.", reply_markup=main_menu_kb()); return
            await conn.execute(
                "UPDATE deal SET user2_id=$1, amount2_cents=$2, participant2=$3, matched_at=now() WHERE id=$4",
                me["id"], fresh["amount1_cents"], (2 if fresh["participant1"] == 1 else 1), deal_id
            )
            await conn.execute(
                "UPDATE app_user SET balance_cents=balance_cents-$1, held_cents=held_cents+$1 WHERE id=$2",
                need, me["id"]
            )
            await conn.execute(
                "INSERT INTO ledger (user_id, kind, amount_cents, ref_deal_id) VALUES ($1,'bet_hold',$2,$3)",
                me["id"], -need, deal_id
            )
            await tr.commit()
        except Exception:
            await tr.rollback()
            raise

    await safe_edit_text(cq.message.chat.id, f"Ты принял ставку (id={deal_id}) на {need/100:.2f} USDT. Ждём результат боя.",
                         reply_markup=main_menu_kb())

# ================= text input =================

@dp.message(F.text)
async def on_text(m: Message):
    user = await ensure_user(m)
    st = STATE.get(m.from_user.id, "idle")

    # --- ожидаем сумму ставки ---
    if st.startswith("await_bet_amount:"):
        _, fight_id, side = st.split(":"); fight_id = int(fight_id); side = int(side)

        ok, res = parse_money_to_cents(m.text)
        if not ok:
            await safe_edit_text(
                m.chat.id,
                f"Ошибка: {res}\n\nВведи сумму ставки (USDT), например: <b>10</b> или <b>10.5</b>",
                reply_markup=bet_enter_amount_kb(fight_id, side)
            )
            return
        cents = int(res)

        # баланс
        fresh_user = await db.fetchrow("SELECT id, balance_cents FROM app_user WHERE id=$1", user["id"])
        if fresh_user["balance_cents"] < cents:
            await safe_edit_text(
                m.chat.id,
                f"Недостаточно средств. Баланс: {fresh_user['balance_cents']/100:.2f} USDT.",
                reply_markup=main_menu_kb()
            )
            STATE[m.from_user.id] = "idle"
            return

        # создаём ставку и замораживаем
        async with (await db.get_pool()).acquire() as conn:
            tr = conn.transaction(); await tr.start()
            try:
                await conn.execute(
                    "INSERT INTO deal (fight_id, user1_id, participant1, amount1_cents) VALUES ($1,$2,$3,$4)",
                    fight_id, user["id"], side, cents
                )
                await conn.execute(
                    "UPDATE app_user SET balance_cents=balance_cents-$1, held_cents=held_cents+$1 WHERE id=$2",
                    cents, user["id"]
                )
                await conn.execute(
                    "INSERT INTO ledger (user_id, kind, amount_cents) VALUES ($1,'bet_hold',$2)",
                    user["id"], -cents
                )
                await tr.commit()
            except Exception:
                await tr.rollback()
                raise

        STATE[m.from_user.id] = "idle"
        await safe_edit_text(
            m.chat.id,
            f"Ставка на <b>{cents/100:.2f} USDT</b> создана. Ждём оппонента.",
            reply_markup=main_menu_kb()
        )
        return

    # --- любой другой текст в idle ---
    await safe_edit_text(m.chat.id, "Извините, я не знаю такой команды.", reply_markup=main_menu_kb())

# ================= run =================

async def main():
    # гарантия, что таблицы есть (не обязательно, но удобно)
    await db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())





