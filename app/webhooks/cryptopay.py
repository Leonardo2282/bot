from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import get_pool
from ..payments.cryptopay import verify_signature

app = FastAPI(title="CryptoPay Webhook")

class Invoice(BaseModel):
    invoice_id: int
    status: str
    asset: str
    amount: float
    payload: str | None = None

class Update(BaseModel):
    update_id: int
    update_type: str
    request_date: int
    payload: Invoice

# динамический путь из настроек
WEBHOOK_PATH = settings.CRYPTO_WEBHOOK_PATH or "/cryptopay/webhook"

@app.post(WEBHOOK_PATH)
async def cryptopay_webhook(req: Request):
    body = await req.body()
    sig = req.headers.get("X-Crypto-Pay-Signature") or req.headers.get("Crypto-Pay-Signature")
    if not verify_signature(body, sig or ""):
        raise HTTPException(status_code=401, detail="bad signature")

    data = Update.model_validate_json(body)
    inv = data.payload
    if inv.status.lower() != "paid":
        return {"ok": True}

    # payload = user_id из createInvoice
    try:
        user_id = int(inv.payload or "0")
    except ValueError:
        return {"ok": True}

    cents = int(round(inv.amount * 100))

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Идемпотентность по external_ref
            result = await conn.execute(
                "INSERT INTO ledger(user_id, kind, amount_cents, external_ref) "
                "VALUES ($1,'deposit',$2,$3) ON CONFLICT (external_ref) DO NOTHING",
                user_id, cents, f"invoice:{inv.invoice_id}:{inv.asset}"
            )
            if result.endswith("1"):
                await conn.execute(
                    "UPDATE app_user SET balance_cents=balance_cents+$1 WHERE id=$2",
                    cents, user_id
                )
    return {"ok": True}
