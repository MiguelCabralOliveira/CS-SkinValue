"""Production inference API for CS skin price forecasts.

Backends supported (zero-shot, no training required):
  - chronos: Amazon Chronos-T5-small (default — fast, MAPE 7-9% h=1, 8-9% h=7)
  - chronos-base: Chronos-T5-base (4× slower, marginal improvement)
  - timesfm: Google TimesFM-2.5-200m (EXPERIMENTAL — silent crashes on
             Python 3.14, marked unstable for now)

Usage
-----
>>> from forecasting import forecast
>>> f = forecast("AK-47 | Redline (Field-Tested)", horizon=7)
>>> f.point          # array of 7 predicted prices
>>> f.lower, f.upper # 80% band (q10/q90)
>>> f.dates          # forecast dates
>>> f.context_price  # last observed price (anchor)
>>> f.model_used     # "chronos" etc.

>>> from forecasting import forecast
>>> f = forecast("AWP | Asiimov (Field-Tested)", horizon=30, model="timesfm")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import polars as pl

from ETL.config import ITEMS_PARQUET


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass
class Forecast:
    """Container returned by ``forecast()``."""
    name: str
    model_used: str
    horizon: int
    context_price: float
    context_last_date: date
    dates: list[date]
    point: np.ndarray           # shape (horizon,)
    lower: np.ndarray           # 10th percentile
    upper: np.ndarray           # 90th percentile
    metadata: dict = field(default_factory=dict)

    def as_dataframe(self) -> pl.DataFrame:
        return pl.DataFrame({
            "date":  self.dates,
            "point": self.point,
            "lower": self.lower,
            "upper": self.upper,
        })

    def __repr__(self) -> str:
        return (
            f"Forecast(name={self.name!r}, model={self.model_used!r}, h={self.horizon}, "
            f"anchor=${self.context_price:.2f}@{self.context_last_date}, "
            f"point=[{self.point[0]:.2f}..{self.point[-1]:.2f}], "
            f"band_width≈{np.mean(self.upper - self.lower):.2f})"
        )


# ---------------------------------------------------------------------------
# Series loader (reuse the same outlier-filtered logic)
# ---------------------------------------------------------------------------

def _load_series(name: str, min_price_ratio: float = 0.05) -> pl.DataFrame:
    """Hourly→daily series, outlier-filtered, sorted by date."""
    row = pl.scan_parquet(ITEMS_PARQUET).filter(pl.col("name") == name).collect()
    if not row.height:
        raise ValueError(f"Item not found in items.parquet: {name!r}")
    r = row.row(0, named=True)
    if r["price_series"]:
        df = (
            pl.DataFrame(r["price_series"])
              .with_columns(pl.from_epoch("ts", time_unit="s").alias("dt"))
              .group_by_dynamic("dt", every="1d")
              .agg([pl.col("price").last().alias("price"), pl.col("volume").sum().alias("volume")])
              .with_columns(pl.col("dt").dt.date().alias("date"))
              .select(["date", "price", "volume"]).sort("date")
        )
    elif r["price_ohlc"]:
        raw = (
            pl.DataFrame(r["price_ohlc"])
              .with_columns(pl.from_epoch("ts", time_unit="ms").dt.date().alias("date"))
              .select(["date", pl.col("c").alias("price")]).sort("date")
        )
        dr = pl.date_range(raw["date"].min(), raw["date"].max(), interval="1d", eager=True)
        df = (
            dr.to_frame(name="date").join(raw, on="date", how="left")
              .with_columns(pl.col("price").forward_fill())
              .with_columns(pl.lit(None, dtype=pl.Float64).alias("volume"))
        )
    else:
        raise ValueError(f"Item {name!r} has no price_series and no price_ohlc")

    if min_price_ratio > 0 and df.height:
        med = df["price"].median()
        df = df.filter(pl.col("price") >= med * min_price_ratio)
    return df


# ---------------------------------------------------------------------------
# Model adapters (lazy-loaded)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _chronos_small():
    import torch
    from chronos import ChronosPipeline
    return ChronosPipeline.from_pretrained(
        "amazon/chronos-t5-small", device_map="cpu", torch_dtype=torch.float32,
    )


@lru_cache(maxsize=1)
def _chronos_base():
    import torch
    from chronos import ChronosPipeline
    return ChronosPipeline.from_pretrained(
        "amazon/chronos-t5-base", device_map="cpu", torch_dtype=torch.float32,
    )


@lru_cache(maxsize=1)
def _timesfm():
    from transformers import TimesFmModelForPrediction
    m = TimesFmModelForPrediction.from_pretrained("google/timesfm-2.5-200m-pytorch")
    m.eval()
    return m


def _chronos_predict(pipeline, history: np.ndarray, horizon: int, num_samples: int = 50):
    import torch
    ctx = torch.tensor(history[-512:], dtype=torch.float32)
    samples = pipeline.predict(inputs=ctx, prediction_length=horizon, num_samples=num_samples)
    arr = samples[0].numpy()  # (num_samples, horizon)
    point = np.median(arr, axis=0)
    lower = np.quantile(arr, 0.1, axis=0)
    upper = np.quantile(arr, 0.9, axis=0)
    return point, lower, upper


def _timesfm_predict(model, history: np.ndarray, horizon: int):
    import torch
    ctx = history[-1024:]
    inputs = torch.tensor([ctx], dtype=torch.float32)
    freq = torch.zeros([1], dtype=torch.long)  # 0 = high-frequency
    with torch.no_grad():
        out = model(past_values=inputs, freq=freq)
    point = out.mean_predictions[0].numpy()[:horizon]
    # Use quantile_predictions if available for intervals
    if hasattr(out, "quantile_predictions") and out.quantile_predictions is not None:
        # quantile_predictions shape: [batch, quantile_horizon_length, num_quantiles]
        # quantiles config = [0.1, 0.2, ..., 0.9] → indices 0=q10, 8=q90
        qp = out.quantile_predictions[0].numpy()
        lower = qp[:horizon, 0]
        upper = qp[:horizon, 8]
    else:
        # fallback: ±2σ assumption (will be coarse)
        spread = np.std(history[-30:]) * 1.28
        lower = point - spread
        upper = point + spread
    return point, lower, upper


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ModelName = Literal["chronos", "chronos-base", "timesfm"]
_VALID_MODELS = ("chronos", "chronos-base", "timesfm")
_STABLE_MODELS = ("chronos", "chronos-base")


def list_available_models(stable_only: bool = True) -> list[str]:
    return list(_STABLE_MODELS if stable_only else _VALID_MODELS)


def forecast(
    name: str,
    horizon: int = 7,
    model: ModelName = "chronos",
    min_history: int = 256,
) -> Forecast:
    """Generate a forecast for an item.

    Parameters
    ----------
    name : str
        Exact `market_hash_name` as stored in ``items.parquet``.
    horizon : int
        Number of forecast steps (days) to predict.
    model : str
        Backend to use. Default 'chronos' (fast, MAPE ~7-9%).
    min_history : int
        Refuse to forecast if fewer than this many cleaned daily prices exist.

    Returns
    -------
    Forecast
        Dataclass with point estimate + 80% band (q10/q90).
    """
    if model not in _VALID_MODELS:
        raise ValueError(f"unknown model {model!r}; valid: {_VALID_MODELS}")
    if horizon < 1:
        raise ValueError("horizon must be >= 1")

    series = _load_series(name)
    if series.height < min_history:
        raise ValueError(
            f"Item {name!r} has only {series.height} days of clean history "
            f"(need {min_history}). Foundation models work best with ≥256 days."
        )

    prices = series["price"].to_numpy().astype(np.float32)
    anchor_price = float(prices[-1])
    anchor_date = series["date"][-1]

    if model == "chronos":
        pipeline = _chronos_small()
        point, lower, upper = _chronos_predict(pipeline, prices, horizon)
    elif model == "chronos-base":
        pipeline = _chronos_base()
        point, lower, upper = _chronos_predict(pipeline, prices, horizon)
    elif model == "timesfm":
        mdl = _timesfm()
        point, lower, upper = _timesfm_predict(mdl, prices, horizon)

    forecast_dates = [anchor_date + timedelta(days=i + 1) for i in range(horizon)]

    return Forecast(
        name=name,
        model_used=model,
        horizon=horizon,
        context_price=anchor_price,
        context_last_date=anchor_date,
        dates=forecast_dates,
        point=np.asarray(point, dtype=float),
        lower=np.asarray(lower, dtype=float),
        upper=np.asarray(upper, dtype=float),
        metadata={"n_context_days": int(series.height)},
    )


def forecast_batch(
    names: list[str],
    horizon: int = 7,
    model: ModelName = "chronos",
) -> pl.DataFrame:
    """Forecast many items, return a long-format DataFrame."""
    rows = []
    for nm in names:
        try:
            f = forecast(nm, horizon=horizon, model=model)
            for d, p, lo, hi in zip(f.dates, f.point, f.lower, f.upper):
                rows.append({
                    "name": nm, "model": model, "date": d,
                    "point": float(p), "lower": float(lo), "upper": float(hi),
                    "anchor_price": f.context_price,
                })
        except Exception as e:
            rows.append({
                "name": nm, "model": model, "date": None,
                "point": None, "lower": None, "upper": None,
                "anchor_price": None, "error": str(e),
            })
    return pl.DataFrame(rows)
