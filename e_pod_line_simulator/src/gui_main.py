"""
GUI主窗口 - 程序的主界面

这个文件包含主窗口类，负责：
1. 创建和布局所有GUI组件
2. 协调各组件之间的通信
3. 处理菜单栏事件
4. 管理仿真流程

GUI布局：
┌─────────────────────────────────────────┐
│ 菜单栏: 文件 | 分析 | 方案 | 报告 | 帮助 │
├──────────┬──────────────────────────────┤
│          │                              │
│ 配置面板  │      2D产线画布              │
│          │                              │
│ 工序列表  │                              │
│ [添加]    │                              │
│ [编辑]    │                              │
│ [删除]    │                              │
├──────────┴──────────────────────────────┤
│ KPI仪表盘: 瓶颈产能 | 日产量 | 成本      │
│ 控制按钮: [开始] [暂停] [停止] [速度]    │
└─────────────────────────────────────────┘
"""

import tkinter as tk
import os
from tkinter import ttk, messagebox, filedialog, simpledialog
from typing import Optional
import threading
import copy
from queue import Empty, Full, Queue

from src.models import ProductionLine, ProductionType, Station, SimulationState
from src.simulation import SimulationEngine
from src.gui_canvas import CanvasView
from src.gui_jobs import BackgroundJob
from src.gui_panels import (
    ConfigPanel,
    KPIDashboard,
    AlertPanel,
    SaveScenarioDialog,
    ScenarioManageDialog,
    GlossaryDialog,
    AboutDialog,
    WizardDialog,
    HrPlanningDialog,
    HistoryDialog,
    SweepDialog,
    OptimizeDialog,
    ResultTableDialog,
    AnalysisGuideDialog,
    ChangeoverDialog,
    HelpDialog,
    format_cost,
)
from src.utils import (
    validate_production_line,
    setup_logger,
    get_first_run_marker_path,
    mark_first_run_done,
    should_show_first_run,
    get_scenarios_path,
    load_config,
    locate_example_config,
)
from src.scenario_manager import ScenarioManager
from src.reporting import export_report
from src.sensitivity import run_sensitivity
from src.history import append_snapshot, build_snapshot
from src.theme import COLORS, ToolTip, apply_theme, show_toast
from src.version import PRODUCT_NAME, __version__


class MainWindow:
    """
    主窗口类 - 应用程序的主界面
    
    这是整个GUI的核心，负责：
    - 创建窗口和布局
    - 管理产线数据
    - 控制仿真流程
    - 处理用户交互
    
    使用方式：
        app = MainWindow()
        app.run()
    """
    
    def __init__(self):
        """
        初始化主窗口
        
        创建所有GUI组件，初始化数据
        """
        # 创建主窗口
        self.root = tk.Tk()
        self.root.title(f"{PRODUCT_NAME} v{__version__}")
        width = min(1500, self.root.winfo_screenwidth() - 60)
        height = min(900, self.root.winfo_screenheight() - 100)
        self.root.geometry(f"{width}x{height}")

        # 应用 V3.0 设计令牌（统一浅色主题）
        apply_theme(self.root)
        
        # 设置窗口最小尺寸
        self.root.minsize(1000, 620)
        
        # 初始化日志
        self.logger = setup_logger()
        self.logger.info("程序启动")

        # 全局异常捕获：Tkinter 回调中的未捕获异常统一记录日志并提示
        self.root.report_callback_exception = self._on_callback_exception
        
        # 数据模型
        self.production_line: Optional[ProductionLine] = None  # 当前产线对象
        self.simulation_engine: Optional[SimulationEngine] = None  # 仿真引擎
        self._state_queue = Queue(maxsize=1)
        self._analysis_busy = False
        self._shift_compute = False
        self._analysis_job = None
        self._config_revision = 0
        self.last_result = None
        self._last_result_revision = None
        self._run_line = None
        self.scenario_manager = ScenarioManager()  # 方案管理器（管理保存的方案）

        # 方案自动持久化：保存/删除方案后写入 configs/scenarios.json
        default_scenario_file = get_scenarios_path()
        self.scenario_manager.set_storage_path(default_scenario_file)
        if self.scenario_manager.load_from_file(default_scenario_file):
            self.logger.info("已加载方案：%d 个", self.scenario_manager.get_scenario_count())
        
        # 创建GUI组件
        self._create_menu_bar()  # 创建菜单栏
        self._create_main_layout()  # 创建主布局
        self._update_empty_banner(False)
        self._create_status_bar()  # 创建状态栏

        # 未保存修改标记与快捷键
        self._dirty = False
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._menu_exit)
        self.root.after(100, self._drain_simulation_states)

        # V3.3.5：首次运行引导（本地 marker，老用户不打扰）
        self._first_run_marker = get_first_run_marker_path()
        if should_show_first_run(self._first_run_marker):
            self.root.after(600, self._open_first_run_wizard)
        
        # 初始化一个默认产线（可选）
        # self._create_default_line()  # 暂时注释，避免导入问题
    
    def _create_menu_bar(self) -> None:
        """
        创建菜单栏
        
        菜单栏包含：
        - 文件：快速配置向导、保存方案、退出
        - 分析：敏感性试算、批量试算、智能优化、人力规划
        - 方案：方案管理/对比、KPI 历史趋势
        - 报告：导出报告
        - 帮助：使用说明、术语说明、分析指南、关于
        """
        def deferred(callback):
            # Return from the native menu action before entering a modal Tk wait loop.
            # On macOS a synchronous wait_window can hold the accessibility callback.
            return lambda: self.root.after_idle(callback)

        menubar = tk.Menu(self.root)
        self.menubar = menubar
        self.root.config(menu=menubar)
        
        # 文件菜单（V3.3.1 精简：向导 + 保存方案）
        file_menu = tk.Menu(menubar, tearoff=0)
        self.file_menu = file_menu
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="快速配置向导...", command=deferred(self._menu_wizard))
        file_menu.add_command(label="打开配置...", command=deferred(self._menu_open_config))
        file_menu.add_separator()
        file_menu.add_command(label="保存方案...", command=deferred(self._btn_save_scenario))
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=deferred(self._menu_exit))

        # 分析菜单（试算与优化）
        analysis_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="分析", menu=analysis_menu)
        analysis_menu.add_command(label="敏感性试算...", command=deferred(self._menu_sensitivity))
        analysis_menu.add_command(label="批量试算...", command=deferred(self._menu_sweep))
        analysis_menu.add_command(label="智能优化...", command=deferred(self._menu_optimize))
        analysis_menu.add_command(label="人力规划...", command=deferred(self._menu_hr_planning))

        # 方案菜单（管理与历史）
        scenario_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="方案", menu=scenario_menu)
        scenario_menu.add_command(label="方案管理 / 对比...", command=deferred(self._menu_manage_scenarios))
        scenario_menu.add_command(label="KPI 历史趋势...", command=deferred(self._menu_history))

        # 报告菜单
        report_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="报告", menu=report_menu)
        report_menu.add_command(label="导出报告", command=deferred(self._menu_export_report))

        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="使用说明", command=deferred(self._menu_help))
        help_menu.add_command(label="术语说明", command=deferred(self._menu_glossary))
        help_menu.add_command(label="分析指南...", command=deferred(self._menu_analysis_guide))
        help_menu.add_command(label="关于", command=deferred(self._menu_about))
    
    def _create_main_layout(self) -> None:
        """
        创建主布局
        
        主布局分为四个区域：
        1. 左侧：配置面板（工序列表、编辑功能）
        2. 中间：画布视图（2D产线可视化）
        3. 中间底部：KPI仪表盘和控制按钮
        4. 最右侧：报警栏（纵列显示）
        """
        # 创建主容器（使用PanedWindow实现可调整大小的分割）
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=0)
        self.root.columnconfigure(0, weight=1)
        header = ttk.Frame(self.root, padding=(20, 16))
        header.grid(row=0, column=0, sticky='ew')
        self.project_title = ttk.Label(header, text="PuffLine Planner", style="Heading.TLabel")
        self.project_title.pack(anchor=tk.W)
        self.project_subtitle = ttk.Label(
            header, text="本地产线仿真与人力规划", foreground=COLORS['text_secondary'])
        self.project_subtitle.pack(anchor=tk.W, pady=(5, 0))
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self.main_paned = main_paned
        
        # 左侧：配置面板（加宽以完整显示工序列表）
        left_frame = ttk.Frame(main_paned, width=380)
        main_paned.add(left_frame, weight=1)
        
        self.config_panel = ConfigPanel(
            left_frame,
            on_add=self._on_add_station,
            on_edit=self._on_edit_station,
            on_delete=self._on_delete_station,
            on_select=self._on_select_station
        )
        self.config_panel.pack(fill=tk.BOTH, expand=True)
        
        # 中间：画布和KPI
        right_frame = ttk.Frame(main_paned)
        right_frame.rowconfigure(0, weight=1)
        right_frame.rowconfigure(1, weight=0)
        right_frame.columnconfigure(0, weight=1)
        main_paned.add(right_frame, weight=4)

        # 分析提醒必须有正权重。weight=0 时，窗口从尚未映射的 1px 撑开后
        # 这一栏分不到宽度，分隔条会贴在右缘，报警和「清空」都点不到。
        alert_frame = ttk.Frame(main_paned, width=280)
        main_paned.add(alert_frame, weight=1)
        self._pane_layout_attempts = 0
        self.root.after_idle(self._set_default_panes)
        
        # 画布视图（2D可视化）
        canvas_frame = ttk.Frame(right_frame)

        # V3.3.5：空产线引导横幅（无工序时显示，一键打开向导）
        self.empty_banner = ttk.Frame(canvas_frame)
        ttk.Label(
            self.empty_banner,
            text="还没有配置工序，点下方按钮一键生成示例产线：",
            foreground=COLORS["text_secondary"],
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            self.empty_banner,
            text="打开快速配置向导",
            command=self._menu_wizard,
            style="Accent.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            self.empty_banner,
            text="打开示例配置",
            command=self._open_hardware_example,
        ).pack(side=tk.LEFT, padx=(8, 0))

        canvas_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        self.canvas_viewport = ttk.Frame(canvas_frame)
        self.canvas_viewport.pack(fill=tk.BOTH, expand=True)
        self.canvas_viewport.rowconfigure(0, weight=1)
        self.canvas_viewport.columnconfigure(0, weight=1)
        self.canvas_view = CanvasView(
            self.canvas_viewport,
            width=800,
            height=520,
            on_reorder=self._on_canvas_reorder,
            on_station_edit=self._on_edit_station,
            on_station_select=self._on_canvas_select_station,
            on_station_menu=self._on_canvas_station_menu,
        )
        self.canvas_view.grid(row=0, column=0, sticky="nsew")
        canvas_y = ttk.Scrollbar(
            self.canvas_viewport, orient=tk.VERTICAL, command=self.canvas_view.yview
        )
        canvas_x = ttk.Scrollbar(
            self.canvas_viewport, orient=tk.HORIZONTAL, command=self.canvas_view.xview
        )
        canvas_y.grid(row=0, column=1, sticky="ns")
        canvas_x.grid(row=1, column=0, sticky="ew")
        self.canvas_view.configure(yscrollcommand=canvas_y.set, xscrollcommand=canvas_x.set)
        
        # 底部：KPI仪表盘和控制按钮
        bottom_frame = ttk.Frame(right_frame)
        bottom_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        
        # KPI仪表盘
        self.kpi_dashboard = KPIDashboard(bottom_frame)
        self.kpi_dashboard.pack(fill=tk.X, pady=5)
        self.kpi_dashboard.set_run_state('stopped')
        
        # 控制按钮
        control_frame = ttk.Frame(bottom_frame)
        control_frame.pack(fill=tk.X)
        action_frame = ttk.Frame(bottom_frame)
        action_frame.pack(fill=tk.X, pady=(4, 0))
        
        # 开始按钮
        self.btn_start = ttk.Button(
            control_frame,
            text="开始仿真",
            command=self._btn_start_simulation,
            style="Accent.TButton",
        )
        self.btn_start.pack(side=tk.LEFT, padx=5)
        self.btn_compute = ttk.Button(
            control_frame,
            text="计算本班结果",
            command=self._btn_compute_shift,
            style="Secondary.TButton",
        )
        self.btn_compute.pack(side=tk.LEFT, padx=5)
        ToolTip(
            self.btn_compute,
            "立刻算完本班，不播放动画；完成后刷新本次产出、报警和 KPI 历史。",
        )
        
        # 暂停按钮
        self.btn_pause = ttk.Button(
            control_frame,
            text="暂停",
            command=self._btn_pause_simulation,
            state=tk.DISABLED,
            style="Secondary.TButton",
        )
        self.btn_pause.pack(side=tk.LEFT, padx=5)
        
        # 停止按钮
        self.btn_stop = ttk.Button(
            control_frame,
            text="停止",
            command=self._btn_stop_simulation,
            state=tk.DISABLED,
            style="Danger.TButton",
        )
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        # 停机切换（换型）按钮（V3.3.1）
        self.btn_changeover = ttk.Button(
            action_frame,
            text="停机切换（换型）",
            command=self._btn_changeover,
            state=tk.DISABLED,
            style="Secondary.TButton",
        )
        self.btn_changeover.pack(side=tk.LEFT, padx=5)
        ToolTip(
            self.btn_changeover,
            "仿真运行中，手动触发所选工序的换型停机（如更换型号或规格）",
        )
        
        # 速度选择
        speed_frame = ttk.Frame(control_frame)
        speed_frame.pack(side=tk.LEFT, padx=10)
        
        ttk.Label(speed_frame, text="速度:").pack(side=tk.LEFT)
        self.speed_var = tk.StringVar(value="16")
        speed_combo = ttk.Combobox(
            speed_frame,
            textvariable=self.speed_var,
            values=["1", "2", "8", "16", "32", "64"],  # 添加32倍和64倍速
            width=5,
            state="readonly"
        )
        speed_combo.pack(side=tk.LEFT)
        self.speed_combo = speed_combo
        speed_combo.bind("<<ComboboxSelected>>", self._on_speed_changed)
        
        self.btn_output_unit = ttk.Button(
            action_frame, text="产出单位", command=self._configure_output_unit)
        self.btn_output_unit.pack(side=tk.LEFT, padx=3)
        # 班次配置按钮
        self.btn_config_shift = ttk.Button(action_frame, text="班次配置", command=self._btn_config_shift)
        self.btn_config_shift.pack(side=tk.LEFT, padx=5)
        
        # 保存方案按钮
        self.btn_save_scenario = ttk.Button(
            action_frame,
            text="保存方案",
            command=self._btn_save_scenario,
            style="Secondary.TButton",
        )
        self.btn_save_scenario.pack(side=tk.LEFT, padx=5)

        # 关键控件 Tooltip（V1.2.0）
        ToolTip(self.btn_start, "开始仿真（空格）")
        ToolTip(self.btn_pause, "暂停仿真（空格）")
        ToolTip(self.btn_stop, "停止仿真")
        ToolTip(self.btn_config_shift, "配置班次时长 / 休息时间 / 时薪")
        ToolTip(self.btn_save_scenario, "保存当前产线配置为方案")
        ToolTip(self.speed_combo, "演示动画倍速（1x-64x）；运行中修改立即生效，不改变本班业务时间")
        
        actions = [self.btn_changeover, self.btn_output_unit,
                   self.btn_config_shift, self.btn_save_scenario]

        def reflow_actions(event):
            columns = 4 if event.width >= 680 else 2
            for button in actions:
                button.pack_forget()
            for index, button in enumerate(actions):
                button.grid(row=index // columns, column=index % columns,
                            sticky='ew', padx=3, pady=3)
            for column in range(4):
                action_frame.columnconfigure(column, weight=1 if column < columns else 0)
        action_frame.bind('<Configure>', reflow_actions)

        # 报警面板（右侧纵列）
        self.alert_panel = AlertPanel(alert_frame)
        self.alert_panel.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_status_bar(self) -> None:
        """
        创建状态栏
        
        状态栏显示程序运行状态和提示信息
        """
        status_frame = ttk.Frame(self.root)
        status_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        self.status_bar = ttk.Label(status_frame, text="就绪", relief=tk.FLAT, anchor=tk.W)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.analysis_progress = ttk.Progressbar(status_frame, mode="indeterminate", length=120)
        self.btn_cancel_analysis = ttk.Button(
            status_frame, text="取消分析", command=self._cancel_analysis
        )
        self.status_meta = ttk.Label(status_frame, text="", relief=tk.FLAT, anchor=tk.E)
        self.status_meta.pack(side=tk.RIGHT, padx=(4, 0))

    def _set_default_panes(self) -> None:
        """窗口有真实宽度后再放分隔条，让右侧分析提醒默认可拖。"""
        if not hasattr(self, "main_paned"):
            return
        try:
            if not self.root.winfo_exists():
                return
            self.main_paned.update_idletasks()
            total = self.main_paned.winfo_width()
            # 首次 after_idle 时窗口往往还没映射，宽度仍是 1。
            # 这时写入的 sashpos 会被夹到边缘，之后再放大也不会把提醒栏拉回来。
            if total <= 100 or not self.root.winfo_ismapped():
                self._pane_layout_attempts = getattr(self, "_pane_layout_attempts", 0) + 1
                if self._pane_layout_attempts <= 40:
                    self.root.after(50, self._set_default_panes)
                return
            self._pane_layout_attempts = 0
            alert = min(300, max(260, int(total * 0.18)))
            left = min(360, max(280, int(total * 0.22)))
            if left + alert + 360 > total:
                alert = max(220, total // 5)
                left = max(220, min(left, total - alert - 360))
            self.main_paned.sashpos(0, left)
            self.main_paned.sashpos(1, max(left + 200, total - alert))
        except tk.TclError:
            return
    
    def _create_default_line(self) -> None:
        """
        创建一个默认产线（用于测试和演示）
        
        如果用户没有加载配置，提供一个示例产线
        """
        try:
            from .models import CollaborationType
        except ImportError:
            from models import CollaborationType
        
        line = ProductionLine("示例产线")
        
        # 添加几个示例工序
        line.add_station(Station(
            id="s01",
            name="注油",
            process_time=25,
            worker_count=2,
            collaboration_type=CollaborationType.PARALLEL
        ))
        
        line.add_station(Station(
            id="s02",
            name="焊接",
            process_time=30,
            worker_count=3,
            collaboration_type=CollaborationType.PARALLEL
        ))
        
        line.add_station(Station(
            id="s03",
            name="包装",
            process_time=15,
            worker_count=2,
            collaboration_type=CollaborationType.PARALLEL
        ))
        
        self.production_line = line
        self._update_display()
    
    def _update_display(self) -> None:
        """
        更新所有GUI显示
        
        当产线数据发生变化时，调用此方法更新所有显示
        """
        has_stations = self.production_line is not None and bool(self.production_line.stations)
        self._update_empty_banner(has_stations)
        if self.production_line is None:
            return
        
        # 更新配置面板（传递产线引用，用于查找Station对象）
        self.config_panel.update_stations(self.production_line.stations, self.production_line)

        # V1.3：计量单位随生产类型变化（颗/升/袋）
        unit = self.production_line.get_unit()
        self.config_panel.set_unit(unit)
        self.kpi_dashboard.set_unit(unit)
        
        # 更新画布
        self.canvas_view.update_production_line(self.production_line)
        
        # 更新KPI
        self._update_kpi()
        self._update_status_meta()

    def _update_empty_banner(self, has_stations: bool) -> None:
        """空产线引导横幅：无工序时显示，有工序时隐藏"""
        if not hasattr(self, 'empty_banner'):
            return
        if has_stations:
            self.empty_banner.pack_forget()
            self.btn_start.config(style="Accent.TButton")
        else:
            self.btn_start.config(style="Secondary.TButton")
            self.empty_banner.pack(
                fill=tk.X, padx=5, pady=(5, 0), before=self.canvas_viewport
            )

    def _update_status_meta(self) -> None:
        """状态栏右侧：当前生产类型与计量单位"""
        if not hasattr(self, 'status_meta'):
            return
        if self.production_line is None:
            self.status_meta.config(text="")
            return
        type_map = {
            ProductionType.ASSEMBLY: "通用离散装配",
            ProductionType.LIQUID_FILLING: "液体批次",
            ProductionType.POUCH_PACKAGING: "机台包装",
        }
        ptype = type_map.get(self.production_line.production_type, "通用离散装配")
        if hasattr(self, 'project_title'):
            self.project_title.config(text=self.production_line.name)
            self.project_subtitle.config(text=(
                f"{ptype}　/　{self.production_line.shift_hours}小时班次　/　"
                f"产出单位：{self.production_line.get_unit()}"))
        self.status_meta.config(
            text=f"生产类型：{ptype}　|　单位：{self.production_line.get_unit()}/h"
        )
    
    def _update_kpi(self) -> None:
        """
        更新KPI仪表盘
        
        计算并显示关键绩效指标
        """
        if self.production_line is None:
            return
        
        bottleneck_capacity = self.production_line.get_bottleneck_capacity()
        daily_output = self.production_line.calculate_daily_output()
        total_cost = self.production_line.calculate_total_cost()
        unit_cost = self.production_line.calculate_unit_cost()
        balance_rate = self.production_line.calculate_line_balance_rate()
        upph = self.production_line.calculate_upph()
        
        self.kpi_dashboard.update_kpis({
            'bottleneck_capacity': bottleneck_capacity,
            'daily_output': daily_output,
            'total_cost': total_cost,
            'unit_cost': unit_cost,
            'balance_rate': balance_rate,
            'upph': upph
        })
        self.kpi_dashboard.update_v13_kpis({
            'batch_cycle_min': self.production_line.calculate_batch_cycle_min(),
            'batch_pass_rate': self.production_line.calculate_batch_pass_rate(),
            'yield_rate': self.production_line.calculate_avg_yield_rate(),
            'machine_oee': self.production_line.calculate_avg_machine_oee(),
        })
    
    # ==================== 菜单事件处理 ====================

    def _menu_exit(self) -> None:
        """文件菜单 - 退出"""
        if not self._confirm_discard():
            return
        if messagebox.askyesno("确认", "确定要退出吗？"):
            if self._analysis_job:
                self._analysis_job.cancel()
            if self.simulation_engine:
                self.simulation_engine.stop()
            self.root.destroy()

    def _menu_wizard(self) -> None:
        """文件菜单 - 快速配置向导"""
        if not self._can_change_configuration() or not self._confirm_discard():
            return
        dialog = WizardDialog(self.root)
        if not dialog.result:
            return

        self.production_line = copy.deepcopy(dialog.result["production_line"])
        self._mark_dirty()
        self._update_display()
        self.status_bar.config(text="快速配置向导完成")
        self.logger.info("快速配置向导完成：%s", self.production_line.name)

        if dialog.result.get("auto_start") and self.production_line.stations:
            self._btn_start_simulation()

    def _menu_open_config(self) -> None:
        """文件菜单：打开 JSON 配置。未保存时沿用现有确认。"""
        if not self._can_change_configuration() or not self._confirm_discard():
            return
        example = locate_example_config()
        initial = os.path.dirname(example) if example else None
        path = filedialog.askopenfilename(
            title="打开配置",
            initialdir=initial or "",
            filetypes=[("产线配置", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self._load_config_file(path)

    def _open_hardware_example(self) -> None:
        """空画布快捷入口：直接打开消费硬件示例，找不到再交给文件对话框。"""
        if not self._can_change_configuration() or not self._confirm_discard():
            return
        path = locate_example_config()
        if path:
            self._load_config_file(path)
            return
        messagebox.showinfo(
            "未找到示例",
            "未找到 examples/consumer-hardware-demo.json，请手动选择配置文件。",
        )
        example = locate_example_config()
        initial = os.path.dirname(example) if example else None
        path = filedialog.askopenfilename(
            title="打开配置",
            initialdir=initial or "",
            filetypes=[("产线配置", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self._load_config_file(path)

    def _load_config_file(self, path: str) -> None:
        """读取配置并刷新主界面。失败不替换当前产线。"""
        try:
            line = load_config(path)
        except Exception as exc:
            self.logger.exception("打开配置失败")
            messagebox.showerror("打开失败", str(exc))
            return
        if line is None:
            messagebox.showerror("打开失败", "无法读取该配置，请检查 JSON 格式与工序参数。")
            return
        self.production_line = line
        self._invalidate_results()
        self._dirty = False
        self._update_title()
        self._update_display()
        self.status_bar.config(text=f"已打开配置：{line.name}")

    def _open_first_run_wizard(self) -> None:
        """首次运行：自动打开快速配置向导（完成或取消后不再自动弹出）"""
        self.logger.info("首次运行：自动打开快速配置向导")
        self._menu_wizard()
        mark_first_run_done(self._first_run_marker)
    
    def _menu_analysis_guide(self) -> None:
        """分析菜单 - 分析指南（V3.3）"""
        AnalysisGuideDialog(self.root)
        self.status_bar.config(text="分析指南已打开")

    def _menu_manage_scenarios(self) -> None:
        """分析菜单 - 方案管理 / 对比（V3.3 合并）"""
        if not self._can_change_configuration():
            return
        ScenarioManageDialog(
            self.root,
            self.scenario_manager,
            on_import=self._import_scenario,
        )

    def _import_scenario(self, name: str) -> None:
        """导入方案：把所选方案加载到主界面并刷新"""
        if not self._can_change_configuration() or not self._confirm_discard():
            return False
        scenario = self.scenario_manager.get_scenario(name)
        if scenario is None:
            messagebox.showerror("错误", f"方案不存在：{name}")
            return False
        self.production_line = copy.deepcopy(scenario.production_line)
        self._invalidate_results()
        self._dirty = False
        self._update_title()
        self._update_display()
        self.status_bar.config(text=f"已导入方案：{name}")
        return True
    
    def _btn_save_scenario(self) -> bool:
        """保存方案按钮点击事件"""
        if not self._can_change_configuration():
            return False
        # 检查是否有产线配置
        if self.production_line is None:
            messagebox.showwarning("警告", "没有可保存的产线配置\n请先创建或加载产线")
            return False
        
        # 检查方案数量限制
        if not self.scenario_manager.can_add_scenario():
            messagebox.showwarning(
                "方案数量已达上限",
                f"方案数量已达到上限（{self.scenario_manager.MAX_SCENARIOS}个）\n"
                f"请先删除一个方案或使用方案对比功能"
            )
            return False
        
        # 打开保存方案对话框
        dialog = SaveScenarioDialog(self.root, self.scenario_manager, self.production_line)
        
        # 检查保存结果
        if dialog.result:
            self._dirty = False
            self._update_title()
            show_toast(self.root, f"方案已保存：{dialog.result['name']}")
            if dialog.result.get('path'):
                self.status_bar.config(
                    text=f"已保存方案：{dialog.result['name']}（已导出：{dialog.result['path']}）"
                )
            else:
                self.status_bar.config(text=f"已保存方案：{dialog.result['name']}")
            return True
        return False
    
    def _menu_export_report(self) -> None:
        """分析菜单 - 导出报告"""
        if self._analysis_busy:
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "没有可导出的产线配置")
            return
        live_engine = self.simulation_engine
        current_result = None
        if live_engine and live_engine.is_running:
            if not live_engine.is_paused:
                messagebox.showinfo("请先暂停", "请暂停或停止仿真后导出本次运行结果。")
                return
            current_result = copy.deepcopy(live_engine.build_result())
        elif (self.last_result is not None
              and self._last_result_revision == self.production_line.configuration_revision()):
            current_result = copy.deepcopy(self.last_result)
        if current_result is not None:
            choice = messagebox.askyesnocancel(
                "选择报告数据", "是否导出本次运行结果（保留实际产出和换型记录）？\n"
                "是：导出本次结果；否：按当前配置重新试算；取消：返回。"
            )
            if choice is None:
                return
            if not choice:
                current_result = None
        elif not messagebox.askyesno(
            "需要重新试算", "当前配置没有有效运行结果。是否重新试算并导出？"
        ):
            return
        file_path = filedialog.asksaveasfilename(
            title="导出报告",
            defaultextension=".xlsx",
            filetypes=[
                ("Excel 报告", "*.xlsx"),
                ("PDF 报告", "*.pdf"),
                ("所有文件", "*.*"),
            ],
        )
        if not file_path:
            return
        try:
            line = copy.deepcopy(self.production_line)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return

        def work(progress):
            progress("正在生成报告...")
            if current_result is None:
                engine = SimulationEngine(line)
                engine.random_seed = 0
                result = engine.run_sync(
                    duration_hours=line.shift_hours, cancel_event=progress.cancel_event
                )
            else:
                result = current_result
            progress("正在写入报告...")
            if not export_report(result, file_path):
                raise OSError("报告导出失败，请检查路径或依赖")
            return file_path, result

        def exported(value):
            path, result = value
            self.last_result = copy.deepcopy(result)
            self._last_result_revision = line.configuration_revision()
            self.kpi_dashboard.update_actual_output(result.total_output)
            elapsed = (result.elapsed_seconds if getattr(result, "run_id", "")
                       else result.duration_seconds)
            self.kpi_dashboard.update_timestamp(elapsed / 60)
            show_toast(self.root, f"报告已导出：{path}")

        self._start_analysis_job(
            work,
            exported,
            "正在生成报告...",
        )

    def _set_analysis_busy(self, busy: bool) -> None:
        self._analysis_busy = busy
        if busy:
            self._busy_control_states = {
                widget: str(widget.cget("state"))
                for widget in (
                    self.btn_start, self.btn_compute, self.btn_pause, self.btn_stop,
                    self.btn_changeover, self.btn_save_scenario,
                    self.btn_config_shift, self.speed_combo,
                )
            }
            for widget in self._busy_control_states:
                widget.config(state=tk.DISABLED)
            self.config_panel.set_editable(False)
            self.canvas_view.drag_enabled = False
            for label in ("文件", "分析", "方案", "报告"):
                self.menubar.entryconfig(label, state=tk.DISABLED)
            self.analysis_progress.pack(side=tk.RIGHT, padx=6)
            self.analysis_progress.start(10)
            self.btn_cancel_analysis.config(
                state=tk.NORMAL,
                text="取消" if getattr(self, "_shift_compute", False) else "取消分析",
            )
            self.btn_cancel_analysis.pack(side=tk.RIGHT, padx=4)
        else:
            self.analysis_progress.stop()
            self.analysis_progress.pack_forget()
            self.btn_cancel_analysis.pack_forget()
            for widget, state in self._busy_control_states.items():
                widget.config(state=state)
            running = bool(self.simulation_engine and self.simulation_engine.is_running)
            self.config_panel.set_editable(not running)
            self.canvas_view.drag_enabled = not running
            for label in ("文件", "分析", "方案", "报告"):
                self.menubar.entryconfig(label, state=tk.NORMAL)
            if running:
                self._lock_configuration(True)
            else:
                self.btn_compute.config(text="计算本班结果")

    def _start_analysis_job(
        self, work, on_success, status: str, finished_status: str = "分析完成",
    ) -> None:
        if self._analysis_busy:
            return
        self._set_analysis_busy(True)
        self.status_bar.config(text=status)
        revision = self._config_revision

        def success(value):
            computing = self._shift_compute
            self._shift_compute = False
            self._set_analysis_busy(False)
            self._analysis_job = None
            if revision != self._config_revision:
                if computing:
                    self.kpi_dashboard.set_run_state("stopped")
                self.status_bar.config(text="配置已变化，已丢弃过期分析结果")
                return
            on_success(value)
            if computing:
                self.kpi_dashboard.set_run_state("stopped")
            override = getattr(self, "_result_status", None)
            self._result_status = None
            self.status_bar.config(text=override or finished_status)

        def error(exc):
            computing = self._shift_compute
            self._shift_compute = False
            self._set_analysis_busy(False)
            self._analysis_job = None
            if computing:
                self.kpi_dashboard.set_run_state("stopped")
            self.logger.error("后台分析失败：%s", exc)
            messagebox.showerror("分析失败", f"{exc}\n\n详细日志：logs/app.log")
            self.status_bar.config(text="计算失败" if computing else "分析失败")

        def cancelled():
            computing = self._shift_compute
            self._shift_compute = False
            self._set_analysis_busy(False)
            self._analysis_job = None
            if computing:
                self.kpi_dashboard.set_run_state("stopped")
            self.status_bar.config(text="本班计算已取消" if computing else "分析已取消")

        self._shift_compute = getattr(self, "_shift_compute", False)
        self._analysis_job = BackgroundJob(
            self.root, work, success, error,
            on_progress=lambda value: self.status_bar.config(text=str(value)),
            on_cancel=cancelled,
        )
        self._analysis_job.start()

    def _cancel_analysis(self) -> None:
        if self._analysis_job:
            self._analysis_job.cancel()
            self.btn_cancel_analysis.config(state=tk.DISABLED)
            self.status_bar.config(text="正在取消分析...")

    def _menu_sensitivity(self) -> None:
        """分析菜单 - 敏感性试算（V3.1 P2）"""
        if not self._supports_station_analysis():
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "没有可试算的产线配置")
            return
        line = copy.deepcopy(self.production_line)
        self._start_analysis_job(
            lambda progress: run_sensitivity(
                line, duration_hours=line.shift_hours, cancel_event=progress.cancel_event
            ),
            self._show_sensitivity_results,
            "正在运行敏感性试算...",
        )

    def _show_sensitivity_results(self, scenarios) -> None:
        revision = self._config_revision
        rows = []
        for s in scenarios:
            rows.append({
                "label": s["label"],
                "output": s["total_output"],
                "unit_cost": format_cost(s.get("theoretical_unit_cost")),
                "actual_unit_cost": format_cost(s.get("actual_unit_cost")),
                "delta_output": s["delta_output"],
                "delta_unit_cost": format_cost(s.get("delta_unit_cost")),
                "feasible": "可行" if s.get("feasible", True) else "不可行",
                "constraints": "；".join(s.get("constraints", [])),
            })
        columns = [
            ("label", "方案", 120, tk.W),
            ("output", "总产出", 90, tk.E),
            ("unit_cost", "理论单位人工成本", 130, tk.E),
            ("actual_unit_cost", "实际单位人工成本", 130, tk.E),
            ("delta_output", "Δ产出", 80, tk.E),
            ("delta_unit_cost", "Δ单位成本", 100, tk.E),
            ("feasible", "可行性", 70, tk.W),
            ("constraints", "约束", 220, tk.W),
        ]

        def _apply_best(_dialog=None) -> None:
            if self._config_revision != revision:
                messagebox.showwarning("结果已过期", "配置已变化，请重新试算。")
                return
            best = next(
                (s for s in scenarios if s.get("apply") and s.get("feasible", True)
                 and s["delta_output"] > 0),
                None,
            )
            if best is None:
                best = next(
                    (s for s in scenarios if s.get("apply") and s.get("feasible", True)
                     and s.get("delta_unit_cost") is not None and s["delta_unit_cost"] < 0),
                    None,
                )
            if best is None:
                messagebox.showinfo("提示", "没有可应用的建议方案")
                return
            self._apply_suggestion(best)
            if _dialog:
                _dialog._close()
            messagebox.showinfo(
                "已应用",
                f"已应用「{best['label']}」到当前产线（Δ产出 {best['delta_output']:+g}）",
            )

        ResultTableDialog(
            self.root,
            "敏感性试算结果",
            columns,
            rows,
            highlight_key="label",
            highlight_value="基线",
            actions=[("应用最优建议", _apply_best)],
            help_text=(
                "总产出为末端良品；理论单位人工成本=预计日人工÷理论日产量；"
                "实际单位人工成本=实际窗口人工÷本次仿真良品，零产出不可计算。\n"
                "「应用最优建议」会把首个 Δ产出>0（或 Δ单位成本<0）的方案"
                "应用到当前产线。"
            ),
        )
        self.status_bar.config(text="敏感性试算完成")

    def _apply_suggestion(self, scenario) -> None:
        """把敏感性试算建议应用到当前产线（V3.3）"""
        if not self._can_change_configuration():
            return
        app = scenario.get("apply")
        if not app:
            return
        if "material_prices" in app:
            for material in self.production_line.materials:
                if material.name in app["material_prices"]:
                    material.unit_cost = app["material_prices"][material.name]
        else:
            station = self.production_line.get_station(app["station_id"])
            if station is not None:
                setattr(station, app["attr"], app["value"])
        self._mark_dirty()
        self._update_display()

    def _menu_hr_planning(self) -> None:
        """分析菜单 - 人力规划（V3.2，面向 HRBP/HR）"""
        if not self._supports_station_analysis():
            return
        if self.production_line is None:
            messagebox.showwarning("警告", "没有可规划的产线，请先新建或打开产线")
            return
        HrPlanningDialog(self.root, self.production_line)
        self.status_bar.config(text="人力规划完成")

    def _menu_history(self) -> None:
        """分析菜单 - KPI 历史趋势（V3.2 P1）"""
        HistoryDialog(self.root, self.production_line)
        self.status_bar.config(text="KPI 历史趋势已关闭")

    def _menu_sweep(self) -> None:
        """分析菜单 - 批量试算（V3.2 P1）"""
        if not self._supports_station_analysis():
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "没有可试算的产线配置")
            return
        SweepDialog(self.root, self.production_line)
        self.status_bar.config(text="批量试算已关闭")

    def _menu_optimize(self) -> None:
        """分析菜单 - 智能优化（V3.2 P2，遗传算法）"""
        if not self._supports_station_analysis():
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "没有可优化的产线配置")
            return
        OptimizeDialog(
            self.root,
            self.production_line,
            on_apply=self._refresh_after_optimize,
            can_apply=self._can_change_configuration,
        )
        self.status_bar.config(text="智能优化已关闭")

    def _refresh_after_optimize(self) -> None:
        """智能优化方案应用到产线后刷新主界面（V3.3.1）"""
        self._mark_dirty()
        self._update_display()
        self.status_bar.config(text="已应用智能优化方案")
    
    def _menu_help(self) -> None:
        """帮助菜单 - 使用说明"""
        HelpDialog(self.root)
    
    def _menu_about(self) -> None:
        """帮助菜单 - 关于"""
        AboutDialog(self.root)

    def _menu_glossary(self) -> None:
        """帮助菜单 - 术语说明"""
        GlossaryDialog(self.root)

    def _on_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        """Tkinter 回调异常统一处理：记录日志并弹窗提示"""
        if self.logger:
            self.logger.error("界面回调异常", exc_info=(exc_type, exc_value, exc_tb))
        messagebox.showerror(
            "程序错误",
            f"发生未预期错误：{exc_value}\n\n详细日志：logs/app.log",
        )

    def _mark_dirty(self) -> None:
        """标记产线配置有未保存修改"""
        self._dirty = True
        self._invalidate_results()
        self._update_title()
        self.status_bar.config(text="有未保存的修改")

    def _update_title(self) -> None:
        self.root.title(f"{'* ' if self._dirty else ''}{PRODUCT_NAME} v{__version__}")

    def _invalidate_results(self) -> None:
        """Keep archived results, but never present them as this configuration's output."""
        self._config_revision = getattr(self, '_config_revision', 0) + 1
        if hasattr(self, 'kpi_dashboard'):
            self.kpi_dashboard.clear_actual_output(
                "配置已变化，需重新仿真" if getattr(self, 'last_result', None) else "尚未仿真"
            )
        if hasattr(self, 'canvas_view'):
            self.canvas_view.simulation_state = None
        if hasattr(self, 'alert_panel'):
            self.alert_panel.clear()

    def _can_change_configuration(self) -> bool:
        if getattr(self, '_analysis_busy', False):
            messagebox.showinfo("分析进行中", "请先取消或等待当前分析完成。")
            return False
        engine = getattr(self, 'simulation_engine', None)
        if engine and engine.is_running:
            messagebox.showinfo("请先停止仿真", "运行或暂停时配置保持锁定，请停止后修改。")
            return False
        return True

    def _supports_station_analysis(self) -> bool:
        if not self._can_change_configuration():
            return False
        if (self.production_line
                and self.production_line.production_type == ProductionType.LIQUID_FILLING):
            messagebox.showinfo(
                "当前模型暂不支持", "液体采用配方批次模型。当前不支持按工序人数反推编制、"
                "敏感性试算或遗传优化；可使用批次仿真和报告查看放行结果。"
            )
            return False
        return True

    def _set_file_command_state(self, state: str, labels) -> None:
        """按菜单文字锁定命令，避免插入新菜单项后下标错位。"""
        end = self.file_menu.index("end")
        if end is None:
            return
        wanted = set(labels)
        for index in range(end + 1):
            if self.file_menu.type(index) != "command":
                continue
            if self.file_menu.entrycget(index, "label") in wanted:
                self.file_menu.entryconfig(index, state=state)

    def _lock_configuration(self, locked: bool) -> None:
        self.config_panel.set_editable(not locked)
        self.canvas_view.drag_enabled = not locked
        self.btn_config_shift.config(state=tk.DISABLED if locked else tk.NORMAL)
        self.btn_save_scenario.config(state=tk.DISABLED if locked else tk.NORMAL)
        self.btn_compute.config(state=tk.DISABLED if locked else tk.NORMAL)
        state = tk.DISABLED if locked else tk.NORMAL
        self._set_file_command_state(
            state, ("快速配置向导...", "打开配置...", "保存方案..."),
        )
        for label in ('分析', '方案'):
            self.menubar.entryconfig(label, state=state)

    def _confirm_discard(self) -> bool:
        """
        确认放弃未保存修改

        Returns:
            bool: True 表示可以继续（放弃修改），False 表示取消操作
        """
        if not self._dirty:
            return True
        choice = messagebox.askyesnocancel(
            "未保存的修改",
            "当前配置有未保存的修改。是否先保存？\n是：保存后继续；否：放弃；取消：返回。",
        )
        if choice is None:
            return False
        return self._btn_save_scenario() if choice else True

    def _bind_shortcuts(self) -> None:
        """绑定全局键盘快捷键"""
        self.root.bind('<Control-s>', lambda e: self._btn_save_scenario())
        if self.root.tk.call('tk', 'windowingsystem') == 'aqua':
            self.root.bind('<Command-s>', lambda e: self._btn_save_scenario())
        self.root.bind('<space>', self._on_space_shortcut)

    def _on_space_shortcut(self, event) -> str:
        """空格：开始/暂停/恢复仿真（输入框内不触发）"""
        if self._analysis_busy:
            return 'break'
        focused = self.root.focus_get()
        if focused is not None and focused.winfo_class() in {
            'Entry', 'TEntry', 'Text', 'TCombobox', 'Spinbox', 'TSpinbox',
            'Button', 'TButton', 'Checkbutton', 'TCheckbutton',
            'Radiobutton', 'TRadiobutton', 'Treeview',
        }:
            return 'break'
        if self.simulation_engine and self.simulation_engine.is_running:
            if self.simulation_engine.is_paused:
                self._btn_start_simulation()  # 恢复
            else:
                self._btn_pause_simulation()
        else:
            self._btn_start_simulation()
        return 'break'

    def _on_speed_changed(self, _event=None) -> None:
        """运行中改演示倍速。只写引擎的墙钟倍速，不重开仿真。"""
        engine = self.simulation_engine
        if engine is None or not getattr(engine, "is_running", False):
            return
        try:
            pace = int(self.speed_var.get())
        except (TypeError, ValueError):
            return
        if pace > 0:
            engine.speed = pace

    def _record_finished_run(self, result, run_line) -> None:
        """与动画仿真自然结束相同：本次产出、时间戳和 KPI 历史。"""
        copied = copy.deepcopy(result)
        self.last_result = copied
        self._last_result_revision = run_line.configuration_revision()
        self.kpi_dashboard.update_actual_output(copied.total_output)
        elapsed = (copied.elapsed_seconds if getattr(copied, "run_id", "")
                   else copied.duration_seconds)
        self.kpi_dashboard.update_timestamp(elapsed / 60.0)
        if not append_snapshot(build_snapshot(run_line, copied)):
            raise OSError("KPI 历史快照未能保存")

    def _reset_canvas_after_run(self) -> None:
        """结束后画布回到空闲，避免把上一轮在制当成当前配置。"""
        if self.canvas_view and self.production_line:
            self.canvas_view.simulation_state = None
            for station in self.production_line.stations:
                station.current_status = "idle"
                station.wip_count = 0
            self.canvas_view.update_production_line(self.production_line)

    def _show_result_alerts(self, result) -> None:
        """同步计算没有逐条推送，完成后一次性刷入本班报警。"""
        self.alert_panel.clear()
        for alert in getattr(result, "alerts", None) or []:
            self.alert_panel.add_alert(alert)
    
    # ==================== 按钮事件处理 ====================
    
    def _btn_start_simulation(self) -> None:
        """开始仿真按钮"""
        if self._analysis_busy:
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "请先添加至少一个工序")
            return
        
        # 校验产线配置
        valid, error = validate_production_line(self.production_line)
        if not valid:
            messagebox.showerror("错误", f"产线配置无效：{error}")
            return
        
        # 判断是否是新的仿真（停止后重新开始）还是暂停后继续
        is_new_simulation = False
        
        if self.simulation_engine is None:
            # 没有仿真引擎，是新的仿真
            is_new_simulation = True
            # 创建新的仿真引擎
            self._run_line = copy.deepcopy(self.production_line)
            self.simulation_engine = SimulationEngine(self._run_line)
            self.simulation_engine.random_seed = 0
            # 设置状态更新回调
            self.simulation_engine.set_callback(
                lambda state, engine=self.simulation_engine:
                self._on_simulation_state_update(engine, state)
            )
        elif not self.simulation_engine.is_running:
            # 仿真已停止，是新的仿真
            is_new_simulation = True
            # 创建新的仿真引擎
            self._run_line = copy.deepcopy(self.production_line)
            self.simulation_engine = SimulationEngine(self._run_line)
            self.simulation_engine.random_seed = 0
            # 设置状态更新回调
            self.simulation_engine.set_callback(
                lambda state, engine=self.simulation_engine:
                self._on_simulation_state_update(engine, state)
            )
        elif self.simulation_engine.is_paused:
            # 暂停后继续，恢复仿真
            self.simulation_engine.resume()
            # 不清空报警信息
            self.status_bar.config(text="仿真已恢复")
            # 更新按钮状态
            self.btn_start.config(state=tk.DISABLED)
            self.btn_start.config(text="开始仿真")
            self.btn_pause.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.NORMAL)
            self.kpi_dashboard.set_run_state('running')
            return  # 不需要重新启动仿真
        
        # 如果是新的仿真，清空报警信息并重置时间戳
        if is_new_simulation:
            self.alert_panel.clear()
            self.kpi_dashboard.reset_timestamp()  # 重置时间戳
            self.kpi_dashboard.update_actual_output(0)
            self.kpi_dashboard.set_run_state('running')
            # 运行中锁定配置；速度下拉保持可用，只影响动画墙钟。
            self._lock_configuration(True)
        else:
            # 暂停后继续
            self.kpi_dashboard.set_run_state('running')
        
        # 获取仿真速度
        speed = int(self.speed_var.get())
        
        # 运行仿真（使用产线的班次配置）
        duration_hours = self.production_line.shift_hours if self.production_line else 8.0
        self.simulation_engine.run(duration_hours=duration_hours, speed=speed)
        
        # 更新按钮状态
        self.btn_start.config(state=tk.DISABLED)
        self.btn_start.config(text="开始仿真")
        self.btn_pause.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_changeover.config(state=tk.NORMAL)
        
        self.status_bar.config(text="仿真运行中...")
        engine = self.simulation_engine
        self.root.after(100, lambda: self._poll_simulation_completion(engine))

    def _btn_compute_shift(self) -> None:
        """立刻算完本班。走 run_sync，收尾与仿真自然完成一致。"""
        if self._analysis_busy:
            return
        if self.simulation_engine and self.simulation_engine.is_running:
            messagebox.showinfo("请先停止仿真", "演示仿真进行中，请停止后再计算本班结果。")
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "请先添加至少一个工序")
            return
        valid, error = validate_production_line(self.production_line)
        if not valid:
            messagebox.showerror("错误", f"产线配置无效：{error}")
            return
        line = copy.deepcopy(self.production_line)
        duration = line.shift_hours
        self._shift_compute = True
        self.alert_panel.clear()
        self.kpi_dashboard.reset_timestamp()
        self.kpi_dashboard.update_actual_output(0)
        self.kpi_dashboard.set_run_state("computing")
        self.btn_compute.config(text="计算中")

        def work(progress):
            progress("正在计算本班结果...")
            engine = SimulationEngine(line)
            engine.random_seed = 0
            return engine.run_sync(
                duration_hours=duration, cancel_event=progress.cancel_event,
            )

        def done(result):
            try:
                self._record_finished_run(result, line)
                self._show_result_alerts(result)
                self._reset_canvas_after_run()
            except Exception as exc:
                self.logger.exception("记录本班结果失败")
                self._result_status = "本班已算完，历史快照未保存"
                messagebox.showwarning(
                    "记录失败", f"本班已算完，但历史快照未保存：{exc}",
                )

        self._start_analysis_job(
            work, done, "正在计算本班结果...", finished_status="本班结果已计算",
        )

    def _poll_simulation_completion(self, engine: SimulationEngine) -> None:
        """Observe worker completion on the Tk thread and restore controls."""
        if self.simulation_engine is not engine:
            return
        if engine.is_running:
            self.root.after(100, lambda: self._poll_simulation_completion(engine))
        elif self._analysis_busy:
            self.root.after(100, lambda: self._poll_simulation_completion(engine))
        else:
            self._finalize_simulation(completed=True)
    
    def _btn_pause_simulation(self) -> None:
        """暂停仿真按钮"""
        if self.simulation_engine:
            self.simulation_engine.pause()
            self.btn_start.config(state=tk.NORMAL, text="继续仿真")
            self.btn_pause.config(state=tk.DISABLED)
            self.status_bar.config(text="仿真已暂停")
            self.kpi_dashboard.set_run_state('paused')
            # 暂停时，时间戳不再更新（保持当前值）
    
    def _btn_stop_simulation(self) -> None:
        """停止仿真按钮"""
        self._finalize_simulation(completed=False)

    def _finalize_simulation(self, completed: bool) -> None:
        """Record the last snapshot and always stop the current engine."""
        if self.simulation_engine:
            engine = self.simulation_engine
            stopped = False
            try:
                engine.stop()
                stopped = True
                result = engine.build_result()
                run_line = self._run_line or self.production_line
                self._record_finished_run(result, run_line)
            except Exception as exc:
                self.logger.exception("记录仿真快照失败")
                messagebox.showwarning("记录失败", f"仿真已结束，但历史快照未保存：{exc}")
            finally:
                if not stopped:
                    engine.stop()
                # 确保清理资源
                self.simulation_engine = None
                
                # 更新按钮状态
                self.btn_start.config(state=tk.NORMAL)
                self.btn_start.config(text="开始仿真")
                self.btn_pause.config(state=tk.DISABLED)
                self.btn_stop.config(state=tk.DISABLED)
                self.btn_changeover.config(state=tk.DISABLED)
                
                self._reset_canvas_after_run()
                
                # 注意：停止时不清空报警信息，只在重新开始仿真时清空
                self.status_bar.config(text="仿真已完成" if completed else "仿真已停止")
                self.kpi_dashboard.set_run_state('stopped')
                # 恢复编辑能力
                self.canvas_view.drag_enabled = True
                self.speed_combo.config(state='readonly')
                self._lock_configuration(False)
    
    def _configure_output_unit(self) -> None:
        """配置离散产出标签，不转换物理数量。"""
        if not self._can_change_configuration() or self.production_line is None:
            return
        from tkinter import simpledialog
        from src.utils import validate_production_line
        value = simpledialog.askstring(
            "产出单位", "单位标签（如件、台、套）；不执行数量换算。液体固定为千克。",
            initialvalue=self.production_line.get_unit(), parent=self.root)
        if value is None:
            return
        candidate = copy.deepcopy(self.production_line)
        candidate.output_unit = value.strip()
        if not candidate.output_unit:
            messagebox.showerror("错误", "产出单位不能为空")
            return
        valid, error = validate_production_line(candidate)
        if not valid:
            messagebox.showerror("错误", error)
            return
        self.production_line.output_unit = candidate.output_unit
        self._mark_dirty()
        self._update_display()

    def _btn_config_shift(self) -> None:
        """班次配置按钮"""
        if not self._can_change_configuration():
            return
        if self.production_line is None:
            self.production_line = ProductionLine("新产线")
        
        # 打开班次配置对话框
        try:
            from .gui_panels import ShiftConfigDialog
        except ImportError:
            from gui_panels import ShiftConfigDialog
        
        dialog = ShiftConfigDialog(
            self.root,
            shift_hours=self.production_line.shift_hours,
            break_minutes=self.production_line.break_minutes,
            worker_hourly_wage=self.production_line.worker_hourly_wage
        )
        
        if dialog.result:
            # 更新产线配置
            self.production_line.shift_hours = dialog.result['shift_hours']
            self.production_line.break_minutes = dialog.result['break_minutes']
            self.production_line.worker_hourly_wage = dialog.result['worker_hourly_wage']
            
            self._mark_dirty()
            # 更新KPI显示
            self._update_kpi()
            
            self.status_bar.config(text="班次配置已更新")
    
    # ==================== 配置面板回调 ====================
    
    def _on_add_station(self) -> None:
        """配置面板 - 添加工序回调"""
        if not self._can_change_configuration():
            return
        try:
            from .gui_panels import StationDialog
        except ImportError:
            from gui_panels import StationDialog
        
        dialog = StationDialog(self.root, title="添加工序", production_line=self.production_line)
        if dialog.result:
            if self.production_line is None:
                self.production_line = ProductionLine("新产线")
            
            station = dialog.result
            self.production_line.add_station(station)
            self._mark_dirty()
            self._update_display()
            self.status_bar.config(text=f"已添加工序：{station.name}")
    
    def _on_edit_station(self, station_id: Optional[str] = None) -> None:
        """配置面板 - 编辑工序回调"""
        if not self._can_change_configuration():
            return
        try:
            from .gui_panels import StationDialog
        except ImportError:
            from gui_panels import StationDialog
        
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "没有可编辑的工序")
            return
        
        # 如果没有指定ID，获取选中的工序
        if station_id is None:
            selected = self.config_panel.get_selected_station()
            if selected is None:
                messagebox.showwarning("警告", "请先选择一个工序")
                return
            station_id = selected.id
        
        # 找到要编辑的工序
        station = self.production_line.get_station(station_id)
        if station is None:
            messagebox.showerror("错误", f"找不到工序：{station_id}")
            return
        
        # 打开编辑对话框
        dialog = StationDialog(
            self.root,
            title="编辑工序",
            station=station,
            production_line=self.production_line,
        )
        if dialog.result:
            # 更新工序数据
            updated = dialog.result
            station.name = updated.name
            station.process_time = updated.process_time
            station.worker_count = updated.worker_count
            station.oee = updated.oee
            station.collaboration_type = updated.collaboration_type
            station.job_role = updated.job_role
            station.machine_takt = updated.machine_takt
            station.machine_count = updated.machine_count
            station.min_machine_takt = updated.min_machine_takt
            station.changeover_time = updated.changeover_time
            station.clean_time_minutes = updated.clean_time_minutes
            station.sampling_rate = updated.sampling_rate
            station.defect_rate = updated.defect_rate
            station.rework_minutes = updated.rework_minutes
            station.efficiency = updated.efficiency
            station.buffer_capacity = updated.buffer_capacity
            
            self._mark_dirty()
            self._update_display()
            self.status_bar.config(text=f"已更新工序：{station.name}")
    
    def _on_delete_station(self) -> None:
        """配置面板 - 删除工序回调"""
        if not self._can_change_configuration():
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "没有可删除的工序")
            return
        
        selected = self.config_panel.get_selected_station()
        if selected is None:
            messagebox.showwarning("警告", "请先选择一个工序")
            return
        
        if messagebox.askyesno("确认", f"确定要删除工序 '{selected.name}' 吗？"):
            self.production_line.remove_station(selected.id)
            self._mark_dirty()
            self._update_display()
            self.status_bar.config(text=f"已删除工序：{selected.name}")
    
    def _on_select_station(self, station: Station) -> None:
        """配置面板 - 选择工序回调"""
        # 在画布上高亮显示选中的工序
        if self.canvas_view:
            self.canvas_view.highlight_station(station.id)

    def _on_canvas_reorder(self, order_ids: list) -> None:
        """画布拖拽排序回调：更新产线工序顺序"""
        if not self._can_change_configuration():
            return
        if self.production_line is None:
            return
        if self.simulation_engine and self.simulation_engine.is_running:
            messagebox.showwarning("警告", "仿真运行中不能调整工序顺序")
            self.canvas_view.update_production_line(self.production_line)
            return

        station_map = {s.id: s for s in self.production_line.stations}
        self.production_line.stations = [
            station_map[sid] for sid in order_ids if sid in station_map
        ]
        self._mark_dirty()
        self._update_display()
        self.status_bar.config(text="工序顺序已调整")
        self.logger.info("拖拽调整工序顺序：%s", order_ids)

    def _on_canvas_select_station(self, station_id: str) -> None:
        """画布单击工序：高亮并联动配置面板"""
        if self.canvas_view:
            self.canvas_view.highlight_station(station_id)
        if self.config_panel:
            self.config_panel.select_station(station_id)

    def _on_canvas_station_menu(self, station_id: str, x: int, y: int) -> None:
        """画布右键菜单：编辑 / 删除 / 触发切换"""
        station = self.production_line.get_station(station_id) if self.production_line else None
        if station is None:
            return

        running = bool(self.simulation_engine and self.simulation_engine.is_running)
        edit_state = tk.DISABLED if running or self._analysis_busy else tk.NORMAL

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="编辑工序",
            command=lambda: self._on_edit_station(station_id),
            state=edit_state,
        )
        menu.add_command(
            label="删除工序",
            command=lambda: self._delete_station_by_id(station_id),
            state=edit_state,
        )
        menu.add_separator()
        menu.add_command(label="触发切换...", command=lambda: self._trigger_changeover(station_id))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _delete_station_by_id(self, station_id: str) -> None:
        """按 ID 删除工序（供右键菜单调用）"""
        if not self._can_change_configuration():
            return
        if self.production_line is None:
            return
        station = self.production_line.get_station(station_id)
        if station is None:
            return
        if messagebox.askyesno("确认", f"确定要删除工序 '{station.name}' 吗？"):
            self.production_line.remove_station(station_id)
            self._mark_dirty()
            self._update_display()
            self.status_bar.config(text=f"已删除工序：{station.name}")

    def _btn_changeover(self) -> None:
        """主界面按钮 - 停机切换（换型）"""
        if self._analysis_busy:
            return
        if self.production_line is None or not self.production_line.stations:
            messagebox.showwarning("警告", "没有可切换的工序")
            return
        dialog = ChangeoverDialog(self.root, self.production_line)
        if not dialog.result:
            return
        self._trigger_changeover(
            dialog.result["station_id"], dialog.result["minutes"]
        )

    def _trigger_changeover(self, station_id: str, minutes: int = 45) -> None:
        """触发指定工序的换型停机"""
        if self._analysis_busy:
            return
        if (self.production_line
                and self.production_line.production_type == ProductionType.LIQUID_FILLING):
            messagebox.showinfo(
                "请配置配方换型", "液体采用配方批次模型，请在配置中的配方换型矩阵定义换型；"
                "当前不支持离散工序手动停机。"
            )
            return
        if not self.simulation_engine or not self.simulation_engine.is_running:
            messagebox.showwarning("警告", "请先开始仿真，再执行停机切换")
            return
        station = self.production_line.get_station(station_id)
        if station is None:
            return
        try:
            self.simulation_engine.trigger_changeover(station_id, minutes)
            self.canvas_view.highlight_station(station_id)
            self.status_bar.config(
                text=f"已提交「{station.name}」换型请求，当前加工完成后停机{minutes}分钟"
            )
            show_toast(self.root, f"「{station.name}」换型请求已提交，等待当前加工完成")
        except ValueError as e:
            messagebox.showerror("错误", str(e))

    # ==================== 仿真回调 ====================
    
    def _on_simulation_state_update(self, engine: SimulationEngine, state: SimulationState) -> None:
        """
        仿真状态更新回调
        
        当仿真状态发生变化时，SimPy会调用这个函数
        注意：这个函数可能在SimPy线程中调用，需要切换到GUI线程
        
        Args:
            state: 新的仿真状态
        """
        # Only the Tk thread may touch Tk; keep just the newest state.
        try:
            self._state_queue.put_nowait((engine, state))
        except Full:
            try:
                self._state_queue.get_nowait()
            except Empty:
                pass
            self._state_queue.put_nowait((engine, state))

    def _drain_simulation_states(self) -> None:
        """Consume the latest worker snapshot on the Tk event loop."""
        latest = None
        while True:
            try:
                latest = self._state_queue.get_nowait()
            except Empty:
                break
        if latest and latest[0] is self.simulation_engine:
            self._update_simulation_state(latest[1])
        self.root.after(100, self._drain_simulation_states)
    
    def _update_simulation_state(self, state: SimulationState) -> None:
        """
        更新仿真状态显示（在GUI线程中执行）
        
        Args:
            state: 仿真状态
        """
        # 更新画布显示
        self.canvas_view.update_simulation_state(state)
        self.kpi_dashboard.update_actual_output(state.total_output)
        
        # 更新状态栏显示仿真时间
        if state:
            hours = int(state.current_time // 3600)
            minutes = int((state.current_time % 3600) // 60)
            seconds = int(state.current_time % 60)
            time_str = f"仿真时间: {hours:02d}:{minutes:02d}:{seconds:02d}"
            self.status_bar.config(text=time_str)
            
            # 更新KPI仪表盘的时间戳（单位：分钟）
            if self.simulation_engine and self.simulation_engine.is_running and not self.simulation_engine.is_paused:
                # 仿真运行中且未暂停，更新时间戳
                timestamp_minutes = state.current_time / 60.0
                self.kpi_dashboard.update_timestamp(timestamp_minutes)
        
        # 更新KPI（如果有新的数据）
        # 这里可以添加实时KPI更新逻辑
        
        # 检查报警队列
        if self.simulation_engine:
            from queue import Queue
            while not self.simulation_engine.alert_queue.empty():
                alert = self.simulation_engine.alert_queue.get()
                self.alert_panel.add_alert(alert)
    
    def run(self) -> None:
        """
        运行主循环
        
        启动Tkinter的事件循环，程序开始运行
        """
        self.root.mainloop()
