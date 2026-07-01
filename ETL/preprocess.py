"""Data preprocessing pipeline for forecasting.

Takes the raw `data/items.parquet` + `events.parquet` + `whitelist_tiers.parquet`
and produces clean, model-ready feature/target parquets in `data/processed/`.

Pipeline stages
---------------
1. **clean_series**: filter outliers, drop frozen segments, mark gaps
2. **build_features**: lags, rolling stats, calendar, event features
3. **build_peer_features**: cross-sectional aggregates per (weapon, date) and (type, date)
4. **build_targets**: forward log-return + price at h ∈ {1, 7}
5. **make_dataset**: orchestrator → 3 parquets ready for training

Run with:
    python -m ETL.cli prep [--tier 3] [--limit N]
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import polars as pl

from ETL.config import (
    CLEAN_SERIES_PARQUET,
    EVENTS_PARQUET,
    FEATURES_PARQUET,
    ITEMS_PARQUET,
    PEER_FEATURES_PARQUET,
    PROCESSED_DIR,
    TARGETS_PARQUET,
    WHITELIST_TIERS_PARQUET,
)

# Regime-change anchor: CS2 launch / new price era.
REGIME_BREAK = pl.date(2023, 1, 1)
HORIZONS = [1, 7]

# ---------------------------------------------------------------------------
# 1. Daily series + cleaning
# ---------------------------------------------------------------------------

def _raw_daily(name: str) -> pl.DataFrame:
    """Same logic as notebooks/_helpers.get_daily but without outlier filter —
    we want to see them in `clean_series` and flag them explicitly."""
    row = pl.scan_parquet(ITEMS_PARQUET).filter(pl.col("name") == name).collect()
    if not row.height:
        return pl.DataFrame()
    r = row.row(0, named=True)

    if r["price_series"]:
        df = (
            pl.DataFrame(r["price_series"])
              .with_columns(pl.from_epoch("ts", time_unit="s").alias("dt"))
              .group_by_dynamic("dt", every="1d")
              .agg([
                  pl.col("price").last().alias("price"),
                  pl.col("volume").sum().alias("volume"),
              ])
              .with_columns(pl.col("dt").dt.date().alias("date"))
              .select(["date", "price", "volume"])
              .sort("date")
        )
        return df.with_columns(pl.lit("series").alias("source"))
    if r["price_ohlc"]:
        raw = (
            pl.DataFrame(r["price_ohlc"])
              .with_columns(pl.from_epoch("ts", time_unit="ms").dt.date().alias("date"))
              .select(["date", pl.col("c").alias("price")])
              .sort("date")
        )
        dr = pl.date_range(raw["date"].min(), raw["date"].max(), interval="1d", eager=True)
        df = (
            dr.to_frame(name="date").join(raw, on="date", how="left")
              .with_columns(pl.col("price").forward_fill())
              .with_columns(pl.lit(None, dtype=pl.Float64).alias("volume"))
        )
        return df.with_columns(pl.lit("ohlc").alias("source"))
    return pl.DataFrame()


def clean_series(name: str,
                 winsor_low: float = 0.005,
                 winsor_high: float = 0.995,
                 zero_vol_threshold: int = 7,
                 frozen_threshold: int = 14) -> pl.DataFrame:
    """Clean a single item's daily series.

    Adds these quality columns:
      - is_outlier_price: price below median * 0.05 (scraper bug)
      - is_zero_vol: volume == 0 (for series source; for ohlc always False)
      - is_frozen: ≥ frozen_threshold consecutive days at same price (delisted/stale)
      - log_ret_raw: raw log return
      - log_ret: winsorized log return (target-grade)
      - post_2023: True if date >= REGIME_BREAK

    Drops rows flagged as is_outlier_price. Frozen segments are KEPT but flagged —
    downstream code can mask them.
    """
    df = _raw_daily(name)
    if df.is_empty():
        return df

    med = df["price"].median()
    df = df.filter(pl.col("price") >= med * 0.05)  # hard outliers gone

    # Frozen segment detection: consecutive days where price didn't move.
    same_streak = (
        df.with_columns((pl.col("price") != pl.col("price").shift(1)).cum_sum().alias("group"))
          .with_columns(pl.col("price").count().over("group").alias("streak_len"))
    )
    df = same_streak.with_columns(
        (pl.col("streak_len") >= frozen_threshold).alias("is_frozen")
    ).drop(["group", "streak_len"])

    # Zero-volume flag (only meaningful for series source)
    if "volume" in df.columns:
        df = df.with_columns(
            (pl.col("volume").fill_null(0) == 0).alias("is_zero_vol_day")
        )
    else:
        df = df.with_columns(pl.lit(False).alias("is_zero_vol_day"))

    # Log returns: raw + winsorized
    p = df["price"].to_numpy()
    raw_r = np.concatenate([[np.nan], np.diff(np.log(p))])
    finite = raw_r[np.isfinite(raw_r)]
    if len(finite) >= 50:
        lo, hi = np.quantile(finite, [winsor_low, winsor_high])
        wins_r = np.clip(raw_r, lo, hi)
    else:
        wins_r = raw_r

    df = df.with_columns(
        pl.Series("log_ret_raw", raw_r),
        pl.Series("log_ret", wins_r),
        (pl.col("date") >= REGIME_BREAK).alias("post_2023"),
        pl.lit(name).alias("name"),
    )
    return df


# ---------------------------------------------------------------------------
# 2. Feature engineering (per item)
# ---------------------------------------------------------------------------

def build_features_for_item(clean: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    """Per-item features. Assumes `clean` came from clean_series()."""
    if clean.is_empty():
        return clean

    # Lagged returns (1..14)
    lag_exprs = {f"lag_ret_{k}": pl.col("log_ret").shift(k) for k in range(1, 15)}
    # Rolling vol / mean
    rolling_exprs = {
        "ret_mean_5":  pl.col("log_ret").rolling_mean(5),
        "ret_mean_20": pl.col("log_ret").rolling_mean(20),
        "vol_5":  pl.col("log_ret").rolling_std(5),
        "vol_20": pl.col("log_ret").rolling_std(20),
        "vol_60": pl.col("log_ret").rolling_std(60),
        "price_mean_20": pl.col("price").rolling_mean(20),
        "price_max_60":  pl.col("price").rolling_max(60),
        "price_min_60":  pl.col("price").rolling_min(60),
    }
    df = clean.with_columns(**lag_exprs, **rolling_exprs)

    # Price-relative-to-trend
    df = df.with_columns(
        (pl.col("price") / pl.col("price_mean_20") - 1).alias("price_dev_ma20"),
    )

    # Calendar features
    df = df.with_columns(
        pl.col("date").dt.weekday().alias("dow"),
        pl.col("date").dt.month().alias("month"),
        pl.col("date").dt.day().alias("dom"),
    )

    # Event features — use vectorized merge_asof for speed
    case_releases = (
        events.filter(pl.col("event_type") == "case_release")
              .select(pl.col("event_date").alias("_case"))
              .sort("_case")
    )
    updates = (
        events.filter(pl.col("event_type") == "update")
              .select(pl.col("event_date").alias("_upd"))
              .sort("_upd")
    )
    ops_starts = (
        events.filter(pl.col("event_type") == "operation_start")
              .select(pl.col("event_date").alias("_op"))
              .sort("_op")
    )
    df = df.sort("date")
    df = df.join_asof(case_releases, left_on="date", right_on="_case", strategy="backward")
    df = df.join_asof(updates, left_on="date", right_on="_upd", strategy="backward")
    df = df.join_asof(ops_starts, left_on="date", right_on="_op", strategy="backward")
    df = df.with_columns(
        ((pl.col("date") - pl.col("_case")).dt.total_days().clip(0, 365)).alias("days_since_case"),
        ((pl.col("date") - pl.col("_upd")).dt.total_days().clip(0, 60)).alias("days_since_update"),
        ((pl.col("date") - pl.col("_op")).dt.total_days().clip(0, 365)).alias("days_since_op_start"),
    ).drop(["_case", "_upd", "_op"])

    return df


# ---------------------------------------------------------------------------
# 3. Peer features (cross-sectional)
# ---------------------------------------------------------------------------

def build_peer_features(all_clean: pl.DataFrame, items_meta: pl.DataFrame) -> pl.DataFrame:
    """Compute per-(date, weapon) and per-(date, type) aggregates.

    Returns long DataFrame ready to join back on (date, name → weapon/type).
    """
    joined = all_clean.join(items_meta.select(["name", "weapon", "type"]), on="name", how="left")

    peer_weapon = (
        joined.filter(pl.col("log_ret").is_not_null())
              .group_by(["date", "weapon"])
              .agg([
                  pl.col("log_ret").mean().alias("peer_w_ret_mean"),
                  pl.col("log_ret").std().alias("peer_w_ret_std"),
                  pl.len().alias("peer_w_n"),
              ])
    )
    peer_type = (
        joined.filter(pl.col("log_ret").is_not_null())
              .group_by(["date", "type"])
              .agg([
                  pl.col("log_ret").mean().alias("peer_t_ret_mean"),
                  pl.len().alias("peer_t_n"),
              ])
    )
    # Market-wide return (mean across all items that day)
    market = (
        joined.filter(pl.col("log_ret").is_not_null())
              .group_by("date")
              .agg([
                  pl.col("log_ret").mean().alias("market_ret_mean"),
                  pl.col("log_ret").std().alias("market_ret_std"),
              ])
    )
    return {
        "weapon": peer_weapon,
        "type": peer_type,
        "market": market,
    }


# ---------------------------------------------------------------------------
# 4. Targets
# ---------------------------------------------------------------------------

def add_targets(df: pl.DataFrame, horizons: list[int] = HORIZONS) -> pl.DataFrame:
    """Add y_price_h{H} and y_log_ret_h{H} for each horizon."""
    p = df["price"].to_numpy()
    n = len(p)
    out_cols = {}
    for h in horizons:
        price_h = np.full(n, np.nan)
        ret_h = np.full(n, np.nan)
        for i in range(n - h):
            price_h[i] = p[i + h]
            if p[i] > 0:
                ret_h[i] = float(np.log(p[i + h] / p[i]))
        out_cols[f"y_price_h{h}"] = pl.Series(price_h)
        out_cols[f"y_log_ret_h{h}"] = pl.Series(ret_h)
    return df.with_columns(**out_cols)


# ---------------------------------------------------------------------------
# 5. Orchestrator
# ---------------------------------------------------------------------------

def make_dataset(tier: int = 3, limit: int | None = None,
                 exclude_doppler: bool = True, exclude_souvenirs: bool = True,
                 min_history_days: int = 730) -> dict:
    """Build features + targets for all items in tier ≤ N. Writes parquets to disk."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Items meta
    print(f"[prep] loading whitelist (tier ≤ {tier})...", flush=True)
    if not WHITELIST_TIERS_PARQUET.exists():
        raise FileNotFoundError("whitelist_tiers.parquet missing — run `python -m ETL.cli tiers`")
    wl = pl.read_parquet(WHITELIST_TIERS_PARQUET)
    items = wl.filter(pl.col("tier").is_not_null() & (pl.col("tier") <= tier))
    if exclude_doppler:
        items = items.filter(~pl.col("is_doppler"))
    if exclude_souvenirs:
        items = items.filter(~pl.col("is_souvenir"))
    if limit:
        items = items.head(limit)
    print(f"[prep]   {items.height} items")

    if not EVENTS_PARQUET.exists():
        raise FileNotFoundError("events.parquet missing — run `python -m ETL.cli events`")
    events = pl.read_parquet(EVENTS_PARQUET)

    # 2. Clean series for each item
    print(f"[prep] cleaning per-item series...", flush=True)
    t0 = time.time()
    all_clean = []
    for i, nm in enumerate(items["name"].to_list()):
        c = clean_series(nm)
        if not c.is_empty() and c.height >= min_history_days:
            all_clean.append(c)
        if (i + 1) % 25 == 0:
            print(f"[prep]   cleaned {i+1}/{items.height} ({time.time()-t0:.1f}s)", flush=True)
    if not all_clean:
        raise RuntimeError("no items had enough clean history")
    clean = pl.concat(all_clean)
    clean.write_parquet(CLEAN_SERIES_PARQUET)
    print(f"[prep] clean: {clean.height:,} rows / {clean['name'].n_unique()} items → {CLEAN_SERIES_PARQUET}")
    print(f"[prep]   flagged frozen days: {clean['is_frozen'].sum():,}  ({clean['is_frozen'].mean()*100:.1f}%)")
    print(f"[prep]   flagged zero-vol days: {clean['is_zero_vol_day'].sum():,}  ({clean['is_zero_vol_day'].mean()*100:.1f}%)")

    # 3. Peer features
    print(f"[prep] computing peer features...", flush=True)
    peers = build_peer_features(clean, items)
    # Join: features eventually need weapon + type columns
    clean_with_meta = clean.join(items.select(["name", "weapon", "type"]), on="name", how="left")

    # 4. Per-item features
    print(f"[prep] building features per item...", flush=True)
    t0 = time.time()
    all_feats = []
    for i, (nm,) in enumerate(items.select(["name"]).iter_rows()):
        c = clean.filter(pl.col("name") == nm)
        if c.is_empty():
            continue
        f = build_features_for_item(c, events)
        # Join peers
        weapon = items.filter(pl.col("name") == nm)["weapon"].item()
        typ = items.filter(pl.col("name") == nm)["type"].item()
        f = f.join(
            peers["weapon"].filter(pl.col("weapon") == weapon).drop("weapon"),
            on="date", how="left",
        )
        f = f.join(
            peers["type"].filter(pl.col("type") == typ).drop("type"),
            on="date", how="left",
        )
        f = f.join(peers["market"], on="date", how="left")
        f = add_targets(f, HORIZONS)
        # tag metadata
        f = f.with_columns(
            pl.lit(weapon).alias("weapon"),
            pl.lit(typ).alias("type"),
        )
        all_feats.append(f)
        if (i + 1) % 25 == 0:
            print(f"[prep]   featurized {i+1}/{items.height} ({time.time()-t0:.1f}s)", flush=True)
    feats = pl.concat(all_feats, how="diagonal_relaxed")
    feats.write_parquet(FEATURES_PARQUET)
    print(f"[prep] features: {feats.height:,} rows → {FEATURES_PARQUET}")

    # 5. Targets as separate parquet for downstream convenience
    target_cols = ["name", "date"] + [f"y_price_h{h}" for h in HORIZONS] + [f"y_log_ret_h{h}" for h in HORIZONS]
    targets = feats.select(target_cols)
    targets.write_parquet(TARGETS_PARQUET)
    print(f"[prep] targets:  {targets.height:,} rows → {TARGETS_PARQUET}")

    # 6. Peer features as standalone parquet (already inlined; save weapon-level for re-use)
    peers["weapon"].write_parquet(PEER_FEATURES_PARQUET)
    print(f"[prep] peer (weapon level): → {PEER_FEATURES_PARQUET}")

    # Final summary
    print()
    print("[prep] FEATURE COLUMN SUMMARY:")
    for c, dt in feats.schema.items():
        nulls = feats[c].null_count()
        non_null_pct = (1 - nulls / feats.height) * 100
        print(f"  {c:25}  {str(dt):15}  non-null={non_null_pct:5.1f}%")

    return {
        "clean": clean,
        "features": feats,
        "targets": targets,
        "peers": peers,
    }
