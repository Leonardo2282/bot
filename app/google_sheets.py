import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
load_dotenv()


# ====== Модель строки боя из таблицы ======
@dataclass
class SheetFight:
    # оба варианта: либо есть ID в первой колонке, либо нет
    id_from_sheet: Optional[int]
    title: str
    p1: str
    p2: str
    photo_url: Optional[str]
    starts_at: Optional[datetime]
    status: str  # upcoming / today / live / done


def _get_client() -> gspread.Client:
    cred_path = os.getenv("GSHEET_CREDENTIALS_JSON", "service_account.json")
    if not os.path.exists(cred_path):
        raise RuntimeError(f"Service account json not found: {cred_path}")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_file(cred_path, scopes=scopes)
    return gspread.authorize(creds)


def _parse_dt(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    raw = str(raw).strip()
    # Пытаемся несколько форматов
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            # считаем, что в таблице UTC
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_fights_from_sheet() -> List[SheetFight]:
    spreadsheet_id = os.getenv("GSHEET_SPREADSHEET_ID")
    rng = os.getenv("GSHEET_RANGE")  # пример: "Лист1!A2:G" или "Sheet1!A2:F"
    if not spreadsheet_id:
        raise RuntimeError("GSHEET_SPREADSHEET_ID is not set")
    if not rng:
        raise RuntimeError("GSHEET_RANGE is not set (пример: Лист1!A2:G)")

    gc = _get_client()

    # Читаем значения разом
    sh = gc.open_by_key(spreadsheet_id)
    # values_get возвращает двумерный массив строк
    data = sh.values_get(rng).get("values", [])

    fights: List[SheetFight] = []

    for idx, row in enumerate(data, start=2):  # визуально: это строка листа (с A2)
        # Нормализуем длину: добьём пустыми до 7
        r = [c.strip() if isinstance(c, str) else c for c in row]
        while len(r) < 7:
            r.append("")

        # Два поддерживаемых формата:
        # 7 колонок: ID | Title | p1 | p2 | photo | starts | status
        # 6 колонок: Title | p1 | p2 | photo | starts | status
        if len(row) >= 7 and r[0] != "" and r[1] != "":
            # с ID
            try:
                id_sheet = int(str(r[0]).strip())
            except Exception:
                id_sheet = None
            title, p1, p2, photo, starts_raw, status = r[1], r[2], r[3], r[4], r[5], r[6]
        else:
            # без ID
            id_sheet = None
            title, p1, p2, photo, starts_raw, status = r[0], r[1], r[2], r[3], r[4], r[5]

        # пропускаем пустые строки
        if not title or not p1 or not p2:
            continue

        starts_at = _parse_dt(starts_raw)
        status = (status or "upcoming").strip().lower()

        fights.append(
            SheetFight(
                id_from_sheet=id_sheet,
                title=title,
                p1=p1,
                p2=p2,
                photo_url=photo or None,
                starts_at=starts_at,
                status=status if status in {"upcoming", "today", "live", "done"} else "upcoming",
            )
        )

    # Немного логов в консоль, чтобы видеть что реально прочитали
    print(f"[GS] fetched {len(fights)} fights from range {rng}")
    for i, f in enumerate(fights[:5], 1):
        print(f"  {i}) {f.title!r} | {f.p1} vs {f.p2} | {f.starts_at} | {f.status}")

    return fights

