"""Cross-marketplace arbitrage from the keyfigures multi-market quotes.

Each item's keyfigures carries a current buy price + volume per marketplace
(Steam, Skinport, SkinBaron, CS.Money, Buff/uuskins, Haloskins, SkinSwap,
Skinland). This module turns that into an indicative arbitrage view: buy on the
cheapest venue, sell on the venue with the best net proceeds (after that venue's
sale fee).

These are *indicative* spreads — `current_price` is a live ask/quote, not a
guaranteed fill, and cross-venue transfers take time. Treat as a screen, not a
guaranteed profit.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

import polars as pl

from ETL.config import ITEMS_PARQUET, MARKETPLACE_FEES

# Quotes outside this band around the median buy price are treated as bad data
# (mismatched item / stale listing) and excluded from the arbitrage view. Steam
# routinely sits ~1.5× above third-party venues, so the upper bound is generous.
_OUTLIER_LOW = 0.4
_OUTLIER_HIGH = 2.5

# Sale-side fees (fraction of gross lost when selling). MARKETPLACE_FEES covers
# the third-party venues; Steam (~13% net for CS) and Skinland aren't in it.
SELL_FEES: dict[str, float] = {
    "steam": 0.13,
    "skinland": 0.05,
    **{k: v["sell"] for k, v in MARKETPLACE_FEES.items()},
}
_DEFAULT_FEE = 0.10


@dataclass
class MarketQuote:
    market: str
    buy_price: float
    volume: Optional[float]
    sell_fee: float
    net_sell: float          # proceeds if you list & sell here, after fee


@dataclass
class Arbitrage:
    name: str
    steam_price: Optional[float]
    cheapest_buy: MarketQuote
    best_sell: MarketQuote
    spread_abs: float        # best_sell.net_sell - cheapest_buy.buy_price
    spread_pct: float        # spread_abs / cheapest_buy.buy_price * 100
    quotes: list[MarketQuote]


def market_quotes(name: str) -> list[MarketQuote]:
    row = (
        pl.scan_parquet(ITEMS_PARQUET)
          .filter(pl.col("name") == name)
          .select("keyfigures")
          .collect()
    )
    if not row.height:
        raise ValueError(f"Item not found: {name!r}")
    kf = row.row(0, named=True)["keyfigures"]
    if not kf:
        return []

    quotes: list[MarketQuote] = []
    for market, v in kf.items():
        if not isinstance(v, dict):
            continue
        price = v.get("current_price")
        if price is None or price <= 0:
            continue
        fee = SELL_FEES.get(market, _DEFAULT_FEE)
        quotes.append(MarketQuote(
            market=market,
            buy_price=float(price),
            volume=(float(v["current_volume"]) if v.get("current_volume") is not None else None),
            sell_fee=fee,
            net_sell=float(price) * (1 - fee),
        ))
    return quotes


def _drop_outliers(quotes: list[MarketQuote]) -> list[MarketQuote]:
    """Remove quotes far from the median buy price (likely mismatched/bad data)."""
    if len(quotes) < 3:
        return quotes
    med = statistics.median(q.buy_price for q in quotes)
    if med <= 0:
        return quotes
    kept = [q for q in quotes if _OUTLIER_LOW <= q.buy_price / med <= _OUTLIER_HIGH]
    return kept if len(kept) >= 2 else quotes


def arbitrage(name: str) -> Arbitrage:
    quotes = _drop_outliers(market_quotes(name))
    if not quotes:
        raise ValueError(f"No market quotes for {name!r}")

    cheapest = min(quotes, key=lambda q: q.buy_price)
    best_sell = max(quotes, key=lambda q: q.net_sell)
    steam = next((q.buy_price for q in quotes if q.market == "steam"), None)
    spread_abs = best_sell.net_sell - cheapest.buy_price

    return Arbitrage(
        name=name,
        steam_price=steam,
        cheapest_buy=cheapest,
        best_sell=best_sell,
        spread_abs=spread_abs,
        spread_pct=spread_abs / cheapest.buy_price * 100 if cheapest.buy_price else 0.0,
        quotes=sorted(quotes, key=lambda q: q.buy_price),
    )
