import re
from typing import Optional
from urllib.parse import quote

from ETL.config import (
    CSGOSTOCKS_KEYFIGURES_URL,
    CSGOSTOCKS_PRICE_OHLC_URL,
    CSGOSTOCKS_PRICE_URL,
    OHLC_TYPES,
)
from ETL.http import get_json


def parse_item_name(full_name: str) -> tuple[str, Optional[str], Optional[str], str]:
    base_name = full_name
    exterior: Optional[str] = None
    category: Optional[str] = None

    match = re.search(r'\s*\(([^)]+)\)\s*$', full_name)
    if match:
        exterior = match.group(1)
        base_name = full_name[:match.start()].strip()

    has_star = base_name.startswith("★")
    has_stattrak = "StatTrak" in base_name or "™" in base_name

    name_for_path = base_name
    name_for_query = base_name

    if has_star:
        category = "★"
        name_for_query = base_name.replace("★", "", 1).strip()

    if has_stattrak:
        name_for_path = re.sub(r'StatTrak™?\s*', '', name_for_path)
        name_for_query = re.sub(r'StatTrak™?\s*', '', name_for_query)

    if base_name.startswith("Souvenir "):
        name_for_query = name_for_query.replace("Souvenir ", "", 1)
        name_for_path = name_for_path.replace("Souvenir ", "", 1)
        if not category:
            category = "Souvenir"

    if exterior:
        name_for_path = f"{name_for_path} ({exterior})"

    return name_for_query, exterior, category, name_for_path


def _uses_ohlc_endpoint(item_type: Optional[str]) -> bool:
    return bool(item_type) and item_type.lower() in OHLC_TYPES


def fetch_price(name_for_path: str, *, name: Optional[str] = None,
                category: Optional[str] = None, exterior: Optional[str] = None,
                item_type: Optional[str] = None,
                full_name: Optional[str] = None) -> Optional[dict | list]:
    if _uses_ohlc_endpoint(item_type):
        encoded = quote(full_name or name_for_path, safe='')
        url = CSGOSTOCKS_PRICE_OHLC_URL.format(name=encoded)
        params = None
    else:
        encoded = quote(name_for_path, safe='')
        url = CSGOSTOCKS_PRICE_URL.format(name=encoded)
        params = {}
        if name:
            params["name"] = name
        if category:
            params["category"] = category
        if exterior:
            params["exterior"] = exterior
    try:
        return get_json(url, params=params)
    except Exception:
        return None


def fetch_keyfigures(name_for_path: str) -> Optional[dict]:
    encoded = quote(name_for_path, safe='')
    url = CSGOSTOCKS_KEYFIGURES_URL.format(name=encoded)
    try:
        return get_json(url)
    except Exception:
        return None


def normalize_price(raw, *, item_type: Optional[str]) -> tuple[Optional[list], Optional[list], Optional[int]]:
    """Return (price_series, price_ohlc, last_update). One series is populated when raw is valid."""
    if raw is None:
        return None, None, None

    if _uses_ohlc_endpoint(item_type):
        if not isinstance(raw, dict):
            return None, None, None
        ohlc_rows = raw.get("ohlc")
        last_update = raw.get("last_update")
        if not ohlc_rows:
            return None, None, last_update
        return None, [
            {"ts": row[0], "o": row[1], "h": row[2], "l": row[3], "c": row[4]}
            for row in ohlc_rows if len(row) >= 5
        ], last_update

    rows = None
    last_update = None
    if isinstance(raw, dict):
        rows = raw.get("data")
        last_update = raw.get("last_update")
    elif isinstance(raw, list):
        rows = raw
    if not rows:
        return None, None, last_update
    return [
        {"ts": row[0], "price": row[1], "volume": row[2]}
        for row in rows if len(row) >= 3
    ], None, last_update


def enrich_item(item: dict) -> dict:
    item_name = item.get("name", "")
    item_type = item.get("type", "")
    out = dict(item)
    if not item_name:
        out["price_series"] = None
        out["price_ohlc"] = None
        out["keyfigures"] = None
        return out

    name_for_query, exterior, category, name_for_path = parse_item_name(item_name)
    price_raw = fetch_price(
        name_for_path,
        name=name_for_query,
        category=category,
        exterior=exterior,
        item_type=item_type,
        full_name=item_name,
    )
    series, ohlc, last_update = normalize_price(price_raw, item_type=item_type)
    out["price_series"] = series
    out["price_ohlc"] = ohlc
    out["price_last_update"] = last_update
    out["keyfigures"] = fetch_keyfigures(item_name)
    return out
