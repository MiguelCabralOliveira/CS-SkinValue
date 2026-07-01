"""Shared helpers for forecasting notebooks.

Notebooks 04..09 all need the same: read a single item's series, list the
universe, load the event timeline, join events as features. This module
centralizes those so they don't drift across notebooks.

Usage from a notebook:
    import sys; sys.path.insert(0, ".")
    from _helpers import get_daily, list_modelable, load_events, daily_with_events
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

# Resolve dataset paths relative to repo root (notebooks/ is one level deep).
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
PARQUET = _REPO / "data" / "items.parquet"
EVENTS = _REPO / "data" / "events.parquet"
WHITELIST = _REPO / "data" / "whitelist_tiers.parquet"
DOPPLERS = _REPO / "data" / "doppler_phases.parquet"


# ---------------------------------------------------------------------------
# Single-item daily series
# ---------------------------------------------------------------------------

def get_daily(name: str, source: str = "auto", min_price_ratio: float = 0.05) -> pl.DataFrame:
    """Daily-close series for an item.

    Handles both backends:
      - price_series (hourly): resample to daily-last + sum volume
      - price_ohlc (2-day):    forward-fill into daily

    Drops outlier rows where ``price < median * min_price_ratio`` — the upstream
    scrapers occasionally emit near-zero prints that destroy returns. Pass 0 to
    keep all rows.
    """
    row = pl.scan_parquet(PARQUET).filter(pl.col("name") == name).collect()
    if not row.height:
        raise ValueError(f"not found: {name}")
    r = row.row(0, named=True)

    if (source in ("auto", "series")) and r["price_series"]:
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
    elif (source in ("auto", "ohlc")) and r["price_ohlc"]:
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
    else:
        return pl.DataFrame()

    if df.height and min_price_ratio > 0:
        med = df["price"].median()
        df = df.filter(pl.col("price") >= med * min_price_ratio)
    return df


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def list_top_items(
    n: int = 50,
    by: str = "dollar_volume",
    min_history_days: int = 365 * 3,
    exclude_souvenirs: bool = True,
    include_ohlc: bool = True,
    min_steam_vol: float = 5,
    min_price: float = 5.0,
) -> pl.DataFrame:
    """Legacy API shim used by notebooks 05/06/07.

    Reads directly from items.parquet (not whitelist_tiers) to preserve the
    original semantics — tier-based filtering is opt-in via `list_modelable`.
    """
    q = (
        pl.scan_parquet(PARQUET)
          .with_columns(
              pl.col("price_series").list.len().fill_null(0).alias("n_series_pts"),
              pl.col("price_ohlc").list.len().fill_null(0).alias("n_ohlc_pts"),
              pl.col("keyfigures").struct.field("steam").struct.field("current_price").alias("steam"),
              pl.col("keyfigures").struct.field("steam").struct.field("current_volume").alias("steam_vol"),
          )
          .with_columns((pl.col("steam") * pl.col("steam_vol")).alias("dollar_volume"))
    )
    history_expr = pl.col("n_series_pts") >= min_history_days * 24
    if include_ohlc:
        history_expr = history_expr | (pl.col("n_ohlc_pts") >= min_history_days // 2)
    q = q.filter(
        pl.col("steam").is_not_null()
        & (pl.col("steam_vol") >= min_steam_vol)
        & (pl.col("steam") >= min_price)
        & history_expr
    ).select(["name", "weapon", "quality", "type", "steam", "steam_vol",
              "dollar_volume", "n_series_pts", "n_ohlc_pts"])
    if exclude_souvenirs:
        q = q.filter(~pl.col("name").str.contains("Souvenir"))
    sort_col = {"dollar_volume": "dollar_volume", "volume": "steam_vol", "price": "steam"}[by]
    return q.sort(sort_col, descending=True).head(n).collect()


def list_modelable(
    tier: int = 2,
    exclude_doppler: bool = True,
    exclude_souvenirs: bool = True,
    only_fresh: bool = True,
) -> pl.DataFrame:
    """Items at or above the given tier.

    tier=1 returns ultra-liquid only (~22 items); tier=2 returns 1+2 (~310);
    tier=3 returns 1+2+3 (~1.3k). Fresh items have last observation within 14d.
    """
    if not WHITELIST.exists():
        raise FileNotFoundError(
            f"{WHITELIST} missing — run `python -m ETL.cli tiers` first."
        )
    df = pl.read_parquet(WHITELIST)
    df = df.filter(pl.col("tier").is_not_null() & (pl.col("tier") <= tier))
    if exclude_doppler:
        df = df.filter(~pl.col("is_doppler"))
    if exclude_souvenirs:
        df = df.filter(~pl.col("is_souvenir"))
    if only_fresh:
        df = df.filter(pl.col("stale_days") <= 14)
    return df.sort(["tier", "dollar_volume_30d"], descending=[False, True])


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def load_events(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    event_types: list[str] | None = None,
) -> pl.DataFrame:
    """Read the event timeline. Filter by date window and event types if given."""
    if not EVENTS.exists():
        raise FileNotFoundError(
            f"{EVENTS} missing — run `python -m ETL.cli events` first."
        )
    df = pl.read_parquet(EVENTS)
    if start_date is not None:
        if isinstance(start_date, str):
            start_date = date.fromisoformat(start_date)
        df = df.filter(pl.col("event_date") >= start_date)
    if end_date is not None:
        if isinstance(end_date, str):
            end_date = date.fromisoformat(end_date)
        df = df.filter(pl.col("event_date") <= end_date)
    if event_types:
        df = df.filter(pl.col("event_type").is_in(event_types))
    return df.sort("event_date")


def _operations_active_mask(dates: pl.Series, events: pl.DataFrame) -> pl.Series:
    """Boolean per date: is an operation running (between start and end)?"""
    ops = events.filter(pl.col("event_type") == "operation_start").select(
        pl.col("event_date").alias("op_start"),
        pl.col("event_name"),
    )
    ends = events.filter(pl.col("event_type") == "operation_end").select(
        pl.col("event_date").alias("op_end"),
        pl.col("event_name"),
    )
    intervals = ops.join(ends, on="event_name", how="inner")
    out = []
    for d in dates.to_list():
        active = any(
            row["op_start"] <= d <= row["op_end"]
            for row in intervals.iter_rows(named=True)
        )
        out.append(active)
    return pl.Series("in_operation", out)


def daily_with_events(name: str, source: str = "auto") -> pl.DataFrame:
    """Daily series joined with event-proximity features.

    Adds columns:
      - days_since_update: int       (days since last 'update' event, capped 60)
      - in_operation: bool           (is an Operation active that day)
      - dow: int                     (0=Mon..6=Sun)
    """
    df = get_daily(name, source=source)
    if df.is_empty():
        return df

    ev = load_events()
    updates = ev.filter(pl.col("event_type") == "update")["event_date"].sort().to_list()

    # days_since_update via merge_asof
    update_df = pl.DataFrame({
        "event_date": updates,
        "_anchor": updates,
    })
    df = (
        df.sort("date")
          .join_asof(update_df, left_on="date", right_on="event_date", strategy="backward")
          .with_columns(
              ((pl.col("date") - pl.col("_anchor")).dt.total_days().clip(0, 60))
                .alias("days_since_update")
          )
          .drop(["event_date", "_anchor"])
    )
    df = df.with_columns(
        pl.col("date").dt.weekday().alias("dow"),
        _operations_active_mask(df["date"], ev).alias("in_operation"),
    )
    return df


# ---------------------------------------------------------------------------
# Doppler taxonomy
# ---------------------------------------------------------------------------

def list_doppler_phases(base_name: str | None = None) -> pl.DataFrame:
    """Catalog of Doppler / Gamma Doppler phase variants."""
    if not DOPPLERS.exists():
        raise FileNotFoundError(
            f"{DOPPLERS} missing — run `python -m ETL.cli dopplers` first."
        )
    df = pl.read_parquet(DOPPLERS)
    if base_name:
        df = df.filter(pl.col("base_market_hash_name") == base_name)
    return df
