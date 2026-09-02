"""
mlflow_utils.py --- MLflow experiment tracking for stock prediction models.

Uses SQLite backend (mlflow.db) for compatibility with MLflow 3.x.
Set MLFLOW_ALLOW_FILE_STORE=true env var as fallback for older experiments.
"""

import mlflow
import mlflow.sklearn
from pathlib import Path
from contextlib import contextmanager
import logging
import time
import os

logger = logging.getLogger(__name__)

MLFLOW_DIR = Path(__file__).parent / "mlruns"
DB_PATH = Path(__file__).parent / "mlflow.db"
EXPERIMENT_NAME = "stock-prediction-v1"

# Allow both old file store and new SQLite
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"


def setup_tracking():
    """Initialize MLflow tracking with SQLite backend."""
    tracking_uri = f"sqlite:///{DB_PATH.as_posix()}"
    mlflow.set_tracking_uri(tracking_uri)

    try:
        experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
        if experiment is None:
            mlflow.create_experiment(
                EXPERIMENT_NAME,
                tags={"project": "stock-tracker", "version": "v1"},
            )
            logger.info(f"Created MLflow experiment: {EXPERIMENT_NAME}")
    except Exception as e:
        logger.warning(f"Experiment setup: {e}")

    logger.info(f"MLflow tracking URI: {tracking_uri}")
    return tracking_uri


@contextmanager
def log_training_run(symbol: str, model_type: str,
                     params: dict = None, tags: dict = None,
                     log_model: bool = True):
    """
    Context manager: auto start/end MLflow run, log parameters.

    Args:
        symbol: Stock code
        model_type: "lightgbm" | "transformer" | "dqn" | "all"
        params: Hyperparameters dict
        tags: Tags dict
        log_model: Whether to auto-log model
    """
    setup_tracking()
    mlflow.set_experiment(EXPERIMENT_NAME)

    run_tags = {
        "symbol": symbol,
        "model_type": model_type,
        **(tags or {}),
    }

    run_name = f"{symbol}_{model_type}_{int(time.time())}"
    run = mlflow.start_run(run_name=run_name, tags=run_tags)

    try:
        if params:
            mlflow.log_params(params)
        logger.info(f"MLflow run started: {run_name}")
        yield run
        mlflow.set_tag("status", "success")
    except Exception as e:
        mlflow.set_tag("status", "failed")
        mlflow.set_tag("error", str(e)[:250])
        logger.error(f"Training failed for {run_name}: {e}")
        raise
    finally:
        mlflow.end_run()
        logger.info(f"MLflow run ended: {run_name}")


def log_metrics_dict(metrics: dict, step: int = None):
    """Batch log metrics."""
    mlflow.log_metrics(metrics, step=step)


def log_prediction_accuracy(y_true: list, y_pred: list, prefix: str = ""):
    """Calculate and log prediction accuracy metrics."""
    import numpy as np

    y_true = np.array(y_true, dtype=float)
    y_pred = np.array(y_pred, dtype=float)

    if len(y_true) > 1:
        true_direction = np.diff(y_true) > 0
        pred_direction = np.diff(y_pred) > 0
        direction_acc = np.mean(true_direction == pred_direction)
    else:
        direction_acc = 0.0

    mse = np.mean((y_true - y_pred) ** 2)
    mae = np.mean(np.abs(y_true - y_pred))
    rmse = np.sqrt(mse)

    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    metrics = {
        f"{prefix}mse": round(mse, 6),
        f"{prefix}rmse": round(rmse, 4),
        f"{prefix}mae": round(mae, 4),
        f"{prefix}r2": round(r2, 4),
        f"{prefix}direction_accuracy": round(direction_acc, 4),
    }
    mlflow.log_metrics(metrics)
    return metrics


def load_best_model(metric: str = "direction_accuracy", greater_is_better: bool = True):
    """Load best model run by metric."""
    setup_tracking()
    mlflow.set_experiment(EXPERIMENT_NAME)

    order = "DESC" if greater_is_better else "ASC"
    runs = mlflow.search_runs(order_by=[f"metrics.{metric} {order}"], max_results=1)

    if runs.empty:
        logger.warning("No MLflow runs found")
        return None

    best = runs.iloc[0]
    logger.info(f"Best run: {best['run_id']} ({metric}={best.get(f'metrics.{metric}', 'N/A')})")
    return best


def get_model_summary(symbol: str = None) -> list[dict]:
    """Get training summary for a stock."""
    setup_tracking()
    mlflow.set_experiment(EXPERIMENT_NAME)

    filter_str = f"tags.symbol = '{symbol}'" if symbol else ""
    runs = mlflow.search_runs(filter_string=filter_str)

    if runs.empty:
        return []

    columns = [
        "run_id", "tags.model_type", "tags.symbol", "tags.status",
        "metrics.direction_accuracy", "metrics.r2", "metrics.rmse", "start_time",
    ]
    available = [c for c in columns if c in runs.columns]
    summary = runs[available].to_dict("records")

    for s in summary:
        s["direction_accuracy"] = s.get("metrics.direction_accuracy", "N/A")
        s["r2"] = s.get("metrics.r2", "N/A")
        s["rmse"] = s.get("metrics.rmse", "N/A")
        s["model_type"] = s.get("tags.model_type", "unknown")
        s["status"] = s.get("tags.status", "unknown")

    return summary
