"""Doppler / Gamma Doppler phase taxonomy.

The dataset's `current_price` for a Doppler item is an average across phases,
which differ by >10× ($200 Phase 4 vs $5000 Black Pearl). Forecasting that
average is forecasting noise.

This module builds the **catalog** of phase variants — base item × exterior ×
phase — so future scraping can populate per-phase prices. The catalog itself
gives the forecasting pipeline a flag (`is_doppler`) for excluding aggregated
Dopplers from modelling until we have phase-resolved data.

Sources:
  - bymykel/CSGO-API (open data, kept current) for phase taxonomy
  - data/items.parquet for the exteriors we already track

Prices are NOT scraped here. Steam Market does not list phases as separate
items. Third-party APIs that do (CSFloat, BUFF, pricempire, csmoney) all
require auth/keys we don't have. The `fetch_phase_price` function is a
placeholder so the pipeline can be filled in when API access is available.

Run with:
    python -m ETL.cli dopplers
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import requests
import polars as pl

from ETL.config import (
    BYMYKEL_SKINS_URL,
    DOPPLER_PHASES_PARQUET,
    ITEMS_PARQUET,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

EXTERIORS = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]


def fetch_bymykel_skins() -> list[dict]:
    """Direct request — bymykel is a static JSON on raw.githubusercontent.com."""
    r = requests.get(BYMYKEL_SKINS_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()


def build_doppler_catalog() -> pl.DataFrame:
    """Returns one row per (weapon_skin × exterior × phase) combination."""
    skins = fetch_bymykel_skins()
    rows = []
    for s in skins:
        nm = s.get("name", "")
        if "Doppler" not in nm:
            continue
        phase = s.get("phase")
        if not phase:
            continue
        weapon_id = (s.get("weapon") or {}).get("id", "")
        # Each entry in bymykel = one phase of one base skin. Cross with exteriors.
        for ext in EXTERIORS:
            base_with_ext = f"{nm} ({ext})"
            phased_name = f"{nm} ({ext}) - {phase}"
            rows.append({
                "base_name": nm,
                "weapon_id": weapon_id,
                "exterior": ext,
                "phase": phase,
                "base_market_hash_name": base_with_ext,
                "phased_market_hash_name": phased_name,
                "is_gamma": "Gamma" in nm,
            })
    return pl.DataFrame(rows)


def cross_reference_with_dataset(catalog: pl.DataFrame) -> pl.DataFrame:
    """Mark catalog rows whose base_with_ext exists in data/items.parquet.

    Items.parquet uses names like '★ Karambit | Doppler (Factory New)' — same
    format as base_market_hash_name. We bring along the current aggregated price
    so each phase row has a reference anchor for sanity-checking.
    """
    items = (
        pl.scan_parquet(ITEMS_PARQUET)
          .select([
              pl.col("name").alias("base_market_hash_name"),
              pl.col("keyfigures").struct.field("steam").struct.field("current_price").alias("base_steam_price"),
              pl.col("keyfigures").struct.field("steam").struct.field("current_volume").alias("base_steam_vol"),
          ])
          .collect()
    )
    return catalog.join(items, on="base_market_hash_name", how="left")


def fetch_phase_price(phased_market_hash_name: str) -> dict | None:
    """Placeholder for a per-phase price lookup.

    Stays as None until a data source becomes available. Candidates:
      - CSFloat API (paid)
      - pricempire commercial tier
      - BUFF163 reverse-engineered scrape

    The pipeline tolerates None — phase rows just stay without prices, and
    downstream models exclude them via the `has_phase_price` flag.
    """
    return None


def attach_phase_prices(catalog: pl.DataFrame) -> pl.DataFrame:
    """Today: all None. Future runs: populate from fetch_phase_price.

    Saving as None columns now defines the schema, so downstream code can
    rely on it without checking for column existence.
    """
    return catalog.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("phase_price"),
        pl.lit(None, dtype=pl.Float64).alias("phase_vol"),
        pl.lit("none", dtype=pl.String).alias("phase_price_source"),
        pl.lit(datetime.now(timezone.utc), dtype=pl.Datetime).alias("snapshot_ts"),
    )


def run_dopplers(path: Path = DOPPLER_PHASES_PARQUET) -> pl.DataFrame:
    print("[dopplers] fetching bymykel/CSGO-API...", flush=True)
    catalog = build_doppler_catalog()
    print(f"[dopplers]   {catalog.height} phase rows ({catalog['base_name'].n_unique()} base skins)")

    print("[dopplers] cross-referencing with items.parquet...", flush=True)
    enriched = cross_reference_with_dataset(catalog)
    matched = enriched.filter(pl.col("base_steam_price").is_not_null())
    print(f"[dopplers]   {matched.height} rows matched against existing items "
          f"({matched['base_market_hash_name'].n_unique()} unique base × exterior)")

    print("[dopplers] attaching phase price snapshot (stub — none today)...", flush=True)
    final = attach_phase_prices(enriched)

    path.parent.mkdir(parents=True, exist_ok=True)
    final.write_parquet(path)
    print(f"[dopplers] saved {final.height:,} rows → {path}")
    print(final.group_by(["is_gamma", "phase"]).agg(pl.len().alias("n")).sort(["is_gamma", "n"], descending=[False, True]))
    return final
