"""工具函数单元测试"""

import logging
import os

import pandas as pd

from src.models import (
    CollaborationType,
    ProductionLine,
    ProductionType,
    Station,
    create_liquid_line,
)
from src.utils import (
    calculate_roi,
    create_excel_template,
    format_number,
    format_time,
    get_alert_color,
    get_status_color,
    get_first_run_marker_path,
    import_from_excel,
    load_config,
    load_ui_config,
    mark_first_run_done,
    parse_shift_params,
    resolve_template_reset,
    save_config,
    save_ui_config,
    should_show_first_run,
    setup_logger,
    validate_production_line,
    validate_station,
)


def test_first_run_marker(tmp_path):
    marker = str(tmp_path / "first_run.json")
    # 标记不存在：首次运行应展示引导
    assert should_show_first_run(marker) is True
    # 写入标记后不再展示
    assert mark_first_run_done(marker) is True
    assert should_show_first_run(marker) is False
    # 标记内容可重复写入（幂等）
    assert mark_first_run_done(marker) is True


def test_first_run_marker_missing_parent_dir(tmp_path):
    marker = str(tmp_path / "nested" / "deep" / "first_run.json")
    assert mark_first_run_done(marker) is True
    assert should_show_first_run(marker) is False


def test_get_first_run_marker_path_is_absolute():
    path = get_first_run_marker_path()
    assert path.endswith("first_run.json")
    assert os.path.isabs(path)


def test_parse_shift_params_valid():
    assert parse_shift_params("8", "60", "20.5") == (8.0, 60.0, 20.5)
    assert parse_shift_params(" 8.5 ", "0", "20") == (8.5, 0.0, 20.0)


def test_parse_shift_params_invalid():
    # 非数字与越界值均给出明确字段提示
    for hours in ("abc", "0", "-1", ""):
        try:
            parse_shift_params(hours, "60", "20")
        except ValueError as exc:
            assert "班次时长" in str(exc)
        else:
            raise AssertionError(f"应拒绝班次时长：{hours!r}")
    for minutes in ("abc", "-5", ""):
        try:
            parse_shift_params("8", minutes, "20")
        except ValueError as exc:
            assert "休息时间" in str(exc)
        else:
            raise AssertionError(f"应拒绝休息时间：{minutes!r}")
    for wage in ("abc", "0", "-1", ""):
        try:
            parse_shift_params("8", "60", wage)
        except ValueError as exc:
            assert "工人时薪" in str(exc)
        else:
            raise AssertionError(f"应拒绝时薪：{wage!r}")


def test_resolve_template_reset():
    # 未导入 Excel：模板变更应直接重置
    assert resolve_template_reset(False) is True
    # 已导入 Excel：用户确认才重置，否则保留
    assert resolve_template_reset(True, lambda: True) is True
    assert resolve_template_reset(True, lambda: False) is False
    # 无确认函数时保守不重置
    assert resolve_template_reset(True) is False


def test_validate_station():
    assert validate_station("注油", 25, 2) == (True, "")
    assert validate_station("", 25, 2)[0] is False
    assert validate_station("注油", 0, 2)[0] is False
    assert validate_station("注油", 25, 0)[0] is False


def test_validate_production_line():
    line = ProductionLine("测试")
    assert validate_production_line(line)[0] is False
    line.add_station(Station("s01", "注油", 25, 2))
    assert validate_production_line(line) == (True, "")

    liquid = create_liquid_line()
    assert validate_production_line(liquid) == (True, "")
    liquid.cleanroom_limits = {"C": 1}
    ok, msg = validate_production_line(liquid)
    assert ok is False and "超过上限" in msg


def test_save_and_load_config(tmp_path):
    line = ProductionLine("往返测试")
    line.add_station(Station("s01", "注油", 25, 2, collaboration_type=CollaborationType.PARALLEL))
    path = str(tmp_path / "line.json")
    assert save_config(line, path) is True

    restored = load_config(path)
    assert restored is not None
    assert restored.name == "往返测试"
    assert restored.stations[0].name == "注油"


def test_load_config_missing_file(tmp_path):
    assert load_config(str(tmp_path / "none.json")) is None


def test_format_time():
    assert format_time(3661) == "01:01:01"


def test_color_helpers():
    assert get_status_color("running") == "#34C759"
    assert get_status_color("unknown") == "#E5E5EA"
    assert get_alert_color("warning") == "#FF9F0A"


def test_calculate_roi():
    assert calculate_roi(10000, 200) == 50.0
    assert calculate_roi(10000, 0) == 0.0
    assert calculate_roi(10000, -1) == 0.0


def test_format_number():
    assert format_number(123.456) == "123.46"
    assert format_number(1.2, 3) == "1.200"


def test_create_and_import_excel_template(tmp_path):
    path = str(tmp_path / "template.xlsx")
    assert create_excel_template(path) is True
    line, error = import_from_excel(path)
    assert line is not None and error is None
    assert len(line.stations) == 4
    assert line.stations[0].name == "注油"


def test_create_and_import_liquid_template(tmp_path):
    path = str(tmp_path / "liquid.xlsx")
    assert create_excel_template(path, production_type="liquid_filling") is True
    line, error = import_from_excel(path)
    assert line is not None and error is None
    assert line.production_type == ProductionType.LIQUID_FILLING
    assert len(line.recipes) == 1
    assert line.recipes[0].name == "经典烟草"
    assert len(line.tanks) == 2
    assert len(line.batches) == 1


def test_import_excel_new_fields_and_line_config(tmp_path):
    """V3.3.1：导入模板支持 V1.3+/V3.2 字段与产线级配置"""
    path = str(tmp_path / "liquid_new.xlsx")
    assert create_excel_template(path, production_type="liquid_filling") is True

    from openpyxl import load_workbook

    wb = load_workbook(path)
    ws = wb["工序"]
    ws.append([
        "灌装", 1.5, 2, "并联", 0.85, 0.95, 45, 100,
        2.0, 30, 0.2, 0.01, 15, "灌装操作员", "C",
        "尼古丁:20;丙二醇:400",
    ])
    wb["产线配置"]["B6"] = 2  # cip_interval_batches
    wb["产线配置"]["B7"] = 1.0  # cip_interval_hours
    wb.save(path)

    line, error = import_from_excel(path)
    assert line is not None and error is None
    filling = next(s for s in line.stations if s.name == "灌装")
    assert filling.machine_takt == 2.0
    assert filling.clean_time_minutes == 30
    assert filling.sampling_rate == 0.2
    assert filling.defect_rate == 0.01
    assert filling.rework_minutes == 15
    assert filling.job_role.value == "filling_operator"
    assert filling.cleanroom_zone == "C"
    assert filling.bom == {"尼古丁": 20.0, "丙二醇": 400.0}
    assert line.cip_interval_batches == 2
    assert line.cip_interval_hours == 1.0
    assert len(line.materials) == 1
    assert line.inventory.get("棉芯") == 1000
    assert len(line.material_arrivals) == 1


def test_import_excel_missing_file(tmp_path):
    line, error = import_from_excel(str(tmp_path / "none.xlsx"))
    assert line is None
    assert "不存在" in error


def test_import_excel_missing_required_column(tmp_path):
    df = pd.DataFrame({"工序名": ["注油"]})
    path = str(tmp_path / "bad.xlsx")
    df.to_excel(path, index=False, engine="openpyxl")
    line, error = import_from_excel(path)
    assert line is None
    assert "缺少必填列" in error


def test_setup_logger_writes_file(tmp_path):
    log_path = str(tmp_path / "app.log")
    logger = setup_logger(log_path)
    logger.info("测试日志")
    for handler in logger.handlers:
        handler.flush()
    content = open(log_path, encoding="utf-8").read()
    assert "测试日志" in content


def test_ui_config_roundtrip(tmp_path, monkeypatch):
    import src.utils as utils_mod

    path = str(tmp_path / "ui.json")
    monkeypatch.setattr(utils_mod, "UI_CONFIG_FILE", path)
    assert save_ui_config({"theme": "dark"}) is True
    assert load_ui_config() == {"theme": "dark"}
    assert load_ui_config()["theme"] == "dark"
