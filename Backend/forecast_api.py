import threading
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    from pydantic import ConfigDict  # pydantic v2
except ImportError:  # pydantic v1
    ConfigDict = None

try:
    from .forecasting_core import (
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
except ImportError:  # Allows running as a script from Backend/
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

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
CSV_PATH = PROJECT_DIR / "Dataset" / "retail_store_inventory.csv"
FRONTEND_LOGIN_HTML_PATH = PROJECT_DIR / "Frontend" / "login.html"
FRONTEND_HTML_PATH = PROJECT_DIR / "Frontend" / "dashboard.html"
FRONTEND_RESULTS_HTML_PATH = PROJECT_DIR / "Frontend" / "dashboard_results.html"
HISTORY_DB_PATH = PROJECT_DIR / "Backend" / "forecast_history.db"
DEFAULT_HORIZON = 7

app = FastAPI(title="Supply Chain Forecasting Engine", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=PROJECT_DIR / "Frontend" / "assets"), name="assets")
_engine_lock = threading.Lock()


@dataclass
class _EngineCache:
    csv_mtime_ns: int = -1
    df: Optional[pd.DataFrame] = None
    trained_by_category: Dict[str, Any] = field(default_factory=dict)


_engine_cache = _EngineCache()


def _get_history_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_history_db() -> None:
    with _get_history_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forecast_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                category TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                anchor_date TEXT,
                horizon INTEGER NOT NULL,
                history_lookback_days INTEGER NOT NULL,
                history_points INTEGER NOT NULL,
                forecast_points INTEGER NOT NULL,
                latest_actual REAL,
                total_forecast REAL,
                average_forecast REAL
            )
            """
        )
        existing_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(forecast_history)").fetchall()
        }
        if "start_date" not in existing_cols:
            conn.execute("ALTER TABLE forecast_history ADD COLUMN start_date TEXT")
        if "end_date" not in existing_cols:
            conn.execute("ALTER TABLE forecast_history ADD COLUMN end_date TEXT")
        conn.commit()


def _save_forecast_history(
    *,
    category: str,
    start_date: Optional[str],
    end_date: Optional[str],
    anchor_date: Optional[str],
    horizon: int,
    history_lookback_days: int,
    history: List[Dict[str, Any]],
    forecast: List[Dict[str, Any]],
) -> None:
    latest_actual: Optional[float] = None
    if history:
        try:
            latest_actual = float(history[-1].get("actual_units_sold"))
        except (TypeError, ValueError):
            latest_actual = None

    forecast_vals: List[float] = []
    for row in forecast:
        try:
            forecast_vals.append(float(row.get("forecast_units_sold")))
        except (TypeError, ValueError):
            continue

    total_forecast = float(sum(forecast_vals)) if forecast_vals else 0.0
    avg_forecast = (total_forecast / len(forecast_vals)) if forecast_vals else 0.0

    with _get_history_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO forecast_history (
                category,
                start_date,
                end_date,
                anchor_date,
                horizon,
                history_lookback_days,
                history_points,
                forecast_points,
                latest_actual,
                total_forecast,
                average_forecast
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category,
                start_date,
                end_date,
                anchor_date,
                int(horizon),
                int(history_lookback_days),
                int(len(history)),
                int(len(forecast)),
                latest_actual,
                total_forecast,
                avg_forecast,
            ),
        )
        conn.commit()


def _load_recent_history(category: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(limit, 100))
    query = """
        SELECT
            id,
            created_at,
            category,
            start_date,
            end_date,
            anchor_date,
            horizon,
            history_lookback_days,
            history_points,
            forecast_points,
            latest_actual,
            total_forecast,
            average_forecast
        FROM forecast_history
    """
    params: List[Any] = []
    if category:
        query += " WHERE lower(category) = lower(?) "
        params.append(category)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(safe_limit)

    with _get_history_db_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


_init_history_db()


class SalesRecord(BaseModel):
    date: str
    units_sold: float
    category: Optional[str] = None
    product_id: Optional[str] = None

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    else:
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


def _resolve_selector(df: pd.DataFrame, category: str) -> tuple[str, str]:
    # Resolve user input against product/category columns with case-insensitive matching.
    requested = _norm_key(category)
    if not requested:
        raise ValueError("category cannot be empty")

    candidate_cols: List[str] = []
    if "product id" in df.columns:
        candidate_cols.append("product id")
    if PRODUCT_COL in df.columns and PRODUCT_COL not in candidate_cols:
        candidate_cols.append(PRODUCT_COL)

    for col in candidate_cols:
        values = df[col].dropna().astype(str).map(str.strip)
        if values.empty:
            continue

        exact = values[values == category]
        if not exact.empty:
            return col, str(exact.iloc[0])

        lower_map: Dict[str, str] = {}
        for v in values:
            k = v.lower()
            if k not in lower_map:
                lower_map[k] = v
        if requested in lower_map:
            return col, lower_map[requested]

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


def _get_trained_model(df: pd.DataFrame, resolved_category: str, selector_col: str) -> Any:
    key = f"{selector_col}|{_norm_key(resolved_category)}"
    if key in _engine_cache.trained_by_category:
        return _engine_cache.trained_by_category[key]

    daily = build_daily_series(df, category=resolved_category, selector_col=selector_col)
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

    # Accept product-id style payloads by mapping them to API's category field.
    if PRODUCT_COL not in out:
        if "product id" in out and _norm_text(out["product id"]):
            out[PRODUCT_COL] = out["product id"]
        elif "product_id" in out and _norm_text(out["product_id"]):
            out[PRODUCT_COL] = out["product_id"]

    if PRODUCT_COL in out and not _norm_text(out[PRODUCT_COL]):
        out.pop(PRODUCT_COL, None)

    return out


def _record_identifier(record: SalesRecord) -> str:
    if record.category is not None and _norm_text(record.category):
        return _norm_text(record.category)
    if getattr(record, "product_id", None) is not None and _norm_text(record.product_id):
        return _norm_text(record.product_id)
    return ""


def _append_sales_rows(records: List[SalesRecord], persist: bool = True) -> pd.DataFrame:
    existing = pd.read_csv(CSV_PATH)
    existing.columns = existing.columns.str.strip().str.lower()

    new_rows = pd.DataFrame([_normalize_payload_record(r) for r in records])
    if new_rows.empty:
        return existing

    if DATE_COL not in new_rows.columns or PRODUCT_COL not in new_rows.columns or TARGET_COL not in new_rows.columns:
        raise HTTPException(
            status_code=400,
            detail="Each record requires date, units_sold, and category (or product_id/product id).",
        )

    if PRODUCT_COL in existing.columns and PRODUCT_COL in new_rows.columns:
        existing_values = existing[PRODUCT_COL].dropna().astype(str).map(str.strip)
        canonical_map: Dict[str, str] = {}
        for v in existing_values:
            lk = v.lower()
            if lk not in canonical_map:
                canonical_map[lk] = v
        normalized = new_rows[PRODUCT_COL].astype("string").str.strip()
        new_rows[PRODUCT_COL] = normalized.map(
            lambda c: canonical_map.get(c.lower(), c) if isinstance(c, str) and c else c
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
    selector_col, resolved_category = _resolve_selector(df, category)
    trained = _get_trained_model(df, resolved_category, selector_col=selector_col)
    forecast_df = forecast_next_days(trained, horizon=horizon, anchor_date=anchor_date)
    return forecast_df.to_dict(orient="records")


def _history_for_category(
    df: pd.DataFrame,
    category: str,
    lookback_days: int = 60,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    selector_col, resolved_category = _resolve_selector(df, category)
    category_rows = df[df[selector_col].astype(str).str.strip() == resolved_category].copy()
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


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=307)


@app.get("/login", include_in_schema=False)
def login() -> FileResponse:
    if not FRONTEND_LOGIN_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Login file not found at {FRONTEND_LOGIN_HTML_PATH}",
        )
    return FileResponse(FRONTEND_LOGIN_HTML_PATH)


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    if not FRONTEND_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dashboard file not found at {FRONTEND_HTML_PATH}",
        )
    return FileResponse(FRONTEND_HTML_PATH)


@app.get("/dashboard/results", include_in_schema=False)
def dashboard_results() -> FileResponse:
    if not FRONTEND_RESULTS_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dashboard results file not found at {FRONTEND_RESULTS_HTML_PATH}",
        )
    return FileResponse(FRONTEND_RESULTS_HTML_PATH)


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
        product_ids: List[str] = []
        if "product id" in df.columns:
            pvalues = df["product id"].dropna().astype(str).map(str.strip)
            product_ids = sorted({v for v in pvalues if v})
    return {"categories": categories, "product_ids": product_ids}


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
        _save_forecast_history(
            category=category,
            start_date=rows[0]["date"] if rows else None,
            end_date=rows[-1]["date"] if rows else anchor_date,
            anchor_date=anchor_date,
            horizon=horizon,
            history_lookback_days=history_lookback_days,
            history=history,
            forecast=rows,
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


@app.get("/forecast-history")
def get_forecast_history(
    category: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    items = _load_recent_history(category=category, limit=limit)
    return {
        "count": len(items),
        "items": items,
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

        requested_categories = sorted({cid for cid in (_record_identifier(r) for r in request.records) if cid})
        if not requested_categories:
            raise HTTPException(
                status_code=400,
                detail="records must include category or product_id/product id.",
            )
        updated_categories = []
        for c in requested_categories:
            try:
                _, resolved = _resolve_selector(combined, c)
                updated_categories.append(resolved)
            except ValueError:
                updated_categories.append(c)
        updated_categories = sorted(set(updated_categories))
        anchors: Dict[str, pd.Timestamp] = {}
        for category in updated_categories:
            category_dates = [
                r.date
                for r in request.records
                if _norm_key(_record_identifier(r)) == _norm_key(category)
            ]
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
