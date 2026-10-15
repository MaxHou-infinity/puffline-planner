"""人力规划模块单元测试（V3.2 P0，headless）"""

import pytest

from src.models import CollaborationType, JobRole, ProductionLine, ProductionType, Station
from src.hr_planning import (
    LaborCostConfig,
    LearningCurveConfig,
    ShiftPlan,
    build_hr_summary,
    calculate_labor_costs,
    current_headcount_by_role,
    ramp_days_to_full,
    required_headcount_by_role,
    required_headcount_by_station,
    weekly_hiring_gap,
)


def make_assembly_line(worker_count=1, process_time=60.0):
    line = ProductionLine("装配线", shift_hours=8, break_minutes=60)
    line.add_station(Station(
        "s01", "组装", process_time, worker_count,
        oee=1.0, efficiency=1.0, changeover_time=0,
    ))
    return line


def test_required_headcount_parallel_scales_with_target():
    line = make_assembly_line()
    shift = ShiftPlan(shifts_per_day=1, shift_hours=8, break_minutes=60)
    # 有效 7h；单人工位产能 60/h
    assert required_headcount_by_station(line, 420, shift)["s01"] == 1
    assert required_headcount_by_station(line, 840, shift)["s01"] == 2


def test_collaborative_headcount_keeps_team():
    line = ProductionLine("协同线")
    line.add_station(Station(
        "s01", "组装", 30.0, 3,
        collaboration_type=CollaborationType.COLLABORATIVE,
        oee=1.0, efficiency=1.0, changeover_time=0,
    ))
    shift = ShiftPlan(shifts_per_day=1, shift_hours=8, break_minutes=60)
    assert required_headcount_by_station(line, 100000, shift)["s01"] == 3


def test_labor_costs_formula():
    line = make_assembly_line()
    shift = ShiftPlan(shifts_per_day=1, shift_hours=8, break_minutes=60)
    cost = LaborCostConfig(
        base_hourly_wage=20.0,
        social_insurance_rate=0.30,
        absence_rate=0.0,
    )
    result = calculate_labor_costs(line, 10, shift, cost)
    # 208 元/人·天 × 10 = 2080；月 = 54080
    assert result["daily_labor_cost"] == pytest.approx(2080.0)
    assert result["monthly_labor_cost"] == pytest.approx(54080.0)

    cost_absent = LaborCostConfig(
        base_hourly_wage=20.0,
        social_insurance_rate=0.30,
        absence_rate=0.1,
    )
    result_absent = calculate_labor_costs(line, 10, shift, cost_absent)
    assert result_absent["covered_headcount"] == 12


def test_weekly_hiring_gap_timeline():
    required = {"filling_operator": 5, "qc_technician": 1}
    current = {"filling_operator": 2, "qc_technician": 1}
    plan = [(1, "filling_operator", 2), (3, "filling_operator", 1)]
    rows = weekly_hiring_gap(required, current, plan, weeks=4)
    assert rows[0]["gap"]["filling_operator"] == 1
    assert rows[1]["gap"]["filling_operator"] == 1
    assert rows[2]["gap"]["filling_operator"] == 0
    assert rows[3]["total_gap"] == 0
    # 新增口径：第 1 周新增 1，后续无新增（不是每周重复招 1 人）
    assert rows[0]["new_gap"] == 1
    assert rows[1]["new_gap"] == 0
    assert rows[2]["new_gap"] == 0


def test_ramp_days_to_full():
    line = make_assembly_line(worker_count=8)
    shift = ShiftPlan(shifts_per_day=1, shift_hours=8, break_minutes=60)
    required = {"general": 10}
    current = {"general": 8}
    learning = LearningCurveConfig(
        ramp_days=10, start_efficiency=0.5, target_efficiency=1.0
    )
    days = ramp_days_to_full(line, 0, shift, required, current, learning)
    assert days == 10


def test_build_hr_summary_shape():
    line = make_assembly_line(worker_count=2)
    shift = ShiftPlan(shifts_per_day=2, shift_hours=8, break_minutes=60)
    cost = LaborCostConfig()
    learning = LearningCurveConfig(ramp_days=30)
    summary = build_hr_summary(
        line, 2000, shift, cost, learning,
        current={"general": 2}, hiring_plan=[],
    )
    assert summary["total_headcount"] >= 2
    assert "headcount_by_station" in summary
    assert "headcount_by_role" in summary
    assert summary["costs"]["daily_labor_cost"] > 0
    assert len(summary["weekly_gap"]) == 12
    assert summary["days_to_full"] is None
    assert not summary['feasible']
    assert "current_headcount" in summary
    assert "current_total" in summary
    assert "initial_gap" in summary
    assert "required_hourly" in summary
    assert "station_derivation" in summary
    assert "excess_headcount" in summary
    assert "one_time_hiring" in summary
    first = summary["station_derivation"][0]
    assert "process_time" in first and "oee" in first
    assert "efficiency" in first and "changeover_minutes" in first


def test_current_headcount_by_role_sums_stations():
    line = ProductionLine("在岗测试")
    line.add_station(Station("s01", "镭雕", 2.0, 5))
    line.add_station(Station("s02", "注油", 2.0, 1))
    line.add_station(Station("s03", "包装", 2.0, 2))

    current = current_headcount_by_role(line)
    assert current == {"general": 8}


def test_empty_current_means_zero_staff():
    """回归：当前在岗为空按 0 计，缺口=需求，达产需要招聘"""
    line = ProductionLine("方案-1", shift_hours=8, break_minutes=60)
    for i, workers in enumerate([5, 1, 1, 1, 1, 1, 2, 2]):
        line.add_station(Station(f"s{i:02d}", f"工序{i}", 60.0, workers))
    shift = ShiftPlan(shifts_per_day=1, shift_hours=8, break_minutes=60)
    summary = build_hr_summary(
        line, 100, shift, LaborCostConfig(), LearningCurveConfig(),
        current={}, hiring_plan=[],
    )
    assert summary["current_total"] == 0
    assert summary["initial_gap"] == summary["total_headcount"]
    assert summary["one_time_hiring"] == summary["total_headcount"]
    assert summary["days_to_full"] is None
    assert summary["weekly_gap"][0]["new_gap"] == summary["total_headcount"]
    assert summary["weekly_gap"][1]["new_gap"] == 0
    assert summary["excess_headcount"] == 0
    assert len(summary["station_derivation"]) == 8


def test_two_shifts_require_two_crews_and_same_paid_hours():
    line = make_assembly_line()
    cost = LaborCostConfig(social_insurance_rate=0)
    summaries = [build_hr_summary(
        line, 840, ShiftPlan(shifts_per_day=shifts), cost,
        LearningCurveConfig(), {'general': 2}, [],
    ) for shifts in (1, 2)]
    assert [row['headcount_per_shift'] for row in summaries] == [2, 1]
    assert [row['total_headcount'] for row in summaries] == [2, 2]
    assert [row['headcount_by_role']['general'] for row in summaries] == [2, 2]
    assert [row['costs']['daily_labor_cost'] for row in summaries] == [320, 320]
    assert all(row['costs']['per_unit_labor_cost'] == pytest.approx(320 / 840, abs=.0001)
               for row in summaries)


def test_support_roles_enter_total_cost_and_absence_coverage():
    line = make_assembly_line()
    line.labor_config = {'cleaner': 2}
    summary = build_hr_summary(
        line, 420, ShiftPlan(), LaborCostConfig(social_insurance_rate=0),
        LearningCurveConfig(), {'general': 1, 'cleaner': 2}, [],
    )
    assert summary['total_headcount'] == 3
    assert summary['headcount_per_shift'] == 3
    assert summary['excess_headcount'] == 0
    assert summary['costs']['daily_labor_cost'] == 480
    covered = build_hr_summary(
        line, 420, ShiftPlan(), LaborCostConfig(social_insurance_rate=0, absence_rate=.1),
        LearningCurveConfig(), {'general': 1, 'cleaner': 2}, [],
    )
    assert covered['costs']['covered_headcount_by_role'] == {'general': 2, 'cleaner': 3}
    assert covered['costs']['covered_headcount'] == 5
    assert covered['costs']['daily_labor_cost'] == 800


def test_absence_coverage_drives_hiring_gaps_and_feasibility():
    line = make_assembly_line()
    line.labor_config = {'cleaner': 2}
    summary = build_hr_summary(
        line, 420, ShiftPlan(), LaborCostConfig(social_insurance_rate=0, absence_rate=.1),
        LearningCurveConfig(), {'general': 1, 'cleaner': 2}, [],
    )
    assert summary['total_headcount'] == 3
    assert summary['headcount_by_role'] == {'general': 1, 'cleaner': 2}
    assert summary['covered_headcount'] == summary['costs']['covered_headcount'] == 5
    assert summary['covered_headcount_by_role'] == {'general': 2, 'cleaner': 3}
    assert summary['initial_gap'] == summary['one_time_hiring'] == 2
    assert summary['weekly_gap'][0]['gap'] == {'general': 1, 'cleaner': 1}
    assert summary['excess_headcount'] == 0
    assert summary['days_to_full'] is None
    assert summary['capacity_feasible']
    assert not summary['feasible']


def test_absence_coverage_hiring_keeps_roles_and_shifts_independent():
    line = make_assembly_line()
    line.labor_config = {'cleaner': 2}
    summary = build_hr_summary(
        line, 840, ShiftPlan(shifts_per_day=2),
        LaborCostConfig(social_insurance_rate=0, absence_rate=.1),
        LearningCurveConfig(ramp_days=5, start_efficiency=.5),
        {'general': 2, 'cleaner': 4}, [(1, 'general', 2), (2, 'cleaner', 2)],
    )
    assert summary['headcount_per_shift'] == 3
    assert summary['total_headcount'] == 6
    assert summary['covered_headcount_per_shift'] == 5
    assert summary['covered_headcount'] == 10
    assert summary['covered_headcount_by_role_per_shift'] == {'general': 2, 'cleaner': 3}
    assert summary['covered_headcount_by_role'] == {'general': 4, 'cleaner': 6}
    assert summary['initial_gap'] == 4
    assert summary['one_time_hiring'] == 0
    assert summary['weekly_gap'][0]['gap'] == {'general': 0, 'cleaner': 2}
    assert summary['days_to_full'] == 12
    assert summary['feasible']
    assert summary['costs']['daily_labor_cost'] == 1600


def test_extra_operator_cannot_replace_absence_cover_for_support_role():
    line = make_assembly_line()
    line.labor_config = {'cleaner': 2}
    summary = build_hr_summary(
        line, 420, ShiftPlan(), LaborCostConfig(social_insurance_rate=0, absence_rate=.1),
        LearningCurveConfig(), {'general': 3, 'cleaner': 2}, [],
    )
    assert summary['current_total'] == summary['covered_headcount'] == 5
    assert summary['initial_gap'] == summary['one_time_hiring'] == 1
    assert summary['excess_headcount'] == 1
    assert summary['days_to_full'] is None
    assert not summary['feasible']


def test_turnover_budget_replaces_absence_covered_staff():
    line = make_assembly_line()
    costs = calculate_labor_costs(
        line, 3, ShiftPlan(),
        LaborCostConfig(absence_rate=.1, monthly_turnover_rate=.25,
                        recruitment_cost_per_head=100, training_cost_per_head=50),
        headcount_by_role_per_shift={'general': 1, 'cleaner': 2},
    )
    assert costs['covered_headcount'] == 5
    assert costs['monthly_recruit_training'] == 300


def test_role_specific_ramp_does_not_use_extra_operators_as_qc():
    line = make_assembly_line()
    learning = LearningCurveConfig(ramp_days=10)
    required = {'general': 2, 'qc_technician': 1}
    current = {'general': 3}
    assert ramp_days_to_full(line, 420, ShiftPlan(), required, current, learning) == 10
    assert ramp_days_to_full(line, 420, ShiftPlan(), required, current, learning, []) is None
    assert ramp_days_to_full(
        line, 420, ShiftPlan(), required, current, learning, [(4, 'qc_technician', 1)]
    ) == 31


def test_hr_reports_machine_and_collaborative_capacity_constraints():
    for machine, collaboration in [(1, CollaborationType.PARALLEL),
                                   (None, CollaborationType.COLLABORATIVE)]:
        line = make_assembly_line(worker_count=3, process_time=60)
        station = line.stations[0]
        station.collaboration_type = collaboration
        station.machine_takt = machine
        summary = build_hr_summary(
            line, 100000, ShiftPlan(), LaborCostConfig(), LearningCurveConfig(),
            {'general': 3}, [],
        )
        assert summary['headcount_by_station']['s01'] == 3
        assert not summary['capacity_feasible']
        assert not summary['feasible']
        assert summary['days_to_full'] is None
        assert any('加人不能' in reason for reason in summary['constraints'])


def test_hr_rejects_unmodeled_liquid_and_invalid_shift_plans():
    line = make_assembly_line()
    line.production_type = ProductionType.LIQUID_FILLING
    with pytest.raises(ValueError, match='烟油'):
        required_headcount_by_station(line, 100, ShiftPlan())
    line.production_type = ProductionType.ASSEMBLY
    for shift in [ShiftPlan(shifts_per_day=0), ShiftPlan(shifts_per_day=4),
                  ShiftPlan(break_minutes=480)]:
        with pytest.raises(ValueError, match='班次'):
            required_headcount_by_station(line, 420, shift)


def test_role_shortage_is_not_hidden_by_total_excess_in_summary():
    line = make_assembly_line(worker_count=2)
    line.stations[0].job_role = JobRole.GENERAL
    line.labor_config = {'qc_technician': 1}
    summary = build_hr_summary(
        line, 840, ShiftPlan(), LaborCostConfig(), LearningCurveConfig(ramp_days=5),
        {'general': 3}, [(2, 'qc_technician', 1)],
    )
    assert summary['initial_gap'] == 1
    assert summary['excess_headcount'] == 1
    assert summary['one_time_hiring'] == 0
    assert summary['days_to_full'] == 12
    assert summary['feasible']
