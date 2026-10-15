"""方案管理单元测试"""

import pytest

from src.models import ProductionLine, Station
from src.scenario_manager import ScenarioManager


def make_line(name="方案产线"):
    line = ProductionLine(name)
    line.add_station(Station("s01", "注油", 25, 2))
    return line


def test_max_scenarios_is_five():
    manager = ScenarioManager()
    assert manager.MAX_SCENARIOS == 5


def test_save_up_to_five_scenarios():
    manager = ScenarioManager()
    for i in range(5):
        ok, err = manager.save_scenario(f"方案{i+1}", make_line(), "测试")
        assert ok, err
    ok, err = manager.save_scenario("方案6", make_line())
    assert ok is False
    assert "上限" in err


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "scenarios.json")
    manager = ScenarioManager()
    manager.set_storage_path(path)
    manager.save_scenario("方案A", make_line(), "描述A")
    manager.save_scenario("方案B", make_line(), "描述B")

    loaded = ScenarioManager()
    assert loaded.load_from_file(path) is True
    assert loaded.get_scenario_count() == 2
    assert loaded.get_scenario("方案A").description == "描述A"
    assert loaded.get_scenario("方案B").production_line.stations[0].name == "注油"


def test_delete_persists(tmp_path):
    path = str(tmp_path / "scenarios.json")
    manager = ScenarioManager()
    manager.set_storage_path(path)
    manager.save_scenario("方案A", make_line())
    manager.delete_scenario("方案A")

    loaded = ScenarioManager()
    loaded.load_from_file(path)
    assert loaded.get_scenario_count() == 0


def test_compare_scenarios():
    manager = ScenarioManager()
    manager.save_scenario("方案A", make_line(), "基准")
    line_b = make_line()
    line_b.stations[0].worker_count = 1  # 成本更低
    line_b.stations[0].oee = 1.0
    line_b.stations[0].efficiency = 1.0
    manager.save_scenario("方案B", line_b, "优化")
    line_c = make_line()
    line_c.stations[0].worker_count = 5  # 成本更高
    manager.save_scenario("方案C", line_c, "冗余")

    comparison = manager.compare_scenarios(["方案A", "方案B", "方案C"])
    assert len(comparison["scenarios"]) == 3
    assert len(comparison["differences"]) == 7
    assert "方案B" in comparison["recommendation"]


def test_compare_scenarios_validation():
    manager = ScenarioManager()
    manager.save_scenario("方案A", make_line())
    with pytest.raises(ValueError):
        manager.compare_scenarios(["方案A"])
    with pytest.raises(ValueError):
        manager.compare_scenarios(["方案A", "不存在"])


def test_compare_scenarios_no_recommendation():
    manager = ScenarioManager()
    empty1 = ProductionLine("空1")
    empty2 = ProductionLine("空2")
    manager.save_scenario("空方案A", empty1)
    manager.save_scenario("空方案B", empty2)
    comparison = manager.compare_scenarios(["空方案A", "空方案B"])
    assert comparison["recommendation"] == "无法推荐"


def test_save_scenario_validation():
    manager = ScenarioManager()
    ok, err = manager.save_scenario("", make_line())
    assert ok is False and "不能为空" in err
    manager.save_scenario("方案A", make_line())
    ok, err = manager.save_scenario("方案A", make_line())
    assert ok is False and "已存在" in err


def test_delete_missing_scenario():
    manager = ScenarioManager()
    ok, err = manager.delete_scenario("不存在")
    assert ok is False


def test_can_add_and_can_compare():
    manager = ScenarioManager()
    assert manager.can_add_scenario() is True
    assert manager.can_compare() is False
    manager.save_scenario("方案A", make_line())
    assert manager.can_compare() is False
    manager.save_scenario("方案B", make_line())
    assert manager.can_compare() is True


def test_get_scenario_missing_returns_none():
    manager = ScenarioManager()
    assert manager.get_scenario("不存在") is None


def test_load_from_file_missing_or_corrupt(tmp_path):
    manager = ScenarioManager()
    assert manager.load_from_file(str(tmp_path / "none.json")) is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert manager.load_from_file(str(bad)) is False


def test_persist_failure_rolls_back_save(tmp_path):
    manager = ScenarioManager()
    # Persistence must succeed before the UI can promise a saved snapshot.
    manager.set_storage_path(str(tmp_path))
    ok, err = manager.save_scenario("方案A", make_line())
    assert ok is False and "失败" in err
    assert manager.get_scenario("方案A") is None


def test_delete_failure_keeps_original_scenario(tmp_path):
    manager = ScenarioManager()
    manager.save_scenario("方案A", make_line())
    manager.set_storage_path(str(tmp_path))
    ok, err = manager.delete_scenario("方案A")
    assert ok is False and "保留" in err
    assert manager.get_scenario("方案A") is not None


def test_scenario_read_is_an_independent_snapshot():
    manager = ScenarioManager()
    manager.save_scenario("方案A", make_line())
    working = manager.get_scenario("方案A")
    working.production_line.stations[0].worker_count = 19
    assert manager.get_scenario("方案A").production_line.stations[0].worker_count == 2


def test_scenarios_cannot_compare_different_units():
    from src.models import ProductionType
    manager = ScenarioManager()
    manager.save_scenario("A", make_line())
    other = make_line()
    other.production_type = ProductionType.POUCH_PACKAGING
    manager.save_scenario("B", other)
    with pytest.raises(ValueError, match="计量单位"):
        manager.compare_scenarios(["A", "B"])


def test_export_scenario_custom_path(tmp_path):
    path = str(tmp_path / "custom.json")
    manager = ScenarioManager()
    manager.save_scenario("方案A", make_line(), "自定义导出")

    assert manager.export_scenario("方案A", path) is True
    loaded = ScenarioManager()
    assert loaded.load_from_file(path) is True
    assert loaded.get_scenario_count() == 1
    assert loaded.get_scenario("方案A").description == "自定义导出"

    # 不存在的方案导出失败
    assert manager.export_scenario("不存在", path) is False
