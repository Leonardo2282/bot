# app/payments/cryptopay.py

import aiohttp
from typing import Dict, Any, List
from ..config import settings

API = "https://pay.crypt.bot/api/"

async def _post(method: str, payload: dict | None = None) -> dict:
    headers = {"Crypto-Pay-API-Token": settings.CRYPTO_PAY_TOKEN}
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.post(API + method, json=payload or {}) as r:
            data = await r.json()
            if not isinstance(data, dict) or not data.get("ok"):
                raise RuntimeError(f"CryptoPay API error: {data}")
            return data["result"]

async def create_invoice(amount_cents: int, asset: str, payload: str) -> dict:
    amount = amount_cents / 100
    res = await _post("createInvoice", {"asset": asset, "amount": amount, "payload": payload})
    # нормализуем типы
    res["invoice_id"] = int(res["invoice_id"])
    return res

async def get_invoices(invoice_ids: list[int]) -> list[dict]:
    # Crypto Pay ждёт строку с id через запятую
    ids_csv = ",".join(str(i) for i in invoice_ids)
    res = await _post("getInvoices", {"invoice_ids": ids_csv})
    # ВАЖНО: API возвращает {"items": [ {...}, {...} ]}
    items = res.get("items", []) if isinstance(res, dict) else []
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict):
            # приводим типы
            if "invoice_id" in it:
                try:
                    it["invoice_id"] = int(it["invoice_id"])
                except Exception:
                    pass
            out.append(it)
    return out

async def transfer(tg_user_id: int, amount_cents: int, asset: str, spend_id: str) -> Dict[str, Any]:
    amount = str(amount_cents / 100)
    return await _post("transfer", {
        "user_id": str(tg_user_id),
        "asset": asset,
        "amount": amount,
        "spend_id": spend_id,
    })

# частичный/полный возврат через transfer, если используешь
import uuid
async def refund(invoice_id: int, amount_cents: int, tg_user_id: int, asset: str | None = None) -> dict:
    spend_id = f"refund:{invoice_id}:{amount_cents}:{uuid.uuid4().hex[:8]}"
    return await transfer(
        tg_user_id=tg_user_id,
        amount_cents=amount_cents,
        asset=asset or settings.CRYPTO_DEFAULT_ASSET,
        spend_id=spend_id,
    )