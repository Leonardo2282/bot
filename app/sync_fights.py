# app/sync_fights.py
import os
import argparse
import asyncio
from typing import Optional, List

from .db import execute, fetchval, fetch
from .google_sheets import fetch_fights_from_sheet, SheetFight


# ====== SQL ======
# Ищем бой по "уникальной тройке": (title, p1, p2)
SQL_FIND_EXISTING = """
SELECT id FROM fight
WHERE title = $1 AND participant1_name = $2 AND participant2_name = $3
LIMIT 1;
"""

# Создаём новый бой
SQL_INSERT = """
INSERT INTO fight (title, participant1_name, participant2_name, photo_url, starts_at, status)
VALUES ($1,$2,$3,$4,$5,$6)
RETURNING id;
"""

# Обновляем существующий
SQL_UPDATE = """
UPDATE fight
SET photo_url = $2,
    starts_at = $3,
    status    = $4
WHERE id = $1;
"""

# Выгрузить все id боёв
SQL_ALL_IDS = "SELECT id, title, participant1_name, participant2_name FROM fight;"

# Удалить бой по id
SQL_DELETE = "DELETE FROM fight WHERE id = ANY($1::bigint[]);"


async def upsert_fight(item: SheetFight) -> int:
    """
    Возвращает id боя: найденного или созданного.
    """
    fid = await fetchval(SQL_FIND_EXISTING, item.title, item.p1, item.p2)
    if fid is None:
        fid = await fetchval(
            SQL_INSERT,
            item.title,
            item.p1,
            item.p2,
            item.photo_url,
            item.starts_at,
            item.status,
        )
        print(f"[SYNC] + created fight #{fid}: {item.title!r}")
    else:
        await execute(SQL_UPDATE, fid, item.photo_url, item.starts_at, item.status)
        print(f"[SYNC] * updated fight #{fid}: {item.title!r}")
    return fid


async def sync_fights_once() -> None:
    """
    Разовая синхронизация: читает строки из Google Sheets и приводит таблицу fight в актуальное состояние.
    """
    items = fetch_fights_from_sheet()
    print(f"[SYNC] will upsert {len(items)} items")

    touched_ids: List[int] = []
    for it in items:
        fid = await upsert_fight(it)
        touched_ids.append(fid)

    # Удаление старых боёв
    all_db = await fetch(SQL_ALL_IDS)
    all_ids = [r["id"] for r in all_db]
    to_delete = list(set(all_ids) - set(touched_ids))

    if to_delete:
        await execute(SQL_DELETE, to_delete)
        print(f"[SYNC] - deleted {len(to_delete)} old fights")

    print(f"[SYNC] done. touched={len(touched_ids)} kept, deleted={len(to_delete)}")


async def watch_loop(poll_seconds: Optional[int] = None) -> None:
    """
    Бесконечный цикл синка: опрашивает таблицу раз в poll_seconds (по умолчанию из .env).
    """
    if poll_seconds is None:
        try:
            poll_seconds = int(os.getenv("GSHEET_POLL_SECONDS", "20"))
        except ValueError:
            poll_seconds = 20

    print(f"[SYNC] watch started (interval={poll_seconds}s). Ctrl+C to stop.")
    while True:
        try:
            await sync_fights_once()
        except Exception as e:
            # Логируем и ждём подольше, чтобы не долбить API при ошибках
            print(f"[SYNC] ERROR: {e!r}")
            await asyncio.sleep(max(poll_seconds, 30))
            continue

        await asyncio.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="Sync fights from Google Sheets to DB")
    parser.add_argument("--once", action="store_true", help="Run single sync and exit")
    parser.add_argument("--watch", action="store_true", help="Run continuous sync loop")
    parser.add_argument("--interval", type=int, default=None, help="Poll interval seconds (overrides env)")
    args = parser.parse_args()

    async def _run():
        if args.once and not args.watch:
            await sync_fights_once()
            return
        # по умолчанию — watcher
        await watch_loop(args.interval)

    asyncio.run(_run())


if __name__ == "__main__":
    main()


