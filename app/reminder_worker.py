import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from .config import settings
from . import db

BOT = Bot(settings.BOT_TOKEN)

async def notify_admins(text: str):
    for aid in settings.ADMIN_IDS:
        try:
            await BOT.send_message(aid, text)
        except Exception:
            pass

async def loop():
    while True:
        try:
            # Найти бои, у которых starts_at прошёл > 1 часа, а статус всё ещё не done
            rows = await db.fetch("""
                SELECT * FROM fight
                WHERE (status IN ('upcoming','today','live'))
                  AND starts_at IS NOT NULL
                  AND starts_at < now() - interval '1 hour'
                ORDER BY starts_at
                LIMIT 20
            """)
            if rows:
                for r in rows:
                    await notify_admins(
                        f"⚠️ Пора выставить результат по бою:\n<b>{r['title']}</b>\n"
                        f"{r['participant1_name']} vs {r['participant2_name']}\n"
                        f"ID: {r['id']}  (status={r['status']})"
                    )
            await asyncio.sleep(600)  # каждые 10 минут
        except Exception:
            await asyncio.sleep(600)

async def main():
    await loop()

if __name__ == "__main__":
    asyncio.run(main())