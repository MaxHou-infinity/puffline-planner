"""仿真引擎单元测试（headless 模式与切换事件）"""

import time

import pytest

from src.models import (
    CollaborationType,
    ProductionType,
    ProductionLine,
    Recipe,
    Station,
    Tank,
    Batch,
    Material,
    MaterialArrival,
    create_liquid_line,
)
from src.simulation import SimulationEngine, detect_waste


def make_line(name="测试产线"):
    line = ProductionLine(name, shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "注油", 1.0, 1, changeover_time=0))
    line.add_station(Station("s02", "包装", 1.0, 1, changeover_time=0))
    return line


def test_headless_run_returns_result():
    result = SimulationEngine(make_line()).run_sync(duration_hours=1.0)

    assert result.total_output > 0
    assert result.line_name == "测试产线"
    assert result.station_outputs["s01"] > 0
    assert result.kpis["bottleneck_capacity"] > 0
    assert result.duration_seconds == 3600


def test_tank_full_waits_instead_of_silent_drop():
    """V3.2 L2：罐容不足时批次等待并报警，禁止静默丢弃"""
    line = create_liquid_line()
    line.tanks = [Tank("T1", "成品罐", 100.0, 95.0)]
    line.batches = [Batch("B001", "经典烟草", 500.0)]
    recipe = line.recipes[0]
    recipe.mixing_time_min = 10.0
    recipe.aging_time_min = 0.0
    recipe.filling_rate_l_per_h = 200.0
    recipe.qc_time_min = 5.0
    recipe.clean_time_min = 0.0
    # 批次475千克超过100千克罐容，须分段入罐等待配方速率排空。

    result = SimulationEngine(line).run_sync(duration_hours=6.0)

    assert any(
        b["batch_id"] == "B001" and b["yield_l"] > 0
        for b in result.batch_results
    )
    assert any(a.alert_type == "tank_full" for a in result.alerts)
    assert all(t.current_level_l <= t.capacity_l + 1e-6 for t in line.tanks)


def test_sequential_batches_with_periodic_cip_by_interval():
    """V3.2 L3：批次按序执行，按批次数触发周期性 CIP"""
    line = ProductionLine("周期CIP测试", production_type=ProductionType.LIQUID_FILLING)
    line.recipes.append(Recipe(
        name="配方A", batch_volume_l=100, yield_rate=0.95,
        mixing_time_min=10, aging_time_min=0,
        filling_rate_l_per_h=600, qc_time_min=5, clean_time_min=30,
    ))
    line.tanks.append(Tank("T01", "调配罐", 10000, 0))
    line.batches.append(Batch("B001", "配方A", 100))
    line.batches.append(Batch("B002", "配方A", 100))
    line.batches.append(Batch("B003", "配方A", 100))
    line.cip_interval_batches = 2

    result = SimulationEngine(line).run_sync(duration_hours=24.0)

    periodic = [e for e in result.cleaning_events if e.get("reason") == "periodic"]
    assert len(periodic) == 1
    assert periodic[0]["recipe_to"] == "配方A"
    assert len(result.batch_results) == 3
    # 顺序执行：后一批在前一批放行后开始
    assert result.batch_results[1]["start_time"] >= result.batch_results[0]["end_time"]
    assert result.batch_results[2]["start_time"] >= result.batch_results[1]["end_time"]


def test_periodic_cip_by_hours():
    """V3.2 L3：按运行小时数触发周期性 CIP"""
    line = ProductionLine("周期CIP小时", production_type=ProductionType.LIQUID_FILLING)
    line.recipes.append(Recipe(
        name="配方A", batch_volume_l=100, yield_rate=0.95,
        mixing_time_min=50, aging_time_min=0,
        filling_rate_l_per_h=600, qc_time_min=5, clean_time_min=30,
    ))
    line.tanks.append(Tank("T01", "调配罐", 10000, 0))
    line.batches.append(Batch("B001", "配方A", 100))
    line.batches.append(Batch("B002", "配方A", 100))
    line.cip_interval_hours = 1.0

    result = SimulationEngine(line).run_sync(duration_hours=24.0)

    periodic = [e for e in result.cleaning_events if e.get("reason") == "periodic"]
    assert len(periodic) >= 1
    assert periodic[0]["clean_min"] == 30
    assert len(result.batch_results) == 2


def test_material_shortage_blocks_batch_until_arrival():
    """V3.2 L4：原料不足时批次阻塞，到货后恢复"""
    line = ProductionLine("原料测试", production_type=ProductionType.LIQUID_FILLING)
    line.recipes.append(Recipe(
        name="配方A", batch_volume_l=100, yield_rate=0.95,
        mixing_time_min=10, aging_time_min=0,
        filling_rate_l_per_h=600, qc_time_min=5, clean_time_min=0,
        ingredients={"尼古丁": 20.0, "丙二醇": 400.0, "香料": 80.0},
    ))
    line.materials.append(Material("尼古丁", "kg", 0.0))
    line.materials.append(Material("丙二醇", "kg", 1000.0))
    line.materials.append(Material("香料", "kg", 1000.0))
    line.inventory = {"尼古丁": 0.0, "丙二醇": 1000.0, "香料": 1000.0}
    line.material_arrivals.append(MaterialArrival(30.0, "尼古丁", 50.0))
    line.tanks.append(Tank("T01", "成品罐", 10000, 0))
    line.batches.append(Batch("B001", "配方A", 100))

    result = SimulationEngine(line).run_sync(duration_hours=24.0)

    assert any(a.alert_type == "material_shortage" for a in result.alerts)
    consume = [e for e in result.material_events if e["type"] == "consume"]
    arrival = [e for e in result.material_events if e["type"] == "arrival"]
    assert len(arrival) == 1
    assert any(e["material"] == "尼古丁" and e["quantity"] == 20.0 for e in consume)
    assert result.inventory["尼古丁"] == 50.0 - 20.0
    assert len(result.batch_results) == 1


def test_assembly_bom_consumption_and_shortage():
    """V3.2 L9：组装工序按 BOM 消耗组件，缺料阻塞、到货恢复"""
    line = ProductionLine("BOM测试", shift_hours=1, break_minutes=0)
    line.add_station(Station(
        "s01", "棉芯安装", 1.0, 1,
        oee=1.0, efficiency=1.0, changeover_time=0,
        bom={"棉芯": 1},
    ))
    line.add_station(Station(
        "s02", "包装", 1.0, 1,
        oee=1.0, efficiency=1.0, changeover_time=0,
    ))
    line.materials.append(Material("棉芯", "个", 0.0))
    line.inventory = {"棉芯": 0.0}
    line.material_arrivals.append(MaterialArrival(30.0, "棉芯", 5000.0))

    result = SimulationEngine(line).run_sync(duration_hours=2.0)

    assert any(a.alert_type == "material_shortage" for a in result.alerts)
    consumes = [
        e for e in result.material_events
        if e["type"] == "consume" and e.get("station_id") == "s01"
    ]
    assert len(consumes) > 0
    assert result.station_outputs["s01"] == len(consumes)
    assert result.inventory["棉芯"] == pytest.approx(5000.0 - len(consumes))


def test_assembly_rework_returns_item_to_line():
    """V3.2 L9：缺陷件重新入线返工，最终可流向下游"""
    line = ProductionLine("返工测试", shift_hours=1, break_minutes=0)
    line.add_station(Station(
        "s01", "注油", 1.0, 1,
        oee=1.0, efficiency=1.0, changeover_time=0,
    ))
    line.add_station(Station(
        "s02", "质检", 1.0, 1,
        oee=1.0, efficiency=1.0, changeover_time=0,
        sampling_rate=1.0, defect_rate=0.5, rework_minutes=2.0,
    ))
    engine = SimulationEngine(line)
    engine.random_seed = 7
    result = engine.run_sync(duration_hours=0.5)

    assert len(result.quality_results) >= 1
    assert result.station_outputs["s02"] >= 1


def test_liquid_tanks_drain_by_recipe_rate():
    """V3.4：成品罐按配方灌装速度消耗，操作员人数不改变速率。"""
    line = create_liquid_line()
    tank = Tank("T1", "成品罐", 5000.0, 500.0)
    line.tanks = [tank]

    result = SimulationEngine(line).run_sync(duration_hours=1.0)

    first_capacity = line.recipes[0].filling_rate_l_per_h
    closing_level = result.closing_tanks[0]["current_level_l"]
    withdrawn = 500.0 - closing_level
    assert withdrawn > 0
    assert withdrawn <= first_capacity + first_capacity / 60.0 + 1e-6
    assert closing_level >= 0


def test_changeover_reduces_output_and_records_event():
    engine = SimulationEngine(make_line())
    engine.trigger_changeover("s01", minutes=30)
    result_with = engine.run_sync(duration_hours=1.0)

    result_without = SimulationEngine(make_line()).run_sync(duration_hours=1.0)

    assert len(result_with.changeover_events) == 1
    assert result_with.changeover_events[0]["station_id"] == "s01"
    assert any(a.alert_type == "changeover" for a in result_with.alerts)
    assert result_with.total_output < result_without.total_output


def test_trigger_changeover_validates_station():
    engine = SimulationEngine(make_line())
    with pytest.raises(ValueError):
        engine.trigger_changeover("missing", minutes=45)
    with pytest.raises(ValueError):
        engine.trigger_changeover("s01", minutes=0)


def test_wip_samples_collected():
    result = SimulationEngine(make_line()).run_sync(duration_hours=0.5)
    assert len(result.wip_samples) > 0
    assert all("wip" in sample for sample in result.wip_samples)


def test_detect_waste():
    line = ProductionLine("浪费测试")
    line.add_station(Station("s01", "瓶颈", 30.0, 1))
    line.add_station(Station("s02", "过剩", 10.0, 5))
    bottleneck_capacity = line.get_bottleneck_capacity()
    alerts = detect_waste(line.stations, bottleneck_capacity)
    assert any(a.alert_type == "waste" for a in alerts)


def test_set_callback_and_state_updates():
    engine = SimulationEngine(make_line())
    collected = []
    engine.set_callback(lambda state: collected.append(state))
    result = engine.run_sync(duration_hours=0.05)
    assert len(collected) > 0
    assert result.total_output > 0


def test_callback_exception_does_not_crash_simulation():
    engine = SimulationEngine(make_line())
    engine.set_callback(lambda state: (_ for _ in ()).throw(RuntimeError("模拟异常")))
    result = engine.run_sync(duration_hours=0.02)
    assert result.total_output >= 0


def test_changeover_event_for_other_station_is_routed():
    engine = SimulationEngine(make_line())
    engine.trigger_changeover("s02", minutes=10)
    result = engine.run_sync(duration_hours=0.2)
    assert len(result.changeover_events) == 1
    assert result.changeover_events[0]["station_id"] == "s02"


def test_multiple_changeover_events_chain_in_order():
    engine = SimulationEngine(make_line())
    engine.trigger_changeover("s01", minutes=10)
    engine.trigger_changeover("s01", minutes=5)
    result_two = engine.run_sync(duration_hours=1.0)

    engine_single = SimulationEngine(make_line())
    engine_single.trigger_changeover("s01", minutes=10)
    result_single = engine_single.run_sync(duration_hours=1.0)

    assert len(result_two.changeover_events) == 2
    assert result_two.total_output < result_single.total_output


def test_collaborative_station_run():
    line = ProductionLine("协同产线", shift_hours=1, break_minutes=0)
    line.add_station(Station(
        "s01", "组装", 5.0, 3,
        collaboration_type=CollaborationType.COLLABORATIVE,
    ))
    result = SimulationEngine(line).run_sync(duration_hours=0.1)
    assert result.total_output > 0


def test_wip_blockage_alert():
    line = ProductionLine("堵塞测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "快工序", 1.0, 10, buffer_capacity=5))
    line.add_station(Station("s02", "慢工序", 100.0, 1, buffer_capacity=5))
    result = SimulationEngine(line).run_sync(duration_hours=1.0)
    assert any(a.alert_type == "blockage" for a in result.alerts)


def test_wip_respects_buffer_capacity():
    line = ProductionLine("WIP边界测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "快工序", 1.0, 10, buffer_capacity=100))
    line.add_station(Station("s02", "慢工序", 100.0, 1, buffer_capacity=5))
    result = SimulationEngine(line).run_sync(duration_hours=1.0)

    # WIP 采样恒为非负且不超过缓冲区容量
    assert all(0 <= sample["wip"] <= 5 for sample in result.wip_samples)
    # 结果汇总中的 WIP 同样受容量约束
    assert 0 <= result.station_wips["s02"] <= 5
    # 下游满缓冲时触发堵塞报警
    assert any(a.alert_type == "blockage" for a in result.alerts)


def test_empty_line_headless_run():
    line = ProductionLine("空产线")
    result = SimulationEngine(line).run_sync(duration_hours=0.02)
    assert result.total_output == 0


def test_threaded_run_pause_resume_stop():
    engine = SimulationEngine(make_line())
    engine.set_callback(lambda state: None)
    engine.run(duration_hours=0.01, speed=10)
    assert engine.is_running is True
    time.sleep(0.3)
    engine.pause()
    assert engine.is_paused is True
    time.sleep(0.2)
    engine.resume()
    assert engine.is_paused is False
    time.sleep(0.3)
    engine.stop()
    time.sleep(0.1)
    assert engine.is_running is False
    results = engine.get_results()
    assert "total_output" in results


def test_threaded_run_real_time_mode():
    engine = SimulationEngine(make_line())
    engine.run(duration_hours=2 / 3600, speed=1)
    time.sleep(0.3)
    engine.stop()
    time.sleep(0.2)
    assert engine.is_running is False


def test_liquid_batch_simulation_matches_manual():
    line = create_liquid_line()
    recipe = line.recipes[0]
    result = SimulationEngine(line).run_sync(duration_hours=24.0)

    assert len(result.batch_results) == 1
    batch = result.batch_results[0]
    assert batch["status"] == "released"
    assert batch["recipe_name"] == "经典烟草"

    # 手工计算：调配 + 陈化 + 灌装 + QC（分钟）
    fill_min = recipe.batch_volume_l / recipe.filling_rate_l_per_h * 60
    expected_cycle = (
        recipe.mixing_time_min + recipe.aging_time_min + fill_min + recipe.qc_time_min
    )
    assert abs(batch["cycle_min"] - expected_cycle) / expected_cycle < 0.03

    # 罐液位：批次量 × 收率，随后被灌装线持续消耗（V3.2）
    assert 0.0 <= line.tanks[0].current_level_l < 500 * 0.95
    # 人力汇总
    assert result.labor_summary["qc_technician"] == 1
    assert result.unit == "千克"
    # V1.3 扩展 KPI
    assert result.kpis["batch_cycle_min"] > 0
    assert result.kpis["batch_pass_rate"] == pytest.approx(1.0)
    assert result.kpis["yield_rate"] == pytest.approx(0.95)
    assert result.kpis["cost_per_liter"] > 0


def test_cleaning_between_recipes():
    line = ProductionLine("换配方测试", production_type=ProductionType.LIQUID_FILLING)
    line.recipes.append(Recipe(
        name="配方A", batch_volume_l=100, yield_rate=0.95,
        mixing_time_min=10, aging_time_min=0,
        filling_rate_l_per_h=600, qc_time_min=5, clean_time_min=0,
    ))
    line.recipes.append(Recipe(
        name="配方B", batch_volume_l=100, yield_rate=0.95,
        mixing_time_min=10, aging_time_min=0,
        filling_rate_l_per_h=600, qc_time_min=5, clean_time_min=30,
    ))
    line.tanks.append(Tank("T01", "调配罐", 1000, 0))
    line.batches.append(Batch("B001", "配方A", 100))
    line.batches.append(Batch("B002", "配方B", 100))

    result = SimulationEngine(line).run_sync(duration_hours=24.0)

    assert len(result.cleaning_events) == 1
    assert result.cleaning_events[0]["recipe_from"] == "配方A"
    assert result.cleaning_events[0]["recipe_to"] == "配方B"
    assert result.cleaning_events[0]["clean_min"] == 30

    batch_b = result.batch_results[1]
    fill_min = 100 / 600 * 60
    expected_b = 10 + fill_min + 5  # 调配+灌装+QC（清洗在批次间单独计时）
    assert abs(batch_b["cycle_min"] - expected_b) < 0.03 * expected_b
    # 清洗发生在 B 开始前
    assert result.cleaning_events[0]["time"] < batch_b["start_time"]


def test_pouch_machine_takt_throughput():
    line = ProductionLine("袋装节拍测试", production_type=ProductionType.POUCH_PACKAGING)
    line.add_station(Station(
        "p01", "填充机", 1.0, 2,
        machine_count=2, machine_takt=1.0, oee=1.0, efficiency=1.0,
        changeover_time=0, clean_time_minutes=0,
    ))
    result = SimulationEngine(line).run_sync(duration_hours=1.0)
    # 理论：3600 秒 / 1.0 秒 × 2 机台 = 7200 袋
    assert 7100 <= result.total_output <= 7300
    assert result.kpis["machine_oee"] == pytest.approx(1.0)
    assert result.kpis["cost_per_pouch"] > 0
    assert result.unit == "袋"


def test_oee_three_factor_decomposition():
    """V3.2 L10：工序指标包含 OEE 可用率×性能率×合格率"""
    line = ProductionLine("OEE分解", production_type=ProductionType.POUCH_PACKAGING)
    line.add_station(Station(
        "p01", "填充机", 1.0, 1,
        machine_takt=1.0, oee=1.0, efficiency=1.0,
        changeover_time=0, clean_time_minutes=0,
    ))
    result = SimulationEngine(line).run_sync(duration_hours=0.5)

    m = result.station_metrics["p01"]
    assert "oee_availability" in m
    assert "oee_performance" in m
    assert "oee_quality" in m
    assert m["oee_quality"] == 1.0
    assert abs(
        m["oee_total"]
        - m["oee_availability"] * m["oee_performance"] * m["oee_quality"]
    ) < 1e-6


def test_changeover_matrix_overrides_recipe_clean_time():
    """V3.2 L10：换型矩阵时长优先于配方清洗时长"""
    line = ProductionLine("换型矩阵", production_type=ProductionType.LIQUID_FILLING)
    line.recipes.append(Recipe(
        name="配方A", batch_volume_l=100, yield_rate=0.95,
        mixing_time_min=10, aging_time_min=0,
        filling_rate_l_per_h=600, qc_time_min=5, clean_time_min=30,
    ))
    line.recipes.append(Recipe(
        name="配方B", batch_volume_l=100, yield_rate=0.95,
        mixing_time_min=10, aging_time_min=0,
        filling_rate_l_per_h=600, qc_time_min=5, clean_time_min=30,
    ))
    line.changeover_matrix = {"配方A": {"配方B": 15.0}}
    line.tanks.append(Tank("T01", "成品罐", 10000, 0))
    line.batches.append(Batch("B001", "配方A", 100))
    line.batches.append(Batch("B002", "配方B", 100))

    result = SimulationEngine(line).run_sync(duration_hours=24.0)

    recipe_change = [
        e for e in result.cleaning_events if e.get("reason") == "recipe_change"
    ]
    assert len(recipe_change) == 1
    assert recipe_change[0]["clean_min"] == 15.0


def test_liquid_quality_gate_rework():
    line = create_liquid_line()
    qc = line.get_station("s03")
    qc.sampling_rate = 1.0
    qc.defect_rate = 1.0
    qc.rework_minutes = 15.0

    engine = SimulationEngine(line)
    engine.random_seed = 42
    result = engine.run_sync(duration_hours=24.0)

    assert len(result.quality_results) == 1
    assert result.quality_results[0]["rework_count"] >= 1
    assert result.quality_results[0]["pass_rate"] < 1.0
    assert result.batch_results[0]["pass_rate"] < 1.0


def test_liquid_rejected_batch_isolated_not_shipped():
    """V3.2 L5：未通过质量门的批次隔离，不计入可发运产出"""
    line = create_liquid_line()
    qc = line.get_station("s03")
    qc.sampling_rate = 1.0
    qc.defect_rate = 1.0
    engine = SimulationEngine(line)
    engine.random_seed = 42
    result = engine.run_sync(duration_hours=24.0)

    assert result.batch_results[0]["status"] == "rejected"
    assert result.kpis["rejected_batches"] == 1
    assert result.kpis["shippable_quantity"] == 0.0
    assert all(t.current_level_l == 0.0 for t in line.tanks)
    assert any(a.alert_type == "batch_rejected" for a in result.alerts)


def test_pouch_quality_gate_reduces_output():
    line = ProductionLine("袋装质检测试", production_type=ProductionType.POUCH_PACKAGING)
    line.add_station(Station(
        "p01", "在线检测", 1.0, 1,
        machine_takt=1.0, oee=1.0, efficiency=1.0,
        changeover_time=0, clean_time_minutes=0,
        sampling_rate=1.0, defect_rate=1.0, rework_minutes=5.0,
    ))
    engine = SimulationEngine(line)
    engine.random_seed = 42
    result = engine.run_sync(duration_hours=0.5)

    assert len(result.quality_results) > 0
    assert all(q["defect"] for q in result.quality_results)
    # 缺陷率 100% 时合格产出为 0（缺陷件返工隔离，不计产出）
    assert result.total_output == 0
    assert len(result.quality_results) >= 3


def test_bottleneck_suggestion_respects_collaboration_type():
    line = make_line()
    # make_line 工序为并联，构造协同瓶颈
    collab = Station("s03", "协同工序", 20.0, 2,
                     collaboration_type=CollaborationType.COLLABORATIVE)
    line.add_station(collab)
    result = SimulationEngine(line).run_sync(duration_hours=0.5)
    bottleneck_alerts = [a for a in result.alerts if a.alert_type == "bottleneck"]
    assert bottleneck_alerts
    assert "加人不会提升产能" in bottleneck_alerts[0].suggestion


def test_bottleneck_suggestion_for_machine_takt():
    from src.models import create_pouch_line
    result = SimulationEngine(create_pouch_line()).run_sync(duration_hours=0.5)
    bottleneck_alerts = [a for a in result.alerts if a.alert_type == "bottleneck"]
    assert bottleneck_alerts
    assert "机台数" in bottleneck_alerts[0].suggestion


def test_wip_suggestion_direction_and_dedup():
    line = ProductionLine("WIP建议测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "快上游", 1.0, 10, buffer_capacity=100))
    line.add_station(Station("s02", "慢下游", 100.0, 1, buffer_capacity=5))
    result = SimulationEngine(line).run_sync(duration_hours=1.0)

    blockage = [a for a in result.alerts if a.alert_type == "blockage"]
    # 去重后：warning（80%）+ critical（满缓冲持续升级），不应每 30 秒刷屏
    assert len(blockage) <= 4, len(blockage)
    wip_alerts = [
        a for a in blockage if ("WIP堆积" in a.message or "缓冲区已满" in a.message)
    ]
    blocked_alerts = [a for a in blockage if "被下游堵塞" in a.message]
    assert wip_alerts and blocked_alerts
    for alert in wip_alerts:
        assert "上游" in alert.suggestion
        assert "限制上游投料" in alert.suggestion
    assert any(a.severity == "critical" for a in wip_alerts)
    critical = next(a for a in wip_alerts if a.severity == "critical")
    assert "自80%预警以来" in critical.message


def test_starvation_alert():
    line = ProductionLine("饥饿测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "慢上游", 100.0, 1, buffer_capacity=100))
    line.add_station(Station("s02", "快下游", 1.0, 10, buffer_capacity=100))
    result = SimulationEngine(line).run_sync(duration_hours=1.0)

    starvation = [a for a in result.alerts if a.alert_type == "starvation"]
    assert starvation
    assert "上游" in starvation[0].suggestion


def test_station_metrics_and_warmup():
    line = make_line()
    without = SimulationEngine(line).run_sync(duration_hours=1.0)
    assert "s01" in without.station_metrics
    assert without.station_metrics["s01"]["running_sec"] > 0
    assert 0 <= without.station_metrics["s01"]["utilization"] <= 1

    with_warmup = SimulationEngine(make_line()).run_sync(
        duration_hours=1.0, warmup_minutes=30.0
    )
    assert 0 < with_warmup.total_output < without.total_output


def test_predictive_wip_alert():
    line = ProductionLine("预测预警测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "快上游", 2.0, 2, buffer_capacity=100))
    line.add_station(Station("s02", "慢下游", 20.0, 1, buffer_capacity=100))
    result = SimulationEngine(line).run_sync(duration_hours=1.0)

    predictive = [
        a for a in result.alerts
        if a.severity == "info" and "预计" in a.message
    ]
    assert predictive
    assert "80%预警线" in predictive[0].message
    assert (
        "按实测上游速率" in predictive[0].message
        or "按上游产能估算" in predictive[0].message
    )


def test_wip_suggestion_mentions_downstream_root_cause():
    line = ProductionLine("下游根因测试", shift_hours=1, break_minutes=0)
    line.add_station(Station("s01", "上游", 1.0, 1))
    line.add_station(Station("s02", "中游", 1.0, 1))
    line.add_station(Station("s03", "慢下游", 100.0, 1))
    engine = SimulationEngine(line)
    suggestion = engine._wip_suggestion(line.stations[1], 1)
    assert "下游" in suggestion and "堵塞停线" in suggestion
