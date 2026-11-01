"""真实显示环境验收；默认跳过，防止无桌面CI触发Tk本机崩溃。"""
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('PUFFLINE_GUI_TESTS') != '1', reason='需要显式启用真实桌面验收')


def test_modern_controls_and_small_window_workflows(monkeypatch, tmp_path):
    from tkinter import ttk
    from src import gui_main
    from src.gui_panels import StationDialog, WizardDialog, build_template_line
    from src.models import JobRole
    from src.simulation import SimulationEngine
    from src.theme import COLORS

    monkeypatch.setattr(gui_main, 'should_show_first_run', lambda *args: False)
    app = gui_main.MainWindow()
    errors = []
    app.root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    assert app.project_title.cget("text") == ""
    assert not app.project_title.winfo_ismapped()
    assert app.root.title().endswith(f"v{gui_main.__version__}")
    try:
        app.production_line = build_template_line('standard')
        app._update_display()
        assert app.project_title.cget("text") == "新产线"
        app.root.geometry('1280x800+20+20')
        app.root.update()
        total = app.main_paned.winfo_width()
        left, alert = gui_main.default_pane_widths(total)
        app.main_paned.sashpos(0, left)
        app.main_paned.sashpos(1, max(left + 200, total - alert))
        app.root.update()
        assert app.config_panel.tree.xview()[1] > 0.98
        app.root.geometry('1000x620')
        app.root.update()
        assert app.alert_panel.text_widget.cget('background') == COLORS['surface']
        style = app.root._puffline_style
        assert style.theme_use() == 'puffline'
        assert 'Puff.Entry.border' in str(style.layout('TEntry'))
        assert 'Puff.Combobox.downarrow' in str(style.layout('TCombobox'))
        check_value = gui_main.tk.BooleanVar(value=False)
        check = ttk.Checkbutton(app.root, text='验收选择框', variable=check_value)
        check.invoke()
        assert check_value.get()
        check.destroy()
        for button in (app.btn_start, app.btn_save_scenario, app.btn_output_unit):
            assert button.winfo_viewable()
            assert button.winfo_rootx() + button.winfo_width() <= app.root.winfo_rootx() + 1000

        class Probe(StationDialog):
            def _create_widgets(self, station):
                super()._create_widgets(station)
                def exercise():
                    self.dialog.geometry('660x560')
                    self.dialog.update()
                    assert self.save_button.winfo_viewable()
                    assert self.save_button.winfo_rooty() + self.save_button.winfo_height() <= (
                        self.dialog.winfo_rooty() + self.dialog.winfo_height())
                    # 原生下拉列表应可打开/关闭，主题不改变键盘控件行为。
                    self.job_role_combo.focus_set()
                    self.job_role_combo.event_generate('<Down>')
                    self.job_role_combo.event_generate('<Escape>')
                    self.job_role_var.set('测试员')
                    self.machine_takt_var.set('10')
                    self.min_takt_var.set('8')
                    self.machine_count_var.set('2')
                    self._btn_ok()
                self.dialog.after(100, exercise)

        old = app.production_line.stations[0]
        dialog = Probe(app.root, station=old, production_line=app.production_line)
        assert dialog.result.job_role == JobRole.TEST_OPERATOR
        assert dialog.result.min_machine_takt == 8
        assert dialog.result.efficiency == old.efficiency
        assert dialog.result.bom == old.bom
        class WizardProbe(WizardDialog):
            def _create_widgets(self):
                super()._create_widgets()
                def inspect_pages():
                    try:
                        self.dialog.update()
                        filler = ttk.Frame(self.step_container, height=1800)
                        filler.pack(fill="x")
                        self.dialog.update()
                        self._body_canvas.yview_moveto(1)
                        assert self._body_canvas.yview()[0] > 0.5
                        self._pin_scroll_top()
                        assert self._body_canvas.yview()[0] < 0.02
                        filler.destroy()
                        self.dialog.update()
                        assert self.btn_cancel.winfo_height() >= 24
                        assert self.btn_cancel.winfo_height() + 2 >= (
                            self.btn_cancel.winfo_reqheight()
                        )
                        radio = self._template_radios["simple"]
                        assert radio.winfo_rooty() >= self._body_canvas.winfo_rooty() - 2
                        assert radio.winfo_rooty() < (
                            self._body_canvas.winfo_rooty() + self._body_canvas.winfo_height()
                        )
                        self._show_step(2)
                        self.dialog.update()
                        forms = self.page_shift.winfo_children()
                        entries = [child for form in forms for child in form.winfo_children()
                                   if isinstance(child, ttk.Entry)]
                        assert len(entries) == 3
                        assert all(entry.winfo_viewable() for entry in entries)
                        assert all(entry.winfo_height() > 10 for entry in entries)
                        assert self.dialog.focus_get() == self.shift_hours_entry
                    finally:
                        self._cancel()
                self.dialog.after(100, inspect_pages)
        WizardProbe(app.root)
        # 数据仍可送入真实仿真引擎，非HTML演示值。
        result = SimulationEngine(app.production_line).run_sync(1)
        assert result.total_output > 0
        _check_alert_column(app)
        _check_scenario_and_form_details(app, monkeypatch, tmp_path)
        app.root.update()
        assert not errors
    finally:
        app.root.destroy()


def _wait_until(root, predicate, timeout=2.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.05)
    root.update()
    return predicate()


def _check_alert_column(app):
    """默认 1500 与 1880 宽时，分析提醒占一列，分隔条离开右缘且可拖。"""
    alert_pane = str(app.alert_panel.master)
    assert app.main_paned.pane(alert_pane)['weight'] >= 1
    for width, height in ((1500, 900), (1880, 980)):
        app.root.geometry(f'{width}x{height}+20+20')
        ready = _wait_until(
            app.root,
            lambda: app.main_paned.winfo_width() > 400
            and app.alert_panel.winfo_width() >= 220,
        )
        assert ready, app.alert_panel.winfo_width()
        paned_width = app.main_paned.winfo_width()
        sash = app.main_paned.sashpos(1)
        assert sash < paned_width - 180
        before = app.alert_panel.winfo_width()
        app.main_paned.sashpos(1, sash - 80)
        app.root.update()
        assert app.alert_panel.winfo_width() >= before + 40
        assert app.btn_start.winfo_width() > 20


def _check_scenario_and_form_details(app, monkeypatch, tmp_path):
    """方案管理按钮留在窗口内；对比失败不留空白窗；说明和表单可滚动。"""
    import tkinter as tk
    from tkinter import ttk
    from src.gui_panels import (
        HelpSection,
        ScenarioCompareDialog,
        ScenarioManageDialog,
        scrollable_form,
    )
    from src.models import ProductionLine, ProductionType, Station
    from src.scenario_manager import ScenarioManager

    shown = []
    monkeypatch.setattr(
        'src.gui_panels.messagebox.showerror',
        lambda *args, **kwargs: shown.append((args, kwargs)),
    )
    manager = ScenarioManager()
    manager.set_storage_path(str(tmp_path / 'scenarios.json'))

    def make_line(name):
        line = ProductionLine(name)
        line.add_station(Station('s01', '零件装配', 25, 2))
        return line

    ok, err = manager.save_scenario('方案A', make_line('甲'))
    assert ok, err
    other = make_line('乙')
    other.production_type = ProductionType.POUCH_PACKAGING
    ok, err = manager.save_scenario('方案B', other)
    assert ok, err

    manage = ScenarioManageDialog(app.root, manager)
    manage.dialog.geometry('780x480+80+80')
    manage.dialog.update()
    buttons = {}

    def walk(widget):
        if isinstance(widget, ttk.Button):
            buttons[widget.cget('text')] = widget
        for child in widget.winfo_children():
            walk(child)

    walk(manage.dialog)
    limit = manage.dialog.winfo_rooty() + manage.dialog.winfo_height()
    for text in ('导入方案', '删除选中', '方案对比', '关闭'):
        button = buttons[text]
        assert button.winfo_ismapped()
        assert button.winfo_rooty() + button.winfo_height() <= limit + 1
    manage.dialog.destroy()

    before = {str(child) for child in app.root.winfo_children()}
    compare = ScenarioCompareDialog(app.root, manager, ['方案A', '方案B'])
    app.root.update()
    assert compare.dialog is None
    assert shown and '对比方案失败' in shown[-1][0][1]
    after = {str(child) for child in app.root.winfo_children() if child.winfo_exists()}
    assert after == before

    help_host = tk.Toplevel(app.root)
    help_host.geometry('420x160+40+40')
    section = HelpSection(help_host, '使用说明', '说明正文\n' * 12)
    section.pack(fill=tk.X)
    section._toggle()
    help_host.update()
    assert section.body.cget('yscrollcommand')
    assert section.body_scroll.winfo_ismapped()
    help_host.destroy()

    form_host = tk.Toplevel(app.root)
    form_host.geometry('360x180+60+60')
    content = scrollable_form(form_host)
    for index in range(24):
        ttk.Label(content, text=f'字段{index}').pack(anchor=tk.W)
    form_host.update()
    canvas = content.master
    target = content.winfo_children()[-1]
    for _ in range(6):
        target.event_generate('<Button-5>')
    form_host.update()
    assert canvas.yview()[0] > 0
    form_host.destroy()
