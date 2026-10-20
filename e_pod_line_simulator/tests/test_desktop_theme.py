"""真实显示环境验收；默认跳过，防止无桌面CI触发Tk本机崩溃。"""
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('PUFFLINE_GUI_TESTS') != '1', reason='需要显式启用真实桌面验收')


def test_modern_controls_and_small_window_workflows(monkeypatch):
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
    try:
        app.production_line = build_template_line('standard')
        app._update_display()
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
        app.root.update()
        assert not errors
    finally:
        app.root.destroy()
