from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from forecasting_core import (
    PRODUCT_COL,
    TARGET_COL,
    DATE_COL,
    MODEL_INFO,
    build_daily_series,
    forecast_next_days,
    load_sales_data,
    parse_date_series,
    train_forecast_model,
)

CSV_PATH = Path("retail_store_inventory.csv")
UI_PATH = Path("sales_forecast_ui.html")
RESULTS_UI_PATH = Path("forecast_results.html")
DEFAULT_HORIZON = 7

app = FastAPI(title="Supply Chain Forecasting Engine", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
_engine_lock = threading.Lock()


@dataclass
class _EngineCache:
    csv_mtime_ns: int = -1
    df: Optional[pd.DataFrame] = None
    trained_by_category: Dict[str, Any] = field(default_factory=dict)


_engine_cache = _EngineCache()


class SalesRecord(BaseModel):
    date: str
    category: str
    units_sold: float

    class Config:
        extra = "allow"


class SalesIngestRequest(BaseModel):
    records: List[SalesRecord]
    horizon: int = DEFAULT_HORIZON
    persist: bool = True


def _norm_text(value: Any) -> str:
    return str(value).strip()


def _norm_key(value: Any) -> str:
    return _norm_text(value).lower()


def _resolve_category(df: pd.DataFrame, category: str) -> str:
    # Resolve user input to dataset category with case-insensitive matching.
    requested = _norm_key(category)
    if not requested:
        raise ValueError("category cannot be empty")

    values = df[PRODUCT_COL].dropna().astype(str).map(str.strip)
    if values.empty:
        raise ValueError("No category data available")

    exact = values[values == category]
    if not exact.empty:
        return str(exact.iloc[0])

    lower_map: Dict[str, str] = {}
    for v in values:
        k = v.lower()
        if k not in lower_map:
            lower_map[k] = v

    if requested in lower_map:
        return lower_map[requested]
    raise ValueError(f"No rows found for category {category}")


def _read_csv_mtime_ns() -> int:
    try:
        return CSV_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        return -1


def _invalidate_engine_cache() -> None:
    _engine_cache.csv_mtime_ns = -1
    _engine_cache.df = None
    _engine_cache.trained_by_category.clear()


def _get_cached_df() -> pd.DataFrame:
    mtime_ns = _read_csv_mtime_ns()
    if _engine_cache.df is None or _engine_cache.csv_mtime_ns != mtime_ns:
        _engine_cache.df = load_sales_data(CSV_PATH)
        _engine_cache.csv_mtime_ns = mtime_ns
        _engine_cache.trained_by_category.clear()
    return _engine_cache.df


def _get_trained_model(df: pd.DataFrame, resolved_category: str) -> Any:
    key = _norm_key(resolved_category)
    if key in _engine_cache.trained_by_category:
        return _engine_cache.trained_by_category[key]

    daily = build_daily_series(df, category=resolved_category)
    trained = train_forecast_model(daily, target=TARGET_COL)
    _engine_cache.trained_by_category[key] = trained
    return trained


def _record_to_dict(record: SalesRecord) -> Dict[str, Any]:
    if hasattr(record, "model_dump"):
        return record.model_dump()  # pydantic v2
    return record.dict()  # pydantic v1


def _normalize_payload_record(r: SalesRecord) -> Dict[str, Any]:
    payload = _record_to_dict(r)
    out: Dict[str, Any] = {}

    for key, value in payload.items():
        normalized = key.replace("_", " ").strip().lower()
        out[normalized] = value

    extras = getattr(r, "__pydantic_extra__", None) or {}
    for key, value in extras.items():
        out[key.strip().lower()] = value

    return out


def _append_sales_rows(records: List[SalesRecord], persist: bool = True) -> pd.DataFrame:
    existing = pd.read_csv(CSV_PATH)
    existing.columns = existing.columns.str.strip().str.lower()

    new_rows = pd.DataFrame([_normalize_payload_record(r) for r in records])
    if new_rows.empty:
        return existing

    if DATE_COL not in new_rows.columns or PRODUCT_COL not in new_rows.columns or TARGET_COL not in new_rows.columns:
        raise HTTPException(status_code=400, detail="Each record requires date, category, units_sold")

    if PRODUCT_COL in existing.columns and PRODUCT_COL in new_rows.columns:
        existing_values = existing[PRODUCT_COL].dropna().astype(str).map(str.strip)
        canonical_map: Dict[str, str] = {}
        for v in existing_values:
            lk = v.lower()
            if lk not in canonical_map:
                canonical_map[lk] = v
        new_rows[PRODUCT_COL] = new_rows[PRODUCT_COL].astype(str).map(str.strip).map(
            lambda c: canonical_map.get(c.lower(), c)
        )

    for col in existing.columns:
        if col not in new_rows.columns:
            new_rows[col] = pd.NA

    for col in new_rows.columns:
        if col not in existing.columns:
            existing[col] = pd.NA

    combined = pd.concat([existing, new_rows[existing.columns]], ignore_index=True)
    if persist:
        combined.to_csv(CSV_PATH, index=False)
    return combined


def _forecast_for_category(
    df: pd.DataFrame,
    category: str,
    horizon: int,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    resolved_category = _resolve_category(df, category)
    trained = _get_trained_model(df, resolved_category)
    forecast_df = forecast_next_days(trained, horizon=horizon, anchor_date=anchor_date)
    return forecast_df.to_dict(orient="records")


def _history_for_category(
    df: pd.DataFrame,
    category: str,
    lookback_days: int = 60,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    resolved_category = _resolve_category(df, category)
    category_rows = df[df[PRODUCT_COL].astype(str).str.strip() == resolved_category].copy()
    if category_rows.empty:
        return []

    category_rows[DATE_COL] = parse_date_series(category_rows[DATE_COL])
    category_rows = category_rows.dropna(subset=[DATE_COL, TARGET_COL])

    daily = (
        category_rows.groupby(DATE_COL, as_index=False)[TARGET_COL]
        .sum()
        .sort_values(DATE_COL)
    )
    if anchor_date is not None:
        anchor_date = pd.to_datetime(anchor_date).normalize()
        daily = daily[daily[DATE_COL] <= anchor_date]

    if lookback_days > 0:
        daily = daily.tail(lookback_days)

    return [
        {
            "date": d.date().isoformat(),
            "actual_units_sold": round(float(v), 3),
        }
        for d, v in zip(daily[DATE_COL], daily[TARGET_COL])
    ]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if UI_PATH.exists():
        return UI_PATH.read_text(encoding="utf-8")
    return "<h1>UI file not found</h1><p>Create sales_forecast_ui.html in the project root.</p>"


@app.get("/results", response_class=HTMLResponse)
def results_page() -> str:
    if RESULTS_UI_PATH.exists():
        return RESULTS_UI_PATH.read_text(encoding="utf-8")
    # Graceful fallback: keep navigation working even if details page was removed.
    if UI_PATH.exists():
        return UI_PATH.read_text(encoding="utf-8")
    return "<h1>UI file not found</h1><p>Create sales_forecast_ui.html in the project root.</p>"


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "model_info": MODEL_INFO}


@app.get("/categories")
def get_categories() -> Dict[str, Any]:
    with _engine_lock:
        df = _get_cached_df()
        values = (
            df[PRODUCT_COL]
            .dropna()
            .astype(str)
            .map(str.strip)
        )
        categories = sorted({v for v in values if v})
    return {"categories": categories}


@app.get("/forecast/{category}")
def get_forecast(
    category: str,
    horizon: int = DEFAULT_HORIZON,
    anchor_date: Optional[str] = None,
    history_lookback_days: int = 365,
) -> Dict[str, Any]:
    if horizon <= 0:
        raise HTTPException(status_code=400, detail="horizon must be positive")
    if history_lookback_days <= 0:
        raise HTTPException(status_code=400, detail="history_lookback_days must be positive")

    parsed_anchor: Optional[pd.Timestamp] = None
    if anchor_date:
        parsed_anchor = pd.to_datetime(anchor_date, errors="coerce")
        if pd.isna(parsed_anchor):
            raise HTTPException(status_code=400, detail="anchor_date must be a valid date")
        parsed_anchor = parsed_anchor.normalize()

    with _engine_lock:
        df = _get_cached_df()
        try:
            rows = _forecast_for_category(
                df,
                category=category,
                horizon=horizon,
                anchor_date=parsed_anchor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        history = _history_for_category(
            df,
            category=category,
            lookback_days=history_lookback_days,
            anchor_date=parsed_anchor,
        )

    return {
        "category": category,
        "horizon": horizon,
        "history_lookback_days": history_lookback_days,
        "anchor_date": anchor_date,
        "model_info": MODEL_INFO,
        "history": history,
        "forecast": rows,
    }


@app.post("/sales")
def ingest_sales(request: SalesIngestRequest) -> Dict[str, Any]:
    if not request.records:
        raise HTTPException(status_code=400, detail="records cannot be empty")
    if request.horizon <= 0:
        raise HTTPException(status_code=400, detail="horizon must be positive")

    with _engine_lock:
        combined = _append_sales_rows(request.records, persist=request.persist)
        if request.persist:
            _invalidate_engine_cache()
        combined = combined.copy()
        combined.columns = combined.columns.str.strip().str.lower()
        combined[DATE_COL] = parse_date_series(combined[DATE_COL])
        combined = combined.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET_COL])

        requested_categories = sorted({_norm_text(r.category) for r in request.records})
        updated_categories = []
        for c in requested_categories:
            try:
                updated_categories.append(_resolve_category(combined, c))
            except ValueError:
                updated_categories.append(c)
        updated_categories = sorted(set(updated_categories))
        anchors: Dict[str, pd.Timestamp] = {}
        for category in updated_categories:
            category_dates = [r.date for r in request.records if _norm_key(r.category) == _norm_key(category)]
            parsed_dates = parse_date_series(pd.Series(category_dates))
            parsed_dates = parsed_dates.dropna()
            if not parsed_dates.empty:
                anchors[category] = parsed_dates.max().normalize()

        forecasts: Dict[str, Any] = {}
        histories: Dict[str, Any] = {}

        for category in updated_categories:
            try:
                forecasts[category] = _forecast_for_category(
                    combined,
                    category=category,
                    horizon=request.horizon,
                    anchor_date=anchors.get(category),
                )
            except ValueError:
                forecasts[category] = []
            histories[category] = _history_for_category(
                combined,
                category=category,
                anchor_date=anchors.get(category),
            )

    return {
        "message": "Sales data appended and forecasts refreshed",
        "model_info": MODEL_INFO,
        "updated_categories": updated_categories,
        "horizon": request.horizon,
        "forecasts": forecasts,
        "histories": histories,
    }


# Run: uvicorn forecast_api:app --reload --host 0.0.0.0 --port 8000
