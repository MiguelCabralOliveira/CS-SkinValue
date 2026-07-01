from pathlib import Path

CSMARKET_ITEMS_URL = "https://api.csmarketapi.com/v1/items/"
CSGOSTOCKS_PRICE_URL = "https://www.csgostocks.com/api/prices/price/{name}"
CSGOSTOCKS_PRICE_OHLC_URL = "https://www.csgostocks.com/api/prices/price/ohlc/{name}"
CSGOSTOCKS_KEYFIGURES_URL = "https://www.csgostocks.com/api/prices/price/keyfigures/{name}"

# CS2 / CS:GO appid for Steam APIs
CS_APPID = 730
STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
VALVE_RSS_URL = "https://blog.counter-strike.net/index.php/feed/"
BYMYKEL_SKINS_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/skins.json"
# Liquipedia courtesy: identify the bot
USER_AGENT = "CS-SkinValue/0.1 (research; github.com/miguel/cs-skinvalue)"

# Hardcoded — Liquipedia/Wikipedia have these in prose only, not tables.
# Tier reflects how much these affected skin prices (high = lots of new items released).
CS_OPERATIONS = [
    {"name": "Operation Payback",        "start": "2013-04-25", "end": "2013-08-31", "tier": "low"},
    {"name": "Operation Bravo",          "start": "2013-09-19", "end": "2014-01-06", "tier": "low"},
    {"name": "Operation Phoenix",        "start": "2014-02-20", "end": "2014-06-05", "tier": "mid"},
    {"name": "Operation Breakout",       "start": "2014-07-01", "end": "2014-10-02", "tier": "high"},
    {"name": "Operation Vanguard",       "start": "2014-11-11", "end": "2015-03-31", "tier": "mid"},
    {"name": "Operation Bloodhound",     "start": "2015-05-26", "end": "2015-09-30", "tier": "mid"},
    {"name": "Operation Wildfire",       "start": "2016-02-17", "end": "2016-07-15", "tier": "high"},
    {"name": "Operation Hydra",          "start": "2017-05-23", "end": "2017-11-13", "tier": "high"},
    {"name": "Operation Shattered Web",  "start": "2019-11-18", "end": "2020-03-30", "tier": "high"},
    {"name": "Operation Broken Fang",    "start": "2020-12-03", "end": "2021-04-30", "tier": "high"},
    {"name": "Operation Riptide",        "start": "2021-09-21", "end": "2022-02-21", "tier": "high"},
]

EXCLUDED_TYPES = {"sticker", "graffiti", "container"}
OHLC_TYPES = {"gloves", "knife"}

REQUEST_TIMEOUT = (5, 30)
RETRY_MAX_ATTEMPTS = 5
RETRY_WAIT_INITIAL = 1.0
RETRY_WAIT_MAX = 30.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
ITEMS_PARQUET = DATA_DIR / "items.parquet"
CATALOG_PARQUET = DATA_DIR / "catalog.parquet"
PROBE_RESULT_PATH = DATA_DIR / "probe_result.json"
EVENTS_PARQUET = DATA_DIR / "events.parquet"
DOPPLER_PHASES_PARQUET = DATA_DIR / "doppler_phases.parquet"
WHITELIST_TIERS_PARQUET = DATA_DIR / "whitelist_tiers.parquet"

# Processed feature datasets (forecasting inputs)
PROCESSED_DIR = DATA_DIR / "processed"
CLEAN_SERIES_PARQUET = PROCESSED_DIR / "clean_series.parquet"
FEATURES_PARQUET = PROCESSED_DIR / "features.parquet"
TARGETS_PARQUET = PROCESSED_DIR / "targets.parquet"
PEER_FEATURES_PARQUET = PROCESSED_DIR / "peer_features.parquet"

DEFAULT_WORKERS_FALLBACK = 10

# Marketplace fees (sale-side, fraction of gross). Used by 09_arbitrage_v2.
# Placeholders — confirm in marketplace TOS before relying on these.
MARKETPLACE_FEES = {
    "csmoney":   {"buy": 0.00, "sell": 0.07},
    "skinport":  {"buy": 0.00, "sell": 0.12},
    "skinbaron": {"buy": 0.00, "sell": 0.15},
    "skinswap":  {"buy": 0.00, "sell": 0.10},
    "haloskins": {"buy": 0.00, "sell": 0.05},
    "uuskins":   {"buy": 0.00, "sell": 0.08},
}
