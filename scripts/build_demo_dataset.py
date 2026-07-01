"""Build a small, real local dataset without needing the paid catalog API key.

We already have the full catalog committed as `items.json`. This script:
  1. Selects a curated subset of liquid, deep-history skins from that catalog.
  2. Writes it to `data/catalog.parquet` (the exact schema the ETL expects), so
     `load_catalog()` serves it from cache and never calls the CS Market API.
  3. Runs the existing enrichment pipeline (public csgostocks endpoints, no key)
     to fetch price history + keyfigures, then consolidates → `data/items.parquet`.

Usage:
    ./.venv/Scripts/python.exe scripts/build_demo_dataset.py [--all] [--limit N]

    (default: curated ~25-item demo set; --all enriches the whole catalog — slow)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ETL.config import CATALOG_PARQUET, DATA_DIR  # noqa: E402
from ETL.pipeline import run_pipeline  # noqa: E402

# Famous, highly liquid skins with years of history. Matched exactly against
# the `name` (market_hash_name) field in items.json.
CURATED = [
    "AK-47 | Redline (Field-Tested)",
    "AK-47 | Asiimov (Field-Tested)",
    "AK-47 | Vulcan (Field-Tested)",
    "AK-47 | Bloodsport (Field-Tested)",
    "AK-47 | Neon Rider (Field-Tested)",
    "AK-47 | Slate (Field-Tested)",
    "AWP | Asiimov (Field-Tested)",
    "AWP | Redline (Field-Tested)",
    "AWP | Hyper Beast (Field-Tested)",
    "AWP | Neo-Noir (Field-Tested)",
    "AWP | Wildfire (Field-Tested)",
    "M4A4 | Asiimov (Field-Tested)",
    "M4A1-S | Hyper Beast (Field-Tested)",
    "M4A1-S | Player Two (Field-Tested)",
    "USP-S | Kill Confirmed (Minimal Wear)",
    "USP-S | Orion (Factory New)",
    "USP-S | Printstream (Field-Tested)",
    "Glock-18 | Fade (Factory New)",
    "Glock-18 | Water Elemental (Factory New)",
    "Desert Eagle | Printstream (Field-Tested)",
    "Desert Eagle | Blaze (Factory New)",
    "P250 | Asiimov (Field-Tested)",
    "SSG 08 | Blood in the Water (Minimal Wear)",
    "Five-SeveN | Case Hardened (Field-Tested)",
    "M4A4 | The Emperor (Field-Tested)",
]


def build_catalog(all_items: bool, limit: int | None) -> pd.DataFrame:
    with open(ROOT / "items.json", encoding="utf-8") as f:
        catalog = json.load(f)
    df = pd.DataFrame(catalog)

    if not all_items:
        present = set(df["name"])
        chosen = [n for n in CURATED if n in present]
        missing = [n for n in CURATED if n not in present]
        if missing:
            print(f"  (skipping {len(missing)} curated names not in catalog: "
                  f"{missing[:3]}{'...' if len(missing) > 3 else ''})")
        df = df[df["name"].isin(chosen)].reset_index(drop=True)

    if limit:
        df = df.head(limit)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Enrich the entire catalog (slow!)")
    ap.add_argument("--limit", type=int, help="Cap number of items")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = build_catalog(args.all, args.limit)
    if df.empty:
        sys.exit("No items selected — nothing to build.")

    # Seed the catalog cache so run_pipeline() never needs the API key.
    df.to_parquet(CATALOG_PARQUET, index=False)
    print(f"Wrote {len(df)} items → {CATALOG_PARQUET}")

    # Enrich via public csgostocks endpoints, then consolidate → items.parquet.
    run_pipeline(
        api_key="unused-catalog-is-cached",
        force=True,
        use_catalog_cache=True,
        workers_override=args.workers,
    )
    print("Done. data/items.parquet is ready.")


if __name__ == "__main__":
    main()
