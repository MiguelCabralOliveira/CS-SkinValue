"""Tier-system whitelist for forecasting.

Computes per-item liquidity (dollar-volume rolling 30d / 90d), post-2023
calendar coverage, and assigns a tier. Thresholds calibrated against the
empirical distribution (the CS skin market is much smaller than equities or
crypto — p95 of $vol/day is only ~$1k):

  Tier 1 (ultra):      dollar_volume_30d >= $5k, price >= $50, post_2023_days >= 365
  Tier 2 (liquid):     dollar_volume_30d >= $1k, price >= $20, post_2023_days >= 365
  Tier 3 (acceptable): dollar_volume_30d >= $200, price >= $5,  post_2023_days >= 365

The tier column is the **best** tier the item qualifies for. Items below tier 3
get tier=None.

For series-sourced items (hourly with volume), we compute rolling 30d/90d
dollar-volume from price_series itself. For OHLC-sourced items (knives/gloves,
no volume in series), we fall back to `steam_price * steam_current_volume` —
a snapshot proxy that overestimates briefly-spiking items but is close enough
for tiering.

Run with:
    python -m ETL.cli tiers
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from ETL.config import ITEMS_PARQUET, WHITELIST_TIERS_PARQUET

REGIME_BREAK = datetime(2023, 1, 1, tzinfo=timezone.utc)


def _build_per_item_stats() -> pl.DataFrame:
    """Project items.parquet → per-item liquidity + history features.

    Uses Polars list expressions so each item's price_series is evaluated
    in-row without exploding to billions of rows.
    """
    lf = pl.scan_parquet(ITEMS_PARQUET)

    # Cutoff timestamps used inside list.eval are computed eagerly.
    now_ts = int(datetime.now(timezone.utc).timestamp())
    regime_break_ts = int(REGIME_BREAK.timestamp())
    cutoff_30d = now_ts - 30 * 86400
    cutoff_90d = now_ts - 90 * 86400

    # For OHLC items, ts is in milliseconds; series is seconds.
    return (
        lf.with_columns(
            pl.col("price_series").list.len().fill_null(0).alias("n_series_pts"),
            pl.col("price_ohlc").list.len().fill_null(0).alias("n_ohlc_pts"),
            pl.col("keyfigures").struct.field("steam").struct.field("current_price").alias("steam_price"),
            pl.col("keyfigures").struct.field("steam").struct.field("current_volume").alias("steam_vol"),
            pl.col("price_series").list.last().struct.field("ts").alias("last_series_ts_s"),
            pl.col("price_ohlc").list.last().struct.field("ts").alias("last_ohlc_ts_ms"),
            # Rolling dollar volume from price_series (last 30 / 90 days).
            (
                pl.col("price_series").list.eval(
                    pl.when(pl.element().struct.field("ts") >= cutoff_30d)
                      .then(pl.element().struct.field("price") * pl.element().struct.field("volume"))
                      .otherwise(0.0)
                ).list.sum() / 30.0
            ).alias("dollar_volume_30d"),
            (
                pl.col("price_series").list.eval(
                    pl.when(pl.element().struct.field("ts") >= cutoff_90d)
                      .then(pl.element().struct.field("price") * pl.element().struct.field("volume"))
                      .otherwise(0.0)
                ).list.sum() / 90.0
            ).alias("dollar_volume_90d"),
            # Days of price_series within post-2023 regime.
            pl.col("price_series").list.eval(
                pl.when(pl.element().struct.field("ts") >= regime_break_ts)
                  .then(pl.element().struct.field("ts"))
                  .otherwise(None)
            ).list.drop_nulls().list.len().alias("series_pts_post_2023"),
            # OHLC post-2023 count (ts in ms here).
            pl.col("price_ohlc").list.eval(
                pl.when(pl.element().struct.field("ts") >= regime_break_ts * 1000)
                  .then(pl.element().struct.field("ts"))
                  .otherwise(None)
            ).list.drop_nulls().list.len().alias("ohlc_pts_post_2023"),
        )
        .with_columns(
            # Estimate calendar days post-2023:
            # series is hourly → divide by 24, OHLC is 2-day → multiply by 2.
            pl.max_horizontal(
                pl.col("series_pts_post_2023") // 24,
                pl.col("ohlc_pts_post_2023") * 2,
            ).alias("post_2023_days"),
            pl.from_epoch("last_series_ts_s", time_unit="s").alias("last_series_dt"),
            pl.from_epoch("last_ohlc_ts_ms", time_unit="ms").alias("last_ohlc_dt"),
            pl.when(pl.col("n_series_pts") > 0).then(pl.lit("series"))
              .when(pl.col("n_ohlc_pts") > 0).then(pl.lit("ohlc"))
              .otherwise(pl.lit("none")).alias("source"),
            pl.col("name").str.contains("Doppler").alias("is_doppler"),
            pl.col("name").str.contains("Souvenir").alias("is_souvenir"),
        )
        .with_columns(
            pl.max_horizontal("last_series_dt", "last_ohlc_dt").alias("last_dt"),
            # OHLC items have no series volume → fall back to snapshot $vol from keyfigures.
            pl.when((pl.col("source") == "ohlc") & (pl.col("dollar_volume_30d") == 0))
              .then(pl.col("steam_price") * pl.col("steam_vol"))
              .otherwise(pl.col("dollar_volume_30d"))
              .alias("dollar_volume_30d"),
        )
        .select([
            "name", "weapon", "quality", "type", "collection",
            "steam_price", "steam_vol",
            "dollar_volume_30d", "dollar_volume_90d",
            "n_series_pts", "n_ohlc_pts", "source",
            "last_dt", "post_2023_days",
            "is_doppler", "is_souvenir",
        ])
        .collect()
    )


def _assign_tier(df: pl.DataFrame) -> pl.DataFrame:
    """Assign best-fit tier. None = below tier 3. Thresholds calibrated to
    actual market: median $vol/day is only $43, p95 is $1k.
    """
    return df.with_columns(
        pl.when(
            (pl.col("dollar_volume_30d") >= 5_000)
            & (pl.col("steam_price") >= 50)
            & (pl.col("post_2023_days") >= 365)
        ).then(pl.lit(1))
        .when(
            (pl.col("dollar_volume_30d") >= 1_000)
            & (pl.col("steam_price") >= 20)
            & (pl.col("post_2023_days") >= 365)
        ).then(pl.lit(2))
        .when(
            (pl.col("dollar_volume_30d") >= 200)
            & (pl.col("steam_price") >= 5)
            & (pl.col("post_2023_days") >= 365)
        ).then(pl.lit(3))
        .otherwise(None)
        .alias("tier")
    )


def _mark_doppler_phase_data(df: pl.DataFrame) -> pl.DataFrame:
    """Cross-reference Doppler items with the phase catalog to set has_phase_data."""
    from ETL.config import DOPPLER_PHASES_PARQUET
    if not DOPPLER_PHASES_PARQUET.exists():
        return df.with_columns(pl.lit(False).alias("has_phase_data"))

    phases = pl.read_parquet(DOPPLER_PHASES_PARQUET)
    # has_phase_data := we have at least one phase row WITH a non-null price.
    items_with_prices = (
        phases.filter(pl.col("phase_price").is_not_null())
              .select(pl.col("base_market_hash_name").unique().alias("name"))
              .with_columns(pl.lit(True).alias("has_phase_data"))
    )
    return (
        df.join(items_with_prices, on="name", how="left")
          .with_columns(pl.col("has_phase_data").fill_null(False))
    )


def run_tiers(path: Path = WHITELIST_TIERS_PARQUET) -> pl.DataFrame:
    print("[tiers] computing per-item liquidity from price_series...", flush=True)
    stats = _build_per_item_stats()
    print(f"[tiers]   {stats.height:,} items projected")

    stats = _assign_tier(stats)
    stats = _mark_doppler_phase_data(stats)

    # Freshness filter: last observation within 14 days of the most recent in
    # the dataset. Items >14d stale get tier=None regardless of liquidity.
    max_dt = stats["last_dt"].max()
    stats = stats.with_columns(
        ((max_dt - pl.col("last_dt")).dt.total_seconds() / 86400).alias("stale_days")
    ).with_columns(
        pl.when(pl.col("stale_days") > 14).then(None).otherwise(pl.col("tier")).alias("tier")
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    stats.write_parquet(path)
    print(f"[tiers] saved {stats.height:,} rows → {path}")

    summary = (
        stats.group_by("tier")
             .agg([
                 pl.len().alias("n"),
                 pl.col("steam_price").median().round(0).alias("med_price"),
                 pl.col("dollar_volume_30d").median().round(0).alias("med_$vol_30d"),
             ])
             .sort("tier", nulls_last=True)
    )
    print(summary)
    print(f"[tiers] Dopplers marked: {stats['is_doppler'].sum()} | "
          f"with phase data: {stats['has_phase_data'].sum()} | "
          f"Souvenirs: {stats['is_souvenir'].sum()}")
    return stats
