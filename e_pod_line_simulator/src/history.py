"""
KPI 历史趋势（V3.2 P1）

跨运行记录 KPI 快照，保存到 configs/run_history.json，供趋势表/图使用。
"""

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from src.models import ProductionLine, SimulationResult
from src.utils import atomic_write_json, get_user_data_path

DEFAULT_HISTORY_PATH = get_user_data_path("run_history.json")

KPI_NAMES = [
    "bottleneck_capacity",
    "daily_output",
    "total_cost",
    "unit_cost",
    "balance_rate",
    "upph",
    "batch_cycle_min",
    "batch_pass_rate",
    "yield_rate",
    "machine_oee",
    "shippable_quantity",
    "rejected_batches",
    "actual_labor_cost",
    "actual_unit_cost",
]

MAX_RECORDS = 200


def build_snapshot(
    line: ProductionLine,
    result: Optional[SimulationResult] = None,
) -> Dict[str, Any]:
    """从产线（或仿真结果）构建 KPI 快照"""
    if result is not None:
        kpis = {name: result.kpis.get(name, 0.0) for name in KPI_NAMES}
        kpis["total_output"] = result.total_output
        kpis["duration_minutes"] = round((result.elapsed_seconds if getattr(result, "run_id", "") else result.duration_seconds) / 60.0, 2)
    else:
        kpis = {
            "bottleneck_capacity": line.get_bottleneck_capacity(),
            "daily_output": line.calculate_daily_output(),
            "total_cost": line.calculate_total_cost(),
            "unit_cost": line.calculate_unit_cost(),
            "balance_rate": line.calculate_line_balance_rate(),
            "upph": line.calculate_upph(),
            "batch_cycle_min": line.calculate_batch_cycle_min(),
            "batch_pass_rate": line.calculate_batch_pass_rate(),
            "yield_rate": line.calculate_avg_yield_rate(),
            "machine_oee": line.calculate_avg_machine_oee(),
            "shippable_quantity": 0.0,
            "rejected_batches": 0.0,
        }
        kpis["total_output"] = 0
        kpis["duration_minutes"] = 0.0
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "line_name": result.line_name if result is not None else line.name,
        "production_type": (result.line_config.get("production_type", line.production_type.value)
                            if result is not None else line.production_type.value),
        "unit": result.unit if result is not None else line.get_unit(),
        "source": "simulation" if result is not None else "configuration",
        "status": getattr(result, "status", "configuration"),
        "elapsed_seconds": getattr(result, "elapsed_seconds", 0),
        "warmup_seconds": getattr(result, "warmup_seconds", 0),
        "planned_seconds": result.duration_seconds if result is not None else 0,
        "model_scope": getattr(result, "model_scope", ""),
        "run_id": getattr(result, "run_id", "") if result is not None else "",
        "config_revision": getattr(result, "config_revision", "") if result is not None else "",
        "random_seed": getattr(result, "random_seed", None) if result is not None else None,
        "measurement_seconds": getattr(result, "measurement_seconds", 0) if result is not None else 0,
        "kpis": {k: round(float(v), 4) if v is not None else None for k, v in kpis.items()},
    }


def _history_path(path: Optional[str]) -> str:
    return path if path is not None else get_user_data_path("run_history.json", migrate=True)


def _read_records(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError("历史文件格式错误，原文件已保留")
    return data


def load_history(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read history without replacing an unreadable original file."""
    try:
        return _read_records(_history_path(path))
    except Exception:
        return []


def append_snapshot(
    record: Dict[str, Any],
    path: Optional[str] = None,
) -> bool:
    try:
        path = _history_path(path)
        records = _read_records(path)
        records.append(record)
        records = records[-MAX_RECORDS:]
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        atomic_write_json(path, records)
        return True
    except Exception:
        return False


def kpi_series(
    history: List[Dict[str, Any]],
    kpi_name: str,
) -> List[Tuple[str, float]]:
    """返回 [(时间, 值)] 序列"""
    return [
        (record.get("timestamp", ""), record.get("kpis", {}).get(kpi_name, 0.0))
        for record in history
        if record.get("kpis", {}).get(kpi_name, 0.0) is not None
    ]


def export_history(
    path_out: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """导出历史记录为 Excel（V3.3）"""
    try:
        import pandas as pd

        records = history if history is not None else load_history()
        if not records:
            return False
        rows = []
        for record in records:
            row = {
                "timestamp": record.get("timestamp", ""),
                "line_name": record.get("line_name", ""),
                "production_type": record.get("production_type", ""),
                "unit": record.get("unit", ""),
                "run_id": record.get("run_id", ""),
                "config_revision": record.get("config_revision", ""),
                "random_seed": record.get("random_seed"),
                "measurement_seconds": record.get("measurement_seconds", 0),
                "status": record.get("status", ""),
                "elapsed_seconds": record.get("elapsed_seconds", 0),
                "warmup_seconds": record.get("warmup_seconds", 0),
                "planned_seconds": record.get("planned_seconds", 0),
                "model_scope": record.get("model_scope", ""),
            }
            row.update({
                k: record.get("kpis", {}).get(k, 0.0) for k in KPI_NAMES
            })
            rows.append(row)
        pd.DataFrame(rows).to_excel(path_out, index=False)
        return True
    except Exception:
        return False


def clear_history(path: Optional[str] = None) -> bool:
    """清空历史记录（V3.3）"""
    try:
        # Keep an empty target so a legacy file is not reimported after clearing.
        atomic_write_json(_history_path(path), [])
        return True
    except Exception:
        return False
