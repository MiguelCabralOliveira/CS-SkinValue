import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ETL.catalog import fetch_catalog
from ETL.config import (
    CATALOG_PARQUET,
    DATA_DIR,
    ITEMS_PARQUET,
    RAW_DIR,
)
from ETL.enrich import enrich_item
from ETL.probe import load_or_run_probe


def _slug(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def _enrich_group(items: list[dict], workers: int, weapon: str) -> list[dict]:
    enriched: list[Optional[dict]] = [None] * len(items)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(enrich_item, item): idx for idx, item in enumerate(items)}
        done = 0
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                enriched[idx] = fut.result()
            except Exception as e:
                fallback = dict(items[idx])
                fallback["price_series"] = None
                fallback["price_ohlc"] = None
                fallback["keyfigures"] = None
                fallback["error"] = str(e)
                enriched[idx] = fallback
            done += 1
            if done % 25 == 0 or done == len(items):
                elapsed = time.perf_counter() - start
                print(f"  [{weapon}] {done}/{len(items)} ({elapsed:.1f}s)")
    return [e for e in enriched if e is not None]


_STRING_COLS = ("hash_name", "name", "weapon", "quality", "type", "collection", "link", "error")


def _sanitize(rows: list[dict]) -> list[dict]:
    """Replace pandas NaN floats with None in known string columns so pyarrow can infer schema."""
    for row in rows:
        for col in _STRING_COLS:
            v = row.get(col)
            if isinstance(v, float) and math.isnan(v):
                row[col] = None
    return rows


def _write_parquet(rows: list[dict], path: Path) -> None:
    table = pa.Table.from_pylist(_sanitize(rows))
    pq.write_table(table, path, compression="zstd")


def load_catalog(api_key: str, use_cache: bool = True) -> pd.DataFrame:
    if use_cache and CATALOG_PARQUET.exists():
        return pd.read_parquet(CATALOG_PARQUET)
    df = fetch_catalog(api_key)
    _ensure_dirs()
    df.to_parquet(CATALOG_PARQUET, index=False)
    print(f"Catalog: {len(df)} items saved to {CATALOG_PARQUET}")
    return df


def run_pipeline(api_key: str, *, weapon: Optional[str] = None,
                 force: bool = False, reprobe: bool = False,
                 use_catalog_cache: bool = True,
                 workers_override: Optional[int] = None) -> None:
    _ensure_dirs()
    catalog = load_catalog(api_key, use_cache=use_catalog_cache)

    if weapon:
        catalog = catalog[catalog["weapon"] == weapon]
        if catalog.empty:
            print(f"No items found for weapon='{weapon}'")
            return

    if workers_override:
        workers = workers_override
    else:
        sample = catalog.head(50).to_dict(orient="records")
        workers = load_or_run_probe(sample, force=reprobe)
    print(f"Using {workers} workers")

    grouped = catalog.groupby("weapon", sort=True)
    total_groups = len(grouped)
    for i, (weapon_name, group) in enumerate(grouped, 1):
        out_path = RAW_DIR / f"{_slug(weapon_name)}.parquet"
        if out_path.exists() and not force:
            print(f"[{i}/{total_groups}] {weapon_name}: skip (exists)")
            continue
        rows = group.to_dict(orient="records")
        print(f"[{i}/{total_groups}] {weapon_name}: enriching {len(rows)} items")
        enriched = _enrich_group(rows, workers, weapon_name)
        _write_parquet(enriched, out_path)
        print(f"[{i}/{total_groups}] {weapon_name}: wrote {out_path.name}")

    if not weapon:
        consolidate()


def consolidate() -> None:
    files = sorted(RAW_DIR.glob("*.parquet"))
    if not files:
        print("No parquet files in data/raw/ to consolidate")
        return
    tables = [pq.read_table(f) for f in files]
    merged = pa.concat_tables(tables, promote_options="default")
    pq.write_table(merged, ITEMS_PARQUET, compression="zstd")
    print(f"Consolidated {len(files)} files → {ITEMS_PARQUET} ({merged.num_rows} rows)")
