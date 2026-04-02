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
import subprocess
import sys
import tempfile
import time
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    from pydantic import ConfigDict  # pydantic v2
except ImportError:  # pydantic v1
    ConfigDict = None

sys.dont_write_bytecode = True

try:
    from .forecasting_core import (
        PRODUCT_COL,
        TARGET_COL,
        DATE_COL,
        MODEL_INFO,
        build_daily_series,
        forecast_next_days,
        load_sales_data,
        normalize_columns,
        prepare_sales_data,
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
        normalize_columns,
        prepare_sales_data,
        parse_date_series,
        train_forecast_model,
    )

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
CLEANED_CITY_LEVEL_CSV_PATH = PROJECT_DIR / "Dataset" / "retail_store_inventory_city_level_cleaned.csv"
CITY_LEVEL_CSV_PATH = PROJECT_DIR / "Dataset" / "retail_store_inventory_city_level.csv"
LEGACY_CSV_PATH = PROJECT_DIR / "Dataset" / "retail_store_inventory.csv"
if CLEANED_CITY_LEVEL_CSV_PATH.exists():
    CSV_PATH = CLEANED_CITY_LEVEL_CSV_PATH
elif CITY_LEVEL_CSV_PATH.exists():
    CSV_PATH = CITY_LEVEL_CSV_PATH
else:
    CSV_PATH = LEGACY_CSV_PATH
LIVE_DATA_DIR = PROJECT_DIR / "Dataset" / "live"
LEGACY_LIVE_CSV_PATH = PROJECT_DIR / "Dataset" / "live_sales_dataset.csv"
LIVE_CSV_PATH = LIVE_DATA_DIR / "simulated_live_data.csv"
FRONTEND_LOGIN_HTML_PATH = PROJECT_DIR / "Frontend" / "login.html"
FRONTEND_HTML_PATH = PROJECT_DIR / "Frontend" / "dashboard.html"
FRONTEND_RESULTS_HTML_PATH = PROJECT_DIR / "Frontend" / "dashboard_results.html"
FRONTEND_ADMIN_HTML_PATH = PROJECT_DIR / "Frontend" / "admin_dashboard.html"
FRONTEND_ABOUT_HTML_PATH = PROJECT_DIR / "Frontend" / "about_project.html"
HISTORY_DB_PATH = PROJECT_DIR / "Backend" / "forecast_history.db"
AUTH_DB_PATH = PROJECT_DIR / "Backend" / "auth.db"
MODEL_METRICS_PATH = PROJECT_DIR / "Backend" / "model_metrics.json"
PREPROCESSING_SCRIPT_PATH = PROJECT_DIR / "Backend" / "preprocessing.py"
FEATURE_ENGINEERING_SCRIPT_PATH = PROJECT_DIR / "Backend" / "features_engineering.py"
MODEL_TRAIN_SCRIPT_PATH = PROJECT_DIR / "Backend" / "model_train.py"
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
    base_csv_mtime_ns: int = -1
    base_df: Optional[pd.DataFrame] = None
    df: Optional[pd.DataFrame] = None
    scope_payload: Optional[Dict[str, Any]] = None
    scope_maps: Optional[
        Tuple[
            Dict[str, List[str]],
            Dict[str, List[str]],
            Dict[str, List[str]],
            Dict[str, List[str]],
            Dict[str, List[str]],
        ]
    ] = None
    forecast_payloads: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    trained_by_category: Dict[str, Any] = field(default_factory=dict)


_engine_cache = _EngineCache()
_live_simulator_lock = threading.Lock()
_live_simulator_stop_event = threading.Event()
_live_simulator_thread: Optional[threading.Thread] = None
_live_simulator_state: Dict[str, Any] = {
    "running": False,
    "interval_seconds": 1,
    "batch_size": 4,
    "horizon": DEFAULT_HORIZON,
    "started_at": "",
    "last_tick_at": "",
    "tick_count": 0,
    "last_generated_count": 0,
    "last_generated_categories": [],
    "last_error": "",
}


def _probe_sqlite_path(path: Path, probe_table: str) -> tuple[bool, str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {probe_table} (id INTEGER)")
            conn.commit()
            conn.execute(f"DROP TABLE IF EXISTS {probe_table}")
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _resolve_runtime_db_path(preferred_path: Path, runtime_name: str) -> Path:
    probe_table = f"__{runtime_name}_rw_probe"
    is_usable, _ = _probe_sqlite_path(preferred_path, probe_table)
    if is_usable:
        return preferred_path

    runtime_dir = Path(tempfile.gettempdir()) / "demandiq_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / preferred_path.name

    try:
        source_exists = preferred_path.exists()
        source_is_newer = (
            source_exists
            and (
                not runtime_path.exists()
                or preferred_path.stat().st_mtime_ns > runtime_path.stat().st_mtime_ns
            )
        )
        if source_is_newer:
            shutil.copy2(preferred_path, runtime_path)
    except Exception:
        pass

    runtime_usable, runtime_error = _probe_sqlite_path(runtime_path, probe_table)
    if runtime_usable:
        return runtime_path

    raise RuntimeError(
        f"SQLite database is not writable in project or temp runtime location for {preferred_path.name}: "
        f"{runtime_error}"
    )


HISTORY_DB_PATH = _resolve_runtime_db_path(HISTORY_DB_PATH, "history")
AUTH_DB_PATH = _resolve_runtime_db_path(AUTH_DB_PATH, "auth")


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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at_epoch)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_expires ON auth_tokens(expires_at_epoch)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_active_email ON users(is_active, email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_full_name ON users(full_name)")

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
                "SELECT id FROM users WHERE email = ?",
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
                resolved_category = _resolve_category_value(df, category)
                _get_trained_model(df, resolved_category)
                trained_categories += 1
            except Exception:
                errors.append(category)
    return {
        "trained_categories": trained_categories,
        "failed_categories": errors,
    }


def _tail_text(text: str, max_lines: int = 12) -> str:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def _load_model_metrics() -> Dict[str, Any]:
    if not MODEL_METRICS_PATH.exists():
        raise FileNotFoundError(f"Model metrics file not found at {MODEL_METRICS_PATH.name}")
    try:
        return json.loads(MODEL_METRICS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model metrics file is invalid JSON: {exc}") from exc


def _run_python_pipeline_script(script_path: Path, timeout_seconds: int = 900) -> Dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        error_tail = _tail_text(completed.stderr or completed.stdout)
        raise RuntimeError(f"{script_path.name} failed.\n{error_tail}")
    return {
        "script": script_path.name,
        "stdout_tail": _tail_text(completed.stdout),
    }


def _run_connected_training_pipeline() -> Dict[str, Any]:
    script_results: List[Dict[str, Any]] = []
    for script_path in (
        PREPROCESSING_SCRIPT_PATH,
        FEATURE_ENGINEERING_SCRIPT_PATH,
        MODEL_TRAIN_SCRIPT_PATH,
    ):
        script_results.append(_run_python_pipeline_script(script_path))

    _invalidate_engine_cache()
    retrained = _retrain_all_categories()
    metrics = _load_model_metrics()
    return {
        "trained_categories": int(retrained.get("trained_categories", 0)),
        "failed_categories": retrained.get("failed_categories", []),
        "model_metrics": metrics,
        "pipeline_scripts": script_results,
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


def _prewarm_engine_cache() -> None:
    try:
        with _engine_lock:
            df = _get_cached_df()
            _ = _get_cached_valid_scope_maps(df)
            _ = _get_cached_scope_payload(df)
    except Exception:
        # Background prewarm should never block app startup.
        pass


@app.on_event("startup")
def _startup_prewarm_engine_cache() -> None:
    threading.Thread(
        target=_prewarm_engine_cache,
        name="demandiq-cache-prewarm",
        daemon=True,
    ).start()


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


class LiveSimulationStartRequest(BaseModel):
    interval_seconds: float = 1.0
    batch_size: int = 4
    horizon: int = DEFAULT_HORIZON


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


class DynamicAddRecordRequest(BaseModel):
    record: Dict[str, Any]


class DynamicEditRecordRequest(BaseModel):
    record_id: str
    updates: Dict[str, Any]


class DynamicDeleteRecordRequest(BaseModel):
    record_id: str


class DynamicCompareRequest(BaseModel):
    product: str = ""
    platform_1: str = ""
    platform_2: str = ""
    from_date: str = ""
    to_date: str = ""
    left_product: str = ""
    right_product: str = ""
    left_from: str = ""
    left_to: str = ""
    right_from: str = ""
    right_to: str = ""


def _norm_text(value: Any) -> str:
    return str(value).strip()


def _norm_key(value: Any) -> str:
    return _norm_text(value).lower()


def _dynamic_alias_map() -> Dict[str, set[str]]:
    return {
        "record_id": {"id", "row_id", "recordid"},
        "date": {"order_date", "sales_date", "transaction_date"},
        "product_id": {"product id", "product", "sku", "item_id", "item id"},
        "category": {"product_category", "item_category"},
        "platform": {"sales_channel", "channel", "marketplace", "source_platform"},
        "units_sold": {"units sold", "qty_sold", "quantity_sold"},
        "price": {"unit_price", "selling_price"},
        "discount": {"discount_pct", "discount_percentage"},
        "inventory_level": {"inventory level", "stock_level", "current_stock"},
        "units_ordered": {"units ordered", "qty_ordered", "quantity_ordered"},
        "demand_forecast": {"demand forecast", "forecast_units", "forecast"},
    }


def _dynamic_output_col(canonical: str) -> str:
    mapping = {
        "record_id": "record_id",
        "date": "date",
        "product_id": "product id",
        "category": "category",
        "units_sold": "units sold",
        "price": "price",
        "discount": "discount",
        "inventory_level": "inventory level",
        "units_ordered": "units ordered",
        "demand_forecast": "demand forecast",
    }
    return mapping.get(canonical, canonical)


def _dynamic_find_col(df: pd.DataFrame, canonical: str) -> Optional[str]:
    alias_map = _dynamic_alias_map()
    aliases = alias_map.get(canonical, set())
    target_key = _col_key(canonical)
    alias_keys = {_col_key(a) for a in aliases}
    for col in df.columns:
        key = _col_key(col)
        if key == target_key or key in alias_keys:
            return str(col)
    return None


def _dynamic_get_series(df: pd.DataFrame, col: Optional[str]) -> pd.Series:
    if not col or col not in df.columns:
        return pd.Series(index=df.index, dtype="object")
    data = df[col]
    if isinstance(data, pd.DataFrame):
        if data.shape[1] == 0:
            return pd.Series(index=df.index, dtype="object")
        return data.iloc[:, 0]
    return data


def _dynamic_ensure_col(df: pd.DataFrame, canonical: str) -> str:
    existing = _dynamic_find_col(df, canonical)
    if existing:
        return existing
    out_col = _dynamic_output_col(canonical)
    df[out_col] = pd.NA
    return out_col


def _dynamic_ensure_record_ids(df: pd.DataFrame) -> str:
    col = _dynamic_ensure_col(df, "record_id")
    used: set[str] = set()
    next_num = 1
    values = _dynamic_get_series(df, col).astype("string")
    for idx in df.index:
        cell = values.loc[idx]
        if isinstance(cell, pd.Series):
            non_null = cell.dropna()
            cell = non_null.iloc[0] if not non_null.empty else ""
        if cell is None:
            raw = ""
        else:
            raw = str(cell).strip()
            if raw.lower() == "nan":
                raw = ""
        if raw and raw.lower() != "nan" and raw not in used:
            used.add(raw)
            continue
        while True:
            cand = f"rec-{next_num:06d}"
            next_num += 1
            if cand not in used:
                df.at[idx, col] = cand
                used.add(cand)
                break
    return col


def _load_dynamic_dataset() -> pd.DataFrame:
    if not CSV_PATH.exists():
        raise HTTPException(status_code=404, detail="Dataset file not found")
    try:
        df = pd.read_csv(CSV_PATH)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read dataset: {exc}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    _dynamic_ensure_record_ids(df)
    return df


def _save_dynamic_dataset(df: pd.DataFrame) -> None:
    df.to_csv(CSV_PATH, index=False)
    _invalidate_engine_cache()


def _canonical_dynamic_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    alias_map = _dynamic_alias_map()
    key_to_canonical: Dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        key_to_canonical[_col_key(canonical)] = canonical
        for alias in aliases:
            key_to_canonical[_col_key(alias)] = canonical
    for key, value in (payload or {}).items():
        canonical = key_to_canonical.get(_col_key(key))
        if canonical:
            out[canonical] = value
    return out


def _dynamic_item_from_row(row: pd.Series, col_map: Dict[str, Optional[str]]) -> Dict[str, Any]:
    def _grab(name: str) -> Any:
        col = col_map.get(name)
        if not col:
            return None
        value = row.get(col, None)
        if isinstance(value, pd.DataFrame):
            if value.empty:
                return None
            value = value.iloc[0, 0]
        if isinstance(value, pd.Series):
            if value.empty:
                return None
            non_null = value.dropna()
            if non_null.empty:
                return None
            value = non_null.iloc[0]
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        return value

    date_val = _grab("date")
    date_iso = ""
    if date_val is not None:
        parsed_series = parse_date_series(pd.Series([date_val]))
        parsed = parsed_series.iloc[0] if len(parsed_series) else pd.NaT
        if not pd.isna(parsed):
            date_iso = pd.Timestamp(parsed).strftime("%Y-%m-%d")
        else:
            date_iso = str(date_val)

    return {
        "record_id": str(_grab("record_id") or "").strip(),
        "date": date_iso,
        "product_id": str(_grab("product_id") or "").strip(),
        "category": str(_grab("category") or "").strip(),
        "units_sold": float(pd.to_numeric(_grab("units_sold"), errors="coerce") or 0.0),
        "price": None if _grab("price") is None else float(pd.to_numeric(_grab("price"), errors="coerce") or 0.0),
        "discount": None if _grab("discount") is None else float(pd.to_numeric(_grab("discount"), errors="coerce") or 0.0),
        "inventory_level": None if _grab("inventory_level") is None else float(pd.to_numeric(_grab("inventory_level"), errors="coerce") or 0.0),
        "units_ordered": None if _grab("units_ordered") is None else float(pd.to_numeric(_grab("units_ordered"), errors="coerce") or 0.0),
        "demand_forecast": None if _grab("demand_forecast") is None else float(pd.to_numeric(_grab("demand_forecast"), errors="coerce") or 0.0),
    }


def _filter_dynamic_product(df: pd.DataFrame, product_value: str) -> pd.DataFrame:
    if not product_value:
        return df
    requested = str(product_value).strip().lower()
    cat_col = _dynamic_find_col(df, "category")
    prod_col = _dynamic_find_col(df, "product_id")
    mask = pd.Series(False, index=df.index, dtype=bool)
    if cat_col:
        cat_values = _dynamic_get_series(df, cat_col).astype("string").fillna("").str.strip().str.lower()
        mask = mask | (cat_values == requested)
    if prod_col:
        prod_values = _dynamic_get_series(df, prod_col).astype("string").fillna("").str.strip().str.lower()
        mask = mask | (prod_values == requested)
    mask = mask.fillna(False).astype(bool)
    return df.loc[mask].copy()


def _normalize_platform_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if "amazon" in lower:
        return "Amazon"
    if "flipkart" in lower:
        return "Flipkart"
    return raw


def _filter_dynamic_platform(df: pd.DataFrame, platform_value: str) -> Tuple[pd.DataFrame, bool]:
    requested = _normalize_platform_name(platform_value)
    if not requested:
        return df.copy(), False

    platform_col = _dynamic_find_col(df, "platform")
    if platform_col:
        normalized = _dynamic_get_series(df, platform_col).map(_normalize_platform_name)
        mask = normalized == requested
        matched = df.loc[mask].copy()
        if not matched.empty:
            return matched, False

    price_col = _dynamic_find_col(df, "price")
    if not price_col:
        return df.iloc[0:0].copy(), False

    prices = pd.to_numeric(_dynamic_get_series(df, price_col), errors="coerce")
    valid_prices = prices.dropna().sort_values()
    if valid_prices.empty:
        return df.iloc[0:0].copy(), False

    median_price = float(valid_prices.iloc[len(valid_prices) // 2])
    if requested == "Amazon":
        mask = prices.notna() & (prices <= median_price)
    elif requested == "Flipkart":
        mask = prices.notna() & (prices > median_price)
    else:
        return df.iloc[0:0].copy(), False
    return df.loc[mask].copy(), True


def _date_filtered(df: pd.DataFrame, from_date: str, to_date: str) -> pd.DataFrame:
    date_col = _dynamic_find_col(df, "date")
    if not date_col:
        return df
    if not from_date or not to_date:
        return df
    start_ts = pd.to_datetime(from_date, errors="coerce")
    end_ts = pd.to_datetime(to_date, errors="coerce")
    if pd.isna(start_ts) or pd.isna(end_ts):
        raise HTTPException(status_code=400, detail="Invalid from/to date values")
    dates = parse_date_series(_dynamic_get_series(df, date_col))
    mask = dates.notna() & (dates >= start_ts) & (dates <= end_ts)
    return df.loc[mask].copy()


def _compare_metrics(df: pd.DataFrame) -> Dict[str, float]:
    rows = float(len(df.index))

    def _avg(canonical: str) -> float:
        col = _dynamic_find_col(df, canonical)
        if not col or rows <= 0:
            return 0.0
        vals = pd.to_numeric(_dynamic_get_series(df, col), errors="coerce")
        return float(vals.mean()) if vals.notna().any() else 0.0

    def _sum(canonical: str) -> float:
        col = _dynamic_find_col(df, canonical)
        if not col:
            return 0.0
        vals = pd.to_numeric(_dynamic_get_series(df, col), errors="coerce")
        return float(vals.sum()) if vals.notna().any() else 0.0

    units_col = _dynamic_find_col(df, "units_sold")
    price_col = _dynamic_find_col(df, "price")
    discount_col = _dynamic_find_col(df, "discount")
    units = pd.to_numeric(_dynamic_get_series(df, units_col), errors="coerce") if units_col else pd.Series(dtype="float64")
    prices = pd.to_numeric(_dynamic_get_series(df, price_col), errors="coerce") if price_col else pd.Series(dtype="float64")
    discounts = pd.to_numeric(_dynamic_get_series(df, discount_col), errors="coerce") if discount_col else pd.Series(dtype="float64")
    valid_units = units.dropna()
    demand_stability = 0.0
    if not valid_units.empty:
        mean_units = float(valid_units.mean())
        std_units = float(valid_units.std(ddof=0))
        if mean_units > 0:
            demand_stability = max(0.0, 100.0 - ((std_units / mean_units) * 100.0))

    estimated_revenue = 0.0
    if units_col and price_col:
        revenue_series = (units.fillna(0.0) * prices.fillna(0.0)).astype(float)
        estimated_revenue = float(revenue_series.sum())

    total_units = _sum("units_sold")
    estimated_unit_cost = 42.0
    estimated_cost = total_units * estimated_unit_cost
    estimated_profit = estimated_revenue - estimated_cost
    estimated_profit_margin = (estimated_profit / estimated_revenue * 100.0) if estimated_revenue > 0 else 0.0

    return {
        "rows": rows,
        "total_units_sold": _sum("units_sold"),
        "avg_units_sold": _avg("units_sold"),
        "avg_price": _avg("price"),
        "avg_discount": _avg("discount"),
        "avg_inventory_level": _avg("inventory_level"),
        "avg_units_ordered": _avg("units_ordered"),
        "avg_demand_forecast": _avg("demand_forecast"),
        "demand_stability": demand_stability,
        "estimated_revenue": estimated_revenue,
        "estimated_profit": estimated_profit,
        "estimated_profit_margin": estimated_profit_margin,
        "estimated_cost": estimated_cost,
    }


def _score_metric_pair(
    left_val: float,
    right_val: float,
    higher_is_better: bool = True,
) -> Tuple[float, float]:
    left = float(left_val)
    right = float(right_val)
    scale = max(abs(left), abs(right), 1e-9)
    if not higher_is_better:
        left = -left
        right = -right
    left_norm = (left / scale + 1.0) / 2.0
    right_norm = (right / scale + 1.0) / 2.0
    return max(0.0, left_norm), max(0.0, right_norm)


def _build_compare_recommendation(
    platform_1: str,
    platform_2: str,
    left_metrics: Dict[str, float],
    right_metrics: Dict[str, float],
) -> Dict[str, Any]:
    weights: List[Tuple[str, float, bool, str]] = [
        ("total_units_sold", 0.30, True, "higher total sales"),
        ("avg_units_sold", 0.18, True, "stronger average demand"),
        ("demand_stability", 0.18, True, "better demand stability"),
        ("estimated_revenue", 0.16, True, "higher estimated revenue"),
        ("estimated_profit_margin", 0.10, True, "better estimated profit margin"),
        ("avg_price", 0.05, True, "stronger pricing"),
        ("avg_discount", 0.03, False, "lower discount dependence"),
    ]

    left_score = 0.0
    right_score = 0.0
    contributions: List[Tuple[str, float, str]] = []
    for key, weight, higher_is_better, reason in weights:
        left_component, right_component = _score_metric_pair(
            left_metrics.get(key, 0.0),
            right_metrics.get(key, 0.0),
            higher_is_better=higher_is_better,
        )
        left_weighted = left_component * weight
        right_weighted = right_component * weight
        left_score += left_weighted
        right_score += right_weighted
        advantage = left_weighted - right_weighted
        if abs(advantage) > 1e-9:
            winner = platform_1 if advantage > 0 else platform_2
            contributions.append((winner, abs(advantage), reason))

    if left_score > right_score:
        best_platform = platform_1
    elif right_score > left_score:
        best_platform = platform_2
    else:
        best_platform = "Tie"

    reasons = [reason for winner, _, reason in sorted(contributions, key=lambda item: item[1], reverse=True) if winner == best_platform][:3]
    if best_platform == "Tie":
        summary = f"Both platforms are evenly matched on the current weighted score."
    elif reasons:
        summary = f"{best_platform} is recommended due to {', '.join(reasons[:2])}."
    else:
        summary = f"{best_platform} is recommended on the weighted comparison score."

    return {
        "best_platform": best_platform,
        "scores": {
            platform_1: round(left_score * 100.0, 2),
            platform_2: round(right_score * 100.0, 2),
        },
        "summary": summary,
        "weighted_basis": [
            {"metric": key, "weight": weight, "higher_is_better": higher_is_better}
            for key, weight, higher_is_better, _ in weights
        ],
    }


def _safe_mean_text(df: pd.DataFrame, canonical: str, label: str) -> Optional[str]:
    col = _dynamic_find_col(df, canonical)
    if not col:
        return None
    vals = pd.to_numeric(_dynamic_get_series(df, col), errors="coerce").dropna()
    if vals.empty:
        return None
    return f"{label}: {vals.mean():.2f}"


def _looks_like_product_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]\d{2,}", str(value).strip()))


def _resolve_selector(df: pd.DataFrame, category: str) -> tuple[str, str]:
    # Resolve user input against product/category and geography columns with case-insensitive matching.
    requested = _norm_key(category)
    if not requested:
        raise ValueError("selection cannot be empty")

    candidate_cols: List[str] = []
    prefers_product_id = _looks_like_product_id(category)
    preferred_base_cols = [PRODUCT_COL, "product id"] if not prefers_product_id else ["product id", PRODUCT_COL]
    for col in preferred_base_cols:
        if col in df.columns and col not in candidate_cols:
            candidate_cols.append(col)
    for col in ("city", "state", "region", "country", "store id", "store_name"):
        if col in df.columns and col not in candidate_cols:
            candidate_cols.append(col)

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

    raise ValueError(f"No rows found for selection {category}")


def _resolve_category_value(df: pd.DataFrame, category: str) -> str:
    requested = _norm_key(category)
    if not requested:
        raise ValueError("category cannot be empty")

    candidate_cols: List[str] = []
    preferred_base_cols = [PRODUCT_COL, "product id"] if not _looks_like_product_id(category) else ["product id", PRODUCT_COL]
    for col in preferred_base_cols:
        if col in df.columns and col not in candidate_cols:
            candidate_cols.append(col)

    for col in candidate_cols:
        values = df[col].dropna().astype(str).map(str.strip)
        if values.empty:
            continue
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


def _resolve_city_value(df: pd.DataFrame, city: Optional[str]) -> Optional[str]:
    requested = _norm_key(city or "")
    if not requested:
        return None
    if "city" not in df.columns:
        raise ValueError("City data is not available in the dataset")
    values = df["city"].dropna().astype(str).map(str.strip)
    exact = values[values == str(city)]
    if not exact.empty:
        return str(exact.iloc[0])
    lower_map: Dict[str, str] = {}
    for v in values:
        k = v.lower()
        if k not in lower_map:
            lower_map[k] = v
    if requested in lower_map:
        return lower_map[requested]
    raise ValueError(f"No rows found for city {city}")


def _store_scope_column(df: pd.DataFrame) -> Optional[str]:
    for col in ("store_name", "store id"):
        if col in df.columns:
            return col
    return None


def _resolve_store_value(df: pd.DataFrame, store: Optional[str]) -> Optional[str]:
    requested = _norm_key(store or "")
    if not requested:
        return None
    store_col = _store_scope_column(df)
    if store_col is None:
        raise ValueError("Store data is not available in the dataset")
    values = df[store_col].dropna().astype(str).map(str.strip)
    exact = values[values == str(store)]
    if not exact.empty:
        return str(exact.iloc[0])
    lower_map: Dict[str, str] = {}
    for v in values:
        k = v.lower()
        if k not in lower_map:
            lower_map[k] = v
    if requested in lower_map:
        return lower_map[requested]
    raise ValueError(f"No rows found for store {store}")


def _build_valid_scope_maps(
    df: pd.DataFrame,
) -> tuple[
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[str]],
]:
    if "city" not in df.columns or PRODUCT_COL not in df.columns or TARGET_COL not in df.columns:
        return {}, {}, {}, {}, {}
    store_col = _store_scope_column(df)

    cols = [PRODUCT_COL, "city", TARGET_COL]
    if store_col:
        cols.append(store_col)
    pair_df = df[cols].dropna(subset=[PRODUCT_COL, "city", TARGET_COL]).copy()
    if pair_df.empty:
        return {}, {}, {}, {}, {}

    pair_df[PRODUCT_COL] = pair_df[PRODUCT_COL].astype(str).map(str.strip)
    pair_df["city"] = pair_df["city"].astype(str).map(str.strip)
    pair_df[TARGET_COL] = pd.to_numeric(pair_df[TARGET_COL], errors="coerce").fillna(0.0)
    if store_col:
        pair_df[store_col] = pair_df[store_col].astype(str).map(str.strip)
    pair_df = pair_df[
        pair_df[PRODUCT_COL].ne("")
        & pair_df["city"].ne("")
    ]
    if pair_df.empty:
        return {}, {}, {}, {}, {}

    positive_pairs = (
        pair_df.groupby([PRODUCT_COL, "city"], as_index=False)[TARGET_COL]
        .sum()
    )
    positive_pairs = positive_pairs[positive_pairs[TARGET_COL] > 0].copy()
    if positive_pairs.empty:
        return {}, {}, {}, {}, {}

    category_city_map = {
        str(category): sorted({str(city) for city in values["city"].tolist()})
        for category, values in positive_pairs.groupby(PRODUCT_COL)
    }
    city_category_map = {
        str(city): sorted({str(category) for category in values[PRODUCT_COL].tolist()})
        for city, values in positive_pairs.groupby("city")
    }
    category_city_store_map: Dict[str, List[str]] = {}
    city_store_map: Dict[str, List[str]] = {}
    city_store_category_map: Dict[str, List[str]] = {}
    if store_col:
        store_pairs = pair_df[
            pair_df[store_col].ne("")
        ].groupby([PRODUCT_COL, "city", store_col], as_index=False)[TARGET_COL].sum()
        store_pairs = store_pairs[store_pairs[TARGET_COL] > 0].copy()
        if not store_pairs.empty:
            category_city_store_map = {
                f"{str(category)}|||{str(city)}": sorted({str(store) for store in values[store_col].tolist()})
                for (category, city), values in store_pairs.groupby([PRODUCT_COL, "city"])
            }
            city_store_map = {
                str(city): sorted({str(store) for store in values[store_col].tolist()})
                for city, values in store_pairs.groupby("city")
            }
            city_store_category_map = {
                f"{str(city)}|||{str(store)}": sorted({str(category) for category in values[PRODUCT_COL].tolist()})
                for (city, store), values in store_pairs.groupby(["city", store_col])
            }
    return category_city_map, city_category_map, category_city_store_map, city_store_map, city_store_category_map


def _get_cached_valid_scope_maps(
    df: pd.DataFrame,
) -> tuple[
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[str]],
]:
    if _engine_cache.scope_maps is None:
        _engine_cache.scope_maps = _build_valid_scope_maps(df)
    return _engine_cache.scope_maps


def _build_scope_payload(df: pd.DataFrame) -> Dict[str, Any]:
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
    cities: List[str] = []
    if "city" in df.columns:
        cvalues = df["city"].dropna().astype(str).map(str.strip)
        cities = sorted({v for v in cvalues if v})
    regions: List[str] = []
    if "region" in df.columns:
        rvalues = df["region"].dropna().astype(str).map(str.strip)
        regions = sorted({v for v in rvalues if v})
    states: List[str] = []
    if "state" in df.columns:
        svalues = df["state"].dropna().astype(str).map(str.strip)
        states = sorted({v for v in svalues if v})
    category_city_map, city_category_map, category_city_store_map, city_store_map, city_store_category_map = _get_cached_valid_scope_maps(df)
    return {
        "categories": categories,
        "product_ids": product_ids,
        "cities": cities,
        "regions": regions,
        "states": states,
        "category_city_map": category_city_map,
        "city_category_map": city_category_map,
        "category_city_store_map": category_city_store_map,
        "city_store_map": city_store_map,
        "city_store_category_map": city_store_category_map,
        "dataset_path": str(CSV_PATH.name),
    }


def _get_cached_scope_payload(df: pd.DataFrame) -> Dict[str, Any]:
    if _engine_cache.scope_payload is None:
        _engine_cache.scope_payload = _build_scope_payload(df)
    return _engine_cache.scope_payload


def _apply_optional_date_filter(
    df: pd.DataFrame,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> pd.DataFrame:
    scoped = df.copy()
    if from_date:
        parsed_from = pd.to_datetime(from_date, errors="coerce")
        if pd.isna(parsed_from):
            raise ValueError("from_date must be a valid date")
        scoped = scoped[scoped[DATE_COL] >= parsed_from.normalize()].copy()
    if to_date:
        parsed_to = pd.to_datetime(to_date, errors="coerce")
        if pd.isna(parsed_to):
            raise ValueError("to_date must be a valid date")
        scoped = scoped[scoped[DATE_COL] <= parsed_to.normalize()].copy()
    return scoped


def _scope_df_for_forecast(
    df: pd.DataFrame,
    category: str,
    city: Optional[str] = None,
    store: Optional[str] = None,
) -> tuple[pd.DataFrame, str, Optional[str], Optional[str]]:
    resolved_category = _resolve_category_value(df, category)
    resolved_city = _resolve_city_value(df, city)
    resolved_store = _resolve_store_value(df, store)
    if resolved_city:
        category_city_map, _, category_city_store_map, _, _ = _get_cached_valid_scope_maps(df)
        valid_cities = category_city_map.get(resolved_category, [])
        if valid_cities and resolved_city not in valid_cities:
            raise ValueError(
                f"Category {resolved_category} is not available for city {resolved_city}. "
                f"Valid cities: {', '.join(valid_cities)}"
            )
        if resolved_store:
            valid_stores = category_city_store_map.get(f"{resolved_category}|||{resolved_city}", [])
            if valid_stores and resolved_store not in valid_stores:
                raise ValueError(
                    f"Store {resolved_store} is not available for category {resolved_category} in city {resolved_city}. "
                    f"Valid stores: {', '.join(valid_stores)}"
                )

    scoped_df = df[df[PRODUCT_COL].astype(str).str.strip() == resolved_category].copy()
    if resolved_city:
        scoped_df = scoped_df[scoped_df["city"].astype(str).str.strip() == resolved_city].copy()
    if resolved_store:
        store_col = _store_scope_column(scoped_df)
        if store_col:
            scoped_df = scoped_df[scoped_df[store_col].astype(str).str.strip() == resolved_store].copy()
    if scoped_df.empty:
        city_msg = f" in city {resolved_city}" if resolved_city else ""
        store_msg = f" for store {resolved_store}" if resolved_store else ""
        raise ValueError(f"No rows found for category {resolved_category}{city_msg}{store_msg}")
    return scoped_df, resolved_category, resolved_city, resolved_store


def _city_store_summary(
    df: pd.DataFrame,
    city: str,
    category: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_city = _resolve_city_value(df, city)
    scoped = df[df["city"].astype(str).str.strip() == resolved_city].copy()
    resolved_category: Optional[str] = None
    if category and str(category).strip():
        resolved_category = _resolve_category_value(scoped, category)
        scoped = scoped[scoped[PRODUCT_COL].astype(str).str.strip() == resolved_category].copy()
    scoped = _apply_optional_date_filter(scoped, from_date=from_date, to_date=to_date)
    store_col = _store_scope_column(scoped)
    if store_col is None:
        raise ValueError("Store data is not available in the dataset")
    if scoped.empty:
        return {
            "city": resolved_city,
            "category": resolved_category,
            "from_date": from_date or "",
            "to_date": to_date or "",
            "stores": [],
            "store_count": 0,
        }

    scoped["_store_name"] = scoped[store_col].astype(str).str.strip()
    scoped = scoped[scoped["_store_name"].ne("")].copy()
    if scoped.empty:
        return {
            "city": resolved_city,
            "category": resolved_category,
            "from_date": from_date or "",
            "to_date": to_date or "",
            "stores": [],
            "store_count": 0,
        }

    scoped["_date_sort"] = pd.to_datetime(scoped[DATE_COL], errors="coerce")
    scoped = scoped.sort_values(["_store_name", "_date_sort"])
    grouped = scoped.groupby("_store_name", as_index=False)

    def _latest_numeric(group_df: pd.DataFrame, col: str) -> float:
        if col not in group_df.columns:
            return 0.0
        numeric = pd.to_numeric(group_df[col], errors="coerce")
        valid = numeric.dropna()
        return float(valid.iloc[-1]) if not valid.empty else 0.0

    stores: List[Dict[str, Any]] = []
    for store_name, group_df in grouped:
        latest_row = group_df.iloc[-1]
        stores.append({
            "store": str(store_name),
            "store_id": str(latest_row.get("store id", "") or "").strip(),
            "latest_date": str(latest_row.get(DATE_COL, "") or "").strip(),
            "inventory_level": round(_latest_numeric(group_df, "inventory level"), 2),
            "units_sold": round(float(pd.to_numeric(group_df.get(TARGET_COL), errors="coerce").fillna(0).sum()), 2),
            "units_ordered": round(float(pd.to_numeric(group_df.get("units ordered"), errors="coerce").fillna(0).sum()) if "units ordered" in group_df.columns else 0.0, 2),
            "avg_demand_forecast": round(float(pd.to_numeric(group_df.get("demand forecast"), errors="coerce").dropna().mean()) if "demand forecast" in group_df.columns and pd.to_numeric(group_df.get("demand forecast"), errors="coerce").dropna().size else 0.0, 2),
            "price": round(_latest_numeric(group_df, "price"), 2),
            "discount": round(_latest_numeric(group_df, "discount"), 2),
        })

    stores.sort(key=lambda row: row["store"].lower())
    return {
        "city": resolved_city,
        "category": resolved_category,
        "from_date": from_date or "",
        "to_date": to_date or "",
        "stores": stores,
        "store_count": len(stores),
    }


def _read_csv_mtime_ns() -> int:
    mtimes: List[int] = []
    for path in (CSV_PATH, LIVE_CSV_PATH, LEGACY_LIVE_CSV_PATH):
        try:
            mtimes.append(path.stat().st_mtime_ns)
        except FileNotFoundError:
            continue
    return max(mtimes) if mtimes else -1


def _invalidate_engine_cache() -> None:
    _engine_cache.csv_mtime_ns = -1
    _engine_cache.base_csv_mtime_ns = -1
    _engine_cache.base_df = None
    _engine_cache.df = None
    _engine_cache.scope_payload = None
    _engine_cache.scope_maps = None
    _engine_cache.forecast_payloads.clear()
    _engine_cache.trained_by_category.clear()


def _preferred_live_csv_path() -> Path:
    existing_paths = [path for path in (LIVE_CSV_PATH, LEGACY_LIVE_CSV_PATH) if path.exists()]
    if not existing_paths:
        return LIVE_CSV_PATH
    return max(existing_paths, key=lambda path: path.stat().st_mtime_ns)


def _existing_live_csv_paths() -> List[Path]:
    return sorted(
        [path for path in (LIVE_CSV_PATH, LEGACY_LIVE_CSV_PATH) if path.exists()],
        key=lambda path: path.stat().st_mtime_ns,
    )


def _write_live_sales_dataframe(df: pd.DataFrame) -> None:
    LIVE_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(LIVE_CSV_PATH, index=False)
    df.to_csv(LEGACY_LIVE_CSV_PATH, index=False)


def _load_base_sales_df() -> pd.DataFrame:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Dataset file not found: {CSV_PATH}")
    base_raw = pd.read_csv(CSV_PATH)
    return prepare_sales_data(base_raw)


def _get_cached_base_df() -> pd.DataFrame:
    try:
        base_mtime_ns = CSV_PATH.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Dataset file not found: {CSV_PATH}") from exc

    if _engine_cache.base_df is None or _engine_cache.base_csv_mtime_ns != base_mtime_ns:
        _engine_cache.base_df = _load_base_sales_df()
        _engine_cache.base_csv_mtime_ns = base_mtime_ns
    return _engine_cache.base_df.copy()


def _load_combined_sales_df() -> pd.DataFrame:
    base_df = _get_cached_base_df()

    live_frames: List[pd.DataFrame] = []
    for live_path in _existing_live_csv_paths():
        live_raw = pd.read_csv(live_path)
        live_frames.append(prepare_sales_data(live_raw))

    if live_frames:
        all_cols = list(dict.fromkeys([*base_df.columns.tolist(), *[col for frame in live_frames for col in frame.columns.tolist()]]))
        if not all_cols:
            all_cols = base_df.columns.tolist()
        for col in all_cols:
            if col not in base_df.columns:
                base_df[col] = pd.NA
        aligned_frames = [base_df[all_cols]]
        for live_raw in live_frames:
            working_live = live_raw.copy()
            for col in all_cols:
                if col not in working_live.columns:
                    working_live[col] = pd.NA
            aligned_frames.append(working_live[all_cols])
        combined = pd.concat(aligned_frames, ignore_index=True)
        combined = _dedupe_live_sales_dataframe(combined)
        combined = combined.sort_values(DATE_COL)
    else:
        combined = base_df

    return combined


def _get_cached_df() -> pd.DataFrame:
    mtime_ns = _read_csv_mtime_ns()
    if _engine_cache.df is None or _engine_cache.csv_mtime_ns != mtime_ns:
        _engine_cache.df = _load_combined_sales_df()
        _engine_cache.csv_mtime_ns = mtime_ns
        _engine_cache.trained_by_category.clear()
    return _engine_cache.df


def _get_trained_model(
    df: pd.DataFrame,
    resolved_category: str,
    city: Optional[str] = None,
    store: Optional[str] = None,
) -> Any:
    key = f"{PRODUCT_COL}|{_norm_key(resolved_category)}|{_norm_key(city or '')}|{_norm_key(store or '')}"
    if key in _engine_cache.trained_by_category:
        return _engine_cache.trained_by_category[key]

    scoped_df, _, _, _ = _scope_df_for_forecast(df, resolved_category, city=city, store=store)
    daily = build_daily_series(scoped_df, category=resolved_category, selector_col=PRODUCT_COL)
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


def _dedupe_live_sales_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    dedupe_cols: List[str] = []

    if DATE_COL in working.columns:
        parsed_dates = parse_date_series(working[DATE_COL])
        working["__dedupe_date"] = parsed_dates.dt.strftime("%Y-%m-%d").fillna(
            working[DATE_COL].astype("string").str.strip().str.lower()
        )
        dedupe_cols.append("__dedupe_date")

    for col in ("city", _store_scope_column(working), PRODUCT_COL, "product id"):
        if not col or col not in working.columns:
            continue
        helper_col = f"__dedupe_{col.replace(' ', '_')}"
        working[helper_col] = working[col].astype("string").str.strip().str.lower()
        dedupe_cols.append(helper_col)

    dedupe_cols = [c for c in dedupe_cols if c in working.columns]
    if len(dedupe_cols) < 2:
        return df

    deduped = working.drop_duplicates(subset=dedupe_cols, keep="last").copy()
    helper_cols = [c for c in deduped.columns if c.startswith("__dedupe_")]
    if helper_cols:
        deduped = deduped.drop(columns=helper_cols)
    return deduped


def _append_sales_rows(records: List[SalesRecord], persist: bool = True) -> pd.DataFrame:
    base_existing = pd.read_csv(CSV_PATH)
    base_existing.columns = base_existing.columns.str.strip().str.lower()
    live_paths = _existing_live_csv_paths()
    if live_paths:
        existing_frames: List[pd.DataFrame] = []
        for live_path in live_paths:
            current = pd.read_csv(live_path)
            current.columns = current.columns.str.strip().str.lower()
            existing_frames.append(current)
        existing = pd.concat(existing_frames, ignore_index=True)
        existing = _dedupe_live_sales_dataframe(existing)
    else:
        existing = pd.DataFrame(columns=base_existing.columns)

    new_rows = pd.DataFrame([_normalize_payload_record(r) for r in records])
    if new_rows.empty:
        return _load_combined_sales_df()

    if DATE_COL not in new_rows.columns or PRODUCT_COL not in new_rows.columns or TARGET_COL not in new_rows.columns:
        raise HTTPException(
            status_code=400,
            detail="Each record requires date, units_sold, and category (or product_id/product id).",
        )

    if PRODUCT_COL in new_rows.columns:
        existing_values = pd.concat(
            [
                base_existing[PRODUCT_COL].dropna().astype(str).map(str.strip) if PRODUCT_COL in base_existing.columns else pd.Series(dtype="string"),
                existing[PRODUCT_COL].dropna().astype(str).map(str.strip) if PRODUCT_COL in existing.columns else pd.Series(dtype="string"),
            ],
            ignore_index=True,
        )
        canonical_map: Dict[str, str] = {}
        for v in existing_values:
            lk = v.lower()
            if lk not in canonical_map:
                canonical_map[lk] = v
        normalized = new_rows[PRODUCT_COL].astype("string").str.strip()
        new_rows[PRODUCT_COL] = normalized.map(
            lambda c: canonical_map.get(c.lower(), c) if isinstance(c, str) and c else c
        )

    reference_cols = list(dict.fromkeys([*base_existing.columns.tolist(), *existing.columns.tolist()]))

    for col in reference_cols:
        if col not in new_rows.columns:
            new_rows[col] = pd.NA

    for col in new_rows.columns:
        if col not in existing.columns:
            existing[col] = pd.NA

    live_combined = pd.concat([existing[reference_cols], new_rows[reference_cols]], ignore_index=True)
    live_combined = _dedupe_live_sales_dataframe(live_combined)
    if persist:
        _write_live_sales_dataframe(live_combined)
    return _load_combined_sales_df()


def _live_dataset_row_count() -> int:
    live_paths = _existing_live_csv_paths()
    if not live_paths:
        return 0
    try:
        frames = [pd.read_csv(path) for path in live_paths]
        combined = pd.concat(frames, ignore_index=True)
        combined = _dedupe_live_sales_dataframe(combined)
        return int(len(combined.index))
    except Exception:
        return 0


def _ensure_live_data_path() -> None:
    LIVE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if LEGACY_LIVE_CSV_PATH.exists() and not LIVE_CSV_PATH.exists():
        shutil.copy2(LEGACY_LIVE_CSV_PATH, LIVE_CSV_PATH)


_ensure_live_data_path()


def _clear_live_sales_data() -> Dict[str, Any]:
    _stop_live_simulator()
    _ensure_live_data_path()
    if LIVE_CSV_PATH.exists():
        LIVE_CSV_PATH.unlink(missing_ok=True)
    if LEGACY_LIVE_CSV_PATH.exists():
        LEGACY_LIVE_CSV_PATH.unlink(missing_ok=True)
    _invalidate_engine_cache()
    df = _get_cached_df()
    return _live_simulation_status_payload(df)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_float_value(value: Any, default: float = 0.0) -> float:
    try:
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.isna(numeric):
            return float(default)
        return float(numeric)
    except Exception:
        return float(default)


def _season_for_month(month: int) -> str:
    if month in {12, 1, 2}:
        return "Winter"
    if month in {3, 4, 5}:
        return "Spring"
    if month in {6, 7, 8}:
        return "Monsoon"
    return "Autumn"


def _latest_available_data_date(df: Optional[pd.DataFrame] = None) -> str:
    working = df
    if working is None:
        working = _get_cached_df()
    if DATE_COL not in working.columns or working.empty:
        return ""
    parsed = pd.to_datetime(working[DATE_COL], errors="coerce")
    parsed = parsed.dropna()
    if parsed.empty:
        return ""
    return parsed.max().date().isoformat()


def _live_simulation_status_payload(df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    with _live_simulator_lock:
        state = dict(_live_simulator_state)
    working = df
    live_path = _preferred_live_csv_path()
    if working is None:
        try:
            working = _get_cached_df()
        except Exception:
            working = None
    state.update(
        {
            "base_dataset_path": str(CSV_PATH.name),
            "live_dataset_path": str(live_path.relative_to(PROJECT_DIR)),
            "live_dataset_exists": live_path.exists(),
            "live_dataset_rows": _live_dataset_row_count(),
            "latest_data_date": _latest_available_data_date(working),
        }
    )
    return state


def _build_simulated_sales_records(df: pd.DataFrame, batch_size: int) -> List[SalesRecord]:
    working = df.copy()
    working[DATE_COL] = parse_date_series(working[DATE_COL])
    working = working.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET_COL]).sort_values(DATE_COL)
    if working.empty:
        raise ValueError("No valid rows available for live simulation")

    store_col = _store_scope_column(working)
    group_cols = [PRODUCT_COL]
    if "city" in working.columns:
        group_cols.append("city")
    if store_col and store_col in working.columns:
        group_cols.append(store_col)

    latest_rows = working.groupby(group_cols, as_index=False, dropna=False).tail(1).copy()
    if latest_rows.empty:
        raise ValueError("Unable to create simulation seeds from the dataset")

    requested_n = max(1, int(batch_size))
    random_seed = random.randint(1, 10_000_000)
    sampled_parts: List[pd.DataFrame] = []

    if "category" in latest_rows.columns:
        per_category = latest_rows.groupby("category", dropna=False, group_keys=False).sample(
            n=1,
            replace=False,
            random_state=random_seed,
        )
        sampled_parts.append(per_category)
        requested_n = max(requested_n, len(per_category.index))
        remaining = latest_rows.drop(index=per_category.index, errors="ignore")
    else:
        remaining = latest_rows

    already_selected = sum(len(part.index) for part in sampled_parts)
    extra_needed = max(0, requested_n - already_selected)
    if extra_needed > 0:
        source = remaining if not remaining.empty else latest_rows
        sampled_parts.append(
            source.sample(
                n=extra_needed,
                replace=len(source.index) < extra_needed,
                random_state=random_seed + 1,
            )
        )

    sampled = pd.concat(sampled_parts, ignore_index=False)

    records: List[SalesRecord] = []
    seed_prefix = int(time.time())
    for idx, (_, row) in enumerate(sampled.iterrows(), start=1):
        next_date = pd.to_datetime(row[DATE_COL], errors="coerce")
        if pd.isna(next_date):
            next_date = pd.Timestamp.utcnow().normalize()
        else:
            next_date = next_date.normalize() + pd.Timedelta(days=1)

        units_sold_prev = max(1.0, _safe_float_value(row.get(TARGET_COL), 1.0))
        units_ordered_prev = max(units_sold_prev, _safe_float_value(row.get("units ordered"), units_sold_prev * 1.25))
        inventory_prev = max(units_sold_prev * 2.0, _safe_float_value(row.get("inventory level"), units_sold_prev * 2.0))
        price_prev = max(1.0, _safe_float_value(row.get("price"), 50.0))
        discount_prev = max(0.0, _safe_float_value(row.get("discount"), 5.0))

        weekday_factor = 1.08 if int(next_date.dayofweek) in {4, 5, 6} else 0.97
        promo_flag = 1.0 if random.random() < 0.22 else 0.0
        promo_lift = 1.14 if promo_flag else 1.0
        noise_factor = random.uniform(0.92, 1.14)

        units_sold = max(0.0, round(units_sold_prev * weekday_factor * promo_lift * noise_factor, 3))
        units_ordered = max(units_sold, round(units_ordered_prev * random.uniform(0.95, 1.12), 3))
        inventory_level = max(0.0, round(inventory_prev - units_sold + (units_ordered * random.uniform(0.30, 0.55)), 3))
        price = round(price_prev * random.uniform(0.985, 1.025), 2)
        discount = round(min(45.0, max(0.0, discount_prev + random.uniform(-2.0, 2.5))), 2)
        competitor_pricing = round(max(1.0, price * random.uniform(0.94, 1.05)), 2)
        demand_forecast = round(max(0.0, units_sold * random.uniform(1.01, 1.08)), 2)

        payload = {
            "date": next_date.strftime("%Y-%m-%d"),
            "units_sold": units_sold,
            "category": str(row.get("category") or row.get(PRODUCT_COL) or "").strip(),
            "product_id": str(row.get("product id") or "").strip() or None,
            "store id": row.get("store id"),
            "location": row.get("location"),
            "inventory level": inventory_level,
            "units ordered": units_ordered,
            "demand forecast": demand_forecast,
            "price": price,
            "discount": discount,
            "weather condition": row.get("weather condition") or random.choice(["Sunny", "Cloudy", "Rainy"]),
            "holiday/promotion": promo_flag,
            "competitor pricing": competitor_pricing,
            "seasonality": _season_for_month(int(next_date.month)),
            "record_id": f"live-{seed_prefix}-{idx:03d}",
            "region": row.get("region"),
            "city": row.get("city"),
            "state": row.get("state"),
            "country": row.get("country"),
            "store_name": row.get("store_name"),
        }
        payload = {k: v for k, v in payload.items() if v is not None and str(v).strip() != ""}
        records.append(SalesRecord(**payload))

    return records


def _run_live_simulation_tick(batch_size: int, horizon: int) -> Dict[str, Any]:
    with _engine_lock:
        combined = _get_cached_df()
        records = _build_simulated_sales_records(combined, batch_size=batch_size)
        _append_sales_rows(records, persist=True)
        _invalidate_engine_cache()
        refreshed = _get_cached_df()

    generated_categories = sorted(
        {
            str(record.category or record.product_id or "").strip()
            for record in records
            if str(record.category or record.product_id or "").strip()
        }
    )

    with _live_simulator_lock:
        _live_simulator_state["last_tick_at"] = _utc_now_iso()
        _live_simulator_state["tick_count"] = int(_live_simulator_state.get("tick_count", 0)) + 1
        _live_simulator_state["last_generated_count"] = len(records)
        _live_simulator_state["last_generated_categories"] = generated_categories
        _live_simulator_state["last_error"] = ""
        state = dict(_live_simulator_state)

    return {
        "ok": True,
        "message": "Live simulation tick completed",
        "generated_records": len(records),
        "generated_categories": generated_categories,
        "horizon": horizon,
        "status": _live_simulation_status_payload(refreshed),
        "state": state,
    }


def _live_simulation_loop() -> None:
    while not _live_simulator_stop_event.is_set():
        with _live_simulator_lock:
            interval_seconds = float(_live_simulator_state.get("interval_seconds", 8))
            batch_size = int(_live_simulator_state.get("batch_size", 4))
            horizon = int(_live_simulator_state.get("horizon", DEFAULT_HORIZON))
        try:
            _run_live_simulation_tick(batch_size=batch_size, horizon=horizon)
        except Exception as exc:
            with _live_simulator_lock:
                _live_simulator_state["last_error"] = str(exc)
                _live_simulator_state["last_tick_at"] = _utc_now_iso()
        if _live_simulator_stop_event.wait(max(0.5, interval_seconds)):
            break

    with _live_simulator_lock:
        _live_simulator_state["running"] = False


def _start_live_simulator(interval_seconds: float, batch_size: int, horizon: int) -> Dict[str, Any]:
    global _live_simulator_thread
    safe_interval = max(1.0, float(interval_seconds))
    safe_batch_size = max(1, min(int(batch_size), 25))
    safe_horizon = max(1, min(int(horizon), 30))

    with _live_simulator_lock:
        already_running = bool(_live_simulator_state.get("running"))
        _live_simulator_state["interval_seconds"] = safe_interval
        _live_simulator_state["batch_size"] = safe_batch_size
        _live_simulator_state["horizon"] = safe_horizon
        if already_running:
            return _live_simulation_status_payload()
        _live_simulator_state["running"] = True
        _live_simulator_state["started_at"] = _utc_now_iso()
        _live_simulator_state["last_error"] = ""

    _live_simulator_stop_event.clear()
    _live_simulator_thread = threading.Thread(
        target=_live_simulation_loop,
        name="demandiq-live-simulator",
        daemon=True,
    )
    _live_simulator_thread.start()
    return _live_simulation_status_payload()


def _stop_live_simulator() -> Dict[str, Any]:
    global _live_simulator_thread
    _live_simulator_stop_event.set()
    thread = _live_simulator_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    _live_simulator_thread = None
    with _live_simulator_lock:
        _live_simulator_state["running"] = False
    return _live_simulation_status_payload()


def _forecast_for_category(
    df: pd.DataFrame,
    category: str,
    horizon: int,
    city: Optional[str] = None,
    store: Optional[str] = None,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    _, resolved_category, resolved_city, resolved_store = _scope_df_for_forecast(df, category, city=city, store=store)
    trained = _get_trained_model(df, resolved_category, city=resolved_city, store=resolved_store)
    forecast_df = forecast_next_days(trained, horizon=horizon, anchor_date=anchor_date)
    return forecast_df.to_dict(orient="records")


def _fallback_forecast_for_category(
    df: pd.DataFrame,
    category: str,
    horizon: int,
    city: Optional[str] = None,
    store: Optional[str] = None,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    category_rows, resolved_category, resolved_city, resolved_store = _scope_df_for_forecast(df, category, city=city, store=store)
    category_rows[DATE_COL] = parse_date_series(category_rows[DATE_COL])
    category_rows = category_rows.dropna(subset=[DATE_COL, TARGET_COL]).sort_values(DATE_COL)
    if anchor_date is not None:
        category_rows = category_rows[category_rows[DATE_COL] <= anchor_date]
    if category_rows.empty:
        city_msg = f" in city {resolved_city}" if resolved_city else ""
        store_msg = f" for store {resolved_store}" if resolved_store else ""
        raise ValueError(f"No rows found for category {resolved_category}{city_msg}{store_msg}")

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
    city: Optional[str] = None,
    store: Optional[str] = None,
    lookback_days: int = 60,
    anchor_date: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    category_rows, _, _, _ = _scope_df_for_forecast(df, category, city=city, store=store)
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


def _forecast_cache_key(
    *,
    category: str,
    city: Optional[str],
    store: Optional[str],
    horizon: int,
    anchor_date: Optional[str],
    history_lookback_days: int,
) -> str:
    return json.dumps(
        {
            "category": str(category or "").strip(),
            "city": str(city or "").strip(),
            "store": str(store or "").strip(),
            "horizon": int(horizon),
            "anchor_date": str(anchor_date or "").strip(),
            "history_lookback_days": int(history_lookback_days),
            "csv_mtime_ns": int(_engine_cache.csv_mtime_ns),
        },
        sort_keys=True,
    )


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=307)


@app.get("/login", include_in_schema=False)
def login(request: Request) -> Response:
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
    html = FRONTEND_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content=html, headers=NO_CACHE_HEADERS)


@app.get("/dashboard/results", include_in_schema=False)
def dashboard_results(request: Request) -> Response:
    if not _current_user_from_request(request):
        return RedirectResponse(url="/login", status_code=307)
    if not FRONTEND_RESULTS_HTML_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dashboard results file not found at {FRONTEND_RESULTS_HTML_PATH}",
        )
    html = FRONTEND_RESULTS_HTML_PATH.read_text(encoding="utf-8")

    params = request.query_params
    city = str(params.get("city") or "").strip()
    store = str(params.get("store") or "").strip()
    category = str(params.get("category") or "").strip()
    from_date = str(params.get("from") or "").strip()
    to_date = str(params.get("to") or "").strip()
    mode = str(params.get("mode") or "forecast").strip().lower()

    bootstrap_payload: Dict[str, Any] = {
        "query": {
            "city": city,
            "store": store,
            "category": category,
            "from": from_date,
            "to": to_date,
            "mode": mode,
        }
    }

    if city and store and category and from_date and to_date:
        try:
            from_iso = pd.to_datetime(from_date, errors="coerce")
            to_iso = pd.to_datetime(to_date, errors="coerce")
            if pd.notna(from_iso) and pd.notna(to_iso):
                from_iso = from_iso.normalize()
                to_iso = to_iso.normalize()
                period_days = max(1, int((to_iso - from_iso).days) + 1)
                lookback = max(60, period_days)
                forecast_anchor = (from_iso - pd.Timedelta(days=1)).date().isoformat()
                request_anchor = forecast_anchor if mode != "past" else to_iso.date().isoformat()
                with _engine_lock:
                    df = _get_cached_df()
                    bootstrap_payload["forecast_payload"] = {
                        "category": category,
                        "city": city,
                        "store": store,
                        "anchor_date": request_anchor,
                        "history": _history_for_category(
                            df,
                            category=category,
                            city=city,
                            store=store,
                            lookback_days=lookback,
                            anchor_date=request_anchor,
                        ),
                        "forecast": _forecast_for_category(
                            df,
                            category=category,
                            city=city,
                            store=store,
                            horizon=period_days,
                            anchor_date=request_anchor,
                        ),
                    }
        except Exception as exc:
            bootstrap_payload["bootstrap_error"] = str(exc)

    bootstrap_script = (
        "<script>"
        f"window.__DEMANDIQ_RESULTS_BOOTSTRAP__ = {json.dumps(bootstrap_payload)};"
        "</script>"
    )
    html = html.replace("</body>", f"{bootstrap_script}\n</body>")
    return HTMLResponse(content=html, headers=NO_CACHE_HEADERS)


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
            WHERE email = ?
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
            "SELECT id FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="An account already exists for this email")
        existing_name = conn.execute(
            "SELECT id FROM users WHERE full_name = ?",
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
            "SELECT id, email_verified FROM users WHERE email = ?",
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
            "SELECT id FROM users WHERE email = ?",
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


@app.get("/auth/account-suggestions")
def auth_account_suggestions(q: str = "", limit: int = 12) -> Dict[str, Any]:
    # Login-page helper: return active (non-deleted) accounts only.
    query = str(q or "").strip().lower()
    limit = max(1, min(int(limit or 12), 50))
    with _get_auth_db_conn() as conn:
        if query:
            rows = conn.execute(
                """
                SELECT email, role
                FROM users
                WHERE is_active = 1
                  AND email LIKE ?
                ORDER BY email ASC
                LIMIT ?
                """,
                (f"%{query}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT email, role
                FROM users
                WHERE is_active = 1
                ORDER BY email ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    accounts = [
        {"email": str(r["email"]).strip().lower(), "role": _canonical_role(str(r["role"]))}
        for r in rows
        if str(r["email"]).strip()
    ]
    return {"accounts": accounts}


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "model_info": MODEL_INFO}


@app.get("/live-data/status")
def live_data_status(request: Request) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        return _live_simulation_status_payload(df)


@app.post("/live-data/simulator/start")
def start_live_data_simulator(payload: LiveSimulationStartRequest, request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN, ROLE_INVENTORY_MANAGER})
    status = _start_live_simulator(
        interval_seconds=payload.interval_seconds,
        batch_size=payload.batch_size,
        horizon=payload.horizon,
    )
    return {
        "ok": True,
        "message": "Live simulator started",
        "status": status,
    }


@app.post("/live-data/simulator/stop")
def stop_live_data_simulator(request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN, ROLE_INVENTORY_MANAGER})
    status = _stop_live_simulator()
    return {
        "ok": True,
        "message": "Live simulator stopped",
        "status": status,
    }


@app.post("/live-data/simulator/tick")
def tick_live_data_simulator(request: Request, batch_size: int = 4, horizon: int = DEFAULT_HORIZON) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN, ROLE_INVENTORY_MANAGER})
    return _run_live_simulation_tick(batch_size=max(1, min(int(batch_size), 25)), horizon=max(1, min(int(horizon), 30)))


@app.post("/live-data/clear")
def clear_live_data(request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN})
    status = _clear_live_sales_data()
    return {
        "ok": True,
        "message": "Simulated live data cleared",
        "status": status,
    }


@app.get("/categories")
def get_categories(request: Request) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        return _get_cached_scope_payload(df)


@app.get("/categories/{category}/cities")
def get_cities_for_category(request: Request, category: str) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        resolved_category = _resolve_category_value(df, category)
        category_city_map, _, _, _, _ = _get_cached_valid_scope_maps(df)
        cities = category_city_map.get(resolved_category, [])
    return {
        "category": resolved_category,
        "cities": cities,
        "count": len(cities),
    }


@app.get("/categories/{category}/cities/{city}/stores")
def get_stores_for_category_city(request: Request, category: str, city: str) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        resolved_category = _resolve_category_value(df, category)
        resolved_city = _resolve_city_value(df, city)
        _, _, category_city_store_map, _, _ = _get_cached_valid_scope_maps(df)
        stores = category_city_store_map.get(f"{resolved_category}|||{resolved_city}", [])
    return {
        "category": resolved_category,
        "city": resolved_city,
        "stores": stores,
        "count": len(stores),
    }


@app.get("/cities/{city}/stores")
def get_stores_for_city(request: Request, city: str) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        resolved_city = _resolve_city_value(df, city)
        _, _, _, city_store_map, _ = _get_cached_valid_scope_maps(df)
        stores = city_store_map.get(resolved_city, [])
    return {
        "city": resolved_city,
        "stores": stores,
        "count": len(stores),
    }


@app.get("/cities/{city}/store-summary")
def get_store_summary_for_city(
    request: Request,
    city: str,
    category: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        try:
            return _city_store_summary(df, city=city, category=category, from_date=from_date, to_date=to_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/cities/{city}/stores/{store}/categories")
def get_categories_for_city_store(request: Request, city: str, store: str) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _get_cached_df()
        resolved_city = _resolve_city_value(df, city)
        resolved_store = _resolve_store_value(df, store)
        _, city_category_map, _, _, city_store_category_map = _get_cached_valid_scope_maps(df)
        categories = city_store_category_map.get(f"{resolved_city}|||{resolved_store}", [])
        if not categories:
            categories = city_category_map.get(resolved_city, [])
    return {
        "city": resolved_city,
        "store": resolved_store,
        "categories": categories,
        "count": len(categories),
    }


@app.get("/model-metrics")
def get_model_metrics(request: Request) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    try:
        metrics = _load_model_metrics()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "ok": True,
        "metrics": metrics,
    }


@app.get("/forecast/{category}")
def get_forecast(
    request: Request,
    category: str,
    city: Optional[str] = None,
    store: Optional[str] = None,
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
        cache_key = _forecast_cache_key(
            category=category,
            city=city,
            store=store,
            horizon=horizon,
            anchor_date=anchor_date,
            history_lookback_days=history_lookback_days,
        )
        cached_payload = _engine_cache.forecast_payloads.get(cache_key)
        if cached_payload is not None:
            return cached_payload
        try:
            rows = _forecast_for_category(
                df,
                category=category,
                city=city,
                store=store,
                horizon=horizon,
                anchor_date=parsed_anchor,
            )
        except ValueError as exc:
            msg = str(exc)
            if "Not enough history to train model" in msg:
                rows = _fallback_forecast_for_category(
                    df,
                    category=category,
                    city=city,
                    store=store,
                    horizon=horizon,
                    anchor_date=parsed_anchor,
                )
            elif "No rows found for" in msg:
                raise HTTPException(status_code=404, detail=msg) from exc
            else:
                raise HTTPException(status_code=400, detail=msg) from exc
        history = _history_for_category(
            df,
            category=category,
            city=city,
            store=store,
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

        payload = {
            "category": category,
            "city": city,
            "store": store,
            "horizon": horizon,
            "history_lookback_days": history_lookback_days,
            "anchor_date": anchor_date,
            "model_info": MODEL_INFO,
            "history": history,
            "forecast": rows,
        }
        _engine_cache.forecast_payloads[cache_key] = payload
        return payload


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


@app.get("/dynamic-data/options")
def dynamic_data_options(request: Request) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    with _engine_lock:
        df = _load_dynamic_dataset()
        min_date = ""
        max_date = ""
        date_col = _dynamic_find_col(df, "date")
        if date_col:
            dates = parse_date_series(_dynamic_get_series(df, date_col)).dropna()
            if not dates.empty:
                min_date = dates.min().strftime("%Y-%m-%d")
                max_date = dates.max().strftime("%Y-%m-%d")

        products: set[str] = set()
        for key in ("category", "product_id"):
            col = _dynamic_find_col(df, key)
            if not col:
                continue
            values = _dynamic_get_series(df, col).astype("string").str.strip().dropna()
            products.update({str(v) for v in values if str(v).strip() and str(v).lower() != "nan"})

    return {
        "products": sorted(products),
        "min_date": min_date,
        "max_date": max_date,
    }


@app.get("/dynamic-data/records")
def dynamic_data_records(
    request: Request,
    product: str = "",
    from_date: str = "",
    to_date: str = "",
    limit: int = 200,
) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    safe_limit = max(1, min(int(limit), 1000))
    try:
        with _engine_lock:
            df = _load_dynamic_dataset()
            df = _filter_dynamic_product(df, product)
            if from_date and to_date:
                df = _date_filtered(df, from_date, to_date)
            date_col = _dynamic_find_col(df, "date")
            if date_col:
                date_sort = parse_date_series(_dynamic_get_series(df, date_col))
                df = df.assign(__sort_date__=date_sort).sort_values("__sort_date__", ascending=False).drop(
                    columns=["__sort_date__"], errors="ignore"
                )

            col_map = {k: _dynamic_find_col(df, k) for k in _dynamic_alias_map().keys()}
            items: List[Dict[str, Any]] = []
            for _, row in df.head(safe_limit).iterrows():
                try:
                    items.append(_dynamic_item_from_row(row, col_map))
                except Exception:
                    # Skip malformed rows instead of failing the entire endpoint.
                    continue
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dynamic records failed: {exc}") from exc

    return {
        "count": len(items),
        "items": items,
    }


@app.post("/dynamic-data/records/add")
def dynamic_data_add_record(payload: DynamicAddRecordRequest, request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN, ROLE_INVENTORY_MANAGER})
    normalized = _canonical_dynamic_payload(payload.record)
    date_val = str(normalized.get("date") or "").strip()
    product_val = str(normalized.get("product_id") or normalized.get("category") or "").strip()
    units_val = normalized.get("units_sold", None)
    if not date_val:
        raise HTTPException(status_code=400, detail="record.date is required")
    if not product_val:
        raise HTTPException(status_code=400, detail="record.product_id/category is required")
    if units_val is None:
        raise HTTPException(status_code=400, detail="record.units_sold is required")
    parsed_date = pd.to_datetime(date_val, errors="coerce")
    if pd.isna(parsed_date):
        raise HTTPException(status_code=400, detail="record.date must be a valid date")
    try:
        units_num = float(units_val)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="record.units_sold must be numeric")

    with _engine_lock:
        df = _load_dynamic_dataset()
        rid_col = _dynamic_ensure_record_ids(df)
        row_data: Dict[str, Any] = {}

        row_data[_dynamic_ensure_col(df, "record_id")] = f"rec-{len(df.index) + 1:06d}"
        row_data[_dynamic_ensure_col(df, "date")] = parsed_date.strftime("%Y-%m-%d")
        row_data[_dynamic_ensure_col(df, "product_id")] = product_val
        row_data[_dynamic_ensure_col(df, "category")] = str(normalized.get("category") or product_val)
        row_data[_dynamic_ensure_col(df, "units_sold")] = units_num

        for key in ("price", "discount", "inventory_level", "units_ordered", "demand_forecast"):
            if key in normalized:
                col = _dynamic_ensure_col(df, key)
                value = normalized.get(key)
                if value is None or str(value).strip() == "":
                    row_data[col] = pd.NA
                else:
                    try:
                        row_data[col] = float(value)
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=400, detail=f"record.{key} must be numeric")

        for col in df.columns:
            if col not in row_data:
                row_data[col] = pd.NA
        df = pd.concat([df, pd.DataFrame([row_data])], ignore_index=True)
        _dynamic_ensure_record_ids(df)
        _save_dynamic_dataset(df)
        new_record_id = str(df.iloc[-1][rid_col])

    return {"ok": True, "message": "Record added", "record_id": new_record_id}


@app.post("/dynamic-data/records/edit")
def dynamic_data_edit_record(payload: DynamicEditRecordRequest, request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN, ROLE_INVENTORY_MANAGER})
    record_id = str(payload.record_id or "").strip()
    if not record_id:
        raise HTTPException(status_code=400, detail="record_id is required")
    updates = _canonical_dynamic_payload(payload.updates)
    if not updates:
        raise HTTPException(status_code=400, detail="updates cannot be empty")

    with _engine_lock:
        df = _load_dynamic_dataset()
        rid_col = _dynamic_ensure_record_ids(df)
        mask = df[rid_col].astype("string").str.strip() == record_id
        if not bool(mask.any()):
            raise HTTPException(status_code=404, detail="record_id not found")
        row_idx = df.index[mask][0]

        for key, value in updates.items():
            if key == "record_id":
                continue
            if key == "date":
                parsed = pd.to_datetime(value, errors="coerce")
                if pd.isna(parsed):
                    raise HTTPException(status_code=400, detail="updates.date must be a valid date")
                col = _dynamic_ensure_col(df, "date")
                df.at[row_idx, col] = parsed.strftime("%Y-%m-%d")
                continue

            col = _dynamic_ensure_col(df, key)
            if key in {"units_sold", "price", "discount", "inventory_level", "units_ordered", "demand_forecast"}:
                if value is None or str(value).strip() == "":
                    df.at[row_idx, col] = pd.NA
                else:
                    try:
                        df.at[row_idx, col] = float(value)
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=400, detail=f"updates.{key} must be numeric")
            else:
                df.at[row_idx, col] = str(value).strip()

        _save_dynamic_dataset(df)
    return {"ok": True, "message": "Record updated", "record_id": record_id}


@app.post("/dynamic-data/records/delete")
def dynamic_data_delete_record(payload: DynamicDeleteRecordRequest, request: Request) -> Dict[str, Any]:
    _require_roles(request, {ROLE_ADMIN, ROLE_INVENTORY_MANAGER})
    record_id = str(payload.record_id or "").strip()
    if not record_id:
        raise HTTPException(status_code=400, detail="record_id is required")

    with _engine_lock:
        df = _load_dynamic_dataset()
        rid_col = _dynamic_ensure_record_ids(df)
        mask = df[rid_col].astype("string").str.strip() == record_id
        if not bool(mask.any()):
            raise HTTPException(status_code=404, detail="record_id not found")
        deleted_count = int(mask.sum())
        df = df.loc[~mask].copy()
        _save_dynamic_dataset(df)

    return {"ok": True, "message": "Record deleted", "record_id": record_id, "deleted_count": deleted_count}


@app.post("/dynamic-data/compare")
def dynamic_data_compare(payload: DynamicCompareRequest, request: Request) -> Dict[str, Any]:
    _require_roles(request, ALLOWED_LOGIN_ROLES)
    product = str(payload.product or payload.left_product or payload.right_product).strip()
    platform_1 = _normalize_platform_name(payload.platform_1)
    platform_2 = _normalize_platform_name(payload.platform_2)
    from_date = str(payload.from_date or payload.left_from or payload.right_from).strip()
    to_date = str(payload.to_date or payload.left_to or payload.right_to).strip()

    if not all([product, platform_1, platform_2, from_date, to_date]):
        raise HTTPException(status_code=400, detail="Product, both platforms, and date range are required")
    if platform_1 == platform_2:
        raise HTTPException(status_code=400, detail="Choose two different platforms to compare")

    try:
        with _engine_lock:
            df = _load_dynamic_dataset()
            product_df = _filter_dynamic_product(df, product)
            scoped_df = _date_filtered(product_df, from_date, to_date)
            left_df, left_inferred = _filter_dynamic_platform(scoped_df, platform_1)
            right_df, right_inferred = _filter_dynamic_platform(scoped_df, platform_2)
            left_metrics = _compare_metrics(left_df)
            right_metrics = _compare_metrics(right_df)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dynamic compare failed: {exc}") from exc

    metric_keys = sorted(set(left_metrics.keys()) | set(right_metrics.keys()))
    rows: List[Dict[str, Any]] = []
    for key in metric_keys:
        left_val = float(left_metrics.get(key, 0.0))
        right_val = float(right_metrics.get(key, 0.0))
        if left_val > right_val:
            better = platform_1
        elif right_val > left_val:
            better = platform_2
        else:
            better = "Tie"
        rows.append(
            {
                "metric": key,
                "platform_1_value": left_val,
                "platform_2_value": right_val,
                "better": better,
            }
        )

    recommendation = _build_compare_recommendation(platform_1, platform_2, left_metrics, right_metrics)

    return {
        "product": product,
        "platform_1": platform_1,
        "platform_2": platform_2,
        "from": from_date,
        "to": to_date,
        "fallback_used": bool(left_inferred or right_inferred),
        "inference_mode": "price-median-platform-split" if (left_inferred or right_inferred) else "",
        "same_platform_mode": False,
        "left_rows": int(len(left_df.index)),
        "right_rows": int(len(right_df.index)),
        "profitability": {
            "estimated_unit_cost": 42.0,
            "platform_1": {
                "estimated_revenue": float(left_metrics.get("estimated_revenue", 0.0)),
                "estimated_profit": float(left_metrics.get("estimated_profit", 0.0)),
                "estimated_profit_margin": float(left_metrics.get("estimated_profit_margin", 0.0)),
            },
            "platform_2": {
                "estimated_revenue": float(right_metrics.get("estimated_revenue", 0.0)),
                "estimated_profit": float(right_metrics.get("estimated_profit", 0.0)),
                "estimated_profit_margin": float(right_metrics.get("estimated_profit_margin", 0.0)),
            },
        },
        "recommendation": recommendation,
        "metrics": rows,
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
        "message": "Live sales data ingested and forecasts refreshed",
        "model_info": MODEL_INFO,
        "base_dataset_path": str(CSV_PATH.name),
        "live_dataset_path": str(_preferred_live_csv_path().relative_to(PROJECT_DIR)),
        "live_dataset_rows": _live_dataset_row_count(),
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
                        retrain_result = _run_connected_training_pipeline()
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
            "model_metrics": retrain_result.get("model_metrics", {}),
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
        "model_metrics": retrained.get("model_metrics", {}),
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
    retrained = _run_connected_training_pipeline()
    return {
        "ok": True,
        "message": "Retraining completed",
        "trained_categories": int(retrained.get("trained_categories", 0)),
        "failed_categories": retrained.get("failed_categories", []),
        "model_metrics": retrained.get("model_metrics", {}),
    }


# Run: uvicorn forecast_api:app --reload --host 0.0.0.0 --port 8000
