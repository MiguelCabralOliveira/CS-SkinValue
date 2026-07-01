"""Rolling-origin backtest for forecast accuracy.

For a given item we walk the forecast origin backwards in `horizon`-day steps,
forecast from each origin using only data available at that point, and compare
against what actually happened. This yields an honest, per-item MAPE instead of
a hardcoded marketing number.

Cheap enough to precompute offline for the tiered universe (see
``scripts/build_backtest.py``) and serve as an O(1) lookup from the API.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from forecasting.inference import _chronos_small, _chronos_predict, _load_series


@dataclass
class BacktestResult:
    name: str
    n_folds: int
    mape_h1: float | None      # 1-day-ahead MAPE (%)
    mape_h7: float | None      # mean MAPE over the whole horizon (%)
    horizon: int


def _mape(actual: np.ndarray, pred: np.ndarray) -> float:
    mask = actual != 0
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs(actual[mask] - pred[mask]) / actual[mask]) * 100)


def backtest_item(
    name: str,
    horizon: int = 7,
    folds: int = 3,
    min_history: int = 256,
) -> BacktestResult:
    """Rolling backtest. Origins at N-horizon, N-2*horizon, ... (folds of them)."""
    series = _load_series(name)
    prices = series["price"].to_numpy().astype(np.float32)
    n = len(prices)

    pipeline = _chronos_small()
    h1_apes: list[float] = []
    hfull_apes: list[float] = []
    used = 0

    for f in range(1, folds + 1):
        origin = n - horizon * f
        if origin < min_history:
            break  # not enough training data this far back
        train = prices[:origin]
        actual = prices[origin:origin + horizon]
        if len(actual) < horizon:
            continue
        point, _, _ = _chronos_predict(pipeline, train, horizon)
        point = np.asarray(point, dtype=float)
        h1_apes.append(_mape(actual[:1], point[:1]))
        hfull_apes.append(_mape(actual, point))
        used += 1

    return BacktestResult(
        name=name,
        n_folds=used,
        mape_h1=float(np.nanmean(h1_apes)) if h1_apes else None,
        mape_h7=float(np.nanmean(hfull_apes)) if hfull_apes else None,
        horizon=horizon,
    )
