import asyncio
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import settings
from .db import fetch

async def run_once():
    bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # бои, стартовавшие > 15 минут назад, без winner_participant
    rows = await fetch(
        "SELECT id, title, starts_at FROM fight "
        "WHERE starts_at <= now() - interval '15 minutes' "
        "AND winner_participant IS NULL "
        "ORDER BY starts_at ASC LIMIT 20"
    )
    if not rows:
        await bot.session.close()
        return
    text_lines = [f"#{r['id']} • <b>{r['title']}</b> — стартовал {r['starts_at']:%d.%m %H:%M}"]
    text = "Нужно внести результат боя(ев):\n" + "\n".join(text_lines) + \
           "\n\nИспользуй: <code>/admin_fight_result &lt;fight_id&gt; &lt;1|2&gt;</code>"

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass
    await bot.session.close()

async def main():
    await run_once()

if __name__ == "__main__":
    asyncio.run(main())
