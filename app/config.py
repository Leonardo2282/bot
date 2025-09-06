# app/config.py  (Pydantic v2)
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str = Field(...)
    ADMINS_TG_IDS: str = Field("", description="comma-separated tg ids")

    # Crypto Pay
    CRYPTO_PAY_TOKEN: str = Field(...)
    CRYPTO_DEFAULT_ASSET: str = Field("USDT")
    CRYPTO_NETWORK: str = Field("MAIN_NET")   # ← добавил

    # Комиссия
    FEE_PCT: float = Field(0.10)

    # PostgreSQL
    PGUSER: str = Field(...)
    PGPASSWORD: str = Field("")
    PGDATABASE: str = Field(...)
    PGHOST: str = Field("127.0.0.1")
    PGPORT: int = Field(5432)

    # Google Sheets
    GSHEET_CREDENTIALS_JSON: str = Field("service_account.json")
    GSHEET_SPREADSHEET_ID: str = Field(...)
    GSHEET_WORKSHEET_NAME: str = Field("Лист"
                                       "1")
    GSHEET_RANGE: str = Field("Лист1!A2:G")   # ← добавил
    MAIN_MENU_PHOTO_URL: str = Field("")
    EVENTS_MENU_PHOTO_URL: str = Field("")
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @property
    def ADMIN_IDS(self) -> List[int]:
        if not self.ADMINS_TG_IDS:
            return []
        return [int(x.strip()) for x in self.ADMINS_TG_IDS.split(",") if x.strip()]


settings = Settings()