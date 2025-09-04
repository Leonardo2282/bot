from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyUrl

class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str
    ADMINS_TG_IDS: str = ""   # "111,222"

    # Crypto (можно не трогать сейчас)
    CRYPTO_PAY_TOKEN: Optional[str] = None
    CRYPTO_NETWORK: str = "MAIN_NET"
    CRYPTO_DEFAULT_ASSET: str = "USDT"

    # Postgres
    PGUSER: str = "sergey"
    PGPASSWORD: str = ""
    PGDATABASE: str = "bets"
    PGHOST: str = "127.0.0.1"
    PGPORT: int = 5432

    # Комиссии
    FEE_PCT: float = 0.20

    # Google Sheets
    GSHEET_CREDENTIALS_JSON: str = "service_account.json"
    GSHEET_SPREADSHEET_ID: str = ""
    GSHEET_RANGE: str = "Лист1!A2:G"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def admin_ids(self) -> List[int]:
        return [int(x.strip()) for x in self.ADMINS_TG_IDS.split(",") if x.strip()]

settings = Settings()

