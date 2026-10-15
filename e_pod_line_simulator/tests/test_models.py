"""数据模型单元测试"""

import pytest

from src.models import (
    Batch,
    BatchStatus,
    Alert,
    CollaborationType,
    JobRole,
    ProductionLine,
    ProductionType,
    Recipe,
    Scenario,
    Station,
    Tank,
    create_liquid_line,
    create_pouch_line,
)


def make_station(
    sid="s01",
    name="注油",
    process_time=25.0,
    workers=2,
    collab=CollaborationType.PARALLEL,
    oee=0.85,
):
    return Station(
        id=sid,
        name=name,
        process_time=process_time,
        worker_count=workers,
        collaboration_type=collab,
        oee=oee,
        efficiency=0.95,
    )


def test_station_capacity_parallel():
    station = make_station()
    expected = (3600 / 25) * 2 * 0.85 * 0.95
    assert station.get_capacity() == pytest.approx(expected, rel=1e-6)


def test_alert_severity_is_normalized_for_filtering():
    alert = Alert("material_shortage", " WARNING ", "", "缺料")
    assert alert.severity == "warning"
    assert alert.to_dict()["severity"] == "warning"


def test_station_capacity_collaborative_ignores_workers():
    station = make_station(workers=4, collab=CollaborationType.COLLABORATIVE)
    single = make_station(workers=1, collab=CollaborationType.COLLABORATIVE)
    assert station.get_capacity() == single.get_capacity()


def test_production_line_kpis():
    line = ProductionLine("测试产线")
    line.add_station(make_station("s01", "注油", 25, 2))
    line.add_station(make_station("s02", "焊接", 30, 3))
    bottleneck = line.find_bottleneck()
    assert bottleneck is not None
    assert bottleneck.id == "s01"
    assert line.get_bottleneck_capacity() > 0
    assert line.calculate_daily_output() > 0
    assert line.calculate_total_cost() > 0
    assert line.calculate_unit_cost() > 0
    assert 0 < line.calculate_line_balance_rate() <= 1
    assert line.calculate_upph() > 0


def test_daily_cost_and_output_formula():
    line = ProductionLine(
        "成本测试",
        shift_hours=8,
        break_minutes=60,
        worker_hourly_wage=20.0,
    )
    line.add_station(make_station("s01", "注油", 25, 3))

    # 日成本 = 总人数 × 时薪 × 班次时长（不扣休息）
    assert line.calculate_total_cost() == 3 * 20.0 * 8

    # 日产量在真实班次内扣除休息与一次计划换型，不固定按8h摊销。
    effective_hours = 8 - 60 / 60 - 45 / 60
    assert line.calculate_daily_output() == pytest.approx(
        line.get_bottleneck_capacity() * effective_hours,
        rel=1e-9,
    )


def test_production_line_empty_returns_zero():
    line = ProductionLine("空产线")
    assert line.find_bottleneck() is None
    assert line.get_bottleneck_capacity() == 0
    assert line.calculate_daily_output() == 0
    assert line.calculate_upph() == 0


def test_production_line_duplicate_id_raises():
    line = ProductionLine("测试")
    line.add_station(make_station("s01"))
    with pytest.raises(ValueError):
        line.add_station(make_station("s01"))


def test_production_line_serialization_roundtrip():
    line = ProductionLine("序列化测试", shift_hours=10, break_minutes=30, worker_hourly_wage=25.0)
    line.add_station(make_station())
    line.add_station(make_station("s02", "包装", 15, 2))

    restored = ProductionLine.from_dict(line.to_dict())
    assert restored.name == line.name
    assert restored.shift_hours == 10
    assert len(restored.stations) == 2
    assert restored.stations[0].id == "s01"


def test_scenario_snapshot_is_independent():
    line = ProductionLine("方案测试")
    line.add_station(make_station())
    scenario = Scenario.create("方案A", line, "描述")

    line.stations[0].worker_count = 9
    assert scenario.production_line.stations[0].worker_count == 2
    kpis = scenario.get_kpis()
    assert kpis["total_workers"] == 2


def test_alert_and_simulation_state_serialization():
    from src.models import Alert, SimulationState

    alert = Alert("bottleneck", "critical", "s01", "瓶颈", "建议", 12.5)
    data = alert.to_dict()
    assert data["timestamp_minutes"] == 12.5

    state = SimulationState(current_time=60, station_states={"s01": {}}, total_output=10)
    assert state.to_dict()["total_output"] == 10


def test_production_type_default_and_serialization():
    line = ProductionLine("默认产线")
    assert line.production_type == ProductionType.ASSEMBLY
    line.add_station(make_station())

    data = line.to_dict()
    assert data["production_type"] == "assembly"
    restored = ProductionLine.from_dict(data)
    assert restored.production_type == ProductionType.ASSEMBLY

    line.production_type = ProductionType.LIQUID_FILLING
    restored = ProductionLine.from_dict(line.to_dict())
    assert restored.production_type == ProductionType.LIQUID_FILLING
    assert line.get_unit() == "千克"
    assert restored.get_unit() == "千克"


def test_unit_follows_production_type():
    assert ProductionLine("A").get_unit() == "颗"
    assert create_liquid_line().get_unit() == "千克"
    assert create_pouch_line().get_unit() == "袋"


def test_pouch_station_machine_takt_capacity():
    station = Station(
        id="p01",
        name="填充机",
        process_time=1.0,  # 机台节拍模式下不使用
        worker_count=3,     # 操作员人数
        machine_count=3,    # 设备台数独立配置
        machine_takt=6.0,   # 6 秒/袋
        oee=0.9,
        efficiency=1.0,
        changeover_time=0,
    )
    # 理论产能 = 3600/6 * 3 = 1800 袋/h；×OEE 0.9 = 1620
    assert station.get_capacity() == pytest.approx(1620.0, rel=1e-6)


def test_job_role_and_quality_fields_serialization():
    station = Station(
        id="q01",
        name="QC",
        process_time=30,
        worker_count=1,
        job_role=JobRole.QC_TECHNICIAN,
        cleanroom_zone="C",
        sampling_rate=0.2,
        defect_rate=0.01,
        rework_minutes=10,
        clean_time_minutes=30,
    )
    restored = Station.from_dict(station.to_dict())
    assert restored.job_role == JobRole.QC_TECHNICIAN
    assert restored.cleanroom_zone == "C"
    assert restored.sampling_rate == 0.2
    assert restored.rework_minutes == 10
    assert restored.clean_time_minutes == 30


def test_recipe_tank_batch_roundtrip():
    recipe = Recipe(
        "经典烟草", batch_volume_l=500, yield_rate=0.95,
        nicotine_concentration=20.0, flavor="经典",
        ingredients={"尼古丁": 20.0}, mixing_time_min=60,
        aging_time_min=240, filling_rate_l_per_h=800,
        qc_time_min=30, clean_time_min=60,
    )
    tank = Tank("T01", "调配罐", 2000.0, 0.0)
    batch = Batch("B001", "经典烟草", 500.0, BatchStatus.QUEUED)

    assert Recipe.from_dict(recipe.to_dict()) == recipe
    assert Tank.from_dict(tank.to_dict()) == tank
    restored = Batch.from_dict(batch.to_dict())
    assert restored.status == BatchStatus.QUEUED


def test_liquid_template_line():
    line = create_liquid_line()
    assert line.production_type == ProductionType.LIQUID_FILLING
    assert len(line.recipes) == 1
    assert len(line.tanks) == 2
    assert len(line.batches) == 1
    assert line.cleanroom_limits["C"] == 6
    assert line.labor_config[JobRole.QC_TECHNICIAN.value] == 1

    restored = ProductionLine.from_dict(line.to_dict())
    assert restored.production_type == ProductionType.LIQUID_FILLING
    assert restored.recipes[0].name == "经典烟草"
    assert restored.tanks[0].id == "T01"
    assert restored.batches[0].recipe_name == "经典烟草"
    assert restored.cleanroom_limits["C"] == 6


def test_pouch_template_line_machine_takt():
    line = create_pouch_line()
    assert line.production_type == ProductionType.POUCH_PACKAGING
    filling = line.get_station("p01")
    assert filling.machine_takt == 1.5
    # 生产小时能力不摊销停机；每日总量在产线日历中另扣换型/清洗/休息。
    assert filling.machine_count == 2
    assert filling.get_capacity() == pytest.approx(4104.0, rel=1e-6)


def test_labor_validation():
    line = create_liquid_line()
    assert line.validate_labor() == (True, "")

    # 洁净区超限
    line.cleanroom_limits = {"C": 1}
    ok, msg = line.validate_labor()
    assert ok is False and "超过上限" in msg
    line.cleanroom_limits = {"C": 6}

    # 未知工种
    line.labor_config["bogus"] = 1
    ok, msg = line.validate_labor()
    assert ok is False and "未知工种" in msg
    del line.labor_config["bogus"]

    # 技能矩阵未知角色
    line.skill_matrix["bogus"] = []
    ok, msg = line.validate_labor()
    assert ok is False and "技能矩阵" in msg


def test_machine_count_is_separate_from_operator_count():
    station = Station("m", "machine", 1, 2, machine_takt=6, machine_count=3,
                      oee=1, efficiency=1, changeover_time=0)
    assert station.get_capacity() == 1800
    station.worker_count = 8
    assert station.get_capacity() == 1800
    station.machine_count = 4
    assert station.get_capacity() == 2400


def test_old_machine_configuration_migrates_once_without_mutating_input():
    data = {"id": "m", "name": "machine", "process_time": 1,
            "worker_count": 3, "machine_takt": 6, "collaboration_type": "parallel"}
    restored = Station.from_dict(data)
    assert restored.machine_count == restored.worker_count == 3
    assert "machine_count" not in data
    assert data["collaboration_type"] == "parallel"
    assert Station.from_dict(restored.to_dict()).machine_count == 3
    assert Station("new", "new", 1, 3, machine_takt=6).machine_count == 1


def test_configuration_revision_ignores_runtime_but_detects_real_inputs():
    line = ProductionLine("revision")
    line.add_station(make_station())
    revision = line.configuration_revision()
    line.stations[0].current_status = "running"
    line.stations[0].wip_count = 100
    assert line.configuration_revision() == revision
    line.stations[0].worker_count += 1
    assert line.configuration_revision() != revision


def test_assembly_support_roles_are_in_daily_cost_and_upph():
    line = ProductionLine("supports", shift_hours=8)
    line.add_station(make_station(workers=2))
    line.labor_config = {"general": 2, "cleaner": 1}
    assert line.total_worker_count() == 3
    assert line.calculate_total_cost() == 3 * 20 * 8
    assert line.calculate_upph() == line.get_bottleneck_capacity() / 3
