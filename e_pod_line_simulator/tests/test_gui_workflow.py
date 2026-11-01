"""Configuration/result workflow regressions without opening Tk windows."""
import copy
from queue import Queue
from threading import Event
from types import SimpleNamespace as NS
import pytest
from src import gui_main, gui_panels
from src.gui_canvas import CanvasView
from src.gui_jobs import BackgroundJob, JobProgress
from src.gui_main import MainWindow
from src.gui_panels import KPIDashboard, OptimizeDialog, ResultTableDialog, WizardDialog
from src.gui_panels import build_template_line, format_cost
from src.models import JobRole, ProductionType
from src.scenario_manager import ScenarioManager
from src.simulation import SimulationEngine


class Widget:
    def __init__(self, text=''):
        self.text, self.values = text, {}
    def config(self, **values):
        self.values.update(values)
        self.text = values.get('text', self.text)
    def get(self):
        return self.text
    def title(self, text):
        self.text = text
    def delete(self, *args):
        self.text = ''
    def insert(self, where, text):
        self.text = text
    def destroy(self):
        self.values['destroyed'] = True


@pytest.fixture
def window(monkeypatch):
    w = MainWindow.__new__(MainWindow)
    w.production_line = build_template_line('simple')
    w.scenario_manager = ScenarioManager()
    w.root, w.status_bar = Widget(), Widget()
    w._dirty, w._config_revision, w._analysis_busy = False, 0, False
    w._analysis_job = w.simulation_engine = w.last_result = None
    w._last_result_revision = w._run_line = None
    w._update_display = lambda: None
    w.canvas_view = NS(simulation_state='previous')
    w.kpi_dashboard = NS(clear_actual_output=lambda text: None,
                         update_actual_output=lambda value: None,
                         update_timestamp=lambda value: None)
    w.alert_panel = NS(clear=lambda: None)
    for name in ('showinfo', 'showwarning', 'showerror'):
        monkeypatch.setattr(gui_main.messagebox, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(gui_main, 'show_toast', lambda *args: None)
    return w


def test_import_keeps_saved_baseline_independent(window):
    window.scenario_manager.save_scenario('A', window.production_line)
    original = window.scenario_manager.get_scenario('A').production_line.to_dict()
    assert window._import_scenario('A')
    window.production_line.stations[0].worker_count = 99
    assert window.scenario_manager.get_scenario('A').production_line.to_dict() == original
    assert not window._dirty


def test_import_cancellation_keeps_working_copy(window, monkeypatch):
    original = window.production_line
    window.scenario_manager.save_scenario('A', original)
    window._dirty = True
    monkeypatch.setattr(gui_main.messagebox, 'askyesnocancel', lambda *args: None)
    assert not window._import_scenario('A')
    assert window.production_line is original
    assert window._dirty


@pytest.mark.parametrize('saved', [False, True])
def test_save_returns_truth_and_only_clears_dirty_on_success(window, monkeypatch, saved):
    window._dirty = True
    result = {'name': 'A', 'path': None} if saved else None
    monkeypatch.setattr(gui_main, 'SaveScenarioDialog', lambda *args: NS(result=result))
    assert window._btn_save_scenario() is saved
    assert window._dirty is not saved


def test_exit_after_cancelled_save_keeps_window(window, monkeypatch):
    window._dirty = True
    monkeypatch.setattr(gui_main.messagebox, 'askyesnocancel', lambda *args: True)
    monkeypatch.setattr(gui_main, 'SaveScenarioDialog', lambda *args: NS(result=None))
    window._menu_exit()
    assert 'destroyed' not in window.root.values


def test_window_title_keeps_product_and_version(window):
    from src.version import PRODUCT_NAME, __version__
    window._update_title()
    assert window.root.text == f"{PRODUCT_NAME} v{__version__}"
    window._dirty = True
    window._update_title()
    assert window.root.text == f"* {PRODUCT_NAME} v{__version__}"


def test_layout_helpers_fit_laptop_and_short_wizard():
    """1280 宽工序栏能放下四列表头；矮屏幕上向导仍整窗留在屏幕内。"""
    from src.gui_main import default_pane_widths, project_heading
    from src.gui_panels import place_dialog_on_screen, station_column_spec
    import tkinter as tk
    from src.version import PRODUCT_NAME

    assert project_heading(None) == ""
    assert project_heading("  新产线  ") == "新产线"
    assert PRODUCT_NAME not in project_heading("")
    spec = station_column_spec()
    assert {item["anchor"] for item in spec.values()} == {tk.W}
    minimum = spec["name"]["minwidth"] + sum(
        item["width"] for key, item in spec.items() if key != "name"
    )
    for total in (1232, 1280, 1400, 1440):
        left, alert = default_pane_widths(total)
        assert left - 36 >= minimum
        assert alert >= 220
    width, height, x, y = place_dialog_on_screen(1280, 720, 680, 1600)
    assert x >= 0 and y >= 0
    assert x + width <= 1280
    assert y + height <= 720
    assert height < 1600


def test_wizard_creates_unsaved_working_copy(window, monkeypatch):
    line = build_template_line('standard')
    window.logger = NS(info=lambda *args: None)
    monkeypatch.setattr(gui_main, 'WizardDialog', lambda *args: NS(result={
        'production_line': line, 'auto_start': False,
    }))
    window._menu_wizard()
    assert window._dirty
    assert window.production_line is not line


@pytest.mark.parametrize('state', ['running', 'paused', 'analysis'])
def test_all_edit_entry_points_share_running_guard(window, state):
    original = window.production_line.to_dict()
    window.simulation_engine = NS(is_running=state != 'analysis', is_paused=state == 'paused')
    window._analysis_busy = state == 'analysis'
    window._on_add_station()
    window._on_edit_station('s01')
    window._on_delete_station()
    window._delete_station_by_id('s01')
    window._btn_config_shift()
    window._menu_wizard()
    window._import_scenario('missing')
    assert not window._btn_save_scenario()
    assert window.production_line.to_dict() == original


def test_station_edit_applies_role_and_device_count(window, monkeypatch):
    changed = copy.deepcopy(window.production_line.stations[0])
    changed.job_role, changed.machine_takt, changed.machine_count = JobRole.CLEANER, 2, 3
    monkeypatch.setattr(gui_panels, 'StationDialog', lambda *args, **kwargs: NS(result=changed))
    window._on_edit_station(changed.id)
    actual = window.production_line.get_station(changed.id)
    assert actual.job_role == JobRole.CLEANER
    assert actual.machine_count == 3
    assert window._dirty


def test_edit_invalidates_visible_result_without_destroying_archived_result(window):
    old_result, messages = object(), []
    window.last_result = old_result
    window.kpi_dashboard.clear_actual_output = messages.append
    window._mark_dirty()
    assert window.last_result is old_result
    assert window.canvas_view.simulation_state is None
    assert messages == ['配置已变化，需重新仿真']


def test_export_saved_run_preserves_actual_events_without_rerunning(window, monkeypatch):
    engine = SimulationEngine(copy.deepcopy(window.production_line))
    engine.random_seed = 42
    result = engine.run_sync(duration_hours=.1)
    result.duration_seconds = 8 * 3600  # A partial run with an eight-hour target.
    result.changeover_events = [{'station_id': 's01', 'minutes': 5}]
    window.last_result = result
    window._last_result_revision = window.production_line.configuration_revision()
    monkeypatch.setattr(gui_main.messagebox, 'askyesnocancel', lambda *args: True)
    monkeypatch.setattr(gui_main.filedialog, 'asksaveasfilename', lambda **kwargs: '/tmp/unused.xlsx')
    exported = []
    timestamps, outputs = [], []
    window.kpi_dashboard.update_timestamp = timestamps.append
    window.kpi_dashboard.update_actual_output = outputs.append
    monkeypatch.setattr(gui_main, 'export_report', lambda value, path: exported.append(value) or True)
    monkeypatch.setattr(gui_main, 'SimulationEngine', lambda *args: pytest.fail('must use saved run'))
    window._start_analysis_job = lambda work, success, status: success(
        work(JobProgress(Queue(), Event()))
    )
    window._menu_export_report()
    assert exported[0].run_id == result.run_id
    assert exported[0].changeover_events == result.changeover_events
    assert exported[0].total_output == result.total_output
    assert timestamps == [result.elapsed_seconds / 60]
    assert outputs == [result.total_output]


def test_cancelled_background_job_never_delivers_late_success():
    delivered = []
    owner = NS(winfo_exists=lambda: True, after=lambda *args: None)
    job = BackgroundJob(owner, lambda progress: 1, delivered.append, delivered.append,
                        on_cancel=lambda: delivered.append('cancelled'))
    job.messages.put(('success', 'late'))
    job.cancel()
    job._poll()
    assert delivered == ['cancelled']


def test_locked_canvas_does_not_open_editor():
    view = CanvasView.__new__(CanvasView)
    view.drag_enabled = False
    view._station_id_at = lambda *args: 's01'
    view.on_station_edit = lambda *args: pytest.fail('locked editor')
    view._on_double_click(NS(x=0, y=0))


@pytest.mark.parametrize('production_type', list(ProductionType))
def test_blank_template_is_empty_for_every_production_type(production_type):
    wizard = WizardDialog.__new__(WizardDialog)
    wizard.template_var = Widget('blank')
    wizard.production_type_var = Widget(production_type.value)
    line = wizard._make_template_line()
    assert not line.stations
    assert line.production_type == production_type


def test_wizard_preview_uses_final_shift_values():
    wizard = WizardDialog.__new__(WizardDialog)
    wizard.production_line = build_template_line('simple')
    wizard.shift_hours_var, wizard.break_minutes_var = Widget('4'), Widget('0')
    wizard.wage_var, wizard.summary_text = Widget('30'), Widget()
    wizard._refresh_summary()
    line = wizard.production_line
    assert (line.shift_hours, line.break_minutes, line.worker_hourly_wage) == (4, 0, 30)
    assert f'预计日产量：{line.calculate_daily_output():.0f}' in wizard.summary_text.text
    assert '液体批次只读摘要' not in wizard.summary_text.text


def test_liquid_wizard_summary_is_readonly_and_names_excel():
    from src.gui_panels import format_liquid_readonly_summary
    from src.templates import build_process_template

    line = build_process_template('liquid_filling')
    line.changeover_matrix = {'配方A': {'配方B': 15}}
    text = format_liquid_readonly_summary(line)
    assert '批次量 100 千克' in text
    assert '收率 95.0%' in text
    assert '罐容 500 千克' in text
    assert 'B001 / 配方A / 100 千克' in text
    assert '配方A→配方B 15 分钟' in text
    assert '修改请用 Excel 模板再导入' in text
    assert '主界面不能修改' in text
    assert format_liquid_readonly_summary(build_template_line('simple')) == ''

    wizard = WizardDialog.__new__(WizardDialog)
    wizard.production_line = line
    wizard.shift_hours_var, wizard.break_minutes_var = Widget('8'), Widget('60')
    wizard.wage_var, wizard.summary_text = Widget('20'), Widget()
    wizard._refresh_summary()
    assert '修改请用 Excel 模板再导入' in wizard.summary_text.text


def test_result_table_reads_selected_tree_row():
    dialog = ResultTableDialog.__new__(ResultTableDialog)
    dialog.rows = [{'rank': '基线'}, {'rank': 'TOP1'}]
    dialog.tree = NS(selection=lambda: ('row2',), index=lambda item: 1)
    assert dialog.get_selected_row() == {'rank': 'TOP1'}


def test_optimizer_applies_id_keyed_parameters_and_closes_results(monkeypatch):
    line = build_template_line('simple')
    line.stations[1].name = line.stations[0].name
    dialog = OptimizeDialog.__new__(OptimizeDialog)
    dialog.line, dialog._result_revision = line, line.configuration_revision()
    dialog._top = [{'rank': 1, 'params': {'s02': {'worker_count': 5, 'buffer_capacity': 20}}}]
    applied = []
    dialog.on_apply = lambda: applied.append(True)
    table = NS(get_selected_row=lambda: {'rank': 'TOP1'}, _close=lambda: applied.append('closed'))
    monkeypatch.setattr(gui_panels.messagebox, 'showinfo', lambda *args: None)
    original = line.stations[0].worker_count
    dialog._apply_selected(table)
    assert line.stations[0].worker_count == original
    assert line.stations[1].worker_count == 5
    assert applied == [True, 'closed']


def test_zero_output_cost_and_liquid_precision_are_not_lost():
    assert format_cost(None) == '不可计算'
    dashboard = KPIDashboard.__new__(KPIDashboard)
    dashboard.current_unit, dashboard.actual_output_label = '千克', Widget()
    dashboard.update_actual_output(.125)
    assert '0.125 千克' in dashboard.actual_output_label.text


def test_late_analysis_result_is_discarded_after_revision_change(window, monkeypatch):
    delivered, callbacks = [], {}
    window._set_analysis_busy = lambda busy: setattr(window, '_analysis_busy', busy)
    def job(owner, work, success, error, **kwargs):
        callbacks.update(success=success)
        return NS(start=lambda: None)
    monkeypatch.setattr(gui_main, 'BackgroundJob', job)
    window._start_analysis_job(lambda progress: 'result', delivered.append, 'working')
    window._config_revision += 1
    callbacks['success']('late')
    assert not delivered
    assert '过期' in window.status_bar.text


def test_compute_shift_runs_sync_and_refreshes_result(window, monkeypatch):
    from src.models import Alert

    seen = []

    class Engine:
        def __init__(self, line):
            self.line = line
            self.random_seed = None

        def run_sync(self, duration_hours, cancel_event=None):
            seen.append((duration_hours, self.random_seed, cancel_event))
            return NS(
                line_name="新产线", unit="件",
                line_config={"production_type": "assembly"},
                total_output=4, duration_seconds=duration_hours * 3600, run_id="",
                elapsed_seconds=0, status="completed",
                kpis={"actual_unit_cost": 1.25},
                alerts=[Alert("info", "info", "s01", "缓冲区接近上限")],
            )

    class Progress:
        cancel_event = "token"

        def __call__(self, value):
            seen.append(value)

    window.btn_compute = Widget()
    window._shift_compute = False
    window.alert_panel = NS(
        clear=lambda: seen.append("cleared"),
        add_alert=lambda alert: seen.append(alert.message),
    )
    window.kpi_dashboard.reset_timestamp = lambda: None
    window.kpi_dashboard.set_run_state = lambda state: seen.append(state)
    window._reset_canvas_after_run = lambda: seen.append("canvas")
    monkeypatch.setattr(gui_main, "SimulationEngine", Engine)
    def remember(record):
        seen.append(record["source"])
        return True

    monkeypatch.setattr(gui_main, "append_snapshot", remember)

    def start(work, done, status, finished_status="分析完成"):
        assert status == "正在计算本班结果..."
        assert finished_status == "本班结果已计算"
        assert window.btn_compute.text == "计算中"
        done(work(Progress()))

    window._start_analysis_job = start
    window._btn_compute_shift()
    assert seen[0] == "cleared"
    assert "computing" in seen
    assert (window.production_line.shift_hours, 0, "token") in seen
    assert "simulation" in seen
    assert "缓冲区接近上限" in seen
    assert "canvas" in seen
    assert window.last_result.total_output == 4


def test_animation_pace_can_change_after_start():
    engine = SimulationEngine.__new__(SimulationEngine)
    engine.speed = 32
    assert engine._animation_pace(1) == 32
    engine.speed = 0
    assert engine._animation_pace(8) == 8


def test_running_engine_uses_detached_parameter_snapshot(window, monkeypatch):
    class Engine:
        def __init__(self, line):
            self.line, self.is_running, self.is_paused = line, False, False
        def set_callback(self, callback):
            self.callback = callback
        def run(self, **kwargs):
            self.is_running = True
    monkeypatch.setattr(gui_main, 'SimulationEngine', Engine)
    window._lock_configuration = lambda locked: None
    window.speed_var, window.speed_combo = Widget('64'), Widget()
    window.btn_start = window.btn_pause = window.btn_stop = window.btn_changeover = Widget()
    window.kpi_dashboard.reset_timestamp = lambda: None
    window.kpi_dashboard.set_run_state = lambda state: None
    window.root.after = lambda *args: None
    before = window.production_line.stations[0].worker_count
    window._btn_start_simulation()
    window.production_line.stations[0].worker_count = 99
    assert window.simulation_engine.line.stations[0].worker_count == before
    assert window.simulation_engine.line is not window.production_line


def test_canvas_scrollregion_keeps_all_complex_line_nodes_reachable():
    view = CanvasView.__new__(CanvasView)
    for name in ('station_rects', 'station_texts', 'connection_lines', 'wip_labels',
                 'station_positions', 'station_cards', 'status_strips', 'status_texts',
                 'bottleneck_badges'):
        setattr(view, name, {})
    view.start_x, view.station_spacing, view.station_row_height = 100, 200, 150
    view.selected_station_id = None
    view.delete = lambda *args: None
    view.winfo_width, view.winfo_height = lambda: 600, lambda: 350
    for name in ('_draw_grid', '_draw_station', '_draw_connection_curved',
                 '_draw_type_badge', '_draw_status_legend', '_draw_batch_summary'):
        setattr(view, name, lambda *args: None)
    view.bbox = lambda tag: (0, 0, 600, max(y for x, y in view.station_positions.values()) + 70)
    configured = {}
    view.configure = lambda **kwargs: configured.update(kwargs)
    view.update_production_line(build_template_line('complex'))
    region = configured['scrollregion']
    assert region[3] > view.winfo_height()
    assert all(region[0] <= x <= region[2] and region[1] <= y <= region[3]
               for x, y in view.station_positions.values())


def test_liquid_station_analysis_explains_unsupported_model_before_dialogs(window):
    window.production_line.production_type = ProductionType.LIQUID_FILLING
    window._menu_sensitivity()
    window._menu_sweep()
    window._menu_optimize()
    window._menu_hr_planning()
    assert window._analysis_job is None


def test_optional_export_failure_does_not_claim_exported_path(monkeypatch):
    dialog = gui_panels.SaveScenarioDialog.__new__(gui_panels.SaveScenarioDialog)
    dialog.name_entry = Widget('A')
    dialog.description_text = NS(get=lambda *args: 'description')
    dialog.path_var = Widget('/unwritable/report.json')
    dialog.production_line = build_template_line('simple')
    dialog.scenario_manager = NS(save_scenario=lambda *args: (True, None),
                                 export_scenario=lambda *args: False)
    dialog.dialog = Widget()
    monkeypatch.setattr(gui_panels.messagebox, 'showwarning', lambda *args: None)
    dialog._on_confirm()
    assert dialog.result['name'] == 'A'
    assert dialog.result['path'] is None


@pytest.mark.parametrize('state', ['running', 'paused', 'analysis'])
def test_optimizer_cannot_apply_when_main_configuration_is_locked(window, monkeypatch, state):
    window.simulation_engine = NS(is_running=state != 'analysis', is_paused=state == 'paused')
    window._analysis_busy = state == 'analysis'
    dialog = OptimizeDialog.__new__(OptimizeDialog)
    dialog.line = window.production_line
    dialog.can_apply = window._can_change_configuration
    dialog._result_revision = dialog.line.configuration_revision()
    dialog._top = [{'rank': 1, 'params': {'s01': {'worker_count': 9, 'buffer_capacity': 20}}}]
    original = dialog.line.to_dict()
    dialog.on_apply = lambda: pytest.fail('locked configuration must not be changed')
    table = NS(get_selected_row=lambda: {'rank': 'TOP1'},
               _close=lambda: pytest.fail('locked result must remain open'))
    dialog._apply_selected(table)
    assert dialog.line.to_dict() == original


def test_system_close_of_nested_results_restores_parent_grab(monkeypatch):
    class Top:
        def __init__(self, parent=None):
            self.protocols, self.grabs, self.destroyed = {}, 0, False
        def title(self, *args): pass
        def geometry(self, *args): pass
        def transient(self, *args): pass
        def bind(self, *args): pass
        def after_idle(self, *args): pass
        def update_idletasks(self): pass
        def wait_window(self): pass
        def winfo_screenwidth(self): return 1200
        def winfo_screenheight(self): return 800
        def winfo_width(self): return 820
        def winfo_height(self): return 520
        def winfo_exists(self): return not self.destroyed
        def protocol(self, event, callback): self.protocols[event] = callback
        def grab_set(self): self.grabs += 1
        def destroy(self): self.destroyed = True
    monkeypatch.setattr(gui_panels.tk, 'Toplevel', Top)
    monkeypatch.setattr(ResultTableDialog, '_create_widgets',
                        lambda self: setattr(self, 'tree', NS(focus_set=lambda: None)))
    parent = Top()
    result = ResultTableDialog(parent, 'results', [], [])
    result.dialog.protocols['WM_DELETE_WINDOW']()
    assert result.dialog.destroyed
    assert parent.grabs == 1


class ScenarioTree:
    def __init__(self):
        self.items, self.chosen, self.focused = {}, (), None
        self.next_id, self.has_focus = 0, False
    def selection(self): return self.chosen
    def get_children(self): return tuple(self.items)
    def item(self, item, key): return self.items[item][key]
    def delete(self, item): del self.items[item]
    def tag_configure(self, *args, **kwargs): pass
    def insert(self, *args, **kwargs):
        self.next_id += 1
        item = str(self.next_id)
        self.items[item] = kwargs
        return item
    def selection_set(self, item): self.chosen = (item,)
    def focus(self, item): self.focused = item
    def see(self, item): self.visible = item
    def focus_set(self): self.has_focus = True


def test_scenario_table_initial_keyboard_selection_and_refresh_preserve_row():
    dialog = gui_panels.ScenarioManageDialog.__new__(gui_panels.ScenarioManageDialog)
    dialog.tree = ScenarioTree()
    dialog.scenario_manager = ScenarioManager()
    dialog.scenario_manager.save_scenario('A', build_template_line('simple'))
    dialog.scenario_manager.save_scenario('B', build_template_line('standard'))
    dialog.checked, dialog._img_checked, dialog._img_unchecked = {}, None, None
    dialog._refresh()
    first = dialog.tree.selection()[0]
    assert dialog.tree.item(first, 'tags')[0] == 'A'
    assert dialog.tree.focused == first and dialog.tree.has_focus
    dialog.tree.selection_set(dialog.tree.get_children()[1])
    dialog._refresh()
    assert dialog.tree.item(dialog.tree.selection()[0], 'tags')[0] == 'B'
    assert dialog.tree.focused == dialog.tree.selection()[0]


def test_hr_result_text_and_table_use_same_coverage_headcount(monkeypatch):
    from src.hr_planning import LaborCostConfig, LearningCurveConfig, ShiftPlan, build_hr_summary
    dialog = gui_panels.HrPlanningDialog.__new__(gui_panels.HrPlanningDialog)
    dialog.dialog, dialog.result_text, dialog._single_role = None, Widget(), True
    dialog.summary = build_hr_summary(
        build_template_line('simple'), 100, ShiftPlan(shifts_per_day=2),
        LaborCostConfig(absence_rate=.2), LearningCurveConfig(), {}, [],
    )
    dialog._render()
    assert '初始缺口（覆盖编制−在岗）' in dialog.result_text.text
    assert f"全天覆盖编制（含缺勤）：{dialog.summary['covered_headcount']} 人" in (
        dialog.result_text.text
    )
    captured = []
    monkeypatch.setattr(gui_panels, 'ResultTableDialog',
                        lambda parent, title, columns, rows: captured.extend(rows))
    dialog._open_result_table()
    rows = {row['item']: row['value'] for row in captured}
    assert rows['全天覆盖编制（含缺勤）'] == f"{dialog.summary['covered_headcount']} 人"
    assert rows['初始缺口（覆盖编制−在岗）'] == f"{dialog.summary['initial_gap']} 人"


@pytest.mark.parametrize('widget_class', [
    'TButton', 'TCheckbutton', 'TRadiobutton', 'TEntry', 'TCombobox', 'Treeview',
])
def test_space_does_not_run_global_action_after_focused_control(window, widget_class):
    """Widget class bindings handle space before the root binding is reached."""
    window.root.focus_get = lambda: NS(winfo_class=lambda: widget_class)
    calls = []
    window._btn_start_simulation = lambda: calls.append('start')
    assert window._on_space_shortcut(None) == 'break'
    assert calls == []


def test_space_on_canvas_starts_simulation(window):
    window.root.focus_get = lambda: NS(winfo_class=lambda: 'Canvas')
    calls = []
    window._btn_start_simulation = lambda: calls.append('start')
    assert window._on_space_shortcut(None) == 'break'
    assert calls == ['start']


@pytest.mark.parametrize('minutes', ['0.1', '1.9', 'nan', 'inf', '-1', '0'])
def test_changeover_rejects_non_positive_or_non_integer_minutes(monkeypatch, minutes):
    from src.gui_panels import ChangeoverDialog

    dialog = ChangeoverDialog.__new__(ChangeoverDialog)
    dialog.station_var, dialog.minutes_var = Widget('s01 注油'), Widget(minutes)
    dialog.dialog, dialog.result = Widget(), None
    errors = []
    monkeypatch.setattr(gui_panels.messagebox, 'showerror', lambda *a, **k: errors.append(a))
    dialog._confirm()
    assert errors
    assert dialog.result is None
    assert 'destroyed' not in dialog.dialog.values


def test_native_menu_returns_before_modal_callback(window, monkeypatch):
    """A native AX menu action must not retain the modal wait_window stack."""
    commands, pending, calls = {}, [], []

    class Menu:
        def __init__(self, *args, **kwargs):
            pass
        def add_cascade(self, **kwargs):
            pass
        def add_separator(self):
            pass
        def add_command(self, **kwargs):
            commands[kwargs['label']] = kwargs['command']

    monkeypatch.setattr(gui_main.tk, 'Menu', Menu)
    window.root.after_idle = lambda callback: pending.append(callback)
    window._menu_optimize = lambda: calls.append('opened')
    window._create_menu_bar()
    commands['智能优化...']()
    assert not calls
    assert len(pending) == 1
    pending.pop()()
    assert calls == ['opened']


@pytest.mark.parametrize('break_text', ['0.0', '15.5'])
def test_hr_form_accepts_break_value_from_saved_configuration(monkeypatch, break_text):
    """Shift configuration stores floating point minutes and the form must read them."""
    from src.gui_panels import HrPlanningDialog

    dialog = HrPlanningDialog.__new__(HrPlanningDialog)
    dialog.line = build_template_line('simple')
    values = {
        'target_var': '420', 'shifts_var': '2', 'shift_hours_var': '4.0',
        'break_var': break_text, 'overtime_var': '0', 'wage_var': '30.0',
        'social_var': '0.3', 'absence_var': '0.1', 'turnover_var': '0',
        'ramp_var': '90',
    }
    for name, value in values.items():
        setattr(dialog, name, Widget(value))
    dialog._parse_current = lambda: {'general': 14}
    dialog._parse_hiring = lambda: []
    dialog._render = dialog._open_result_table = lambda: None
    errors = []
    monkeypatch.setattr(gui_panels.messagebox, 'showerror', lambda *a, **k: errors.append(a))
    assert dialog._compute()
    assert not errors
    assert dialog.summary['headcount_per_shift'] > 0


@pytest.mark.parametrize('value,expected,dirty', [
    ('台', '台', True), (None, '件', False), ('a b', '件', False),
])
def test_output_unit_edit_validates_and_invalidates_results(window, monkeypatch,
                                                           value, expected, dirty):
    from tkinter import simpledialog
    monkeypatch.setattr(simpledialog, 'askstring', lambda *args, **kwargs: value)
    window._configure_output_unit()
    assert window.production_line.get_unit() == expected
    assert window._dirty == dirty


def test_output_unit_cannot_change_during_running_simulation(window, monkeypatch):
    from tkinter import simpledialog
    window.simulation_engine = NS(is_running=True, is_paused=False)
    monkeypatch.setattr(simpledialog, 'askstring',
                        lambda *args, **kwargs: pytest.fail('运行中不应打开单位编辑器'))
    window._configure_output_unit()
    assert window.production_line.get_unit() == '件'
