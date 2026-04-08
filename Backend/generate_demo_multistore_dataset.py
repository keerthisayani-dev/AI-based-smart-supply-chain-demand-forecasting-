from __future__ import annotations

from pathlib import Path
import hashlib
import shutil

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = BASE_DIR / "Dataset"
SOURCE_PATH = DATASET_DIR / "retail_store_inventory_city_level_cleaned.csv"
FEATURES_PATH = DATASET_DIR / "retail_store_inventory_city_level_cleaned_features.csv"
BACKUP_DIR = DATASET_DIR / "backups"
TOTAL_STORES_PER_CITY = 3

NUMERIC_FACTOR_MAP = {
    "inventory level": (0.86, 1.00, 1.18),
    "units sold": (0.91, 1.00, 1.12),
    "units ordered": (0.9, 1.00, 1.08),
    "demand forecast": (0.92, 1.00, 1.1),
    "price": (0.98, 1.0, 1.03),
    "discount": (0.95, 1.0, 1.06),
    "competitor pricing": (0.97, 1.0, 1.04),
    "trend": (0.9, 1.0, 1.08),
}
STORE_SUFFIXES = ("Central", "North", "South")


def _slug(text: str, length: int = 3) -> str:
    cleaned = "".join(ch for ch in str(text).upper() if ch.isalnum())
    return (cleaned[:length] or "CTY").ljust(length, "X")


def _stable_jitter(city: str, category: str, date_text: str, store_index: int) -> float:
    payload = f"{city}|{category}|{date_text}|{store_index}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    raw = int(digest[:8], 16)
    return ((raw % 1000) / 1000.0 - 0.5) * 0.08


def _backup_if_exists(path: Path) -> None:
    if not path.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{path.stem}_original{path.suffix}"
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def _apply_numeric_variation(frame: pd.DataFrame, store_index: int) -> pd.DataFrame:
    varied = frame.copy()
    for col, factors in NUMERIC_FACTOR_MAP.items():
        if col not in varied.columns:
            continue
        numeric = pd.to_numeric(varied[col], errors="coerce")
        base_factor = factors[min(store_index, len(factors) - 1)]
        jitter = varied.apply(
            lambda row: _stable_jitter(
                str(row.get("city", "")),
                str(row.get("category", row.get("product id", ""))),
                str(row.get("date", "")),
                store_index,
            ),
            axis=1,
        )
        adjusted = numeric * (base_factor + jitter)
        if col in {"inventory level", "units sold", "units ordered"}:
            adjusted = adjusted.round().clip(lower=0)
        else:
            adjusted = adjusted.round(2).clip(lower=0)
        varied[col] = adjusted
    return varied


def build_demo_dataset() -> pd.DataFrame:
    df = pd.read_csv(SOURCE_PATH)
    df.columns = df.columns.str.strip().str.lower()

    if "city" not in df.columns:
        raise ValueError("Source dataset must include a city column.")

    city_order = sorted({str(v).strip() for v in df["city"].dropna().tolist() if str(v).strip()})
    city_code_map = {city: f"C{idx + 1:02d}" for idx, city in enumerate(city_order)}

    rows: list[pd.DataFrame] = []
    for city in city_order:
      city_df = df[df["city"].astype(str).str.strip() == city].copy()
      if city_df.empty:
          continue
      city_code = city_code_map[city]
      for store_index in range(TOTAL_STORES_PER_CITY):
          variant = _apply_numeric_variation(city_df, store_index)
          suffix = STORE_SUFFIXES[store_index] if store_index < len(STORE_SUFFIXES) else f"Hub {store_index + 1}"
          variant["store_name"] = f"{city} {suffix} Hub"
          variant["store id"] = f"{city_code}S{store_index + 1:02d}"
          if "record_id" in variant.columns:
              variant["record_id"] = (
                  variant["record_id"].astype("string").str.strip().fillna("rec")
                  + f"-{store_index + 1:02d}"
              )
          rows.append(variant)

    if not rows:
        raise ValueError("No rows generated for demo dataset.")

    out = pd.concat(rows, ignore_index=True)
    preferred_columns = list(df.columns)
    for required in ("store_name", "store id"):
        if required not in preferred_columns and required in out.columns:
            preferred_columns.append(required)
    return out[preferred_columns]


def main() -> None:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing source dataset: {SOURCE_PATH}")

    _backup_if_exists(SOURCE_PATH)
    _backup_if_exists(FEATURES_PATH)

    demo_df = build_demo_dataset()
    demo_df.to_csv(SOURCE_PATH, index=False)

    print(f"Saved demo multi-store dataset to: {SOURCE_PATH.name}")
    print(f"Rows: {len(demo_df)}")
    print(f"Cities: {demo_df['city'].astype(str).str.strip().nunique() if 'city' in demo_df.columns else 0}")
    print(f"Stores: {demo_df['store_name'].astype(str).str.strip().nunique() if 'store_name' in demo_df.columns else 0}")
    if 'city' in demo_df.columns and 'store_name' in demo_df.columns:
        city_store_counts = (
            demo_df.groupby('city')['store_name']
            .nunique()
            .sort_index()
        )
        print("Per-city store count sample:")
        print(city_store_counts.head(10).to_string())


if __name__ == "__main__":
    main()
