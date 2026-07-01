import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from ETL.pipeline import consolidate, load_catalog, run_pipeline
from ETL.probe import load_or_run_probe


def _require_key() -> str:
    load_dotenv()
    key = os.getenv("CSMARKETAPI_KEY")
    if not key:
        print("Error: CSMARKETAPI_KEY not set (check .env)", file=sys.stderr)
        sys.exit(1)
    return key


def main() -> None:
    parser = argparse.ArgumentParser(prog="ETL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run full pipeline (per-weapon batches)")
    p_run.add_argument("--weapon", help="Run only one weapon group")
    p_run.add_argument("--force", action="store_true", help="Re-enrich even if parquet exists")
    p_run.add_argument("--reprobe", action="store_true", help="Force concurrency probe")
    p_run.add_argument("--refresh-catalog", action="store_true", help="Re-download catalog from CS Market API")
    p_run.add_argument("--workers", type=int, help="Override worker count (skip probe)")

    p_probe = sub.add_parser("probe", help="Run concurrency probe only")

    p_cons = sub.add_parser("consolidate", help="Merge data/raw/*.parquet → data/items.parquet")

    p_events = sub.add_parser("events", help="Scrape Steam News + Valve RSS + Operations → data/events.parquet")
    p_events.add_argument("--since", help="ISO date (YYYY-MM-DD). Default: 2013-01-01")

    p_dop = sub.add_parser("dopplers", help="Build Doppler phase catalog → data/doppler_phases.parquet")

    p_tiers = sub.add_parser("tiers", help="Compute tier whitelist → data/whitelist_tiers.parquet")

    p_prep = sub.add_parser("prep", help="Build clean features + targets in data/processed/")
    p_prep.add_argument("--tier", type=int, default=3, help="Include items tier ≤ N (default 3)")
    p_prep.add_argument("--limit", type=int, help="Cap number of items (for quick tests)")
    p_prep.add_argument("--min-history", type=int, default=730, help="Skip items with fewer days")

    args = parser.parse_args()

    if args.cmd == "run":
        api_key = _require_key()
        run_pipeline(
            api_key,
            weapon=args.weapon,
            force=args.force,
            reprobe=args.reprobe,
            use_catalog_cache=not args.refresh_catalog,
            workers_override=args.workers,
        )
    elif args.cmd == "probe":
        api_key = _require_key()
        catalog = load_catalog(api_key, use_cache=True)
        sample = catalog.head(50).to_dict(orient="records")
        workers = load_or_run_probe(sample, force=True)
        print(f"Probe done. chosen_workers={workers}")
    elif args.cmd == "consolidate":
        consolidate()
    elif args.cmd == "events":
        from ETL.events import run_events
        since_str = args.since or "2013-01-01"
        since = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        run_events(since=since)
    elif args.cmd == "dopplers":
        from ETL.dopplers import run_dopplers
        run_dopplers()
    elif args.cmd == "tiers":
        from ETL.tiers import run_tiers
        run_tiers()
    elif args.cmd == "prep":
        from ETL.preprocess import make_dataset
        make_dataset(tier=args.tier, limit=args.limit, min_history_days=args.min_history)


if __name__ == "__main__":
    main()
