import asyncio

from .config import settings
from .db import get_pool

FEE_PCT = settings.FEE_PCT

async def settle_once():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT d.id, d.fight_id, d.user1_id, d.amount1_cents, d.participant1,
                       d.user2_id, d.amount2_cents, d.participant2,
                       f.winner_participant
                FROM deal d
                JOIN fight f ON f.id = d.fight_id
                WHERE d.paid = FALSE
                  AND d.user2_id IS NOT NULL
                  AND f.status = 'done'
                  AND f.winner_participant IN (1,2)
                FOR UPDATE SKIP LOCKED
                """
            )
            for r in rows:
                total = (r['amount1_cents'] or 0) + (r['amount2_cents'] or 0)
                payout = int(round(total * (1 - FEE_PCT)))
                if r['winner_participant'] == r['participant1']:
                    winner_id = r['user1_id']
                elif r['winner_participant'] == r['participant2']:
                    winner_id = r['user2_id']
                else:
                    continue

                # освобождаем hold и начисляем выигрыш
                await conn.execute("UPDATE app_user SET held_cents=held_cents-$1 WHERE id=$2", r['amount1_cents'], r['user1_id'])
                await conn.execute("UPDATE app_user SET held_cents=held_cents-$1 WHERE id=$2", r['amount2_cents'], r['user2_id'])
                await conn.execute("UPDATE app_user SET balance_cents=balance_cents+$1 WHERE id=$2", payout, winner_id)

                await conn.execute("UPDATE deal SET paid=TRUE WHERE id=$1", r['id'])
                await conn.execute("INSERT INTO ledger(user_id, kind, amount_cents, ref_deal_id) VALUES ($1,'bet_settle',$2,$3)", winner_id, payout, r['id'])
                fee_cents = total - payout
                await conn.execute("INSERT INTO ledger(user_id, kind, amount_cents, ref_deal_id) VALUES (NULL,'fee',$1,$2)", fee_cents, r['id'])

async def main():
    while True:
        try:
            await settle_once()
        except Exception as e:
            print("settlement error:", e)
        await asyncio.sleep(30)

if __name__ == '__main__':
    asyncio.run(main())
