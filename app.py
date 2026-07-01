"""FastAPI backend for CS-SkinValue forecasting dashboard.

Endpoints
---------
GET  /api/health                 → service status
GET  /api/items                  → list of modelable items (name, tier, anchor_price)
GET  /api/items/search?q=...     → fuzzy substring search over names
GET  /api/forecast               → forecast a single item
POST /api/forecast/batch         → forecast many items at once

Static
------
GET  /            → serves the SvelteKit SPA from frontend/build/
GET  /favicon.ico → fallthrough to static dir
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from forecasting import forecast, list_available_models
from forecasting.inference import forecast_batch

import polars as pl
from ETL.config import WHITELIST_TIERS_PARQUET, ITEMS_PARQUET


# ---------------------------------------------------------------------------
# App + CORS (so dev frontend on :5173 can hit :8000)
# ---------------------------------------------------------------------------
app = FastAPI(
    title="CS-SkinValue API",
    description="Forecasting service for CS2 skin prices",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Cached lookups
# ---------------------------------------------------------------------------
_items_cache: Optional[pl.DataFrame] = None


def _load_items() -> pl.DataFrame:
    """Load the modelable items + current Steam price for the picker."""
    global _items_cache
    if _items_cache is not None:
        return _items_cache

    if not WHITELIST_TIERS_PARQUET.exists():
        # Fall back to items.parquet if the whitelist isn't built
        df = (
            pl.scan_parquet(ITEMS_PARQUET)
              .select([
                  "name", "weapon", "type",
                  pl.col("keyfigures").struct.field("steam").struct.field("current_price").alias("price"),
              ])
              .filter(pl.col("price").is_not_null() & (pl.col("price") >= 5))
              .with_columns(pl.lit(None, dtype=pl.Int32).alias("tier"))
              .collect()
        )
    else:
        df = (
            pl.read_parquet(WHITELIST_TIERS_PARQUET)
              .filter(pl.col("tier").is_not_null())
              .select([
                  "name", "weapon", "type", "tier",
                  pl.col("steam_price").alias("price"),
                  "dollar_volume_30d",
              ])
              .sort(["tier", "dollar_volume_30d"], descending=[False, True])
        )
    _items_cache = df
    return df


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class Health(BaseModel):
    status: str
    models: list[str]
    items_loaded: int


class Item(BaseModel):
    name: str
    weapon: Optional[str] = None
    type: Optional[str] = None
    tier: Optional[int] = None
    price: Optional[float] = None


class ForecastPoint(BaseModel):
    date: str
    point: float
    lower: float
    upper: float


class ForecastResponse(BaseModel):
    name: str
    model: str
    horizon: int
    anchor_price: float
    anchor_date: str
    points: list[ForecastPoint]
    change_pct_7d: Optional[float] = None
    direction: str


class BatchRequest(BaseModel):
    items: list[str] = Field(..., min_length=1, max_length=50)
    horizon: int = Field(7, ge=1, le=30)
    model: str = "chronos"


class BatchItemResult(BaseModel):
    name: str
    anchor_price: Optional[float] = None
    forecast_end: Optional[float] = None
    lower_end: Optional[float] = None
    upper_end: Optional[float] = None
    change_pct: Optional[float] = None
    direction: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health", response_model=Health)
def health():
    items = _load_items()
    return Health(
        status="ok",
        models=list_available_models(),
        items_loaded=items.height,
    )


@app.get("/api/items", response_model=list[Item])
def list_items(
    tier: Optional[int] = Query(None, ge=1, le=3),
    limit: int = Query(500, ge=1, le=5000),
):
    df = _load_items()
    if tier is not None:
        df = df.filter(pl.col("tier") <= tier)
    df = df.head(limit)
    return [Item(**r) for r in df.to_dicts()]


@app.get("/api/items/search", response_model=list[Item])
def search_items(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    df = _load_items()
    matches = df.filter(pl.col("name").str.contains(q, literal=False, strict=False))
    return [Item(**r) for r in matches.head(limit).to_dicts()]


def _direction(anchor: float, point: float) -> str:
    if point > anchor * 1.02:
        return "UP"
    if point < anchor * 0.98:
        return "DOWN"
    return "FLAT"


@app.get("/api/forecast", response_model=ForecastResponse)
def get_forecast(
    name: str = Query(..., min_length=1),
    horizon: int = Query(7, ge=1, le=30),
    model: str = Query("chronos"),
):
    if model not in list_available_models():
        raise HTTPException(400, f"unknown model {model!r}")
    try:
        f = forecast(name, horizon=horizon, model=model)
    except ValueError as e:
        raise HTTPException(404, str(e))

    final_point = float(f.point[-1])
    change_pct = (final_point - f.context_price) / f.context_price * 100

    return ForecastResponse(
        name=f.name,
        model=f.model_used,
        horizon=f.horizon,
        anchor_price=f.context_price,
        anchor_date=f.context_last_date.isoformat(),
        points=[
            ForecastPoint(
                date=d.isoformat(),
                point=float(p),
                lower=float(lo),
                upper=float(hi),
            )
            for d, p, lo, hi in zip(f.dates, f.point, f.lower, f.upper)
        ],
        change_pct_7d=change_pct,
        direction=_direction(f.context_price, final_point),
    )


@app.post("/api/forecast/batch", response_model=list[BatchItemResult])
def batch_forecast(req: BatchRequest):
    if req.model not in list_available_models():
        raise HTTPException(400, f"unknown model {req.model!r}")

    results = []
    for name in req.items:
        try:
            f = forecast(name, horizon=req.horizon, model=req.model)
            final = float(f.point[-1])
            change = (final - f.context_price) / f.context_price * 100
            results.append(BatchItemResult(
                name=name,
                anchor_price=f.context_price,
                forecast_end=final,
                lower_end=float(f.lower[-1]),
                upper_end=float(f.upper[-1]),
                change_pct=change,
                direction=_direction(f.context_price, final),
            ))
        except Exception as e:
            results.append(BatchItemResult(name=name, error=str(e)))
    return results


# ---------------------------------------------------------------------------
# Static frontend (SvelteKit build output at frontend/build/)
# ---------------------------------------------------------------------------
FRONTEND_BUILD = Path(__file__).parent / "frontend" / "build"
if FRONTEND_BUILD.exists():
    app.mount("/_app", StaticFiles(directory=FRONTEND_BUILD / "_app"), name="app")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # API requests should never hit this — FastAPI matches /api/* first
        candidate = FRONTEND_BUILD / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_BUILD / "index.html")
else:
    @app.get("/")
    def root_dev():
        return {
            "status": "API up — frontend not built yet",
            "hint": "cd frontend && npm install && npm run build",
            "docs": "/docs",
        }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
