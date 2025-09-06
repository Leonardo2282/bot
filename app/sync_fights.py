import asyncio
from datetime import datetime
from typing import List, Dict, Any

from . import db
from .google_sheets import fetch_fights_from_sheet

SQL_UPSERT = """
INSERT INTO fight(external_id, title, participant1_name, participant2_name, photo_url, description, starts_at, status, winner_participant)
VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
ON CONFLICT (external_id) DO UPDATE
   SET title=EXCLUDED.title,
       participant1_name=EXCLUDED.participant1_name,
       participant2_name=EXCLUDED.participant2_name,
       photo_url=EXCLUDED.photo_url,
       description=EXCLUDED.description,
       starts_at=EXCLUDED.starts_at,
       status=EXCLUDED.status,
       winner_participant=EXCLUDED.winner_participant
RETURNING id;
"""

async def sync_once():
    items = fetch_fights_from_sheet()
    for it in items:
        fid = await db.fetchval(SQL_UPSERT,
                                it["external_id"],
                                it["title"],
                                it["p1"],
                                it["p2"],
                                it["photo_url"],
                                it["description"],
                                it["starts_at"],
                                it["status"],
                                it["winner"])
    # Также можно удалять из БД те external_id, которых нет в таблице — по желанию

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=20)
    args = parser.parse_args()

    if args.watch:
        print(f"[SYNC] watch started (interval={args.interval}s). Ctrl+C to stop.")
        while True:
            try:
                await sync_once()
            except Exception as ex:
                print(f"[SYNC] ERROR: {ex}")
            await asyncio.sleep(args.interval)
    else:
        await sync_once()
        print("[SYNC] done.")

if __name__ == "__main__":
    asyncio.run(main())