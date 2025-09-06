import gspread
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from .config import settings


def _client():
    json_path = Path(settings.GSHEET_CREDENTIALS_JSON)
    if not json_path.is_absolute():
        json_path = Path.cwd() / json_path
    if not json_path.exists():
        raise RuntimeError(f"GSHEET_CREDENTIALS_JSON not found: {json_path}")
    gc = gspread.service_account(filename=str(json_path))
    return gc

def _ws():
    gc = _client()
    sh = gc.open_by_key(settings.GSHEET_SPREADSHEET_ID)
    # по имени вкладки
    try:
        return sh.worksheet(settings.GSHEET_WORKSHEET_NAME)
    except Exception:
        # fallback: первый лист
        return sh.sheet1

def _parse_dt(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    # 2025-09-10 20:00 or 2025-09-10
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

def fetch_fights_from_sheet() -> List[Dict[str, Any]]:
    """
    Ожидаемые заголовки (в верхней строке):
      external_id | title | p1 | p2 | photo_url | starts_at | status | description | winner
    """
    ws = _ws()
    rows = ws.get_all_records(expected_headers=[
        "external_id", "title", "p1", "p2", "photo_url", "starts_at", "status", "description", "winner"
    ])

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append({
            "external_id": str(r.get("external_id") or "").strip() or None,
            "title": (r.get("title") or "").strip(),
            "p1": (r.get("p1") or "").strip(),
            "p2": (r.get("p2") or "").strip(),
            "photo_url": (r.get("photo_url") or "").strip() or None,
            "starts_at": _parse_dt(r.get("starts_at") or ""),
            "status": (r.get("status") or "upcoming").strip().lower(),
            "description": (r.get("description") or "").strip() or None,
            "winner": int(r.get("winner") or 0) if str(r.get("winner") or "").strip().isdigit() else None,
        })
    return items