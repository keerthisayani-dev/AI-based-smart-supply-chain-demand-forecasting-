import threading
import sqlite3
import hashlib
import secrets
import io
import os
import json
import shutil
import base64
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
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
FRONTEND_ADMIN_HTML_PATH = PROJECT_DIR / "Frontend" / "admin_dashboard.html"
FRONTEND_ABOUT_HTML_PATH = PROJECT_DIR / "Frontend" / "about_project.html"
HISTORY_DB_PATH = PROJECT_DIR / "Backend" / "forecast_history.db"
AUTH_DB_PATH = PROJECT_DIR / "Backend" / "auth.db"
UPLOADS_ROOT = PROJECT_DIR / "uploads"
UPLOADS_SALES_DIR = UPLOADS_ROOT / "sales"
UPLOADS_INVENTORY_DIR = UPLOADS_ROOT / "inventory"
UPLOADS_ARCHIVE_DIR = UPLOADS_ROOT / "archive"
UPLOADS_STAGING_DIR = UPLOADS_ROOT / "staging"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xlsx"}
DEFAULT_HORIZON = 7
DEFAULT_SESSION_HOURS = 12
REMEMBER_ME_SESSION_DAYS = 14
SESSION_COOKIE_NAME = "session_token"
EMAIL_VERIFY_TTL_SECONDS = 24 * 3600
PASSWORD_RESET_TTL_SECONDS = 30 * 60
PASSWORD_POLICY_REGEX = re.compile(r"^(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$")
PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and include 1 uppercase letter, 1 number, and 1 special character"
)
ROLE_ADMIN = "admin"
ROLE_INVENTORY_MANAGER = "inventory_manager"
ROLE_VIEWER = "viewer"

ROLE_ALIASES: Dict[str, str] = {
    "admin": ROLE_ADMIN,
    "inventory_manager": ROLE_INVENTORY_MANAGER,
    "viewer": ROLE_VIEWER,
    # Legacy role aliases from earlier versions.
    "manager": ROLE_ADMIN,
    "planner": ROLE_INVENTORY_MANAGER,
    "supply_chain_manager": ROLE_INVENTORY_MANAGER,
    "analyst": ROLE_VIEWER,
    "viewer_analyst": ROLE_VIEWER,
}

ALLOWED_LOGIN_ROLES = {
    ROLE_ADMIN,
    ROLE_INVENTORY_MANAGER,
    ROLE_VIEWER,
}

SELF_REGISTER_ALLOWED_ROLES = {
    ROLE_INVENTORY_MANAGER,
    ROLE_VIEWER,
}
UPLOAD_TYPE_MAIN_DATASET = "main_sales_dataset"
UPLOAD_TYPE_MONTHLY_UPDATE = "monthly_update"
UPLOAD_TYPE_INVENTORY_STOCK = "inventory_stock_file"
UPLOAD_TYPE_SANDBOX_TRAIN = "sandbox_training_dataset"
ALLOWED_UPLOAD_TYPES = {
    UPLOAD_TYPE_MAIN_DATASET,
    UPLOAD_TYPE_MONTHLY_UPDATE,
    UPLOAD_TYPE_INVENTORY_STOCK,
    UPLOAD_TYPE_SANDBOX_TRAIN,
}
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

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


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    preexisting = set(os.environ.keys())
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # If key came from system env before loading .env, keep it.
        # Otherwise, allow last definition in .env to win.
        if key and key not in preexisting:
            os.environ[key] = value


def _env_str(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env_str(name, "1" if default else "0").lower()
    return v in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str = "") -> List[str]:
    raw = _env_str(name, default)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _bootstrap_admin_accounts() -> List[Dict[str, str]]:
    default_email = "admin@demandiq.com"
    default_password = "DemandIQ@2026"
    default_name = "System Administrator"

    emails = [e.lower() for e in _env_csv("DEMANDIQ_ADMIN_EMAIL", default_email)]
    passwords = _env_csv("DEMANDIQ_ADMIN_PASSWORD", default_password)
    names = _env_csv("DEMANDIQ_ADMIN_FULL_NAME", default_name)

    if not emails:
        emails = [default_email]
    if not passwords:
        passwords = [default_password]
    if not names:
        names = [default_name]

    admins: List[Dict[str, str]] = []
    for idx, email in enumerate(emails):
        admins.append(
            {
                "email": email.lower(),
                "password": passwords[idx] if idx < len(passwords) else passwords[0],
                "full_name": names[idx] if idx < len(names) else names[0],
            }
        )
    return admins


def _bootstrap_admin_email_set() -> set[str]:
    return {item["email"] for item in _bootstrap_admin_accounts()}


_load_env_file(PROJECT_DIR / ".env")


def _get_history_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_auth_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _password_hash(password: str, salt_hex: Optional[str] = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def _is_strong_password(password: str) -> bool:
    return bool(PASSWORD_POLICY_REGEX.match(password or ""))


def _is_valid_signup_email(email: str) -> bool:
    candidate = (email or "").strip().lower()
    if "@" not in candidate:
        return False
    local_part, _, domain_part = candidate.partition("@")
    if not local_part or "." not in domain_part:
        return False
    # Prevent numeric-only usernames like 1234@gmail.com.
    if not any(ch.isalpha() for ch in local_part):
        return False
    return True


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    return _password_hash(password, salt_hex=salt_hex) == stored_hash


def _utc_epoch_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _canonical_role(role: str) -> str:
    return ROLE_ALIASES.get(str(role or "").strip().lower(), "")


def _normalize_full_name(value: str) -> str:
    # Normalize whitespace so name uniqueness checks are consistent.
    return " ".join(str(value or "").strip().split())


def _init_auth_db() -> None:
    with _get_auth_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                email_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at_epoch INTEGER NOT NULL,
                expires_at_epoch INTEGER NOT NULL,
                user_agent TEXT,
                ip_address TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_type TEXT NOT NULL,
                created_at_epoch INTEGER NOT NULL,
                expires_at_epoch INTEGER NOT NULL,
                used_at_epoch INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                event_type TEXT NOT NULL,
                actor_user_id INTEGER,
                actor_email TEXT,
                target_user_id INTEGER,
                target_email TEXT,
                details_json TEXT,
                ip_address TEXT,
                user_agent TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS upload_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                uploaded_by INTEGER,
                uploaded_by_email TEXT,
                upload_type TEXT NOT NULL,
                upload_date TEXT NOT NULL DEFAULT (datetime('now')),
                status TEXT NOT NULL,
                records_processed INTEGER NOT NULL DEFAULT 0,
                duplicates_removed INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                FOREIGN KEY(uploaded_by) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_stock (
                product_id TEXT NOT NULL,
                store_id TEXT NOT NULL,
                current_stock REAL NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(product_id, store_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                staging_path TEXT NOT NULL,
                uploaded_by INTEGER NOT NULL,
                uploaded_by_email TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
                status TEXT NOT NULL,
                total_rows INTEGER NOT NULL DEFAULT 0,
                missing_values INTEGER NOT NULL DEFAULT 0,
                duplicate_rows INTEGER NOT NULL DEFAULT 0,
                date_min TEXT,
                date_max TEXT,
                summary_json TEXT,
                decided_at TEXT,
                decision_note TEXT
            )
            """
        )

        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "email_verified" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")

        count_row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        user_count = int(count_row["c"]) if count_row else 0
        bootstrap_admins = _bootstrap_admin_accounts()
        if user_count == 0:
            seed_users = [(a["email"], a["full_name"], ROLE_ADMIN, a["password"]) for a in bootstrap_admins]
            if _env_bool("DEMANDIQ_SEED_DEFAULT_USERS", False):
                inv_email = _env_str("DEMANDIQ_INVENTORY_EMAIL", "inventory@demandiq.com").lower()
                inv_password = _env_str("DEMANDIQ_INVENTORY_PASSWORD", "DemandIQ@2026")
                viewer_email = _env_str("DEMANDIQ_VIEWER_EMAIL", "viewer@demandiq.com").lower()
                viewer_password = _env_str("DEMANDIQ_VIEWER_PASSWORD", "DemandIQ@2026")
                seed_users.extend(
                    [
                        (inv_email, "Inventory Manager", ROLE_INVENTORY_MANAGER, inv_password),
                        (viewer_email, "Viewer User", ROLE_VIEWER, viewer_password),
                    ]
                )
            for email, full_name, role, plain_password in seed_users:
                conn.execute(
                    """
                    INSERT INTO users (email, full_name, role, password_hash, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (email, full_name, role, _password_hash(plain_password)),
                )
        else:
            rows = conn.execute("SELECT id, role FROM users").fetchall()
            for row in rows:
                role = str(row["role"])
                canonical = _canonical_role(role)
                if canonical and canonical != role:
                    conn.execute("UPDATE users SET role = ? WHERE id = ?", (canonical, int(row["id"])))

        # Keep bootstrap admin accounts in sync with .env so login works without DB reset.
        for admin in bootstrap_admins:
            existing_admin = conn.execute(
                "SELECT id FROM users WHERE lower(email) = ?",
                (admin["email"],),
            ).fetchone()
            if existing_admin is None:
                conn.execute(
                    """
                    INSERT INTO users (email, full_name, role, password_hash, is_active, email_verified)
                    VALUES (?, ?, ?, ?, 1, 1)
                    """,
                    (
                        admin["email"],
                        admin["full_name"],
                        ROLE_ADMIN,
                        _password_hash(admin["password"]),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET full_name = ?, role = ?, password_hash = ?, is_active = 1, email_verified = 1
                    WHERE id = ?
                    """,
                    (
                        admin["full_name"],
                        ROLE_ADMIN,
                        _password_hash(admin["password"]),
                        int(existing_admin["id"]),
                    ),
                )

        conn.commit()


def _create_auth_token(*, user_id: int, token_type: str, ttl_seconds: int) -> str:
    token = secrets.token_urlsafe(40)
    now_epoch = _utc_epoch_now()
    expires_epoch = now_epoch + max(1, int(ttl_seconds))
    with _get_auth_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO auth_tokens (
                token, user_id, token_type, created_at_epoch, expires_at_epoch, used_at_epoch
            ) VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (token, int(user_id), token_type, now_epoch, expires_epoch),
        )
        conn.execute("DELETE FROM auth_tokens WHERE expires_at_epoch <= ?", (now_epoch,))
        conn.commit()
    return token


def _consume_auth_token(*, token: str, token_type: str) -> Optional[int]:
    now_epoch = _utc_epoch_now()
    with _get_auth_db_conn() as conn:
        row = conn.execute(
            """
            SELECT token, user_id, expires_at_epoch, used_at_epoch
            FROM auth_tokens
            WHERE token = ? AND token_type = ?
            """,
            (token, token_type),
        ).fetchone()
        if row is None:
            return None
        if row["used_at_epoch"] is not None or int(row["expires_at_epoch"]) <= now_epoch:
            return None
        conn.execute(
            "UPDATE auth_tokens SET used_at_epoch = ? WHERE token = ?",
            (now_epoch, token),
        )
        conn.commit()
        return int(row["user_id"])


def _create_session(
    *,
    user_id: int,
    user_agent: Optional[str],
    ip_address: Optional[str],
    remember_me: bool = False,
) -> tuple[str, int]:
    session_id = secrets.token_urlsafe(48)
    now_epoch = _utc_epoch_now()
    ttl_seconds = REMEMBER_ME_SESSION_DAYS * 24 * 3600 if remember_me else DEFAULT_SESSION_HOURS * 3600
    expires_epoch = now_epoch + ttl_seconds

    with _get_auth_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions (
                session_id,
                user_id,
                created_at_epoch,
                expires_at_epoch,
                user_agent,
                ip_address
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, now_epoch, expires_epoch, user_agent, ip_address),
        )
        conn.execute("DELETE FROM auth_sessions WHERE expires_at_epoch <= ?", (now_epoch,))
        conn.commit()

    return session_id, ttl_seconds


def _delete_session(session_id: str) -> None:
    with _get_auth_db_conn() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE session_id = ?", (session_id,))
        conn.commit()


def _safe_json_compact(data: Dict[str, Any]) -> str:
    try:
        return json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        return "{}"


def _log_audit_event(
    *,
    event_type: str,
    actor_user_id: Optional[int] = None,
    actor_email: Optional[str] = None,
    target_user_id: Optional[int] = None,
    target_email: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    with _get_auth_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO auth_audit_logs (
                event_type,
                actor_user_id,
                actor_email,
                target_user_id,
                target_email,
                details_json,
                ip_address,
                user_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event_type).strip(),
                int(actor_user_id) if actor_user_id is not None else None,
                (actor_email or "").strip().lower() or None,
                int(target_user_id) if target_user_id is not None else None,
                (target_email or "").strip().lower() or None,
                _safe_json_compact(details or {}),
                (ip_address or "").strip() or None,
                (user_agent or "").strip() or None,
            ),
        )
        conn.commit()


def _canonical_upload_type(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "main": UPLOAD_TYPE_MAIN_DATASET,
        "main_dataset": UPLOAD_TYPE_MAIN_DATASET,
        "main_sales_dataset": UPLOAD_TYPE_MAIN_DATASET,
        "monthly": UPLOAD_TYPE_MONTHLY_UPDATE,
        "monthly_update": UPLOAD_TYPE_MONTHLY_UPDATE,
        "inventory": UPLOAD_TYPE_INVENTORY_STOCK,
        "inventory_stock": UPLOAD_TYPE_INVENTORY_STOCK,
        "inventory_stock_file": UPLOAD_TYPE_INVENTORY_STOCK,
        "sandbox": UPLOAD_TYPE_SANDBOX_TRAIN,
        "sandbox_training": UPLOAD_TYPE_SANDBOX_TRAIN,
        "sandbox_training_dataset": UPLOAD_TYPE_SANDBOX_TRAIN,
    }
    return aliases.get(key, "")


def _col_key(value: str) -> str:
    # Normalize headers so matching is resilient to spaces/underscores/hyphens/special chars.
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _rename_with_aliases(df: pd.DataFrame, alias_map: Dict[str, set[str]]) -> pd.DataFrame:
    normalized_cols = [str(c).strip().lower() for c in df.columns]
    rename_map: Dict[str, str] = {}
    used_targets: set[str] = set()
    normalized_key_map = {col: _col_key(col) for col in normalized_cols}
    for original_col, key in normalized_key_map.items():
        for target, aliases in alias_map.items():
            if target in used_targets:
                continue
            alias_keys = {_col_key(a) for a in aliases}
            if key == _col_key(target) or key in alias_keys:
                rename_map[original_col] = target
                used_targets.add(target)
                break
    if rename_map:
        df = df.rename(columns=rename_map)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _save_upload_metadata(
    *,
    file_name: str,
    stored_path: Path,
    uploaded_by_user_id: Optional[int],
    uploaded_by_email: Optional[str],
    upload_type: str,
    status: str,
    records_processed: int,
    duplicates_removed: int,
    message: str,
) -> None:
    with _get_auth_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO upload_metadata (
                file_name,
                stored_path,
                uploaded_by,
                uploaded_by_email,
                upload_type,
                status,
                records_processed,
                duplicates_removed,
                message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_name,
                str(stored_path),
                uploaded_by_user_id,
                uploaded_by_email,
                upload_type,
                status,
                max(0, int(records_processed)),
                max(0, int(duplicates_removed)),
                message,
            ),
        )
        conn.commit()


def _build_secure_upload_path(upload_type: str, suffix: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
    token = secrets.token_hex(4)
    suffix = suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        suffix = ".csv"
    if upload_type in {UPLOAD_TYPE_MAIN_DATASET, UPLOAD_TYPE_MONTHLY_UPDATE, UPLOAD_TYPE_SANDBOX_TRAIN}:
        target_dir = UPLOADS_SALES_DIR
        prefix = "sales"
    else:
        target_dir = UPLOADS_INVENTORY_DIR
        prefix = "inventory"
    target_dir.mkdir(parents=True, exist_ok=True)
    UPLOADS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"{prefix}_{timestamp}_{token}{suffix}"
    return target_dir / file_name


def _build_staging_path(suffix: str = ".csv") -> Path:
    UPLOADS_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
    token = secrets.token_hex(4)
    ext = suffix.lower() if suffix.lower() in {".csv"} else ".csv"
    return UPLOADS_STAGING_DIR / f"dataset_staging_{ts}_{token}{ext}"


def _parse_uploaded_dataframe(file_name: str, payload: bytes) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".csv":
        text = payload.decode("utf-8-sig", errors="replace")
        return pd.read_csv(io.StringIO(text))
    if suffix == ".xlsx":
        return pd.read_excel(io.BytesIO(payload))
    raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are allowed")


def _validate_main_dataset_staging(df: pd.DataFrame) -> Dict[str, Any]:
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    required = {"date", "product_id", "store_id", "units_sold"}
    missing = [c for c in sorted(required) if c not in out.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {missing}")

    total_rows = int(len(out))
    missing_values = int(out.isna().sum().sum())
    duplicate_rows = int(out.duplicated().sum())
    parsed_dates = parse_date_series(out["date"])
    if parsed_dates.isna().any():
        raise HTTPException(status_code=400, detail="Invalid date values found in 'date' column")
    out["units_sold"] = pd.to_numeric(out["units_sold"], errors="coerce")
    if out["units_sold"].isna().any():
        raise HTTPException(status_code=400, detail="'units_sold' must be numeric")
    if (out["units_sold"] < 0).any():
        raise HTTPException(status_code=400, detail="'units_sold' cannot be negative")

    preview_cols = [c for c in ["date", "product_id", "store_id", "units_sold"] if c in out.columns]
    preview_rows = (
        out[preview_cols]
        .head(5)
        .astype(str)
        .to_dict(orient="records")
    )
    return {
        "total_rows": total_rows,
        "missing_values_count": missing_values,
        "duplicate_rows_count": duplicate_rows,
        "date_min": parsed_dates.min().date().isoformat() if total_rows else None,
        "date_max": parsed_dates.max().date().isoformat() if total_rows else None,
        "preview_rows": preview_rows,
    }


def _validate_sales_frame(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    alias_map = {
        "date": {"order date", "sales date", "transaction date"},
        "product_id": {"product id", "product", "sku", "item id"},
        "store_id": {"store id", "branch_id", "branch id"},
        "category": {"product category", "item category", "department"},
        "units_sold": {"units sold", "unit sold", "unit solds", "units_sold", "qty sold", "quantity sold"},
    }
    out = _rename_with_aliases(df.copy(), alias_map)
    required = {"date", "product_id", "store_id", "units_sold"}
    missing = [c for c in sorted(required) if c not in out.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {missing}")

    out["date"] = parse_date_series(out["date"])
    out["units_sold"] = pd.to_numeric(out["units_sold"], errors="coerce")
    if out["date"].isna().any():
        raise HTTPException(status_code=400, detail="Invalid date format found in uploaded file")
    if out["units_sold"].isna().any():
        raise HTTPException(status_code=400, detail="'units_sold' must be numeric")
    if (out["units_sold"] < 0).any():
        raise HTTPException(status_code=400, detail="'units_sold' cannot be negative")

    before = len(out)
    out = out.drop_duplicates().copy()
    duplicates_removed = max(0, before - len(out))
    if out.empty:
        raise HTTPException(status_code=400, detail="Uploaded dataset has no valid rows")

    if "category.1" in out.columns:
        alt = out["category.1"].astype("string").str.strip()
        valid_alt = alt.notna() & alt.ne("") & alt.str.lower().ne("nan")
        if "category" not in out.columns:
            out["category"] = pd.NA
        out.loc[valid_alt, "category"] = alt[valid_alt]

    if "category" in out.columns:
        out["category"] = out["category"].astype(str).str.strip()
        missing_category = out["category"].eq("") | out["category"].str.lower().eq("nan")
        out.loc[missing_category, "category"] = out.loc[missing_category, "product_id"].astype(str).str.strip()
    else:
        out["category"] = out["product_id"].astype(str).str.strip()
    out["units sold"] = out["units_sold"]
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out, duplicates_removed


def _validate_inventory_frame(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    alias_map = {
        "product_id": {"product id", "product", "sku", "item id"},
        "store_id": {"store id", "branch_id", "branch id"},
        "current_stock": {"current stock", "stock", "stock_level", "inventory level", "inventory_level"},
    }
    out = _rename_with_aliases(df.copy(), alias_map)
    required = {"product_id", "store_id", "current_stock"}
    missing = [c for c in sorted(required) if c not in out.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Missing required columns: {missing}. "
                "For Inventory Stock File, include product_id, store_id, and current_stock "
                "(accepted variants: product id, store id, current stock/stock/stock_level/inventory level)."
            ),
        )

    out["current_stock"] = pd.to_numeric(out["current_stock"], errors="coerce")
    if out["current_stock"].isna().any():
        raise HTTPException(status_code=400, detail="'current_stock' must be numeric")
    if (out["current_stock"] < 0).any():
        raise HTTPException(status_code=400, detail="'current_stock' cannot be negative")
    before = len(out)
    out = out.drop_duplicates().copy()
    duplicates_removed = max(0, before - len(out))
    if out.empty:
        raise HTTPException(status_code=400, detail="Inventory file has no valid rows")
    out["product_id"] = out["product_id"].astype(str).str.strip()
    out["store_id"] = out["store_id"].astype(str).str.strip()
    return out, duplicates_removed


def _retrain_all_categories() -> Dict[str, Any]:
    with _engine_lock:
        df = _get_cached_df()
        trained_categories = 0
        errors: List[str] = []

        values = (
            df[PRODUCT_COL]
            .dropna()
            .astype(str)
            .map(str.strip)
            if PRODUCT_COL in df.columns
            else pd.Series(dtype="object")
        )
        categories = sorted({v for v in values if v})

        for category in categories:
            try:
                selector_col, resolved_category = _resolve_selector(df, category)
                _get_trained_model(df, resolved_category, selector_col=selector_col)
                trained_categories += 1
            except Exception:
                errors.append(category)
    return {
        "trained_categories": trained_categories,
        "failed_categories": errors,
    }


def _train_uploaded_categories_without_persisting(df: pd.DataFrame) -> Dict[str, Any]:
    trained_categories = 0
    errors: List[str] = []

    values = (
        df[PRODUCT_COL]
        .dropna()
        .astype(str)
        .map(str.strip)
        if PRODUCT_COL in df.columns
        else pd.Series(dtype="object")
    )
    categories = sorted({v for v in values if v})
    for category in categories:
        try:
            daily = build_daily_series(df, category=category, selector_col=PRODUCT_COL)
            _ = train_forecast_model(daily, target=TARGET_COL)
            trained_categories += 1
        except Exception:
            errors.append(category)
    return {
        "trained_categories": trained_categories,
        "failed_categories": errors,
    }


def _current_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None

    now_epoch = _utc_epoch_now()
    with _get_auth_db_conn() as conn:
        row = conn.execute(
            """
            SELECT
                u.id,
                u.email,
                u.full_name,
                u.role,
                u.email_verified,
                u.is_active,
                s.expires_at_epoch
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.session_id = ?
            """,
            (session_id,),
        ).fetchone()

        if row is None:
            return None
        if int(row["is_active"]) != 1 or int(row["expires_at_epoch"]) <= now_epoch:
            conn.execute("DELETE FROM auth_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return None

    return {
        "id": int(row["id"]),
        "email": str(row["email"]),
        "full_name": str(row["full_name"]),
        "role": str(row["role"]),
        "email_verified": bool(int(row["email_verified"])),
    }


def _require_authenticated_user(request: Request) -> Dict[str, Any]:
    user = _current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _require_roles(request: Request, allowed_roles: set[str]) -> Dict[str, Any]:
    user = _require_authenticated_user(request)
    role = _canonical_role(str(user.get("role", "")))
    normalized_allowed = {_canonical_role(r) for r in allowed_roles}
    if role not in normalized_allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions for this action")
    return user


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
_init_auth_db()


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


class LoginRequest(BaseModel):
    email: str
    password: str
    role: str
    remember_me: bool = False


class RegisterRequest(BaseModel):
    full_name: str
    email: str
    password: str
    role: str
    remember_me: bool = True


class EmailOnlyRequest(BaseModel):
    email: str


class TokenRequest(BaseModel):
    token: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class AdminUploadDatasetRequest(BaseModel):
    filename: str
    csv_text: str


class AdminUploadDocumentRequest(BaseModel):
    filename: str
    upload_type: str
    file_base64: str
    retrain_model: bool = True


class StageDatasetRequest(BaseModel):
    filename: str
    file_base64: str


class StageActionRequest(BaseModel):
    staging_id: int


class AdminSetUserRoleRequest(BaseModel):
    email: str
    role: str


class AdminCreateUserRequest(BaseModel):
    full_name: str
    email: str
    password: str
    role: str


class AdminDeleteUserRequest(BaseModel):
    email: str


def _norm_text(value: Any) -> str:
    return str(value).strip()


def _norm_key(value: Any) -> str:
    return _norm_text(value).lower()


def _looks_like_product_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]\d{2,}", str(value).strip()))


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


def _fallback_forecast_for_category(
    df: pd.DataFrame,
    category: str,
    horizon: int,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    selector_col, resolved_category = _resolve_selector(df, category)
    category_rows = df[df[selector_col].astype(str).str.strip() == resolved_category].copy()
    if category_rows.empty:
        raise ValueError(f"No rows found for category {category}")
    category_rows[DATE_COL] = parse_date_series(category_rows[DATE_COL])
    category_rows = category_rows.dropna(subset=[DATE_COL, TARGET_COL]).sort_values(DATE_COL)
    if anchor_date is not None:
        category_rows = category_rows[category_rows[DATE_COL] <= anchor_date]
    if category_rows.empty:
        raise ValueError(f"No rows found for category {category}")

    last_date = pd.to_datetime(category_rows[DATE_COL].max()).normalize()
    last_value = float(pd.to_numeric(category_rows[TARGET_COL], errors="coerce").dropna().iloc[-1])
    future_dates = pd.date_range(start=last_date + pd.Timedelta(days=1), periods=max(1, int(horizon)), freq="D")
    return [
        {
            "date": d.date().isoformat(),
            "forecast_units_sold": round(last_value, 3),
        }
        for d in future_dates
    ]


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
def login(request: Request) -> Response:
    if _current_user_from_request(request):
        return RedirectResponse(url="/dashboard", status_code=307)
    if not FRONTEND_LOGIN_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Login file not found at {FRONTEND_LOGIN_HTML_PATH}",
        )
    return FileResponse(FRONTEND_LOGIN_HTML_PATH, headers=NO_CACHE_HEADERS)


@app.get("/dashboard", include_in_schema=False)
def dashboard(request: Request) -> Response:
    if not _current_user_from_request(request):
        return RedirectResponse(url="/login", status_code=307)
    if not FRONTEND_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dashboard file not found at {FRONTEND_HTML_PATH}",
        )
    return FileResponse(FRONTEND_HTML_PATH, headers=NO_CACHE_HEADERS)


@app.get("/dashboard/results", include_in_schema=False)
def dashboard_results(request: Request) -> Response:
    if not _current_user_from_request(request):
        return RedirectResponse(url="/login", status_code=307)
    if not FRONTEND_RESULTS_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dashboard results file not found at {FRONTEND_RESULTS_HTML_PATH}",
        )
    return FileResponse(FRONTEND_RESULTS_HTML_PATH, headers=NO_CACHE_HEADERS)


@app.get("/admin/dashboard", include_in_schema=False)
def admin_dashboard(request: Request) -> Response:
    user = _current_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=307)
    if _canonical_role(str(user.get("role", ""))) != ROLE_ADMIN:
        return RedirectResponse(url="/dashboard", status_code=307)
    if not FRONTEND_ADMIN_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Admin dashboard file not found at {FRONTEND_ADMIN_HTML_PATH}",
        )
    return FileResponse(FRONTEND_ADMIN_HTML_PATH, headers=NO_CACHE_HEADERS)


@app.get("/about-project", include_in_schema=False)
def about_project(request: Request) -> Response:
    if not _current_user_from_request(request):
        return RedirectResponse(url="/login", status_code=307)
    if not FRONTEND_ABOUT_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"About project file not found at {FRONTEND_ABOUT_HTML_PATH}",
        )
    return FileResponse(FRONTEND_ABOUT_HTML_PATH, headers=NO_CACHE_HEADERS)


@app.post("/auth/login")
def auth_login(payload: LoginRequest, request: Request) -> JSONResponse:
    email = payload.email.strip().lower()
    selected_role = _canonical_role(payload.role)
    if not email or not payload.password or not selected_role:
        raise HTTPException(status_code=400, detail="Email, password, and role are required")

    with _get_auth_db_conn() as conn:
        row = conn.execute(
            """
            SELECT id, email, full_name, role, password_hash, is_active, email_verified
            FROM users
            WHERE lower(email) = ?
            """,
            (email,),
        ).fetchone()

    stored_role = _canonical_role(str(row["role"])) if row is not None else ""
    if (
        row is None
        or int(row["is_active"]) != 1
        or stored_role != selected_role
        or not _verify_password(payload.password, str(row["password_hash"]))
    ):
        _log_audit_event(
            event_type="login_failed",
            actor_user_id=int(row["id"]) if row is not None else None,
            actor_email=email,
            details={"selected_role": selected_role},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    session_id, ttl_seconds = _create_session(
        user_id=int(row["id"]),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        remember_me=bool(payload.remember_me),
    )

    response = JSONResponse(
        {
            "ok": True,
            "user": {
                "email": str(row["email"]),
                "full_name": str(row["full_name"]),
                "role": str(row["role"]),
                "email_verified": bool(int(row["email_verified"])),
            },
        }
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=ttl_seconds,
    )
    _log_audit_event(
        event_type="login_success",
        actor_user_id=int(row["id"]),
        actor_email=str(row["email"]),
        details={"role": str(row["role"]), "remember_me": bool(payload.remember_me)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return response


@app.post("/auth/register")
def auth_register(payload: RegisterRequest, request: Request) -> JSONResponse:
    full_name = _normalize_full_name(payload.full_name)
    email = payload.email.strip().lower()
    password = payload.password or ""
    role = _canonical_role(payload.role)
    if len(full_name) < 2:
        raise HTTPException(status_code=400, detail="Full name must be at least 2 characters")
    if not _is_valid_signup_email(email):
        raise HTTPException(
            status_code=400,
            detail="Enter a valid email with at least one letter before @",
        )
    if not _is_strong_password(password):
        raise HTTPException(status_code=400, detail=PASSWORD_POLICY_MESSAGE)
    if email in _bootstrap_admin_email_set():
        raise HTTPException(
            status_code=403,
            detail="This email is reserved as an admin account. Use Sign In with Admin role.",
        )
    if role not in SELF_REGISTER_ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Selected role cannot self-register")

    with _get_auth_db_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="An account already exists for this email")
        existing_name = conn.execute(
            "SELECT id FROM users WHERE lower(trim(full_name)) = lower(trim(?))",
            (full_name,),
        ).fetchone()
        if existing_name is not None:
            raise HTTPException(status_code=409, detail="An account already exists for this full name")

        cur = conn.execute(
            """
            INSERT INTO users (email, full_name, role, password_hash, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (email, full_name, role, _password_hash(password)),
        )
        user_id = int(cur.lastrowid)
        conn.commit()

    session_id, ttl_seconds = _create_session(
        user_id=user_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        remember_me=bool(payload.remember_me),
    )
    response = JSONResponse(
        {
            "ok": True,
            "user": {
                "email": email,
                "full_name": full_name,
                "role": role,
            },
        }
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=ttl_seconds,
    )
    _log_audit_event(
        event_type="account_created",
        actor_user_id=user_id,
        actor_email=email,
        target_user_id=user_id,
        target_email=email,
        details={"role": role, "full_name": full_name},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return response


@app.post("/auth/request-email-verification")
def auth_request_email_verification(payload: EmailOnlyRequest, request: Request) -> Dict[str, Any]:
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    with _get_auth_db_conn() as conn:
        row = conn.execute(
            "SELECT id, email_verified FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No account exists for this email")
    if int(row["email_verified"]) == 1:
        return {"ok": True, "message": "Email is already verified"}

    token = _create_auth_token(
        user_id=int(row["id"]),
        token_type="email_verify",
        ttl_seconds=EMAIL_VERIFY_TTL_SECONDS,
    )
    _log_audit_event(
        event_type="email_verification_requested",
        actor_user_id=int(row["id"]),
        actor_email=email,
        target_user_id=int(row["id"]),
        target_email=email,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "ok": True,
        "message": "Verification token generated",
        "verification_token": token,
        "expires_in_seconds": EMAIL_VERIFY_TTL_SECONDS,
    }


@app.post("/auth/verify-email")
def auth_verify_email(payload: TokenRequest, request: Request) -> Dict[str, Any]:
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")
    user_id = _consume_auth_token(token=token, token_type="email_verify")
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    with _get_auth_db_conn() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
        conn.commit()
    _log_audit_event(
        event_type="email_verified",
        actor_user_id=user_id,
        actor_email=str(row["email"]),
        target_user_id=user_id,
        target_email=str(row["email"]),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "message": "Email verified successfully"}


@app.post("/auth/request-password-reset")
def auth_request_password_reset(payload: EmailOnlyRequest, request: Request) -> Dict[str, Any]:
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    with _get_auth_db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No account exists for this email")

    token = _create_auth_token(
        user_id=int(row["id"]),
        token_type="password_reset",
        ttl_seconds=PASSWORD_RESET_TTL_SECONDS,
    )
    _log_audit_event(
        event_type="password_reset_requested",
        actor_user_id=int(row["id"]),
        actor_email=email,
        target_user_id=int(row["id"]),
        target_email=email,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "ok": True,
        "message": "Password reset token generated",
        "reset_token": token,
        "expires_in_seconds": PASSWORD_RESET_TTL_SECONDS,
    }


@app.post("/auth/reset-password")
def auth_reset_password(payload: ResetPasswordRequest, request: Request) -> Dict[str, Any]:
    token = payload.token.strip()
    new_password = payload.new_password or ""
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")
    if not _is_strong_password(new_password):
        raise HTTPException(status_code=400, detail=PASSWORD_POLICY_MESSAGE)

    user_id = _consume_auth_token(token=token, token_type="password_reset")
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    with _get_auth_db_conn() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (_password_hash(new_password), user_id),
        )
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    _log_audit_event(
        event_type="password_reset_completed",
        actor_user_id=user_id,
        actor_email=str(row["email"]),
        target_user_id=user_id,
        target_email=str(row["email"]),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "message": "Password reset successful. Please sign in again."}


@app.post("/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    user = _current_user_from_request(request)
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        _delete_session(session_id)
    if user:
        _log_audit_event(
            event_type="logout",
            actor_user_id=int(user["id"]),
            actor_email=str(user["email"]),
            details={"role": str(user["role"])},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

    response = JSONResponse({"ok": True})
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response


@app.get("/auth/me")
def auth_me(request: Request) -> Dict[str, Any]:
    user = _require_authenticated_user(request)
    return {"authenticated": True, "user": user}


@app.get("/auth/roles")
def auth_roles() -> Dict[str, Any]:
    return {
        "roles": [
            {"value": ROLE_ADMIN, "label": "Admin"},
            {"value": ROLE_INVENTORY_MANAGER, "label": "Inventory Manager"},
            {"value": ROLE_VIEWER, "label": "Viewer"},
        ]
    }


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "model_info": MODEL_INFO}


@app.get("/categories")
def get_categories(request: Request) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        values = (
            df[PRODUCT_COL]
            .dropna()
            .astype(str)
            .map(str.strip)
        )
        categories = sorted({v for v in values if v})
        if categories:
            has_named_category = any(not _looks_like_product_id(v) for v in categories)
            if has_named_category:
                categories = [v for v in categories if not _looks_like_product_id(v)]
        product_ids: List[str] = []
        if "product id" in df.columns:
            pvalues = df["product id"].dropna().astype(str).map(str.strip)
            product_ids = sorted({v for v in pvalues if v})
    return {"categories": categories, "product_ids": product_ids}


@app.get("/forecast/{category}")
def get_forecast(
    request: Request,
    category: str,
    horizon: int = DEFAULT_HORIZON,
    anchor_date: Optional[str] = None,
    history_lookback_days: int = 365,
) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
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
            msg = str(exc)
            if "Not enough history to train model" in msg:
                rows = _fallback_forecast_for_category(
                    df,
                    category=category,
                    horizon=horizon,
                    anchor_date=parsed_anchor,
                )
            elif "No rows found for category" in msg:
                raise HTTPException(status_code=404, detail=msg) from exc
            else:
                raise HTTPException(status_code=400, detail=msg) from exc
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
    request: Request,
    category: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    items = _load_recent_history(category=category, limit=limit)
    return {
        "count": len(items),
        "items": items,
    }


@app.post("/sales")
def ingest_sales(payload: SalesIngestRequest, request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN})
    if not payload.records:
        raise HTTPException(status_code=400, detail="records cannot be empty")
    if payload.horizon <= 0:
        raise HTTPException(status_code=400, detail="horizon must be positive")

    with _engine_lock:
        combined = _append_sales_rows(payload.records, persist=payload.persist)
        if payload.persist:
            _invalidate_engine_cache()
        combined = combined.copy()
        combined.columns = combined.columns.str.strip().str.lower()
        combined[DATE_COL] = parse_date_series(combined[DATE_COL])
        combined = combined.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET_COL])

        requested_categories = sorted({cid for cid in (_record_identifier(r) for r in payload.records) if cid})
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
                for r in payload.records
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
                    horizon=payload.horizon,
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
        "horizon": payload.horizon,
        "forecasts": forecasts,
        "histories": histories,
    }


def _process_admin_upload(
    *,
    request: Request,
    admin_user: Dict[str, Any],
    upload_type: str,
    filename: str,
    file_bytes: bytes,
    retrain_model: bool,
) -> Dict[str, Any]:
    canonical_type = _canonical_upload_type(upload_type)
    if canonical_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="Invalid upload type")

    original_name = (filename or "").strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="File name is required")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only .csv or .xlsx files are allowed")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Maximum allowed is 5 MB")

    secure_path = _build_secure_upload_path(canonical_type, suffix)
    secure_path.write_bytes(file_bytes)

    duplicates_removed = 0
    records_processed = 0
    info_message = "Upload completed"
    retrain_result: Dict[str, Any] = {"trained_categories": 0, "failed_categories": []}
    retrained_done = False
    low_stock_alerts = 0
    overstock_alerts = 0

    try:
        df = _parse_uploaded_dataframe(original_name, file_bytes)
        if df.empty:
            raise HTTPException(status_code=400, detail="Uploaded file contains no rows")

        if canonical_type in {UPLOAD_TYPE_MAIN_DATASET, UPLOAD_TYPE_MONTHLY_UPDATE, UPLOAD_TYPE_SANDBOX_TRAIN}:
            sales_df, duplicates_removed = _validate_sales_frame(df)
            records_processed = int(len(sales_df))
            model_ready_df = sales_df.copy()
            model_ready_df[DATE_COL] = parse_date_series(model_ready_df[DATE_COL])
            model_ready_df = model_ready_df.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET_COL])

            if canonical_type == UPLOAD_TYPE_SANDBOX_TRAIN:
                retrain_result = _train_uploaded_categories_without_persisting(model_ready_df)
                retrained_done = True
                info_message = "Sandbox training completed from uploaded file. Production dataset unchanged."
            else:
                if CSV_PATH.exists():
                    existing = pd.read_csv(CSV_PATH)
                    existing.columns = [str(c).strip().lower() for c in existing.columns]
                else:
                    existing = pd.DataFrame(columns=model_ready_df.columns)

                # Normalize commonly variant columns before duplicate comparison.
                if "product id" in existing.columns and "product_id" not in existing.columns:
                    existing["product_id"] = existing["product id"]
                if "store id" in existing.columns and "store_id" not in existing.columns:
                    existing["store_id"] = existing["store id"]
                if "units sold" in existing.columns and "units_sold" not in existing.columns:
                    existing["units_sold"] = existing["units sold"]
                if "category" not in existing.columns and "product_id" in existing.columns:
                    existing["category"] = existing["product_id"]

                dedupe_keys = [c for c in ["date", "product_id", "store_id", "category", "units_sold"] if c in model_ready_df.columns]
                dedupe_keys = [c for c in dedupe_keys if c in existing.columns] or dedupe_keys

                incoming_unique = model_ready_df.drop_duplicates(subset=dedupe_keys) if dedupe_keys else model_ready_df.drop_duplicates()
                if existing.empty:
                    rows_to_add = incoming_unique
                    duplicate_against_existing = 0
                elif dedupe_keys:
                    existing_keys = existing[dedupe_keys].drop_duplicates()
                    merged = incoming_unique.merge(existing_keys, on=dedupe_keys, how="left", indicator=True)
                    left_only_mask = merged["_merge"] == "left_only"
                    rows_to_add = incoming_unique.loc[left_only_mask.values].copy()
                    duplicate_against_existing = int((~left_only_mask).sum())
                else:
                    rows_to_add = incoming_unique
                    duplicate_against_existing = 0

                duplicates_removed += duplicate_against_existing

                if rows_to_add.empty:
                    info_message = "Upload skipped: all rows are duplicates of the existing dataset."
                else:
                    if canonical_type == UPLOAD_TYPE_MAIN_DATASET and CSV_PATH.exists():
                        archive_name = f"dataset_backup_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H%M%S')}.csv"
                        shutil.copy2(CSV_PATH, UPLOADS_ARCHIVE_DIR / archive_name)

                    all_cols = list(existing.columns)
                    for c in rows_to_add.columns:
                        if c not in all_cols:
                            all_cols.append(c)
                    for c in all_cols:
                        if c not in existing.columns:
                            existing[c] = pd.NA
                        if c not in rows_to_add.columns:
                            rows_to_add[c] = pd.NA
                    combined = pd.concat([existing[all_cols], rows_to_add[all_cols]], ignore_index=True)
                    combined = combined.drop_duplicates(subset=dedupe_keys) if dedupe_keys else combined.drop_duplicates()
                    combined.to_csv(CSV_PATH, index=False)
                    _invalidate_engine_cache()
                    if retrain_model:
                        retrain_result = _retrain_all_categories()
                        retrained_done = True
                    if canonical_type == UPLOAD_TYPE_MAIN_DATASET:
                        info_message = "Main dataset updated with non-duplicate rows (duplicates skipped)."
                    else:
                        info_message = "Monthly update appended with non-duplicate rows."
        else:
            inventory_df, duplicates_removed = _validate_inventory_frame(df)
            records_processed = int(len(inventory_df))
            with _get_auth_db_conn() as conn:
                for _, r in inventory_df.iterrows():
                    conn.execute(
                        """
                        INSERT INTO inventory_stock (product_id, store_id, current_stock, updated_at)
                        VALUES (?, ?, ?, datetime('now'))
                        ON CONFLICT(product_id, store_id) DO UPDATE SET
                            current_stock = excluded.current_stock,
                            updated_at = datetime('now')
                        """,
                        (str(r["product_id"]), str(r["store_id"]), float(r["current_stock"])),
                    )
                conn.commit()
            low_stock_alerts = int((inventory_df["current_stock"] < 20).sum())
            overstock_alerts = int((inventory_df["current_stock"] > 500).sum())
            info_message = "Inventory stock updated successfully"

        _save_upload_metadata(
            file_name=original_name,
            stored_path=secure_path,
            uploaded_by_user_id=int(admin_user["id"]),
            uploaded_by_email=str(admin_user["email"]),
            upload_type=canonical_type,
            status="success",
            records_processed=records_processed,
            duplicates_removed=duplicates_removed,
            message=info_message,
        )
        _log_audit_event(
            event_type="document_uploaded",
            actor_user_id=int(admin_user["id"]),
            actor_email=str(admin_user["email"]),
            details={
                "upload_type": canonical_type,
                "file_name": original_name,
                "records_processed": records_processed,
                "duplicates_removed": duplicates_removed,
                "retrain_requested": bool(retrain_model),
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return {
            "ok": True,
            "message": info_message,
            "upload_type": canonical_type,
            "stored_file": secure_path.name,
            "records_processed": records_processed,
            "duplicates_removed": duplicates_removed,
            "retrained": retrained_done,
            "trained_categories": int(retrain_result.get("trained_categories", 0)),
            "failed_categories": retrain_result.get("failed_categories", []),
            "inventory_alerts": {
                "low_stock": low_stock_alerts,
                "overstock": overstock_alerts,
            },
        }
    except HTTPException as exc:
        _save_upload_metadata(
            file_name=original_name,
            stored_path=secure_path,
            uploaded_by_user_id=int(admin_user["id"]),
            uploaded_by_email=str(admin_user["email"]),
            upload_type=canonical_type,
            status="failed",
            records_processed=records_processed,
            duplicates_removed=duplicates_removed,
            message=str(exc.detail),
        )
        raise
    except Exception as exc:
        _save_upload_metadata(
            file_name=original_name,
            stored_path=secure_path,
            uploaded_by_user_id=int(admin_user["id"]),
            uploaded_by_email=str(admin_user["email"]),
            upload_type=canonical_type,
            status="failed",
            records_processed=records_processed,
            duplicates_removed=duplicates_removed,
            message=str(exc),
        )
        raise HTTPException(status_code=400, detail=f"Upload processing failed: {exc}") from exc


@app.post("/admin/upload-document")
def admin_upload_document(payload: AdminUploadDocumentRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    file_name = (payload.filename or "").strip() or "upload.csv"
    try:
        file_bytes = base64.b64decode(payload.file_base64 or "", validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid file payload: {exc}") from exc
    return _process_admin_upload(
        request=request,
        admin_user=admin_user,
        upload_type=payload.upload_type,
        filename=file_name,
        file_bytes=file_bytes,
        retrain_model=bool(payload.retrain_model),
    )


@app.post("/admin/upload-dataset")
def admin_upload_dataset(payload: AdminUploadDatasetRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    file_name = (payload.filename or "").strip() or "dataset.csv"
    file_bytes = (payload.csv_text or "").encode("utf-8")
    return _process_admin_upload(
        request=request,
        admin_user=admin_user,
        upload_type=UPLOAD_TYPE_MAIN_DATASET,
        filename=file_name,
        file_bytes=file_bytes,
        retrain_model=False,
    )


@app.post("/admin/dataset/stage")
def admin_stage_dataset(payload: StageDatasetRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    file_name = (payload.filename or "").strip()
    if not file_name.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are allowed for dataset staging")
    try:
        file_bytes = base64.b64decode(payload.file_base64 or "", validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid file payload: {exc}") from exc
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Maximum allowed is 5 MB")

    stage_path = _build_staging_path(".csv")
    stage_path.write_bytes(file_bytes)

    try:
        df = _parse_uploaded_dataframe(file_name, file_bytes)
        summary = _validate_main_dataset_staging(df)
        with _get_auth_db_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO dataset_staging (
                    file_name,
                    staging_path,
                    uploaded_by,
                    uploaded_by_email,
                    status,
                    total_rows,
                    missing_values,
                    duplicate_rows,
                    date_min,
                    date_max,
                    summary_json
                ) VALUES (?, ?, ?, ?, 'staged', ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_name,
                    str(stage_path),
                    int(admin_user["id"]),
                    str(admin_user["email"]),
                    int(summary["total_rows"]),
                    int(summary["missing_values_count"]),
                    int(summary["duplicate_rows_count"]),
                    summary.get("date_min"),
                    summary.get("date_max"),
                    _safe_json_compact(summary),
                ),
            )
            staging_id = int(cur.lastrowid)
            conn.commit()

        _log_audit_event(
            event_type="dataset_staged",
            actor_user_id=int(admin_user["id"]),
            actor_email=str(admin_user["email"]),
            details={
                "staging_id": staging_id,
                "file_name": file_name,
                "total_rows": summary["total_rows"],
                "duplicate_rows": summary["duplicate_rows_count"],
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return {
            "ok": True,
            "staging_id": staging_id,
            "status": "staged",
            "file_name": file_name,
            "summary": summary,
            "message": "Dataset staged successfully. Review and approve to replace production dataset.",
        }
    except Exception:
        if stage_path.exists():
            stage_path.unlink(missing_ok=True)
        raise


@app.post("/admin/dataset/approve")
def admin_approve_dataset(payload: StageActionRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    staging_id = int(payload.staging_id)
    with _get_auth_db_conn() as conn:
        row = conn.execute(
            """
            SELECT id, file_name, staging_path, status
            FROM dataset_staging
            WHERE id = ?
            """,
            (staging_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Staging record not found")
        if str(row["status"]).lower() != "staged":
            raise HTTPException(status_code=400, detail="Only staged uploads can be approved")

    stage_path = Path(str(row["staging_path"]))
    if not stage_path.exists():
        raise HTTPException(status_code=404, detail="Staging file no longer exists")

    file_bytes = stage_path.read_bytes()
    df = _parse_uploaded_dataframe(str(row["file_name"]), file_bytes)
    model_ready_df, _ = _validate_sales_frame(df)

    if CSV_PATH.exists():
        archive_name = f"dataset_backup_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H%M%S')}.csv"
        UPLOADS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CSV_PATH, UPLOADS_ARCHIVE_DIR / archive_name)

    model_ready_df.to_csv(CSV_PATH, index=False)
    _invalidate_engine_cache()
    _ = _get_cached_df()  # trigger preprocessing/load path
    retrained = _retrain_all_categories()

    with _get_auth_db_conn() as conn:
        conn.execute(
            """
            UPDATE dataset_staging
            SET status = 'approved',
                decided_at = datetime('now'),
                decision_note = ?
            WHERE id = ?
            """,
            ("Approved and replaced production dataset", staging_id),
        )
        conn.commit()

    _log_audit_event(
        event_type="dataset_approved",
        actor_user_id=int(admin_user["id"]),
        actor_email=str(admin_user["email"]),
        details={"staging_id": staging_id, "file_name": str(row["file_name"])},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "ok": True,
        "message": "Production dataset replaced successfully. Preprocessing and retraining completed.",
        "staging_id": staging_id,
        "trained_categories": int(retrained.get("trained_categories", 0)),
        "failed_categories": retrained.get("failed_categories", []),
    }


@app.post("/admin/dataset/reject")
def admin_reject_dataset(payload: StageActionRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    staging_id = int(payload.staging_id)
    with _get_auth_db_conn() as conn:
        row = conn.execute(
            """
            SELECT id, file_name, staging_path, status
            FROM dataset_staging
            WHERE id = ?
            """,
            (staging_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Staging record not found")
        if str(row["status"]).lower() != "staged":
            raise HTTPException(status_code=400, detail="Only staged uploads can be rejected")

        conn.execute(
            """
            UPDATE dataset_staging
            SET status = 'rejected',
                decided_at = datetime('now'),
                decision_note = ?
            WHERE id = ?
            """,
            ("Rejected by admin", staging_id),
        )
        conn.commit()

    stage_path = Path(str(row["staging_path"]))
    stage_path.unlink(missing_ok=True)

    _log_audit_event(
        event_type="dataset_rejected",
        actor_user_id=int(admin_user["id"]),
        actor_email=str(admin_user["email"]),
        details={"staging_id": staging_id, "file_name": str(row["file_name"])},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "ok": True,
        "message": "Staged dataset rejected and deleted.",
        "staging_id": staging_id,
    }


@app.get("/admin/users")
def admin_list_users(request: Request, limit: int = 200) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN})
    safe_limit = max(1, min(int(limit), 1000))
    with _get_auth_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, email, full_name, role, is_active, email_verified, created_at
            FROM users
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    items = [dict(r) for r in rows]
    return {"count": len(items), "items": items}


@app.post("/admin/users/create")
def admin_create_user(payload: AdminCreateUserRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    full_name = _normalize_full_name(payload.full_name)
    email = payload.email.strip().lower()
    password = payload.password or ""
    role = _canonical_role(payload.role)

    if len(full_name) < 2:
        raise HTTPException(status_code=400, detail="Full name must be at least 2 characters")
    if not _is_valid_signup_email(email):
        raise HTTPException(
            status_code=400,
            detail="Enter a valid email with at least one letter before @",
        )
    if not _is_strong_password(password):
        raise HTTPException(status_code=400, detail=PASSWORD_POLICY_MESSAGE)
    if role not in ALLOWED_LOGIN_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    with _get_auth_db_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="An account already exists for this email")
        existing_name = conn.execute(
            "SELECT id FROM users WHERE lower(trim(full_name)) = lower(trim(?))",
            (full_name,),
        ).fetchone()
        if existing_name is not None:
            raise HTTPException(status_code=409, detail="An account already exists for this full name")
        cur = conn.execute(
            """
            INSERT INTO users (email, full_name, role, password_hash, is_active, email_verified)
            VALUES (?, ?, ?, ?, 1, 1)
            """,
            (email, full_name, role, _password_hash(password)),
        )
        user_id = int(cur.lastrowid)
        conn.commit()

    _log_audit_event(
        event_type="user_created_by_admin",
        actor_user_id=int(admin_user["id"]),
        actor_email=str(admin_user["email"]),
        target_user_id=user_id,
        target_email=email,
        details={"role": role, "full_name": full_name},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    return {
        "ok": True,
        "message": "User account created",
        "user": {
            "id": user_id,
            "email": email,
            "full_name": full_name,
            "role": role,
        },
    }


@app.get("/admin/audit-logs")
def admin_audit_logs(request: Request, limit: int = 200, event_type: Optional[str] = None) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN})
    safe_limit = max(1, min(int(limit), 1000))
    query = """
        SELECT
            id,
            created_at,
            event_type,
            actor_user_id,
            actor_email,
            target_user_id,
            target_email,
            details_json,
            ip_address,
            user_agent
        FROM auth_audit_logs
    """
    params: List[Any] = []
    if event_type:
        query += " WHERE lower(event_type) = lower(?) "
        params.append(str(event_type).strip())
    query += " ORDER BY id DESC LIMIT ?"
    params.append(safe_limit)

    with _get_auth_db_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    items: List[Dict[str, Any]] = []
    for r in rows:
        entry = dict(r)
        raw = str(entry.get("details_json") or "").strip()
        parsed: Dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"raw": raw}
        entry["details"] = parsed
        items.append(entry)
    return {"count": len(items), "items": items}


@app.post("/admin/users/set-role")
def admin_set_user_role(payload: AdminSetUserRoleRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    email = payload.email.strip().lower()
    role = _canonical_role(payload.role)
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    if role not in ALLOWED_LOGIN_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if email == str(admin_user.get("email", "")).strip().lower() and role != ROLE_ADMIN:
        raise HTTPException(status_code=400, detail="You cannot demote your own active admin account")

    with _get_auth_db_conn() as conn:
        row = conn.execute(
            "SELECT id, role FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        previous_role = _canonical_role(str(row["role"]))
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (role, int(row["id"])),
        )
        conn.commit()

    _log_audit_event(
        event_type="role_changed",
        actor_user_id=int(admin_user["id"]),
        actor_email=str(admin_user["email"]),
        target_user_id=int(row["id"]),
        target_email=email,
        details={"from_role": previous_role, "to_role": role},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    return {
        "ok": True,
        "message": "User role updated",
        "email": email,
        "role": role,
    }


@app.post("/admin/users/delete")
def admin_delete_user(payload: AdminDeleteUserRequest, request: Request) -> Dict[str, Any]:
    admin_user = _require_roles(request, {ROLE_ADMIN})
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    if email == str(admin_user.get("email", "")).strip().lower():
        raise HTTPException(status_code=400, detail="You cannot delete your own active admin account")

    with _get_auth_db_conn() as conn:
        row = conn.execute(
            "SELECT id, full_name, role FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        target_user_id = int(row["id"])
        target_role = _canonical_role(str(row["role"]))
        target_full_name = str(row["full_name"])

        # Remove dependent rows first so delete works consistently for users with history/uploads.
        conn.execute("DELETE FROM upload_metadata WHERE uploaded_by = ?", (target_user_id,))
        conn.execute("DELETE FROM dataset_staging WHERE uploaded_by = ?", (target_user_id,))
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (target_user_id,))
        conn.commit()

    _log_audit_event(
        event_type="user_deleted",
        actor_user_id=int(admin_user["id"]),
        actor_email=str(admin_user["email"]),
        target_user_id=target_user_id,
        target_email=email,
        details={"role": target_role, "full_name": target_full_name},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    return {
        "ok": True,
        "message": "User deleted",
        "email": email,
    }


@app.post("/admin/retrain-model")
def admin_retrain_model(request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN})
    retrained = _retrain_all_categories()
    return {
        "ok": True,
        "message": "Retraining completed",
        "trained_categories": int(retrained.get("trained_categories", 0)),
        "failed_categories": retrained.get("failed_categories", []),
    }


# Run: uvicorn forecast_api:app --reload --host 0.0.0.0 --port 8000
