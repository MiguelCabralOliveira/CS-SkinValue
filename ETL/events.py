"""Event scraping: Steam News API + Valve blog RSS + hardcoded Operations.

Produces a normalized timeline of CS-related events. The output is meant as
feature material for forecasting models (days_since_update, in_operation, etc.).

Run with:
    python -m ETL.cli events --since 2013-01-01
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import polars as pl

from ETL.config import (
    CS_APPID,
    CS_OPERATIONS,
    EVENTS_PARQUET,
    STEAM_NEWS_URL,
    VALVE_RSS_URL,
)
from ETL.http import get_json

# Known weapon list for tag extraction. Matched case-insensitively against title+body.
WEAPONS = [
    "AK-47", "M4A4", "M4A1-S", "AWP", "Desert Eagle", "USP-S", "Glock-18",
    "Five-SeveN", "Tec-9", "P250", "P2000", "Dual Berettas", "CZ75-Auto", "R8 Revolver",
    "MAC-10", "MP9", "MP7", "MP5-SD", "UMP-45", "P90", "PP-Bizon",
    "Galil AR", "FAMAS", "SG 553", "AUG",
    "SSG 08", "G3SG1", "SCAR-20",
    "Nova", "XM1014", "Sawed-Off", "MAG-7",
    "M249", "Negev",
    "Karambit", "Bayonet", "M9 Bayonet", "Butterfly Knife", "Huntsman Knife",
    "Falchion Knife", "Bowie Knife", "Shadow Daggers", "Gut Knife", "Flip Knife",
    "Navaja Knife", "Stiletto Knife", "Talon Knife", "Ursus Knife", "Classic Knife",
    "Paracord Knife", "Survival Knife", "Nomad Knife", "Skeleton Knife",
    "Sport Gloves", "Driver Gloves", "Specialist Gloves", "Hand Wraps",
    "Moto Gloves", "Hydra Gloves", "Bloodhound Gloves",
]
WEAPON_RE = re.compile("|".join(re.escape(w) for w in sorted(WEAPONS, key=len, reverse=True)), re.IGNORECASE)

# Heuristics for classifying news items by their tags + title.
PATCHNOTES_KEYWORDS = {"update", "patch"}
MAJOR_KEYWORDS = {"major", "championship"}
CASE_KEYWORDS = {"case", "weapon case"}
STICKER_KEYWORDS = {"sticker", "capsule"}
OPERATION_KEYWORDS = {"operation"}


# ---------------------------------------------------------------------------
# Steam News API
# ---------------------------------------------------------------------------

def fetch_steam_news(since: datetime | None = None, page_size: int = 100,
                     max_pages: int = 200, sleep_s: float = 0.3) -> list[dict]:
    """Page backwards through Steam News for CS, until older than `since`.

    The API supports `enddate` (unix seconds, exclusive upper bound).
    """
    since_ts = int(since.timestamp()) if since else 0
    end_date = int(time.time()) + 1
    seen_gids: set[str] = set()
    all_items: list[dict] = []

    for page in range(max_pages):
        data = get_json(STEAM_NEWS_URL, params={
            "appid": CS_APPID,
            "count": page_size,
            "enddate": end_date,
        })
        items = (data.get("appnews") or {}).get("newsitems") or []
        if not items:
            break

        new_items = [it for it in items if it.get("gid") not in seen_gids]
        seen_gids.update(it.get("gid") for it in new_items)
        all_items.extend(new_items)

        oldest = min(it["date"] for it in items)
        if oldest <= since_ts:
            break

        end_date = oldest  # next page goes older
        time.sleep(sleep_s)

    if since_ts:
        all_items = [it for it in all_items if it.get("date", 0) >= since_ts]
    return all_items


# ---------------------------------------------------------------------------
# Valve blog RSS
# ---------------------------------------------------------------------------

def fetch_valve_rss() -> list[dict]:
    """Valve blog feed. Note: feed stopped updating around 2023-04 when CS2
    launched, but it has rich historical posts (operation announcements, etc.).
    """
    feed = feedparser.parse(VALVE_RSS_URL)
    items = []
    for e in feed.entries:
        pp = e.get("published_parsed")
        if not pp:
            continue
        ts = int(time.mktime(pp))  # already in UTC tuple
        items.append({
            "gid": e.get("id") or e.get("link"),
            "title": e.get("title", ""),
            "url": e.get("link", ""),
            "date": ts,
            "contents": (e.get("summary") or "")[:5000],
            "tags": [t.get("term", "") for t in (e.get("tags") or [])],
            "feedname": "valve_blog_rss",
        })
    return items


# ---------------------------------------------------------------------------
# Operations table
# ---------------------------------------------------------------------------

def operations_as_events() -> list[dict]:
    """Each operation produces two events: start and end."""
    out = []
    for op in CS_OPERATIONS:
        for ev_type, key in [("operation_start", "start"), ("operation_end", "end")]:
            d = datetime.strptime(op[key], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            out.append({
                "event_date": d.date(),
                "event_type": ev_type,
                "event_name": op["name"],
                "source": "operations_table",
                "url": "",
                "tags": [op["tier"]],
                "raw_text": f"{op['name']} ({op['tier']} impact)",
            })
    return out


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _classify(title: str, body: str, raw_tags: list[str]) -> str:
    """Map a news item to one of: update | major | case_release | sticker_release | operation_start | other."""
    t = title.lower()
    tags = {tag.lower() for tag in (raw_tags or [])}
    if "patchnotes" in tags or any(k in t for k in PATCHNOTES_KEYWORDS):
        # Operation announcement posts also say "update", so check operation first.
        if any(k in t for k in OPERATION_KEYWORDS):
            return "operation_start"
        return "update"
    if any(k in t for k in MAJOR_KEYWORDS):
        return "major"
    if any(k in t for k in OPERATION_KEYWORDS):
        return "operation_start"
    if any(k in t for k in CASE_KEYWORDS):
        return "case_release"
    if any(k in t for k in STICKER_KEYWORDS):
        return "sticker_release"
    return "other"


def _extract_tags(title: str, body: str) -> list[str]:
    text = f"{title}\n{body}"
    found = {m.group(0) for m in WEAPON_RE.finditer(text)}
    # Normalize case to the canonical form
    canonical = {w.lower(): w for w in WEAPONS}
    return sorted({canonical.get(f.lower(), f) for f in found})


def normalize_steam_or_rss(raw: list[dict], source_name: str) -> list[dict]:
    out = []
    for it in raw:
        ts = it.get("date")
        if not ts:
            continue
        title = (it.get("title") or "").strip()
        body = (it.get("contents") or "").strip()
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        out.append({
            "event_date": d,
            "event_type": _classify(title, body, it.get("tags") or []),
            "event_name": title,
            "source": source_name,
            "url": it.get("url", ""),
            "tags": _extract_tags(title, body),
            "raw_text": body[:2000],
        })
    return out


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_events(rows: list[dict], path: Path = EVENTS_PARQUET) -> pl.DataFrame:
    if not rows:
        raise RuntimeError("no events to save")
    df = pl.DataFrame(rows, schema={
        "event_date": pl.Date,
        "event_type": pl.String,
        "event_name": pl.String,
        "source": pl.String,
        "url": pl.String,
        "tags": pl.List(pl.String),
        "raw_text": pl.String,
    })
    # Dedup: same date + identical name + identical source. Different sources can
    # report the same event independently — we keep both as evidence.
    df = df.unique(subset=["event_date", "event_name", "source"]).sort("event_date", descending=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return df


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_events(since: datetime | None = None) -> pl.DataFrame:
    print(f"[events] Steam News (since={since})...", flush=True)
    steam_raw = fetch_steam_news(since=since)
    steam_norm = normalize_steam_or_rss(steam_raw, source_name="steam_news_api")
    print(f"[events]   {len(steam_norm)} items")

    print("[events] Valve blog RSS...", flush=True)
    rss_raw = fetch_valve_rss()
    rss_norm = normalize_steam_or_rss(rss_raw, source_name="valve_blog_rss")
    print(f"[events]   {len(rss_norm)} items")

    print("[events] Operations table...", flush=True)
    ops_norm = operations_as_events()
    print(f"[events]   {len(ops_norm)} items")

    all_rows = steam_norm + rss_norm + ops_norm
    df = save_events(all_rows)
    print(f"[events] saved {df.height:,} rows → {EVENTS_PARQUET}")
    print(df.group_by("event_type").agg(pl.len().alias("n")).sort("n", descending=True))
    return df
