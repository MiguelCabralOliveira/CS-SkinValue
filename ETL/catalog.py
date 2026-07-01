from urllib.parse import quote

import pandas as pd

from ETL.config import CSMARKET_ITEMS_URL, EXCLUDED_TYPES
from ETL.http import get_json


def fetch_catalog(api_key: str) -> pd.DataFrame:
    raw = get_json(CSMARKET_ITEMS_URL, params={"key": api_key})
    rows = []
    for item in raw:
        if item.get("type", "").lower() in EXCLUDED_TYPES:
            continue
        market_hash_name = item.get("market_hash_name", "")
        rows.append({
            "hash_name": item.get("hash_name", ""),
            "name": market_hash_name,
            "weapon": item.get("weapon", "") or "Unknown",
            "quality": item.get("quality", ""),
            "type": item.get("type", ""),
            "collection": item.get("collection", ""),
            "link": f"https://csgostocks.de/charts/item?market_hash_name={quote(market_hash_name)}",
        })
    return pd.DataFrame(rows)
