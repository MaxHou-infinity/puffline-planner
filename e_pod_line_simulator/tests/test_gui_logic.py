"""GUI 交互逻辑单元测试（不依赖显示环境）"""

from types import SimpleNamespace

from src.gui_main import MainWindow

from src.gui_canvas import compute_reorder_order
from src.gui_panels import build_template_line, station_edit_fields
from src.models import ProductionType


def test_compute_reorder_order_move_to_middle():
    order = ["s01", "s02", "s03"]
    positions = {"s01": (0, 0), "s02": (100, 0), "s03": (200, 0)}
    new_order = compute_reorder_order(order, "s03", positions, 150, 0)
    assert new_order == ["s01", "s03", "s02"]


def test_pause_exposes_mouse_resume_button():
    calls = []
    window = MainWindow.__new__(MainWindow)
    window.simulation_engine = SimpleNamespace(pause=lambda: calls.append("pause"))
    window.btn_start = SimpleNamespace(config=lambda **kwargs: calls.append(kwargs))
    window.btn_pause = SimpleNamespace(config=lambda **kwargs: calls.append(kwargs))
    window.status_bar = SimpleNamespace(config=lambda **kwargs: None)
    window.kpi_dashboard = SimpleNamespace(set_run_state=lambda state: calls.append(state))
    window._btn_pause_simulation()
    assert "pause" in calls
    assert {"state": "normal", "text": "继续仿真"} in calls
    assert "paused" in calls


def test_natural_completion_is_observed_on_tk_thread():
    window = MainWindow.__new__(MainWindow)
    engine = SimpleNamespace(is_running=False)
    window.simulation_engine = engine
    calls = []
    window._finalize_simulation = lambda completed: calls.append(completed)
    window._poll_simulation_completion(engine)
    assert calls == [True]


def test_stop_always_stops_engine_when_snapshot_fails(monkeypatch):
    from src import gui_main

    window = MainWindow.__new__(MainWindow)
    calls = []
    widget = SimpleNamespace(config=lambda **kwargs: None)
    window.simulation_engine = SimpleNamespace(
        build_result=lambda: object(), stop=lambda: calls.append("stopped")
    )
    window.production_line = SimpleNamespace(stations=[])
    window.logger = SimpleNamespace(exception=lambda *args: None)
    window.btn_start = window.btn_pause = window.btn_stop = widget
    window.btn_changeover = window.speed_combo = window.status_bar = widget
    window.config_panel = SimpleNamespace(set_editable=lambda value: None)
    window.kpi_dashboard = SimpleNamespace(set_run_state=lambda state: None)
    window.canvas_view = None
    monkeypatch.setattr(gui_main, "build_snapshot", lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(gui_main.messagebox, "showwarning", lambda *args: calls.append("warned"))
    window._btn_stop_simulation()
    assert calls == ["warned", "stopped"]
    assert window.simulation_engine is None


def test_compute_reorder_order_invalid():
    order = ["s01", "s02"]
    positions = {"s01": (0, 0), "s02": (100, 0)}
    assert compute_reorder_order(order, "missing", positions, 0, 0) is None
    assert compute_reorder_order(order, "s01", {"s01": (0, 0)}, 0, 0) is None


def test_build_template_line():
    assert len(build_template_line("simple").stations) == 3
    assert len(build_template_line("standard").stations) == 5
    assert len(build_template_line("complex").stations) == 8
    assert build_template_line("blank").stations == []
    assert build_template_line("complex").stations[0].name == "镭雕"


def test_dialog_handler_methods_exist():
    from src.gui_panels import (
        AlertPanel,
        SaveScenarioDialog,
        ScenarioCompareDialog,
        ScenarioManageDialog,
        ShiftConfigDialog,
        StationDialog,
        WizardDialog,
    )

    assert hasattr(ShiftConfigDialog, "_btn_ok")
    assert hasattr(ShiftConfigDialog, "_btn_cancel")
    assert hasattr(StationDialog, "_btn_ok")
    assert hasattr(StationDialog, "_btn_cancel")
    assert hasattr(SaveScenarioDialog, "_on_confirm")
    assert hasattr(SaveScenarioDialog, "_on_cancel")
    assert hasattr(WizardDialog, "_next")
    assert hasattr(WizardDialog, "_cancel")
    assert hasattr(ScenarioManageDialog, "_delete_selected")
    assert hasattr(ScenarioManageDialog, "_import_selected")
    assert hasattr(ScenarioManageDialog, "_toggle_compare")
    assert hasattr(ScenarioManageDialog, "_toggle_check")
    assert hasattr(ScenarioManageDialog, "_confirm_compare")
    assert hasattr(AlertPanel, "_copy_alerts")
    assert hasattr(SaveScenarioDialog, "_browse_path")
    assert hasattr(ScenarioCompareDialog, "_export_excel")
    assert hasattr(AlertPanel, "_apply_filter")
    assert hasattr(AlertPanel, "_clear")
    assert hasattr(AlertPanel, "_toggle_collapse")


def test_hr_planning_dialog_methods_exist():
    from src.gui_panels import HrPlanningDialog

    assert hasattr(HrPlanningDialog, "_compute")
    assert hasattr(HrPlanningDialog, "_export_excel")
    assert hasattr(HrPlanningDialog, "_export_pdf")
    assert hasattr(HrPlanningDialog, "_cancel")
    assert hasattr(HrPlanningDialog, "_parse_current")
    assert hasattr(HrPlanningDialog, "_parse_hiring")
    assert hasattr(HrPlanningDialog, "_fill_current")
    assert hasattr(HrPlanningDialog, "_open_result_table")


def test_history_dialog_methods_exist():
    from src.gui_panels import HistoryDialog

    assert hasattr(HistoryDialog, "_record")
    assert hasattr(HistoryDialog, "_refresh")
    assert hasattr(HistoryDialog, "_draw_chart")
    assert hasattr(HistoryDialog, "_close")
    assert hasattr(HistoryDialog, "_export_history")
    assert hasattr(HistoryDialog, "_clear_history")


def test_sweep_dialog_methods_exist():
    from src.gui_panels import SweepDialog

    assert hasattr(SweepDialog, "_run")
    assert hasattr(SweepDialog, "_close")
    assert hasattr(SweepDialog, "_draw_chart")


def test_optimize_dialog_methods_exist():
    from src.gui_panels import OptimizeDialog

    assert hasattr(OptimizeDialog, "_run")
    assert hasattr(OptimizeDialog, "_close")
    assert hasattr(OptimizeDialog, "_apply_selected")


def test_result_table_and_help_section_methods_exist():
    from src.gui_panels import HelpSection, ResultTableDialog

    assert hasattr(ResultTableDialog, "_export_excel")
    assert hasattr(ResultTableDialog, "_copy_text")
    assert hasattr(ResultTableDialog, "_close")
    assert hasattr(ResultTableDialog, "get_selected_row")
    assert hasattr(HelpSection, "_toggle")


def test_analysis_guide_dialog_exists():
    from src.gui_panels import AnalysisGuideDialog

    assert hasattr(AnalysisGuideDialog, "GUIDE_TEXT")
    assert len(AnalysisGuideDialog.GUIDE_TEXT) > 100


def test_changeover_dialog_methods_exist():
    from src.gui_panels import ChangeoverDialog

    assert hasattr(ChangeoverDialog, "_confirm")
    assert hasattr(ChangeoverDialog, "_create_widgets")


def test_build_result_rows_keeps_all_rows():
    """V3.3.3 回归：结果表格行插入逻辑不可丢失"""
    from src.gui_panels import build_result_rows

    columns = [("rank", "方案", 60, "center"), ("output", "总产出", 90, "e")]
    rows = [
        {"rank": "基线", "output": 100},
        {"rank": "TOP1", "output": 200},
    ]
    built = build_result_rows(columns, rows, highlight_key="rank", highlight_value="基线")

    assert len(built) == 2
    assert built[0] == (["基线", 100], True)
    assert built[1] == (["TOP1", 200], False)


def test_canvas_capacity_text_uses_dynamic_unit():
    from src.gui_canvas import format_capacity_text

    assert format_capacity_text(100, "袋") == "⚡ 100 袋/h"
    assert format_capacity_text(100, "千克") == "⚡ 100 千克/h"
    assert format_capacity_text(100, "颗") == "⚡ 100 颗/h"


def test_station_edit_fields_mapping():
    assert station_edit_fields(ProductionType.ASSEMBLY) == set()
    assert station_edit_fields(ProductionType.LIQUID_FILLING) == {
        "clean_time_minutes",
        "job_role",
        "sampling_rate",
        "defect_rate",
        "rework_minutes",
    }
    assert station_edit_fields(ProductionType.POUCH_PACKAGING) == {
        "machine_takt",
        "job_role",
        "sampling_rate",
        "defect_rate",
        "rework_minutes",
    }


def test_glossary_fuzzy_search():
    from src.gui_panels import fuzzy_contains

    assert fuzzy_contains("瓶颈", "瓶颈")
    assert fuzzy_contains("罐容约束", "罐容")
    assert fuzzy_contains("OEE分解", "oee")
    assert fuzzy_contains("本周新增需求", "新增需求")
    # 顺序包含（容忍缺字）：query 字符按顺序出现在文本中
    assert fuzzy_contains("批次排产序列", "批序")
    assert not fuzzy_contains("瓶颈", "OEE")


def test_glossary_dialog_search_methods_exist():
    from src.gui_panels import GlossaryDialog

    assert hasattr(GlossaryDialog, "_render")


def test_help_dialog_exists():
    from src.gui_panels import HelpDialog

    assert hasattr(HelpDialog, "HELP_TEXT")
    assert "停机切换" in HelpDialog.HELP_TEXT
    assert "Ctrl+S" in HelpDialog.HELP_TEXT


def test_about_dialog_class_and_urls():
    import os

    from src.gui_panels import AboutDialog
    from src.version import (
        BUG_REPORT_URL,
        PRODUCT_NAME,
        WECHAT_QR_PATH,
        WECHAT_QR_SMALL_PATH,
    )

    assert callable(AboutDialog)
    assert PRODUCT_NAME == "PuffLine Planner"
    assert BUG_REPORT_URL == "https://github.com/MaxHou-infinity/puffline-planner/issues"
    assert isinstance(WECHAT_QR_PATH, str)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.exists(os.path.join(repo_root, WECHAT_QR_PATH))
    assert os.path.exists(os.path.join(repo_root, WECHAT_QR_SMALL_PATH))
