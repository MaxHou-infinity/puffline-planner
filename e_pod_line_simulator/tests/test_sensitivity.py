"""敏感性试算测试（V3.1 P2 / V3.2 P1/P1.3）"""

import threading
from concurrent.futures import CancelledError

import pytest

from src.models import Material, ProductionLine, ProductionType, Station
from src.sensitivity import run_sensitivity, run_sweep


def test_sensitivity_scenarios():
    line = ProductionLine("敏感性测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "慢瓶颈", 2.0, 1))
    line.add_station(Station("s02", "快下游", 1.0, 2))

    scenarios = run_sensitivity(line, duration_hours=1.0)

    assert len(scenarios) >= 3
    base = scenarios[0]
    assert base["label"] == "基线"
    worker = next(s for s in scenarios if s["label"] == "瓶颈+1人")
    assert worker["delta_output"] > 0
    assert worker["delta_unit_cost"] < 0
    assert scenarios[0]["apply"] is None
    assert worker["apply"]["station_id"] == "s01"
    assert worker["apply"]["attr"] == "worker_count"
    assert worker["actual_unit_cost"] > 0
    assert worker["total_cost"] > 0


def test_run_sweep_worker_count_monotonic():
    line = ProductionLine("批量试算", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "瓶颈", 2.0, 1))
    line.add_station(Station("s02", "下游", 1.0, 2))

    rows = run_sweep(line, "worker_count", [1, 2, 3], duration_hours=1.0)

    assert len(rows) == 3
    outputs = [row["total_output"] for row in rows]
    assert outputs == sorted(outputs)
    assert outputs[0] < outputs[-1]
    assert all("total_output" in row and "unit_cost" in row for row in rows)


def test_run_sweep_shift_hours_increases_output():
    line = ProductionLine("批量试算班次", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "注油", 1.0, 1))

    rows = run_sweep(line, "shift_hours", [1, 2], duration_hours=1.0)

    assert len(rows) == 2
    assert rows[1]["total_output"] >= rows[0]["total_output"]
    assert rows[0]["label"] == "shift_hours=1"


def test_sensitivity_extended_variants():
    line = ProductionLine("敏感性扩展", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "瓶颈", 2.0, 2))
    line.add_station(Station("s02", "下游", 1.0, 3))

    scenarios = run_sensitivity(line, duration_hours=1.0)
    labels = [s["label"] for s in scenarios]

    assert "瓶颈+1人" in labels
    assert "瓶颈+2人" in labels
    assert "瓶颈-1人" in labels
    assert "自动化替代10%" in labels
    assert "瓶颈OEE+5%" in labels


def test_sensitivity_material_price_variant():
    line = ProductionLine("原料敏感性", shift_hours=1, break_minutes=0)
    line.add_station(Station(
        "s01", "棉芯安装", 1.0, 1,
        oee=1.0, efficiency=1.0, changeover_time=0,
        bom={"棉芯": 1},
    ))
    line.materials.append(Material("棉芯", "个", 10000.0, unit_cost=2.0))
    line.inventory = {"棉芯": 10000.0}

    scenarios = run_sensitivity(line, duration_hours=1.0)
    labels = [s["label"] for s in scenarios]

    assert "原料价格+10%" in labels
    assert "原料价格-10%" in labels
    up = next(s for s in scenarios if s["label"] == "原料价格+10%")
    base = scenarios[0]
    assert up["material_cost"] > base["material_cost"]
    assert up["delta_material_cost"] > 0


def test_price_only_changes_use_same_quality_seed_and_labor_cost():
    line = ProductionLine('随机质检', shift_hours=1, break_minutes=0)
    line.stations = [Station(
        's', '质检', 1, 1, oee=1, efficiency=1, changeover_time=0,
        sampling_rate=1, defect_rate=.3, bom={'零件': 1},
    )]
    line.materials = [Material('零件', '个', 1000, 2)]
    rows = run_sensitivity(line, duration_hours=.02, simulation_seed=17)
    price_rows = [row for row in rows if row['label'].startswith('原料价格')]
    assert len(price_rows) == 2
    assert all(row['total_output'] == rows[0]['total_output'] for row in price_rows)
    assert all(row['actual_unit_cost'] == rows[0]['actual_unit_cost'] for row in price_rows)
    assert all(row['simulation_seed'] == 17 for row in rows)
    assert rows == run_sensitivity(line, duration_hours=.02, simulation_seed=17)


def test_zero_good_output_has_no_unit_cost():
    line = ProductionLine('零良品', shift_hours=1, break_minutes=0)
    line.stations = [Station('s', '全废', 1, 1, sampling_rate=1, defect_rate=1)]
    rows = run_sweep(line, 'worker_count', [1, 2], duration_hours=.01)
    assert all(not row['feasible'] for row in rows)
    assert all(row['actual_unit_cost'] is None and row['constraints'] for row in rows)


def test_machine_sensitivity_changes_machine_count_not_people():
    line = ProductionLine('机台', shift_hours=1, break_minutes=0)
    line.stations = [Station('s', '机台', 1, 3, machine_takt=1, machine_count=1,
                             oee=1, efficiency=1, changeover_time=0)]
    rows = run_sensitivity(line, duration_hours=.01)
    machine = next(row for row in rows if row['label'] == '增加1台机台')
    assert machine['apply']['attr'] == 'machine_count'
    assert machine['total_output'] > rows[0]['total_output']
    assert machine['total_cost'] == rows[0]['total_cost']
    assert line.stations[0].worker_count == 3


def test_liquid_and_cancellation_do_not_produce_recommendations():
    line = ProductionLine('烟油', production_type=ProductionType.LIQUID_FILLING)
    with pytest.raises(ValueError, match='烟油'):
        run_sensitivity(line)
    with pytest.raises(ValueError, match='烟油'):
        run_sweep(line, 'worker_count', [1])
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        run_sensitivity(ProductionLine('取消'), cancel_event=event)
    with pytest.raises(CancelledError):
        run_sweep(ProductionLine('取消'), 'shift_hours', [1], cancel_event=event)
