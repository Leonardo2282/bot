# app/db.py
import argparse
import asyncpg
from typing import Any, Dict, List, Mapping, Optional

from .config import settings

_pool: Optional[asyncpg.Pool] = None


# ===== pool / helpers =====
async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            user=settings.PGUSER,
            password=getattr(settings, "PGPASSWORD", None),
            database=settings.PGDATABASE,
            host=settings.PGHOST,
            port=settings.PGPORT,
            min_size=1,
            max_size=10,
        )
    return _pool


async def execute(sql: str, *args) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)


async def fetch(sql: str, *args) -> List[Mapping[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(sql, *args)


async def fetchrow(sql: str, *args) -> Optional[Mapping[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql, *args)


async def fetchval(sql: str, *args) -> Any:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)


# ===== schema =====
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_user (
    id           BIGSERIAL PRIMARY KEY,
    tg_user_id   BIGINT UNIQUE NOT NULL,
    username     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fight (
    id                  BIGSERIAL PRIMARY KEY,
    external_id         TEXT UNIQUE,
    title               TEXT NOT NULL,
    participant1_name   TEXT NOT NULL,
    participant2_name   TEXT NOT NULL,
    photo_url           TEXT,
    description         TEXT,
    starts_at           TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'upcoming',  -- upcoming|today|live|done
    winner_participant  INT NULL                            -- 1|2
);

CREATE TABLE IF NOT EXISTS deal (
    id              BIGSERIAL PRIMARY KEY,
    fight_id        BIGINT NOT NULL REFERENCES fight(id) ON DELETE CASCADE,

    -- сторона 1 (создатель)
    user1_id        BIGINT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    participant1    INT NOT NULL,                 -- 1|2
    amount1_cents   BIGINT NOT NULL,
    paid1           BOOLEAN NOT NULL DEFAULT FALSE,
    invoice1_id     BIGINT NULL,

    -- сторона 2 (ответивший)
    user2_id        BIGINT NULL REFERENCES app_user(id) ON DELETE SET NULL,
    participant2    INT NULL,                     -- 1|2
    amount2_cents   BIGINT NULL,
    paid2           BOOLEAN NOT NULL DEFAULT FALSE,
    invoice2_id     BIGINT NULL,

    status          TEXT NOT NULL DEFAULT 'awaiting_match'  -- awaiting_match|matched|void|settled
);

CREATE TABLE IF NOT EXISTS invoice_wait (
    invoice_id  BIGINT PRIMARY KEY,
    kind        TEXT NOT NULL,        -- NEW | MATCH
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


# ===== users =====
async def ensure_user_by_tg(tg_user_id: int, username: Optional[str]) -> Mapping[str, Any]:
    row = await fetchrow(
        "SELECT * FROM app_user WHERE tg_user_id=$1",
        tg_user_id,
    )
    if row:
        # обновим username, если поменялся
        if username and row["username"] != username:
            await execute("UPDATE app_user SET username=$1 WHERE id=$2", username, row["id"])
        return row

    await execute(
        "INSERT INTO app_user(tg_user_id, username) VALUES($1,$2)",
        tg_user_id, username,
    )
    return await fetchrow("SELECT * FROM app_user WHERE tg_user_id=$1", tg_user_id)


# ===== fights =====
async def list_upcoming() -> List[Mapping[str, Any]]:
    return await fetch(
        """
        SELECT * FROM fight
        WHERE status IN ('upcoming','today','live')
        ORDER BY starts_at NULLS LAST, id
        """
    )


async def get_fight(fight_id: int) -> Optional[Mapping[str, Any]]:
    return await fetchrow("SELECT * FROM fight WHERE id=$1", fight_id)


async def upsert_fights(items: List[Dict[str, Any]]) -> None:
    """
    items: dict с полями:
      - external_id (str)
      - title (str)
      - participant1_name (str)
      - participant2_name (str)
      - photo_url (str|None)
      - description (str|None)
      - starts_at (datetime|None)
      - status (str)  'upcoming'|'today'|'live'|'done'
      - winner_participant (int|None)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for it in items:
                await conn.execute(
                    """
                    INSERT INTO fight (external_id, title, participant1_name, participant2_name,
                                       photo_url, description, starts_at, status, winner_participant)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (external_id) DO UPDATE
                      SET title=EXCLUDED.title,
                          participant1_name=EXCLUDED.participant1_name,
                          participant2_name=EXCLUDED.participant2_name,
                          photo_url=EXCLUDED.photo_url,
                          description=EXCLUDED.description,
                          starts_at=EXCLUDED.starts_at,
                          status=EXCLUDED.status,
                          winner_participant=EXCLUDED.winner_participant
                    """,
                    it.get("external_id"),
                    it.get("title"),
                    it.get("participant1_name"),
                    it.get("participant2_name"),
                    it.get("photo_url"),
                    it.get("description"),
                    it.get("starts_at"),
                    it.get("status"),
                    it.get("winner_participant"),
                )


# ===== deals (ставки) =====
async def list_open_deals(fight_id: int, exclude_user_id: Optional[int] = None) -> List[Mapping[str, Any]]:
    if exclude_user_id:
        return await fetch(
            """
            SELECT d.*
            FROM deal d
            WHERE d.fight_id = $1
              AND d.status = 'awaiting_match'
              AND d.user1_id <> $2
            ORDER BY d.id
            """,
            fight_id, exclude_user_id
        )
    else:
        return await fetch(
            """
            SELECT d.*
            FROM deal d
            WHERE d.fight_id = $1
              AND d.status = 'awaiting_match'
            ORDER BY d.id
            """,
            fight_id
        )


async def list_my_deals(user_id: int) -> List[Mapping[str, Any]]:
    return await fetch(
        """
        SELECT d.*, f.title, f.participant1_name AS p1, f.participant2_name AS p2
        FROM deal d
        JOIN fight f ON f.id=d.fight_id
        WHERE d.user1_id=$1 OR d.user2_id=$1
        ORDER BY d.id DESC
        LIMIT 100
        """,
        user_id,
    )

# --- AUTO CHECK (универсально для обычных и inline-сообщений) ---

# == invoices wait ==
import json

async def add_invoice_wait(invoice_id: int, kind: str, payload: dict) -> None:
    await execute(
        "INSERT INTO invoice_wait(invoice_id, kind, payload) VALUES($1,$2,$3) "
        "ON CONFLICT (invoice_id) DO UPDATE SET kind=EXCLUDED.kind, payload=EXCLUDED.payload",
        invoice_id, kind, json.dumps(payload, ensure_ascii=False)
    )


async def get_invoice_wait(invoice_id: int) -> Optional[Mapping[str, Any]]:
    return await fetchrow("SELECT * FROM invoice_wait WHERE invoice_id=$1", invoice_id)


async def del_invoice_wait(invoice_id: int) -> None:
    await execute("DELETE FROM invoice_wait WHERE invoice_id=$1", invoice_id)


async def pending_invoice_ids() -> List[int]:
    rows = await fetch("SELECT invoice_id FROM invoice_wait ORDER BY created_at")
    return [int(r["invoice_id"]) for r in rows]


# == create/match after paid ==
async def create_deal_after_paid(payload: Dict[str, Any], invoice_id: int, user_id: int) -> None:
    """
    Платёж первой стороны прошёл.
    payload: { fight_id, participant, amount_cents, tg_user_id }
    Логика:
      1) пытаемся найти встречную СУЩЕСТВУЮЩУЮ ставку (оплачена 1-й стороной, противоположная сторона, та же сумма).
         Если нашли — дописываем её как user2 (наш пользователь), статус -> matched.
      2) иначе создаём новую запись как awaiting_match.
    """
    fight_id = int(payload["fight_id"])
    side = int(payload["participant"])
    amount_cents = int(payload["amount_cents"])

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # ищем встречную открытую
            opp = await conn.fetchrow(
                """
                SELECT * FROM deal
                WHERE fight_id=$1
                  AND paid1=TRUE
                  AND user2_id IS NULL
                  AND status='awaiting_match'
                  AND participant1 = CASE WHEN $2=1 THEN 2 ELSE 1 END
                  AND amount1_cents = $3
                  AND user1_id <> $4
                ORDER BY id
                LIMIT 1
                """,
                fight_id, side, amount_cents, user_id,
            )
            if opp:
                await conn.execute(
                    """
                    UPDATE deal
                    SET user2_id=$1,
                        participant2=$2,
                        amount2_cents=$3,
                        paid2=TRUE,
                        invoice2_id=$4,
                        status='matched'
                    WHERE id=$5
                    """,
                    user_id, side, amount_cents, invoice_id, opp["id"]
                )
                return

            # нет встречной — создаём новую как «ждёт ответ»
            await conn.execute(
                """
                INSERT INTO deal (fight_id, user1_id, participant1, amount1_cents, paid1, invoice1_id, status)
                VALUES ($1,$2,$3,$4,TRUE,$5,'awaiting_match')
                """,
                fight_id, user_id, side, amount_cents, invoice_id
            )


async def match_deal_after_paid(payload: Dict[str, Any], invoice_id: int, user_id: int) -> None:
    """
    Ответ на конкретную ставку (вариант «Reply» из бота).
    payload: { deal_id, participant, amount_cents, tg_user_id }
    """
    deal_id = int(payload["deal_id"])
    side = int(payload["participant"])
    amount_cents = int(payload["amount_cents"])

    await execute(
        """
        UPDATE deal
        SET user2_id=$1,
            participant2=$2,
            amount2_cents=$3,
            paid2=TRUE,
            invoice2_id=$4,
            status='matched'
        WHERE id=$5
          AND status='awaiting_match'
          AND user2_id IS NULL
        """,
        user_id, side, amount_cents, invoice_id, deal_id
    )


# ===== CLI: init db =====
async def init_db():
    await execute(SCHEMA_SQL)

async def list_deals_to_settle(limit: int = 100) -> List[Mapping[str, Any]]:
    """
    Возвращает сделки, которые нужно закрыть:
      - fight в статусе 'done'
      - deal в статусе 'matched'
    """
    return await fetch(
        """
        SELECT d.*, f.winner_participant, f.title, f.participant1_name, f.participant2_name
        FROM deal d
        JOIN fight f ON f.id=d.fight_id
        WHERE f.status='done'
          AND d.status='matched'
        ORDER BY d.id
        LIMIT $1
        """,
        limit,
    )

def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()

    import asyncio

    async def _run():
        if args.init:
            await init_db()
            print("DB initialized.")

    asyncio.run(_run())


if __name__ == "__main__":
    main_cli()