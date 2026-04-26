import warnings
warnings.filterwarnings("ignore")

from collections import deque
import json
import os
import time
import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt

from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder


BASE_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = BASE_DIR / "Dataset"
FEATURE_PATHS = [
    DATASET_DIR / "retail_store_inventory_city_level_cleaned_features.csv",
    DATASET_DIR / "retail_store_inventory_city_level_features.csv",
    DATASET_DIR / "retail_store_inventory_cleaned_features.csv",
    DATASET_DIR / "retail_store_inventory_features.csv",
]
CSV_PATH = next((path for path in FEATURE_PATHS if path.exists()), None)
if CSV_PATH is None:
    raise FileNotFoundError("No feature-engineered dataset found. Run Backend/features_engineering.py first.")

DATE_COL = "date"
TARGET = "units sold"
MODEL_OUTPUT_DIR = BASE_DIR / "Backend"
METRICS_PATH = MODEL_OUTPUT_DIR / "model_metrics.json"
PREDICTIONS_PATH = MODEL_OUTPUT_DIR / "model_test_predictions.csv"
PLOT_PATH = MODEL_OUTPUT_DIR / "model_training_graph.png"
DROP_FEATURES = {"record_id"}
MAX_PLOT_POINTS = 250
TRAIN_METRIC_SAMPLE_SIZE = 50000
FAST_MODE = str(os.getenv("DEMANDIQ_FAST_TRAIN", "1")).strip().lower() in {"1", "true", "yes", "on"}
FAST_TRAIN_ROW_LIMIT = int(os.getenv("DEMANDIQ_FAST_TRAIN_ROWS", "200000"))
FAST_TOTAL_ROW_LIMIT = int(os.getenv("DEMANDIQ_FAST_TOTAL_ROWS", str(int(FAST_TRAIN_ROW_LIMIT * 1.25))))
FAST_ESTIMATORS = int(os.getenv("DEMANDIQ_FAST_N_ESTIMATORS", "40"))
FULL_ESTIMATORS = int(os.getenv("DEMANDIQ_N_ESTIMATORS", "80"))
AUTO_OPEN_GRAPH = str(os.getenv("DEMANDIQ_AUTO_OPEN_GRAPH", "1")).strip().lower() in {"1", "true", "yes", "on"}
CSV_CHUNK_SIZE = int(os.getenv("DEMANDIQ_CSV_CHUNK_SIZE", "50000"))
MODEL_N_JOBS = int(os.getenv("DEMANDIQ_N_JOBS", "1"))


def _build_read_dtypes(csv_path: Path) -> dict[str, str]:
    sample = pd.read_csv(csv_path, nrows=1000)
    sample.columns = sample.columns.str.strip().str.lower()

    dtype_map: dict[str, str] = {}
    for col in sample.columns:
        if col == DATE_COL:
            continue
        if pd.api.types.is_integer_dtype(sample[col]):
            dtype_map[col] = "int32"
        elif pd.api.types.is_float_dtype(sample[col]):
            dtype_map[col] = "float32"
        else:
            dtype_map[col] = "category"
    return dtype_map


def _safe_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)


def _safe_write_csv(df: pd.DataFrame, path: Path, index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=index)


def _log(message: str) -> None:
    print(message, flush=True)


def _load_training_frame(csv_path: Path, dtype_map: dict[str, str]) -> pd.DataFrame:
    if FAST_MODE:
        # Keep only the most recent rows needed for a presentation-friendly training run.
        tail_chunks: deque[pd.DataFrame] = deque()
        kept_rows = 0
        for chunk in pd.read_csv(csv_path, dtype=dtype_map, low_memory=False, chunksize=CSV_CHUNK_SIZE):
            chunk.columns = chunk.columns.str.strip().str.lower()
            tail_chunks.append(chunk)
            kept_rows += len(chunk)
            while kept_rows > FAST_TOTAL_ROW_LIMIT and tail_chunks:
                dropped = tail_chunks.popleft()
                kept_rows -= len(dropped)
        if not tail_chunks:
            raise ValueError(f"No rows could be loaded from dataset: {csv_path.name}")
        return pd.concat(list(tail_chunks), ignore_index=True)

    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(csv_path, dtype=dtype_map, low_memory=False, chunksize=CSV_CHUNK_SIZE):
        chunk.columns = chunk.columns.str.strip().str.lower()
        chunks.append(chunk)
    if not chunks:
        raise ValueError(f"No rows could be loaded from dataset: {csv_path.name}")
    return pd.concat(chunks, ignore_index=True)


def _encode_object_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col == TARGET:
            continue
        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            encoder = LabelEncoder()
            out[col] = encoder.fit_transform(out[col].astype(str)).astype("int32")
    return out


def main() -> None:
    read_dtypes = _build_read_dtypes(CSV_PATH)
    df = _load_training_frame(CSV_PATH, read_dtypes)

    if TARGET not in df.columns:
        raise ValueError(f"Missing required target column: {TARGET}")

    if DATE_COL in df.columns:
        df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="coerce")
        df = df.sort_values(DATE_COL)
        df["date_ordinal"] = df[DATE_COL].map(lambda value: value.toordinal() if pd.notna(value) else np.nan)
        df["year"] = df[DATE_COL].dt.year.fillna(0).astype(int)
        df["month_from_date"] = df[DATE_COL].dt.month.fillna(0).astype(int)
        df["day_from_date"] = df[DATE_COL].dt.day.fillna(0).astype(int)
        df = df.drop(columns=[DATE_COL])

    df = _encode_object_columns(df)
    df = df.dropna(subset=[TARGET]).copy()
    for col in df.select_dtypes(include=["float64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="float")
    for col in df.select_dtypes(include=["int64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="integer")

    feature_cols = [col for col in df.columns if col != TARGET]
    feature_cols = [col for col in feature_cols if col not in DROP_FEATURES]
    X = df[feature_cols]
    y = pd.to_numeric(df[TARGET], errors="coerce")

    valid_mask = y.notna()
    X = X.loc[valid_mask].copy()
    y = y.loc[valid_mask].copy()

    if len(X) < 2:
        raise ValueError("Not enough valid rows to train the model. Need at least 2 rows after cleaning.")

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        shuffle=False,
    )

    if X_train.empty or X_test.empty:
        raise ValueError("Training split produced an empty train or test set. Add more valid rows to the dataset.")

    if len(X_train) > FAST_TRAIN_ROW_LIMIT:
        X_train = X_train.tail(FAST_TRAIN_ROW_LIMIT).copy()
        y_train = y_train.tail(FAST_TRAIN_ROW_LIMIT).copy()

    n_estimators = FAST_ESTIMATORS if FAST_MODE else FULL_ESTIMATORS
    mode_label = "FAST" if FAST_MODE else "FULL"

    model = ExtraTreesRegressor(
        n_estimators=n_estimators,
        max_depth=18,
        min_samples_leaf=3,
        max_features="sqrt",
        random_state=42,
        n_jobs=MODEL_N_JOBS,
    )
    _log(f"\nStarting model training on {len(X_train)} rows with {len(feature_cols)} features...")
    fit_started_at = time.time()
    try:
        model.fit(X_train, y_train)
    except PermissionError as exc:
        if model.n_jobs == 1:
            raise
        _log(f"Parallel training unavailable ({exc}); retrying with n_jobs=1.")
        model.set_params(n_jobs=1)
        fit_started_at = time.time()
        model.fit(X_train, y_train)
    fit_seconds = time.time() - fit_started_at

    test_pred = model.predict(X_test)
    train_eval_rows = min(TRAIN_METRIC_SAMPLE_SIZE, len(X_train))
    train_pred = model.predict(X_train.tail(train_eval_rows))
    train_true = y_train.tail(train_eval_rows)

    metrics = {
        "input_dataset": CSV_PATH.name,
        "mode": mode_label.lower(),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "feature_count": int(len(feature_cols)),
        "fit_seconds": float(fit_seconds),
        "mae": float(mean_absolute_error(y_test, test_pred)),
        "mse": float(mean_squared_error(y_test, test_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, test_pred))),
        "r2": float(r2_score(y_test, test_pred)),
        "train_mae": float(mean_absolute_error(train_true, train_pred)),
    }

    _log(f"\nTraining dataset: {CSV_PATH.name}")
    _log("\n===== Model Evaluation (ExtraTreesRegressor) =====")
    _log(f"Train rows: {metrics['train_rows']}")
    _log(f"Test rows : {metrics['test_rows']}")
    _log(f"Features  : {metrics['feature_count']}")
    _log(f"Fit time  : {metrics['fit_seconds']:.2f} sec")
    _log(f"MAE       : {metrics['mae']:.3f}")
    _log(f"MSE       : {metrics['mse']:.3f}")
    _log(f"RMSE      : {metrics['rmse']:.3f}")
    _log(f"R2        : {metrics['r2']:.3f}")
    _log(f"Accuracy (%): {metrics['r2'] * 100:.2f}")

    predictions_df = X_test.copy()
    predictions_df["actual_units_sold"] = y_test.values
    predictions_df["predicted_units_sold"] = test_pred
    _safe_write_csv(predictions_df, PREDICTIONS_PATH, index=False)

    plot_df = predictions_df[["actual_units_sold", "predicted_units_sold"]].reset_index(drop=True)
    plt.style.use("seaborn-v0_8-whitegrid")
    plot_step = max(1, len(plot_df) // MAX_PLOT_POINTS)
    plot_view = plot_df.iloc[::plot_step].copy()
    plot_view.index = np.arange(1, len(plot_view) + 1)

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(
        plot_view.index,
        plot_view["actual_units_sold"],
        label="Actual Units Sold",
        color="#2563EB",
        linewidth=2.2,
        marker="o",
        markersize=3.2,
    )
    ax.plot(
        plot_view.index,
        plot_view["predicted_units_sold"],
        label="Predicted Units Sold",
        color="#F59E0B",
        linewidth=2.2,
        marker="s",
        markersize=3.0,
    )
    ax.set_title("Model Training Result: Actual vs Predicted", fontsize=14, weight="bold")
    ax.set_xlabel("Sampled Test Points")
    ax.set_ylabel("Units Sold")
    ax.legend(frameon=True)
    ax.margins(x=0.01)
    ax.grid(True, linestyle="--", alpha=0.35)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(PLOT_PATH, dpi=200)
    plt.close(fig)

    _safe_write_text(METRICS_PATH, json.dumps(metrics, indent=2))
    _log("\nModel training completed successfully.")
    _log(f"Outputs available in: {MODEL_OUTPUT_DIR}")

    if AUTO_OPEN_GRAPH:
        try:
            os.startfile(PLOT_PATH)  # type: ignore[attr-defined]
        except Exception:
            pass


if __name__ == "__main__":
    main()
