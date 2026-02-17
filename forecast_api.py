from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
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
DEFAULT_HORIZON = 7

app = FastAPI(title="Supply Chain Forecasting Engine", version="1.1.0")
_engine_lock = threading.Lock()


class SalesRecord(BaseModel):
    date: str
    product_id: str
    units_sold: float

    class Config:
        extra = "allow"


class SalesIngestRequest(BaseModel):
    records: List[SalesRecord]
    horizon: int = DEFAULT_HORIZON


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


def _append_sales_rows(records: List[SalesRecord]) -> pd.DataFrame:
    existing = pd.read_csv(CSV_PATH)
    existing.columns = existing.columns.str.strip().str.lower()

    new_rows = pd.DataFrame([_normalize_payload_record(r) for r in records])
    if new_rows.empty:
        return existing

    if DATE_COL not in new_rows.columns or PRODUCT_COL not in new_rows.columns or TARGET_COL not in new_rows.columns:
        raise HTTPException(status_code=400, detail="Each record requires date, product_id, units_sold")

    for col in existing.columns:
        if col not in new_rows.columns:
            new_rows[col] = pd.NA

    for col in new_rows.columns:
        if col not in existing.columns:
            existing[col] = pd.NA

    combined = pd.concat([existing, new_rows[existing.columns]], ignore_index=True)
    combined.to_csv(CSV_PATH, index=False)
    return combined


def _forecast_for_product(
    df: pd.DataFrame,
    product_id: str,
    horizon: int,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    daily = build_daily_series(df, product_id=product_id)
    trained = train_forecast_model(daily, target=TARGET_COL)
    forecast_df = forecast_next_days(trained, horizon=horizon, anchor_date=anchor_date)
    return forecast_df.to_dict(orient="records")


def _history_for_product(
    df: pd.DataFrame,
    product_id: str,
    lookback_days: int = 60,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    product_rows = df[df[PRODUCT_COL].astype(str) == str(product_id)].copy()
    if product_rows.empty:
        return []

    product_rows[DATE_COL] = parse_date_series(product_rows[DATE_COL])
    product_rows = product_rows.dropna(subset=[DATE_COL, TARGET_COL])

    daily = (
        product_rows.groupby(DATE_COL, as_index=False)[TARGET_COL]
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


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "model_info": MODEL_INFO}


@app.get("/forecast/{product_id}")
def get_forecast(product_id: str, horizon: int = DEFAULT_HORIZON) -> Dict[str, Any]:
    if horizon <= 0:
        raise HTTPException(status_code=400, detail="horizon must be positive")

    with _engine_lock:
        df = load_sales_data(CSV_PATH)
        try:
            rows = _forecast_for_product(df, product_id=product_id, horizon=horizon)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        history = _history_for_product(df, product_id=product_id)

    return {
        "product_id": product_id,
        "horizon": horizon,
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
        combined = _append_sales_rows(request.records)
        combined = combined.copy()
        combined.columns = combined.columns.str.strip().str.lower()
        combined[DATE_COL] = parse_date_series(combined[DATE_COL])
        combined = combined.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET_COL])

        updated_products = sorted({str(r.product_id) for r in request.records})
        anchors: Dict[str, pd.Timestamp] = {}
        for pid in updated_products:
            pid_dates = [r.date for r in request.records if str(r.product_id) == pid]
            parsed_dates = parse_date_series(pd.Series(pid_dates))
            parsed_dates = parsed_dates.dropna()
            if not parsed_dates.empty:
                anchors[pid] = parsed_dates.max().normalize()

        forecasts: Dict[str, Any] = {}
        histories: Dict[str, Any] = {}

        for pid in updated_products:
            try:
                forecasts[pid] = _forecast_for_product(
                    combined,
                    product_id=pid,
                    horizon=request.horizon,
                    anchor_date=anchors.get(pid),
                )
            except ValueError:
                forecasts[pid] = []
            histories[pid] = _history_for_product(
                combined,
                product_id=pid,
                anchor_date=anchors.get(pid),
            )

    return {
        "message": "Sales data appended and forecasts refreshed",
        "model_info": MODEL_INFO,
        "updated_products": updated_products,
        "horizon": request.horizon,
        "forecasts": forecasts,
        "histories": histories,
    }


# Run: uvicorn forecast_api:app --reload --host 0.0.0.0 --port 8000
