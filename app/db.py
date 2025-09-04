import argparse
import asyncio
import os
from typing import Any, List, Optional, Mapping

import asyncpg

# ---------- ENV ----------
PGUSER = os.getenv("PGUSER", "app")
PGPASSWORD = os.getenv("PGPASSWORD", "app")
PGDATABASE = os.getenv("PGDATABASE", "bets")
PGHOST = os.getenv("PGHOST", "127.0.0.1")
PGPORT = int(os.getenv("PGPORT", "5432"))

_pool: Optional[asyncpg.Pool] = None


# ---------- POOL ----------
async def _conn_init(conn: asyncpg.Connection) -> None:
    # Единые сесс. настройки: схема и таймзона
    await conn.execute("""
        SET search_path TO public;
        SET TIME ZONE 'UTC';
    """)

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            user=PGUSER,
            password=PGPASSWORD,
            database=PGDATABASE,
            host=PGHOST,
            port=PGPORT,
            min_size=1,
            max_size=10,
            init=_conn_init,
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


# ---------- SCHEMA ----------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_user (
    id              BIGSERIAL PRIMARY KEY,
    tg_user_id      BIGINT UNIQUE NOT NULL,
    username        TEXT,
    balance_cents   BIGINT NOT NULL DEFAULT 0,
    held_cents      BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fight (
    id                  BIGSERIAL PRIMARY KEY,
    external_id         TEXT NULL,              -- id строки из Google Sheets (для upsert)
    title               TEXT NOT NULL,
    participant1_name   TEXT NOT NULL,
    participant2_name   TEXT NOT NULL,
    photo_url           TEXT,
    starts_at           TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'upcoming',  -- upcoming | today | live | done | canceled
    winner_participant  INT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (external_id)
);

-- ВНИМАНИЕ: именно 'deal' (без 's').
-- Ошибка 'relation "deal" does not exist' как раз из-за отсутствия этой таблицы.
CREATE TABLE IF NOT EXISTS deal (
    id              BIGSERIAL PRIMARY KEY,
    fight_id        BIGINT NOT NULL REFERENCES fight(id) ON DELETE CASCADE,
    user1_id        BIGINT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    participant1    INT NOT NULL CHECK (participant1 IN (1,2)),
    amount1_cents   BIGINT NOT NULL CHECK (amount1_cents > 0),

    user2_id        BIGINT NULL REFERENCES app_user(id) ON DELETE SET NULL,
    participant2    INT NULL CHECK (participant2 IN (1,2)),
    amount2_cents   BIGINT NULL CHECK (amount2_cents > 0),

    matched_at      TIMESTAMPTZ NULL,
    paid            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deal_fight_id ON deal(fight_id);
CREATE INDEX IF NOT EXISTS idx_deal_user1_id ON deal(user1_id);
CREATE INDEX IF NOT EXISTS idx_deal_user2_id ON deal(user2_id);

CREATE TABLE IF NOT EXISTS ledger (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NULL REFERENCES app_user(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL,              -- deposit | withdraw | bet_hold | bet_settle | fee
    amount_cents    BIGINT NOT NULL,            -- >0 зачисление, <0 списание
    ref_deal_id     BIGINT NULL REFERENCES deal(id) ON DELETE SET NULL,
    external_ref    TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (external_ref)
);

CREATE TABLE IF NOT EXISTS invoice (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    invoice_id      BIGINT UNIQUE NOT NULL,     -- ID из CryptoPay
    asset           TEXT NOT NULL,
    amount_cents    BIGINT NOT NULL,
    status          TEXT NOT NULL,              -- active | paid | expired ...
    bot_invoice_url TEXT NULL,                  -- ссылка/mini app url
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

SEED_SQL = """
INSERT INTO fight (external_id, title, participant1_name, participant2_name, photo_url, starts_at, status)
VALUES
('demo-101','Alpha vs Bravo','Alpha','Bravo','https://picsum.photos/seed/fight101/1200/700', now() + interval '6 hour', 'upcoming'),
('demo-102','Charlie vs Delta','Charlie','Delta','https://picsum.photos/seed/fight102/1200/700', now() + interval '8 hour', 'upcoming'),
('demo-201','Echo vs Foxtrot','Echo','Foxtrot','https://picsum.photos/seed/fight201/1200/700', now() + interval '1 day 2 hour', 'upcoming')
ON CONFLICT (external_id) DO UPDATE
SET title = EXCLUDED.title,
    participant1_name = EXCLUDED.participant1_name,
    participant2_name = EXCLUDED.participant2_name,
    photo_url = EXCLUDED.photo_url,
    starts_at = EXCLUDED.starts_at,
    status = EXCLUDED.status;
"""


# ---------- DDL helpers ----------
async def init_db() -> None:
    """Создаёт все нужные таблицы/индексы (идемпотентно)."""
    await execute(SCHEMA_SQL)

async def drop_db_objects() -> None:
    """Аккуратно удаляет объекты проекта (без удаления самой БД)."""
    sql = """
    DROP TABLE IF EXISTS invoice       CASCADE;
    DROP TABLE IF EXISTS ledger        CASCADE;
    DROP TABLE IF EXISTS deal          CASCADE;
    DROP TABLE IF EXISTS fight         CASCADE;
    DROP TABLE IF EXISTS app_user      CASCADE;
    """
    await execute(sql)

async def seed_demo() -> None:
    await execute(SEED_SQL)


# ---------- CLI ----------
def main_cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true", help="create/update schema")
    parser.add_argument("--seed", action="store_true", help="insert demo data")
    parser.add_argument("--drop", action="store_true", help="drop project tables")
    args = parser.parse_args()

    async def _run():
        if args.drop:
            print("Dropping project tables...")
            await drop_db_objects()
            print("Dropped.")
        if args.init:
            print("Initializing schema...")
            await init_db()
            print("Schema ready.")
        if args.seed:
            print("Seeding demo data...")
            await seed_demo()
            print("Seed done.")

    asyncio.run(_run())


if __name__ == "__main__":
    main_cli()



