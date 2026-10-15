"""遗传算法辅助优化模块单元测试（V3.2 P2）"""

import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest

from src.models import ProductionLine, ProductionType, Station
from src import optimizer
from src.optimizer import optimize


def make_line():
    line = ProductionLine("优化测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "瓶颈", 2.0, 1))
    line.add_station(Station("s02", "下游", 1.0, 2))
    return line


def test_optimize_returns_ranked_top_solutions():
    line = make_line()
    top = optimize(
        line, objective="unit_cost",
        generations=2, population=4, top_n=3,
        duration_hours=0.5,
    )

    assert 1 <= len(top) <= 3
    assert all("rank" in s and "params" in s and "unit_cost" in s for s in top)
    unit_costs = [s["unit_cost"] for s in top]
    assert unit_costs == sorted(unit_costs)


def test_optimize_deterministic_with_seed():
    line = make_line()
    a = optimize(line, generations=2, population=4, duration_hours=0.5, seed=7)
    b = optimize(line, generations=2, population=4, duration_hours=0.5, seed=7)
    assert [s["unit_cost"] for s in a] == [s["unit_cost"] for s in b]


def test_optimize_output_objective_scores_output():
    line = make_line()
    top = optimize(
        line, objective="output",
        generations=2, population=4, duration_hours=0.5,
    )
    assert top
    assert top[0]["score"] == top[0]["total_output"]


def test_optimize_actual_unit_cost_present():
    top = optimize(
        make_line(), objective="unit_cost",
        generations=2, population=4, duration_hours=0.5,
    )
    assert all("actual_unit_cost" in s and s["actual_unit_cost"] > 0 for s in top)


def test_optimize_locked_stations_keep_values():
    line = make_line()
    original = line.get_station("s01")
    top = optimize(
        line, objective="unit_cost",
        generations=2, population=4, duration_hours=0.5,
        locked_stations=["s01"],
    )
    for s in top:
        assert s["params"]["s01"]["worker_count"] == original.worker_count
        assert s["params"]["s01"]["buffer_capacity"] == original.buffer_capacity


def test_optimize_progress_callback_called():
    calls = []

    optimize(
        make_line(), objective="output",
        generations=3, population=4, duration_hours=0.5,
        progress_callback=lambda g, t, s: calls.append((g, t)),
    )

    assert len(calls) == 2
    assert calls[0][0] == 1 and calls[0][1] == 2


def test_better_child_survives_generation_selection(monkeypatch):
    monkeypatch.setattr(optimizer, '_random_individual', lambda *args: [1, 100, 1, 100])
    monkeypatch.setattr(optimizer, '_mutate', lambda *args: [9, 100, 1, 100])

    def evaluate(line, individual, *args):
        return {'individual': individual, 'score': individual[0], 'feasible': True,
                'total_output': individual[0], 'unit_cost': 1}

    monkeypatch.setattr(optimizer, '_evaluate', evaluate)
    top = optimize(make_line(), objective='output', generations=2, population=2)
    assert top[0]['total_output'] == 9


def test_evaluation_uses_actual_labor_cost_and_rejects_zero_output(monkeypatch):
    class Engine:
        output = 10

        def __init__(self, line):
            self.line = line

        def run_sync(self, *args, **kwargs):
            return SimpleNamespace(total_output=self.output, kpis={
                'actual_labor_cost': 20, 'unit_cost': .01, 'total_cost': 999,
            })

    monkeypatch.setattr(optimizer, 'SimulationEngine', Engine)
    line = make_line()
    candidate = optimizer._current_individual(line)
    row = optimizer._evaluate(line, candidate, 'unit_cost', .1, 0)
    assert row['score'] == -2
    assert row['unit_cost'] == 2
    assert row['total_cost'] == 20
    Engine.output = 0
    row = optimizer._evaluate(line, candidate, 'unit_cost', .1, 0)
    assert not row['feasible']
    assert row['actual_unit_cost'] is None
    with pytest.raises(ValueError, match='未找到'):
        optimize(line, generations=1, population=1)


def test_lock_preserves_buffer_below_search_bounds_and_uses_station_ids():
    line = make_line()
    line.stations[0].buffer_capacity = 1
    line.stations[1].name = line.stations[0].name
    top = optimize(line, generations=2, population=2, duration_hours=.01,
                   locked_stations=['s01', 's02'])
    assert set(top[0]['params']) == {'s01', 's02'}
    assert top[0]['params']['s01']['buffer_capacity'] == 1


def test_search_and_stochastic_simulation_seeds_are_reproducible():
    line = make_line()
    line.stations[0].sampling_rate = 1
    line.stations[0].defect_rate = .3
    a = optimize(line, generations=2, population=3, duration_hours=.01,
                 search_seed=11, simulation_seed=22)
    b = optimize(line, generations=2, population=3, duration_hours=.01,
                 search_seed=11, simulation_seed=22)
    assert a == b
    assert all(row['search_seed'] == 11 and row['simulation_seed'] == 22 for row in a)


def test_liquid_optimization_and_cancel_are_explicit():
    line = make_line()
    line.production_type = ProductionType.LIQUID_FILLING
    with pytest.raises(ValueError, match='烟油'):
        optimize(line)
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        optimize(make_line(), cancel_event=event)
