# app/payments/cryptopay.py
import os
import aiohttp
from typing import Any, Dict, Optional

# Если у тебя есть settings — можно использовать его. Иначе берём из ENV.
try:
    from ..config import settings
    _API_TOKEN = getattr(settings, "CRYPTO_PAY_TOKEN", None) or os.getenv("CRYPTO_PAY_TOKEN")
except Exception:
    _API_TOKEN = os.getenv("CRYPTO_PAY_TOKEN")

BASE_URL = "https://pay.crypt.bot/api"

class CryptoPayError(RuntimeError):
    pass


async def _api_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Вызов POST к Crypto Pay API."""
    if not _API_TOKEN:
        raise CryptoPayError("CRYPTO_PAY_TOKEN не задан в окружении/конфиге")
    url = f"{BASE_URL}/{method}"
    headers = {"Crypto-Pay-API-Token": _API_TOKEN}
    async with aiohttp.ClientSession() as sess:
        async with sess.post(url, json=payload, headers=headers, timeout=15) as r:
            data = await r.json()
    if not data.get("ok"):
        raise CryptoPayError(f"{method} failed: {data}")
    return data["result"]


async def _api_get(method: str) -> Dict[str, Any]:
    """Вызов GET к Crypto Pay API."""
    if not _API_TOKEN:
        raise CryptoPayError("CRYPTO_PAY_TOKEN не задан в окружении/конфиге")
    url = f"{BASE_URL}/{method}"
    headers = {"Crypto-Pay-API-Token": _API_TOKEN}
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, headers=headers, timeout=15) as r:
            data = await r.json()
    if not data.get("ok"):
        raise CryptoPayError(f"{method} failed: {data}")
    return data["result"]


# --------- КУРСЫ ---------

async def get_usdt_per_rub() -> float:
    """
    Возвращает СКОЛЬКО USDT за 1 RUB (RUB -> USDT) по реальному API Crypto Pay.
    Без фолбэков: если API недоступно — кидаем исключение.
    """
    rates = await _api_get("getExchangeRates")
    # Ищем сразу RUB -> USDT
    for it in rates:
        if str(it.get("source", "")).upper() == "RUB" and str(it.get("target", "")).upper() == "USDT":
            rate = float(it.get("rate", 0) or 0)
            if rate > 0:
                return rate

    # Если в таблице нет RUB->USDT, пробуем USDT->RUB и инвертируем
    for it in rates:
        if str(it.get("source", "")).upper() == "USDT" and str(it.get("target", "")).upper() == "RUB":
            rub_per_usdt = float(it.get("rate", 0) or 0)
            if rub_per_usdt > 0:
                return 1.0 / rub_per_usdt

    raise CryptoPayError("Не удалось определить курс RUB↔USDT из getExchangeRates")


async def rub_to_usdt(amount_rub: float) -> float:
    """Конвертирует RUB → USDT по текущему курсу Crypto Pay. Округляем до 2 знаков."""
    rate = await get_usdt_per_rub()  # USDT за 1 RUB
    return round(amount_rub * rate, 2)


# --------- СЧЁТ НА ПОПОЛНЕНИЕ ---------

async def create_deposit_invoice(user_id: int, amount_usdt: float, description: Optional[str] = None) -> Dict[str, Any]:
    """
    Создаёт инвойс в USDT через Crypto Pay.
    Возвращает dict с полями от API (в т.ч. 'bot_invoice_url' — ссылка для оплаты в мини-приложении).
    """
    payload = {
        "asset": "USDT",
        "amount": str(amount_usdt),   # строкой безопаснее
        "description": description or f"Deposit for user {user_id}",
        # Можно включить/выключить комментарии/обратную связь:
        "allow_comments": True,
        "allow_anonymous": False,
    }
    result = await _api_post("createInvoice", payload)
    # ожидаемые поля: invoice_id, status, hash, pay_url, bot_invoice_url, created_at, amount, asset, …
    return result

