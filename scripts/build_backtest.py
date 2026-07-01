"""Precompute rolling-origin backtest accuracy for the tiered universe.

Writes data/processed/backtest.parquet with per-item MAPE, which the API serves
as an O(1) lookup on /api/forecast. Run after the tier whitelist exists:

    ./.venv/Scripts/python.exe scripts/build_backtest.py [--tier 2] [--limit N] [--folds 3]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ETL.config import PROCESSED_DIR, WHITELIST_TIERS_PARQUET  # noqa: E402
from forecasting.backtest import backtest_item  # noqa: E402

BACKTEST_PARQUET = PROCESSED_DIR / "backtest.parquet"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=3, help="Backtest items with tier <= N")
    ap.add_argument("--limit", type=int, help="Cap number of items")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--horizon", type=int, default=7)
    args = ap.parse_args()

    if not WHITELIST_TIERS_PARQUET.exists():
        sys.exit("whitelist_tiers.parquet missing — run `ETL.cli tiers` first.")

    items = (
        pl.read_parquet(WHITELIST_TIERS_PARQUET)
          .filter(pl.col("tier").is_not_null() & (pl.col("tier") <= args.tier))
          .sort(["tier", "dollar_volume_30d"], descending=[False, True])
    )
    names = items["name"].to_list()
    if args.limit:
        names = names[: args.limit]
    print(f"Backtesting {len(names)} items (tier<={args.tier}, {args.folds} folds)...")

    rows = []
    start = time.perf_counter()
    for i, name in enumerate(names, 1):
        try:
            r = backtest_item(name, horizon=args.horizon, folds=args.folds)
            rows.append({
                "name": r.name, "n_folds": r.n_folds,
                "mape_h1": r.mape_h1, "mape_h7": r.mape_h7, "horizon": r.horizon,
            })
        except Exception as e:
            rows.append({"name": name, "n_folds": 0, "mape_h1": None,
                         "mape_h7": None, "horizon": args.horizon, "error": str(e)})
        if i % 25 == 0 or i == len(names):
            el = time.perf_counter() - start
            print(f"  {i}/{len(names)} ({el:.0f}s, {el / i:.2f}s/item)", flush=True)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(rows)
    df.write_parquet(BACKTEST_PARQUET)
    ok = df.filter(pl.col("mape_h7").is_not_null())
    print(f"Wrote {df.height} rows -> {BACKTEST_PARQUET}")
    if ok.height:
        print(f"Median MAPE h1={ok['mape_h1'].median():.1f}%  h7={ok['mape_h7'].median():.1f}%")


if __name__ == "__main__":
    main()
