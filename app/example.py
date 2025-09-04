import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from app.config import settings

# ======================
# BOT
# ======================
bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ======================
# MOCK DATA (каждое событие = один бой)
# ======================
EVENTS = [
    {
        "id": 101,
        "title": "Alpha vs Bravo",
        "p1": "Alpha",
        "p2": "Bravo",
        "photo": "https://picsum.photos/seed/fight101/1200/700",
        "starts_at": datetime.now() + timedelta(hours=6),
        "status": "upcoming",
        "description": "Alpha против Bravo — столкновение стилей и характеров.",
    },
    {
        "id": 102,
        "title": "Charlie vs Delta",
        "p1": "Charlie",
        "p2": "Delta",
        "photo": "https://picsum.photos/seed/fight102/1200/700",
        "starts_at": datetime.now() + timedelta(hours=8),
        "status": "upcoming",
        "description": "Долгожданный реванш: кто заберёт вечер?",
    },
    {
        "id": 201,
        "title": "Echo vs Foxtrot",
        "p1": "Echo",
        "p2": "Foxtrot",
        "photo": "https://picsum.photos/seed/fight201/1200/700",
        "starts_at": datetime.now() + timedelta(days=1, hours=2),
        "status": "upcoming",
        "description": "Скоростной технарь против панчера — ставки будут жаркими.",
    },
]

MY_NOTIFICATIONS = {}

# ======================
# HELPERS
# ======================
def main_menu_kb(notify_on: bool = True):
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 События", callback_data="menu:events")
    kb.button(text="💼 Мои ставки", callback_data="menu:mybets")
    kb.button(text="💰 Баланс", callback_data="menu:balance")
    kb.button(text="➕ Пополнить", callback_data="menu:deposit")
    kb.button(text="⬅️ Вывести", callback_data="menu:withdraw")
    kb.button(text=("🔔 Уведомления: ON" if notify_on else "🔕 Уведомления: OFF"),
              callback_data="menu:notify_toggle")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def back_to_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ В меню", callback_data="menu:root")
    return kb.as_markup()


def events_kb():
    kb = InlineKeyboardBuilder()
    for f in sorted(EVENTS, key=lambda x: x["starts_at"]):
        label = f"{f['title']} — {f['starts_at']:%d.%m %H:%M}"
        kb.button(text=label, callback_data=f"fight:{f['id']}")
    kb.button(text="◀️ Назад", callback_data="menu:root")
    kb.adjust(1)
    return kb.as_markup()


def fight_card_kb(fight_id: int, p1: str, p2: str):
    kb = InlineKeyboardBuilder()
    kb.button(text=f"Поставить на {p1}", callback_data=f"bet:{fight_id}:1")
    kb.button(text=f"Поставить на {p2}", callback_data=f"bet:{fight_id}:2")
    kb.button(text="◀️ К событиям", callback_data="menu:events")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


async def safe_edit(message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest:
        await message.answer(text, **kwargs)

# ======================
# HANDLERS
# ======================
@dp.message(Command("start"))
async def cmd_start(m: Message):
    MY_NOTIFICATIONS[m.from_user.id] = True
    await m.answer("Бот-демо с заглушками. Посмотри кнопки и флоу.",
                   reply_markup=main_menu_kb(True))


@dp.callback_query(F.data == "menu:root")
async def cb_root(cq: CallbackQuery):
    notify = MY_NOTIFICATIONS.get(cq.from_user.id, True)
    await safe_edit(cq.message, "Главное меню:", reply_markup=main_menu_kb(notify))


@dp.callback_query(F.data == "menu:events")
async def cb_events(cq: CallbackQuery):
    await safe_edit(cq.message, "Выбери событие (бой):", reply_markup=events_kb())


@dp.callback_query(F.data.startswith("fight:"))
async def cb_fight(cq: CallbackQuery):
    fight_id = int(cq.data.split(":")[1])
    fight = next((f for f in EVENTS if f["id"] == fight_id), None)
    if not fight:
        await cq.answer("Поединок не найден", show_alert=True)
        return
    caption = (f"<b>{fight['title']}</b>\n{fight['p1']} vs {fight['p2']}\n"
               f"Старт: {fight['starts_at']:%d.%m %H:%M}\n"
               f"Статус: {fight['status']}\n\n{fight.get('description','')}")
    await cq.message.answer_photo(
        photo=fight["photo"],
        caption=caption,
        reply_markup=fight_card_kb(fight_id, fight["p1"], fight["p2"])
    )


@dp.callback_query(F.data.startswith("bet:"))
async def cb_bet(cq: CallbackQuery):
    _, fight_id, side = cq.data.split(":")
    await cq.answer()
    await cq.message.answer(
        f"Заглушка ставки: fight_id={fight_id}, сторона={side}. "
        f"В боевом режиме тут откроем ввод суммы/подтверждение."
    )


@dp.callback_query(F.data == "menu:mybets")
async def cb_mybets(cq: CallbackQuery):
    await safe_edit(
        cq.message,
        "Мои ставки (заглушка). Тут будет список активных и завершённых сделок.",
        reply_markup=back_to_menu_kb(),
    )


@dp.callback_query(F.data == "menu:balance")
async def cb_balance(cq: CallbackQuery):
    await safe_edit(
        cq.message,
        "Баланс: <b>100.00</b> USDT, заморожено: <b>0.00</b> (заглушка)",
        reply_markup=back_to_menu_kb(),
    )


@dp.callback_query(F.data == "menu:deposit")
async def cb_deposit(cq: CallbackQuery):
    await safe_edit(
        cq.message,
        "Пополнение (заглушка). Тут покажем кнопку 'Создать счёт' и сумму.",
        reply_markup=back_to_menu_kb(),
    )


@dp.callback_query(F.data == "menu:withdraw")
async def cb_withdraw(cq: CallbackQuery):
    await safe_edit(
        cq.message,
        "Вывод (заглушка). Тут запросим сумму и отправим через Crypto Pay.",
        reply_markup=back_to_menu_kb(),
    )


@dp.callback_query(F.data == "menu:notify_toggle")
async def cb_toggle(cq: CallbackQuery):
    cur = MY_NOTIFICATIONS.get(cq.from_user.id, True)
    MY_NOTIFICATIONS[cq.from_user.id] = not cur
    await cb_root(cq)

# ======================
# ENTRYPOINT
# ======================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
