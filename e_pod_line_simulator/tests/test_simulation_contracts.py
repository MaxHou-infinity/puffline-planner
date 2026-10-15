"""V3.4 acceptance contracts: conservation, time windows and resource semantics."""

import copy
import threading
from concurrent.futures import CancelledError

import pytest

from src.models import (
    Batch, CollaborationType, Material, ProductionLine, ProductionType, Recipe, Station, Tank,
)
from src.simulation import SimulationEngine, detect_waste


def discrete_line(stages=1, workers=1, shift_hours=1):
    line = ProductionLine("contract", shift_hours=shift_hours, break_minutes=0)
    for index in range(stages):
        line.add_station(Station(
            str(index), "station-" + str(index), 1, workers,
            oee=1, efficiency=1, changeover_time=0, buffer_capacity=2,
        ))
    return line


@pytest.mark.parametrize("stages,expected", [(1, 9), (2, 8), (3, 7)])
def test_total_output_is_terminal_good_output(stages, expected):
    result = SimulationEngine(discrete_line(stages)).run_sync(10 / 3600)
    assert result.total_output == expected
    assert result.total_output == result.station_outputs[str(stages - 1)]


def test_upstream_capacity_cannot_multiply_finished_goods():
    results = []
    for workers in (1, 8):
        line = discrete_line(2)
        line.stations[0].worker_count = workers
        results.append(SimulationEngine(line).run_sync(10 / 3600))
    assert results[0].total_output == results[1].total_output == 8


def test_failed_first_station_never_feeds_downstream():
    line = discrete_line(2)
    line.stations[0].sampling_rate = line.stations[0].defect_rate = 1
    result = SimulationEngine(line).run_sync(10 / 3600)
    assert result.total_output == result.station_outputs["1"] == 0
    assert result.station_metrics["0"]["scrap_count"] == 9


def test_middle_rework_preserves_items_with_full_small_buffer():
    line = discrete_line(3)
    line.stations[0].bom = {"part": 1}
    line.materials = [Material("part", initial_stock=20)]
    line.stations[1].sampling_rate = 1
    line.stations[1].defect_rate = .5
    line.stations[1].rework_minutes = .01
    result = SimulationEngine(line).run_sync(120 / 3600)
    scrap = result.station_metrics["1"]["scrap_count"]
    assert 0 < scrap < 20
    assert result.total_output + scrap == 20
    assert result.inventory["part"] == 0
    assert all(wip == 0 for wip in result.station_wips.values())
    assert len([e for e in result.material_events if e["type"] == "consume"]) == 20


def liquid_line(quantity=100):
    line = ProductionLine("liquid", production_type=ProductionType.LIQUID_FILLING,
                          shift_hours=8, break_minutes=0)
    line.recipes = [Recipe(
        "A", batch_volume_l=100, yield_rate=.95, ingredients={"raw": 100},
        mixing_time_min=0, aging_time_min=0, qc_time_min=0,
        filling_rate_l_per_h=100, clean_time_min=0,
    )]
    line.batches = [Batch("B", "A", quantity)]
    line.materials = [Material("raw", initial_stock=1000)]
    line.tanks = [Tank("T", "tank", 10000)]
    return line


@pytest.mark.parametrize("quantity", [10, 100, 200])
def test_actual_batch_quantity_drives_materials_time_and_released_output(quantity):
    result = SimulationEngine(liquid_line(quantity)).run_sync(3)
    assert sum(e["quantity"] for e in result.material_events if e["type"] == "consume") == quantity
    assert result.batch_results[0]["cycle_min"] == pytest.approx(quantity / 100 * 60)
    assert result.total_output == pytest.approx(quantity * .95)
    assert result.total_output == result.kpis["shippable_quantity"]
    assert result.station_metrics == {}
    assert result.model_scope == "recipe_batch_flow_no_station_staffing_capacity"


def test_periodic_cip_cannot_bypass_recipe_change_matrix():
    line = liquid_line(10)
    line.recipes.append(Recipe(
        "B", batch_volume_l=100, yield_rate=1, mixing_time_min=0,
        aging_time_min=0, qc_time_min=0, filling_rate_l_per_h=100,
        clean_time_min=30,
    ))
    line.batches.append(Batch("B2", "B", 10))
    line.cip_interval_batches = 1
    line.changeover_matrix = {"A": {"B": 120}}
    result = SimulationEngine(line).run_sync(4)
    assert len(result.cleaning_events) == 1
    assert result.cleaning_events[0]["clean_min"] == 120
    assert result.cleaning_events[0]["recipe_from"] == "A"
    assert result.cleaning_events[0]["periodic"] is True
    assert result.batch_results[1]["start_time"] >= 126 * 60


def test_operator_count_does_not_change_liquid_batch_speed():
    a = liquid_line()
    a.stations = [Station("s", "operator", 1, 1, changeover_time=0)]
    b = copy.deepcopy(a)
    b.stations[0].worker_count = 8
    ra = SimulationEngine(a).run_sync(2)
    rb = SimulationEngine(b).run_sync(2)
    assert ra.total_output == rb.total_output == 95
    assert ra.batch_results == rb.batch_results
    assert rb.kpis["actual_labor_cost"] == ra.kpis["actual_labor_cost"] * 8


def test_full_shift_deducts_break_changeover_and_cleaning_once():
    line = discrete_line()
    station = line.stations[0]
    station.process_time = 60
    station.changeover_time = 10
    station.clean_time_minutes = 5
    line.break_minutes = 15
    result = SimulationEngine(line).run_sync(1)
    assert result.total_output == line.calculate_daily_output() == 30
    assert result.station_metrics["0"]["utilization"] == pytest.approx(.5)


def test_warmup_uses_one_window_for_output_materials_labor_and_resource_time():
    line = discrete_line(workers=3)
    line.stations[0].bom = {"part": 1}
    line.materials = [Material("part", initial_stock=1000, unit_cost=2)]
    result = SimulationEngine(line).run_sync(100 / 3600, 50 / 60)
    assert result.elapsed_seconds == 100
    assert result.measurement_seconds == result.warmup_seconds == 50
    assert result.total_output == 150
    assert result.kpis["actual_labor_cost"] == pytest.approx(3 * 20 * 50 / 3600, abs=1e-6)
    assert result.kpis["material_cost"] == 150 * 2
    assert result.station_metrics["0"]["running_sec"] == 150
    assert result.station_metrics["0"]["utilization"] == 1
    assert result.kpis["actual_unit_cost"] == pytest.approx(20 / 3600)


@pytest.mark.parametrize("warmup", [1, 2, float("nan"), float("inf"), -.1])
def test_invalid_warmup_rejected_before_running(warmup):
    engine = SimulationEngine(discrete_line())
    with pytest.raises(ValueError):
        engine.run_sync(1 / 60, warmup)
    assert not engine.is_running


def test_parallel_time_metrics_are_bounded_and_independent_of_resource_count():
    metrics = [SimulationEngine(discrete_line(workers=workers)).run_sync(10 / 3600)
               .station_metrics["0"] for workers in (1, 2, 3)]
    for metric in metrics:
        assert metric["utilization"] == 1
        assert metric["oee_total"] == pytest.approx(.9)
        for key in ("oee_availability", "oee_performance", "oee_quality", "oee_total"):
            assert 0 <= metric[key] <= 1


@pytest.mark.parametrize("attribute", ["oee", "efficiency"])
def test_zero_efficiency_models_a_stopped_station(attribute):
    line = discrete_line(2)
    setattr(line.stations[0], attribute, 0)
    result = SimulationEngine(line).run_sync(10 / 3600)
    assert result.total_output == 0
    assert result.kpis["actual_unit_cost"] is None
    assert result.station_metrics["0"]["utilization"] == 0


def test_result_metadata_and_complete_config_can_replay_the_run():
    line = discrete_line(2)
    line.material_arrivals = []
    line.labor_config = {"cleaner": 2}
    engine = SimulationEngine(copy.deepcopy(line))
    engine.random_seed = 7
    result = engine.run_sync(10 / 3600)
    restored = ProductionLine.from_dict(result.line_config)
    assert result.config_revision == restored.configuration_revision()
    assert result.config_revision == line.configuration_revision()
    assert result.run_id and result.random_seed == 7
    assert result.status == "completed"
    assert result.line_config["labor_config"] == {"cleaner": 2}
    assert result.kpis["actual_labor_cost"] == pytest.approx(4 * 20 * 10 / 3600, abs=1e-6)


def test_cancel_is_explicit_and_never_returns_a_completed_result():
    cancel = threading.Event()
    cancel.set()
    engine = SimulationEngine(discrete_line())
    with pytest.raises(CancelledError):
        engine.run_sync(8, cancel_event=cancel)
    assert engine.status == "cancelled"
    assert not engine.is_running


def test_stopped_thread_result_uses_actual_elapsed_labor_cost():
    engine = SimulationEngine(discrete_line())
    engine.set_callback(lambda state: engine.stop() if state.current_time >= 10 else None)
    engine.run(1, speed=1000)
    engine._sim_thread.join(timeout=2)
    assert not engine._sim_thread.is_alive()
    result = engine.build_result()
    assert result.status == "stopped"
    assert 10 <= result.elapsed_seconds < result.duration_seconds
    assert result.kpis["actual_labor_cost"] == pytest.approx(
        20 * result.elapsed_seconds / 3600, abs=1e-6
    )


def test_missing_recipe_is_an_error_instead_of_a_released_zero_quantity_batch():
    line = liquid_line()
    line.batches[0].recipe_name = "missing"
    with pytest.raises(ValueError, match="配方不存在"):
        SimulationEngine(line).run_sync(1)


def test_repeated_runs_reset_batch_recipe_tanks_and_time_statistics():
    line = liquid_line()
    engine = SimulationEngine(line)
    first = engine.run_sync(2)
    second = engine.run_sync(2)
    assert first.run_id != second.run_id
    assert first.config_revision == second.config_revision
    assert first.batch_results == second.batch_results
    assert first.inventory == second.inventory
    assert first.total_output == second.total_output


def test_liquid_run_does_not_mutate_reusable_input_configuration():
    line = liquid_line()
    before = line.to_dict()
    revision = line.configuration_revision()
    result = SimulationEngine(line).run_sync(2)
    assert line.to_dict() == before
    assert result.config_revision == line.configuration_revision() == revision


def test_partial_liquid_run_reports_pending_tank_mass_and_incomplete_batch():
    line = liquid_line(100)
    line.recipes[0].yield_rate = 1
    line.recipes[0].filling_rate_l_per_h = 6000
    line.tanks[0].capacity_l = 10
    result = SimulationEngine(line).run_sync(70 / 3600)
    assert result.total_output == 0
    assert result.batch_results == []
    batch = result.incomplete_batches[0]
    assert batch["input_quantity"] == 100
    assert batch["pending_tank_quantity"] == 90
    assert batch["end_time"] == 0
    assert batch["stage"] == "waiting_tank"
    balance = result.liquid_balance
    assert balance["initial_tank_quantity"] + balance["produced_good_quantity"] == pytest.approx(
        balance["drained_quantity"] + balance["closing_tank_quantity"]
        + balance["pending_tank_quantity"]
    )


def test_batch_pass_rate_counts_released_batches_not_defect_probability():
    from src.models import JobRole

    line = liquid_line()
    line.recipes[0].filling_rate_l_per_h = 6000
    line.batches.append(Batch("B2", "A", 100))
    line.stations = [Station("q", "QC", 1, 1, job_role=JobRole.QC_TECHNICIAN,
                             sampling_rate=1, defect_rate=.5, changeover_time=0)]
    engine = SimulationEngine(line)
    engine.random_seed = 0
    result = engine.run_sync(4 / 60)
    assert [batch["status"] for batch in result.batch_results] == ["released", "rejected"]
    assert result.kpis["batch_pass_rate"] == .5
    assert result.batch_results[1]["pass_rate"] == 0


def test_rework_crossing_warmup_uses_final_outcomes_for_quality_denominator():
    line = discrete_line()
    station = line.stations[0]
    station.sampling_rate = 1
    station.defect_rate = .5
    station.rework_minutes = 1 / 3
    engine = SimulationEngine(line)
    engine.random_seed = 9
    result = engine.run_sync(22 / 3600, 10 / 60)
    metric = result.station_metrics["0"]
    assert result.total_output == metric["good_output"] == 1
    assert metric["completed_items"] == 1
    assert metric["oee_quality"] == 1
    assert 0 < metric["oee_performance"] <= 1
    assert 0 < metric["oee_total"] <= 1


def test_waste_does_not_treat_extra_machines_as_redundant_operators():
    machine = Station("m", "machine", 1, 5, machine_takt=1, machine_count=5,
                      oee=1, efficiency=1, changeover_time=0)
    assert detect_waste([machine], 100) == []
    assert detect_waste([machine], 0) == []


@pytest.mark.parametrize('resource_mode', ['parallel', 'machine', 'collaborative'])
def test_ongoing_starvation_alert_uses_processing_resources(resource_mode):
    line = discrete_line(2)
    line.stations[0].oee = 0
    downstream = line.stations[1]
    downstream.worker_count = 3
    expected_resources = 3
    if resource_mode == 'machine':
        downstream.worker_count = 1
        downstream.machine_takt = 1
        downstream.machine_count = 3
    elif resource_mode == 'collaborative':
        downstream.collaboration_type = CollaborationType.COLLABORATIVE
        expected_resources = 1

    result = SimulationEngine(line).run_sync(600 / 3600)

    alerts = [alert for alert in result.alerts if alert.alert_type == 'starvation']
    assert len(alerts) == 1
    assert alerts[0].timestamp_minutes == 5
    assert result.station_metrics['1']['waiting_sec'] == 600 * expected_resources


def test_ongoing_starvation_alert_clips_warmup_without_ending_wait():
    line = discrete_line(2)
    line.stations[0].oee = 0
    result = SimulationEngine(line).run_sync(900 / 3600, warmup_minutes=5)
    alerts = [alert for alert in result.alerts if alert.alert_type == 'starvation']
    assert len(alerts) == 1
    assert alerts[0].timestamp_minutes == 10
    assert result.station_metrics['1']['waiting_sec'] == 600


@pytest.mark.parametrize('machine_count', [1, 3])
def test_ongoing_blockage_alert_counts_active_resource_intervals(machine_count):
    line = discrete_line(2)
    line.stations[0].machine_takt = 1
    line.stations[0].machine_count = machine_count
    line.stations[1].buffer_capacity = 1
    line.stations[1].oee = 0
    result = SimulationEngine(line).run_sync(600 / 3600)
    alerts = [alert for alert in result.alerts
              if alert.alert_type == 'blockage' and alert.station_id == '0'
              and '累计' in alert.message]
    assert len(alerts) == 1
    assert alerts[0].timestamp_minutes == 6
    assert result.station_metrics['0']['blocked_sec'] >= 598 * machine_count


def test_changeover_records_request_and_actual_start_after_current_item():
    line = discrete_line()
    line.stations[0].process_time = 60
    engine = SimulationEngine(line)
    engine.set_callback(lambda state: engine.trigger_changeover('0', 1)
                        if state.current_time == 10 else None)
    result = engine.run_sync(61 / 3600)
    event = result.changeover_events[0]
    assert result.total_output == 1
    assert event['requested_time'] == 10
    assert event['time'] == event['start_time'] == 60
    assert event['status'] == 'started'
    starts = [alert for alert in result.alerts if '开始切换' in alert.message]
    assert len(starts) == 1
    assert starts[0].timestamp_minutes == 1


def test_unstarted_changeover_request_is_not_reported_as_started():
    line = discrete_line()
    line.stations[0].process_time = 60
    engine = SimulationEngine(line)
    engine.set_callback(lambda state: engine.trigger_changeover('0', 1)
                        if state.current_time == 10 else None)
    result = engine.run_sync(30 / 3600)
    event = result.changeover_events[0]
    assert event['requested_time'] == 10
    assert event['start_time'] is None
    assert 'time' not in event
    assert event['status'] == 'queued'
    assert not any('开始切换' in alert.message for alert in result.alerts)


def test_queued_changeovers_record_actual_sequential_start_times():
    engine = SimulationEngine(discrete_line())
    engine.trigger_changeover('0', 1)
    engine.trigger_changeover('0', 1)
    result = engine.run_sync(121 / 3600)
    assert [event['requested_time'] for event in result.changeover_events] == [0, 0]
    assert [event['start_time'] for event in result.changeover_events] == [0, 60]
    assert [event['end_time'] for event in result.changeover_events] == [60, 120]
    assert all(event['status'] == 'completed' for event in result.changeover_events)


def test_repeat_run_does_not_inherit_a_previous_unstarted_changeover():
    line = discrete_line()
    line.stations[0].process_time = 60
    engine = SimulationEngine(line)
    engine.set_callback(lambda state: engine.trigger_changeover('0', 1)
                        if state.current_time == 10 else None)
    previous = engine.run_sync(30 / 3600)
    engine.set_callback(None)
    repeated = engine.run_sync(61 / 3600)
    assert repeated.total_output == 1
    assert repeated.changeover_events == []
    assert not any(alert.alert_type == 'changeover' for alert in repeated.alerts)
    assert previous.changeover_events[0]['start_time'] is None

