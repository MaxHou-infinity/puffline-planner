"""
GUI面板组件 - 各种功能面板

这个文件包含所有面板组件：
- ConfigPanel: 配置面板（工序列表、编辑功能）
- KPIDashboard: KPI仪表盘（显示关键指标）
- AlertPanel: 报警面板（显示瓶颈、浪费等警报）
- StationDialog: 工序编辑对话框

面板设计原则：
1. 独立性：每个面板是独立的组件，可以单独测试
2. 回调机制：通过回调函数与主窗口通信
3. 数据绑定：面板显示数据，但不直接修改数据
"""

import tkinter as tk
import os
import webbrowser
import copy
from tkinter import ttk, messagebox, filedialog
from typing import List, Optional, Callable, Dict, Any

from src.models import (
    Station,
    Alert,
    CollaborationType,
    JobRole,
    ProductionLine,
)
from src.utils import (
    validate_station,
    get_alert_color,
    parse_shift_params,
    resolve_template_reset,
)
from src.scenario_manager import ScenarioManager
from src.theme import (
    ALERT_ICONS,
    COLORS,
    ToolTip,
    resolve_font_family,
    show_toast,
)
from src.glossary import GLOSSARY
from src.version import (
    BUG_REPORT_URL,
    PRODUCT_NAME,
    WECHAT_QR_PATH,
    WECHAT_QR_SMALL_PATH,
    __version__,
)
from src.models import (
    ProductionType,
    create_liquid_line,
    create_pouch_line,
)
from src.hr_planning import (
    LaborCostConfig,
    LearningCurveConfig,
    ShiftPlan,
    build_hr_summary,
    current_headcount_by_role,
)
from src.reporting import export_hr_pdf, export_hr_report
from src.history import (
    KPI_NAMES,
    append_snapshot,
    build_snapshot,
    kpi_series,
    load_history,
    export_history,
    clear_history,
)
from src.sensitivity import run_sweep
from src.optimizer import _params_text, optimize
from src.simulation import SimulationEngine


def fuzzy_contains(text: str, query: str) -> bool:
    """
    模糊匹配（V3.3.3 术语搜索）

    - 子串匹配：query 出现在 text 中（忽略大小写）；
    - 顺序匹配：query 的字符按顺序全部出现在 text 中（容忍缺字）。
    """
    text_lower = text.lower()
    query_lower = query.lower()
    if not query_lower:
        return True
    if query_lower in text_lower:
        return True
    position = 0
    for char in text_lower:
        if position < len(query_lower) and char == query_lower[position]:
            position += 1
    return position == len(query_lower)


def build_template_line(key: str) -> ProductionLine:
    """
    按模板 key 创建产线（纯函数，供向导与测试复用）

    Args:
        key: simple / standard / complex / blank

    Returns:
        ProductionLine: 新产线
    """
    from src.models import CollaborationType

    line = ProductionLine("新产线")
    if key == "blank":
        return line

    for idx, (name, time, workers, mode) in enumerate(WizardDialog.TEMPLATES[key], 1):
        line.add_station(Station(
            id=f"s{idx:02d}",
            name=name,
            process_time=time,
            worker_count=workers,
            collaboration_type=(
                CollaborationType.PARALLEL if mode == "parallel" else CollaborationType.COLLABORATIVE
            ),
        ))
    return line


def station_edit_fields(production_type: ProductionType) -> set:
    """
    工序编辑字段与生产类型的映射（V1.3）

    - assembly：不显示任何 V1.3 字段
    - liquid_filling：清洗时间（CIP/SIP）+ 质量门（抽检/缺陷/返工）
    - pouch_packaging：机台节拍 + 质量门（抽检/缺陷/返工），
      清洗时间对高速包装无排程意义，不显示
    """
    quality = {'sampling_rate', 'defect_rate', 'rework_minutes'}
    if production_type == ProductionType.LIQUID_FILLING:
        return {'clean_time_minutes', 'job_role'} | quality
    if production_type == ProductionType.POUCH_PACKAGING:
        return {'machine_takt', 'job_role'} | quality
    return set()


JOB_ROLE_LABELS = {
    JobRole.MIXER.value: "调香师/调配工",
    JobRole.FILLING_OPERATOR.value: "灌装操作员",
    JobRole.QC_TECHNICIAN.value: "QC 化验员",
    JobRole.PACKAGING_OPERATOR.value: "包装机手",
    JobRole.CLEANER.value: "清洗工",
    JobRole.GENERAL.value: "通用装配工",
}


class ConfigPanel(ttk.Frame):
    """
    配置面板 - 显示和管理工序列表
    
    功能：
    - 显示所有工序
    - 提供添加、编辑、删除按钮
    - 支持工序选择
    
    使用方式：
        panel = ConfigPanel(parent, on_add=add_callback, ...)
        panel.update_stations(stations)
    """
    
    def __init__(
        self,
        parent,
        on_add: Optional[Callable] = None,
        on_edit: Optional[Callable] = None,
        on_delete: Optional[Callable] = None,
        on_select: Optional[Callable] = None
    ):
        """
        初始化配置面板
        
        Args:
            parent: 父组件
            on_add: 添加工序回调函数
            on_edit: 编辑工序回调函数
            on_delete: 删除工序回调函数
            on_select: 选择工序回调函数
        """
        super().__init__(parent)
        
        # 回调函数
        self.on_add = on_add
        self.on_edit = on_edit
        self.on_delete = on_delete
        self.on_select = on_select
        
        # 当前选中的工序和产线引用（用于查找Station对象）
        self.selected_station: Optional[Station] = None
        self.production_line: Optional[Any] = None  # 保存产线引用，用于查找Station对象
        
        # 创建界面
        self._create_widgets()
    
    def _create_widgets(self) -> None:
        """创建界面组件"""
        # 标题 + 术语速查
        title_frame = ttk.Frame(self)
        title_frame.pack(fill=tk.X, pady=5)
        ttk.Label(title_frame, text="工序配置", font=('Arial', 12, 'bold')).pack(side=tk.LEFT, padx=5)
        self.production_type_label = ttk.Label(
            title_frame,
            text="类型：烟弹组装",
            foreground=COLORS['text_secondary'],
            font=('Arial', 9),
        )
        self.production_type_label.pack(side=tk.LEFT, padx=8)
        self.btn_glossary = ttk.Button(
            title_frame,
            text="术语速查",
            width=8,
            command=self._open_glossary,
        )
        self.btn_glossary.pack(side=tk.RIGHT, padx=5)
        ToolTip(self.btn_glossary, "查看供应链关键术语说明")
        
        # 按钮框架
        button_frame = ttk.Frame(self)
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # 添加按钮
        self.btn_add = ttk.Button(button_frame, text="添加", command=self._btn_add)
        self.btn_add.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_add, "添加新工序")

        # 编辑按钮
        self.btn_edit = ttk.Button(button_frame, text="编辑", command=self._btn_edit)
        self.btn_edit.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_edit, "编辑选中工序")

        # 删除按钮
        self.btn_delete = ttk.Button(button_frame, text="删除", command=self._btn_delete)
        self.btn_delete.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_delete, "删除选中工序")
        
        # 工序列表（使用Treeview显示）
        list_frame = ttk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 创建Treeview（表格视图）
        columns = ('name', 'time', 'workers', 'capacity')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=15)
        
        # 设置列标题
        self.tree.heading('name', text='工序名称')
        self.tree.heading('time', text='耗时(秒)')
        self.tree.heading('workers', text='人数')
        self.tree.heading('capacity', text='产能(颗/h)')
        
        # 设置列宽
        self.tree.column('name', width=100)
        self.tree.column('time', width=80)
        self.tree.column('workers', width=60)
        self.tree.column('capacity', width=100)
        
        # 绑定选择事件
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        
        # 添加滚动条
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        # 布局
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    def update_stations(self, stations: List[Station], production_line: Optional[Any] = None) -> None:
        """
        更新工序列表显示
        
        Args:
            stations: 工序列表
            production_line: 产线对象（可选，用于查找Station对象）
        """
        # 保存产线引用
        if production_line is not None:
            self.production_line = production_line
            self.set_production_type(production_line.production_type)
        
        # 清空现有数据
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # 重置选中状态
        self.selected_station = None
        
        # 添加新数据
        for station in stations:
            capacity = station.get_capacity()
            self.tree.insert(
                '',
                tk.END,
                values=(
                    station.name,
                    f"{station.process_time:.1f}",
                    station.worker_count,
                    f"{capacity:.0f}"
                ),
                tags=(station.id,)  # 使用ID作为tag，便于查找
            )
    
    def get_selected_station(self) -> Optional[Station]:
        """
        获取选中的工序
        
        Returns:
            Optional[Station]: 选中的工序对象，如果没有选中返回None
        """
        return self.selected_station

    def select_station(self, station_id: str) -> None:
        """按 ID 选中工序列表中的一行（供画布单击联动）"""
        for item in self.tree.get_children():
            tags = self.tree.item(item, 'tags')
            if tags and tags[0] == station_id:
                self.tree.selection_set(item)
                self.tree.focus(item)
                self.tree.see(item)
                break
    
    def _btn_add(self) -> None:
        """添加按钮点击"""
        if self.on_add:
            self.on_add()
    
    def _btn_edit(self) -> None:
        """编辑按钮点击"""
        if self.on_edit:
            self.on_edit()
    
    def _btn_delete(self) -> None:
        """删除按钮点击"""
        if self.on_delete:
            self.on_delete()

    def set_editable(self, enabled: bool) -> None:
        """切换工序编辑能力（仿真运行中禁用）"""
        state = tk.NORMAL if enabled else tk.DISABLED
        for btn in (self.btn_add, self.btn_edit, self.btn_delete):
            btn.config(state=state)

    def _open_glossary(self) -> None:
        """打开术语说明对话框"""
        GlossaryDialog(self.winfo_toplevel())

    def set_production_type(self, production_type: ProductionType) -> None:
        """更新生产类型标签"""
        label_map = {
            ProductionType.ASSEMBLY: "类型：烟弹组装",
            ProductionType.LIQUID_FILLING: "类型：烟油灌装",
            ProductionType.POUCH_PACKAGING: "类型：尼古丁袋包装",
        }
        if hasattr(self, 'production_type_label'):
            self.production_type_label.config(
                text=label_map.get(production_type, "类型：烟弹组装")
            )

    def set_unit(self, unit: str) -> None:
        """按生产类型更新工序列表产能单位"""
        if hasattr(self, 'tree'):
            self.tree.heading('capacity', text=f'产能({unit}/h)')
    
    def _on_select(self, event: tk.Event) -> None:
        """工序选择事件"""
        selection = self.tree.selection()
        if not selection:
            self.selected_station = None
            return
        
        # 获取选中的行
        item = selection[0]
        tags = self.tree.item(item, 'tags')
        if tags and len(tags) > 0:
            station_id = tags[0]
            
            # 从产线对象中查找Station对象
            if self.production_line:
                station = self.production_line.get_station(station_id)
                if station:
                    self.selected_station = station
                    
                    # 触发选择回调
                    if self.on_select:
                        self.on_select(station)
                else:
                    self.selected_station = None
            else:
                self.selected_station = None


class KPIDashboard(ttk.LabelFrame):
    """
    KPI仪表盘 - 显示关键绩效指标
    
    显示的KPI：
    - 瓶颈产能：产线最大产能
    - 日产量：预计日产量
    - 总成本：日人力成本
    - 单颗成本：单颗物料成本
    - 产线平衡率：产线平衡程度
    
    使用方式：
        dashboard = KPIDashboard(parent)
        dashboard.update_kpis({'bottleneck_capacity': 850, ...})
    """
    
    def __init__(self, parent):
        """初始化KPI仪表盘"""
        super().__init__(parent, text="KPI仪表盘", padding=10)
        
        # KPI标签
        self.kpi_labels: Dict[str, ttk.Label] = {}
        self.kpi_name_labels: Dict[str, tk.Label] = {}
        self.kpi_unit_labels: Dict[str, tk.Label] = {}
        self.kpi_cells: Dict[str, tk.Frame] = {}
        
        # 创建界面
        self._create_widgets()
    
    def _create_widgets(self) -> None:
        """创建界面组件"""
        # 时间戳显示（第一行，单独显示）
        timestamp_frame = ttk.Frame(self)
        timestamp_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(timestamp_frame, text="仿真时间:", font=('Arial', 10)).pack(side=tk.LEFT, padx=5)
        self.timestamp_label = ttk.Label(
            timestamp_frame,
            text="0.0 分钟",
            font=('Arial', 12, 'bold'),
            foreground='green'
        )
        self.timestamp_label.pack(side=tk.LEFT, padx=5)

        # 运行状态徽标
        self.run_state_label = ttk.Label(
            timestamp_frame,
            text="● 已停止",
            font=('Arial', 10, 'bold'),
            foreground='gray',
        )
        self.run_state_label.pack(side=tk.RIGHT, padx=5)
        
        # 分隔线
        separator = ttk.Separator(self, orient=tk.HORIZONTAL)
        separator.pack(fill=tk.X, pady=10)
        
        # 使用Grid布局，6列显示6个KPI（卡片化）
        kpis = [
            ('bottleneck_capacity', '瓶颈产能', '颗/h'),
            ('daily_output', '预计日产量', '颗'),
            ('total_cost', '日成本', '元'),
            ('unit_cost', '单颗成本', '元/颗'),
            ('balance_rate', '产线平衡率', '%'),
            ('upph', 'UPPH', '颗/人·h')
        ]

        kpi_frame = ttk.Frame(self)
        kpi_frame.pack(fill=tk.BOTH, expand=True)

        family = resolve_font_family()
        for i, (key, label, unit) in enumerate(kpis):
            # 卡片容器
            cell = tk.Frame(
                kpi_frame,
                bg=COLORS['surface'],
                highlightbackground=COLORS['border'],
                highlightthickness=1,
                padx=10,
                pady=8,
            )
            cell.grid(row=0, column=i, sticky='nsew', padx=6, pady=4)
            kpi_frame.grid_columnconfigure(i, weight=1)
            self.kpi_cells[key] = cell

            # KPI 名称
            name_label = tk.Label(
                cell,
                text=label,
                bg=COLORS['surface'],
                fg=COLORS['text_secondary'],
                font=(family, 9),
            )
            name_label.pack()
            self.kpi_name_labels[key] = name_label

            # KPI 数值
            value_label = tk.Label(
                cell,
                text="--",
                bg=COLORS['surface'],
                fg=COLORS['text'],
                font=(family, 15, 'bold'),
            )
            value_label.pack()
            if key == 'total_cost':
                ToolTip(value_label, "日成本 = 总人数 × 时薪 × 班次时长（元/天）")

            # 单位
            unit_label = tk.Label(
                cell,
                text=unit,
                bg=COLORS['surface'],
                fg=COLORS['text_secondary'],
                font=(family, 8),
            )
            unit_label.pack()
            self.kpi_unit_labels[key] = unit_label

            self.kpi_labels[key] = value_label

        # V1.3 扩展 KPI 摘要行
        self.v13_label = ttk.Label(
            self,
            text="",
            font=(family, 9),
            foreground=COLORS['text_secondary'],
        )
        self.v13_label.pack(fill=tk.X, pady=(4, 0))
    
    def update_timestamp(self, minutes: float) -> None:
        """
        更新时间戳显示
        
        Args:
            minutes: 仿真时间（分钟）
        """
        if hasattr(self, 'timestamp_label'):
            self.timestamp_label.config(text=f"{minutes:.1f} 分钟")
    
    def reset_timestamp(self) -> None:
        """重置时间戳为0"""
        if hasattr(self, 'timestamp_label'):
            self.timestamp_label.config(text="0.0 分钟")

    def set_run_state(self, state: str) -> None:
        """
        更新运行状态徽标

        Args:
            state: running / paused / stopped
        """
        if not hasattr(self, 'run_state_label'):
            return
        state_map = {
            'running': ('● 运行中', '#34C759'),
            'paused': ('⏸ 已暂停', '#FF9F0A'),
            'stopped': ('■ 已停止', '#6B7280'),
        }
        text, color = state_map.get(state, state_map['stopped'])
        self.run_state_label.config(text=text, foreground=color)
    
    def update_kpis(self, kpis: Dict[str, float]) -> None:
        """
        更新KPI显示
        
        Args:
            kpis: KPI字典，包含所有KPI值
        """
        # 更新瓶颈产能
        if 'bottleneck_capacity' in kpis:
            value = kpis['bottleneck_capacity']
            self.kpi_labels['bottleneck_capacity'].config(text=f"{value:.0f}")
        
        # 更新日产量
        if 'daily_output' in kpis:
            value = kpis['daily_output']
            self.kpi_labels['daily_output'].config(text=f"{value:.0f}")
        
        # 更新总成本
        if 'total_cost' in kpis:
            value = kpis['total_cost']
            self.kpi_labels['total_cost'].config(text=f"{value:.0f}")
        
        # 更新单颗成本
        if 'unit_cost' in kpis:
            value = kpis['unit_cost']
            self.kpi_labels['unit_cost'].config(text=f"{value:.2f}")
        
        # 更新平衡率
        if 'balance_rate' in kpis:
            value = kpis['balance_rate']
            self.kpi_labels['balance_rate'].config(text=f"{value*100:.1f}")
        
        # 更新UPPH
        if 'upph' in kpis:
            value = kpis['upph']
            self.kpi_labels['upph'].config(text=f"{value:.1f}")

    def update_v13_kpis(self, kpis: Dict[str, float]) -> None:
        """更新 V1.3 扩展 KPI 摘要（批次/收率/机台 OEE/单位成本）"""
        if not hasattr(self, 'v13_label'):
            return
        parts = []
        for key, label, fmt in [
            ('batch_cycle_min', '批次周期', '{:.1f} min'),
            ('batch_pass_rate', '批次合格率', '{:.1%}'),
            ('yield_rate', '收率', '{:.1%}'),
            ('machine_oee', '机台OEE', '{:.1%}'),
            ('cost_per_liter', '元/千克', '{:.2f}'),
            ('cost_per_pouch', '元/袋', '{:.2f}'),
        ]:
            if key in kpis and kpis[key]:
                parts.append(f"{label}: {fmt.format(kpis[key])}")
        self.v13_label.config(text="  |  ".join(parts))

    def set_unit(self, unit: str) -> None:
        """按生产类型更新 KPI 计量单位（颗/千克/袋）"""
        if not hasattr(self, 'kpi_name_labels'):
            return
        label_map = {
            'bottleneck_capacity': ('瓶颈产能', f'{unit}/h'),
            'daily_output': ('预计日产量', unit),
            'total_cost': ('日成本', '元'),
            'unit_cost': ('单位成本', f'元/{unit}'),
            'balance_rate': ('产线平衡率', '%'),
            'upph': ('UPPH', f'{unit}/人·h'),
        }
        for key, (name, unit_text) in label_map.items():
            if key in self.kpi_name_labels:
                self.kpi_name_labels[key].config(text=name)
            if key in self.kpi_unit_labels:
                self.kpi_unit_labels[key].config(text=unit_text)

    def recolor(self, palette: dict) -> None:
        """按主题色板重绘 KPI 卡片（V3.0）"""
        for key, cell in self.kpi_cells.items():
            cell.config(bg=palette['surface'], highlightbackground=palette['border'])
            if key in self.kpi_name_labels:
                self.kpi_name_labels[key].config(bg=palette['surface'], fg=palette['text_secondary'])
            if key in self.kpi_labels:
                self.kpi_labels[key].config(bg=palette['surface'], fg=palette['text'])
            if key in self.kpi_unit_labels:
                self.kpi_unit_labels[key].config(bg=palette['surface'], fg=palette['text_secondary'])
        if hasattr(self, 'v13_label'):
            self.v13_label.config(foreground=palette['text_secondary'])


class AlertPanel(ttk.LabelFrame):
    """
    报警面板 - 显示各种报警信息
    
    报警类型：
    - 瓶颈警报：产线瓶颈工序
    - 浪费警报：产能过剩的工序
    - 堵塞警报：WIP堆积过多
    
    使用方式：
        panel = AlertPanel(parent)
        panel.add_alert(alert)
    """
    
    def __init__(self, parent):
        """初始化报警面板"""
        super().__init__(parent, text="报警信息", padding=8)
        self._alerts: List[Alert] = []
        self._collapsed = False

        # 头部：筛选 / 清空 / 折叠 / 复制
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        self.filter_var = tk.StringVar(value="全部")
        self.filter_combo = ttk.Combobox(
            header,
            textvariable=self.filter_var,
            values=["全部", "严重", "警告", "信息"],
            width=4,
            state="readonly",
        )
        self.filter_combo.pack(side=tk.LEFT, padx=2)
        self.filter_combo.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        self.btn_clear = ttk.Button(header, text="清空", width=5, command=self._clear)
        self.btn_clear.pack(side=tk.LEFT, padx=2)
        self.btn_collapse = ttk.Button(
            header, text="折叠", width=5, command=self._toggle_collapse
        )
        self.btn_collapse.pack(side=tk.LEFT, padx=2)
        self.btn_copy_alerts = ttk.Button(
            header,
            text="📋 复制",
            width=7,
            command=self._copy_alerts,
        )
        self.btn_copy_alerts.pack(side=tk.RIGHT)

        # 报警列表（使用Text组件显示）
        self.text_widget = tk.Text(self, height=10, width=28, wrap=tk.WORD)
        self.text_widget.pack(fill=tk.BOTH, expand=True)

        # 配置文本样式
        self.text_widget.config(state=tk.DISABLED)  # 只读
    
    def add_alert(self, alert: Alert) -> None:
        """
        添加报警信息
        
        Args:
            alert: 报警对象
        """
        self._alerts.append(alert)
        self._render()

    def _render(self) -> None:
        """按筛选条件渲染报警列表（V3.0）"""
        # 启用编辑
        self.text_widget.config(state=tk.NORMAL)
        self.text_widget.delete("1.0", tk.END)

        severity_map = {"全部": "all", "严重": "critical", "警告": "warning", "信息": "info"}
        target = severity_map.get(self.filter_var.get(), "all")

        for alert in self._alerts:
            if target != "all" and alert.severity != target:
                continue
            color = get_alert_color(alert.severity)
            time_str = f"{alert.timestamp_minutes:.1f}分钟"
            icon = ALERT_ICONS.get(alert.severity, '•')
            text = f"[{time_str}] {icon} [{alert.severity.upper()}] {alert.message}\n"
            if alert.suggestion:
                text += f"  建议：{alert.suggestion}\n"
            text += "\n"

            start_pos = self.text_widget.index(tk.END)
            self.text_widget.insert(tk.END, text)
            end_pos = self.text_widget.index(tk.END)
            self.text_widget.tag_add(alert.severity, start_pos, end_pos)
            self.text_widget.tag_config(alert.severity, foreground=color)

        self.text_widget.see(tk.END)
        # 禁用编辑
        self.text_widget.config(state=tk.DISABLED)

    def clear(self) -> None:
        """清空报警信息"""
        self._alerts = []
        self._render()

    def _apply_filter(self) -> None:
        """应用报警级别筛选"""
        self._render()

    def _clear(self) -> None:
        """清空按钮"""
        self._alerts = []
        self._render()

    def _toggle_collapse(self) -> None:
        """折叠/展开报警列表"""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.text_widget.pack_forget()
            self.btn_collapse.config(text="展开")
        else:
            self.text_widget.pack(fill=tk.BOTH, expand=True)
            self.btn_collapse.config(text="折叠")

    def _copy_alerts(self) -> None:
        """一键复制全部报警文本到剪贴板"""
        content = self.text_widget.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("提示", "当前没有报警信息")
            return
        top = self.winfo_toplevel()
        top.clipboard_clear()
        top.clipboard_append(content)
        self.btn_copy_alerts.config(text="✅ 已复制")
        self.after(1200, lambda: self.btn_copy_alerts.config(text="📋 复制报警"))

    def recolor(self) -> None:
        """按当前主题刷新报警级别颜色（V3.0）"""
        for severity in ('critical', 'warning', 'info'):
            try:
                self.text_widget.tag_config(severity, foreground=get_alert_color(severity))
            except Exception:
                pass


class StationDialog:
    """
    工序编辑对话框 - 用于添加或编辑工序
    
    这是一个模态对话框，用户输入工序参数后点击确定
    
    使用方式：
        dialog = StationDialog(parent, title="添加工序")
        if dialog.result:
            station = dialog.result  # 获取创建的工序对象
    """
    
    def __init__(self, parent, title: str = "编辑工序", station: Optional[Station] = None, production_line: Optional[Any] = None):
        """
        初始化对话框
        
        Args:
            parent: 父窗口
            title: 对话框标题
            station: 要编辑的工序对象（如果为None则是添加模式）
            production_line: 产线对象（用于生成新ID）
        """
        self.result: Optional[Station] = None  # 对话框结果
        self.production_line = production_line  # 保存产线引用
        self.station = station  # 保存原始station引用（如果是编辑模式）
        self.line_type = (
            production_line.production_type
            if production_line is not None
            else ProductionType.ASSEMBLY
        )
        
        # 创建对话框窗口
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("460x500")
        self.dialog.transient(parent)  # 设置为父窗口的子窗口
        self.dialog.grab_set()  # 模态对话框
        
        # 创建输入字段
        self._create_widgets(station)
        # V3.0 键盘可达：Esc 取消，Enter 确定
        self.dialog.bind('<Escape>', lambda e: self._btn_cancel())
        self.dialog.bind('<Return>', lambda e: self._btn_ok())
        
        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        
        # 等待对话框关闭
        self.dialog.wait_window()
    
    def _create_widgets(self, station: Optional[Station]) -> None:
        """创建界面组件"""
        # 主框架
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 工序名称
        ttk.Label(main_frame, text="工序名称:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.name_var = tk.StringVar(value=station.name if station else "")
        name_entry = ttk.Entry(main_frame, textvariable=self.name_var, width=30)
        name_entry.grid(row=0, column=1, pady=5)
        
        # 单颗耗时
        ttk.Label(main_frame, text="单件耗时(秒):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.time_var = tk.StringVar(value=str(station.process_time) if station else "30")
        time_entry = ttk.Entry(main_frame, textvariable=self.time_var, width=30)
        time_entry.grid(row=1, column=1, pady=5)
        
        # 工人数量
        ttk.Label(main_frame, text="工人数量:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.workers_var = tk.StringVar(value=str(station.worker_count) if station else "1")
        workers_entry = ttk.Entry(main_frame, textvariable=self.workers_var, width=30)
        workers_entry.grid(row=2, column=1, pady=5)
        
        # 协作模式
        ttk.Label(main_frame, text="协作模式:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.mode_var = tk.StringVar(value=station.collaboration_type.value if station else "parallel")
        mode_frame = ttk.Frame(main_frame)
        mode_frame.grid(row=3, column=1, pady=5, sticky=tk.W)
        ttk.Radiobutton(mode_frame, text="并联", variable=self.mode_var, value="parallel").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(mode_frame, text="协同", variable=self.mode_var, value="collaborative").pack(side=tk.LEFT, padx=5)
        
        # OEE
        ttk.Label(main_frame, text="OEE (0-1):").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.oee_var = tk.StringVar(value=str(station.oee) if station else "0.85")
        oee_entry = ttk.Entry(main_frame, textvariable=self.oee_var, width=30)
        oee_entry.grid(row=4, column=1, pady=5)

        # V3.3.1：工种（按生产类型显示）
        if self.line_type == ProductionType.LIQUID_FILLING:
            role_values = [
                "mixer", "filling_operator", "qc_technician", "cleaner", "general",
            ]
        elif self.line_type == ProductionType.POUCH_PACKAGING:
            role_values = [
                "packaging_operator", "qc_technician", "mixer", "cleaner", "general",
            ]
        else:
            role_values = ["general"]
        self._role_label_to_value = {
            JOB_ROLE_LABELS[v]: v for v in role_values
        }
        current_role = (
            station.job_role.value if station and station.job_role else "general"
        )
        if current_role not in role_values:
            current_role = "general"
        self.job_role_var = tk.StringVar(value=JOB_ROLE_LABELS[current_role])
        self.job_role_label = ttk.Label(main_frame, text="工种:")
        self.job_role_label.grid(row=5, column=0, sticky=tk.W, pady=5)
        self.job_role_combo = ttk.Combobox(
            main_frame,
            textvariable=self.job_role_var,
            values=[JOB_ROLE_LABELS[v] for v in role_values],
            state="readonly",
            width=28,
        )
        self.job_role_combo.grid(row=5, column=1, pady=5)

        # V1.3：生产类型相关字段
        self.machine_takt_var = tk.StringVar(
            value=str(station.machine_takt) if station and station.machine_takt else ""
        )
        self.clean_time_var = tk.StringVar(
            value=str(station.clean_time_minutes) if station else "0"
        )
        self.sampling_var = tk.StringVar(
            value=str(station.sampling_rate) if station else "0"
        )
        self.defect_var = tk.StringVar(
            value=str(station.defect_rate) if station else "0"
        )
        self.rework_var = tk.StringVar(
            value=str(station.rework_minutes) if station else "0"
        )

        self.machine_takt_label = ttk.Label(main_frame, text="机台节拍(秒):")
        self.machine_takt_label.grid(row=6, column=0, sticky=tk.W, pady=5)
        self.machine_takt_entry = ttk.Entry(
            main_frame, textvariable=self.machine_takt_var, width=30
        )
        self.machine_takt_entry.grid(row=6, column=1, pady=5)

        self.clean_time_label = ttk.Label(main_frame, text="清洗时间(分钟):")
        self.clean_time_label.grid(row=7, column=0, sticky=tk.W, pady=5)
        self.clean_time_entry = ttk.Entry(
            main_frame, textvariable=self.clean_time_var, width=30
        )
        self.clean_time_entry.grid(row=7, column=1, pady=5)

        self.sampling_label = ttk.Label(main_frame, text="抽检比例(0-1):")
        self.sampling_label.grid(row=8, column=0, sticky=tk.W, pady=5)
        self.sampling_entry = ttk.Entry(
            main_frame, textvariable=self.sampling_var, width=30
        )
        self.sampling_entry.grid(row=8, column=1, pady=5)

        self.defect_label = ttk.Label(main_frame, text="缺陷率(0-1):")
        self.defect_label.grid(row=9, column=0, sticky=tk.W, pady=5)
        self.defect_entry = ttk.Entry(
            main_frame, textvariable=self.defect_var, width=30
        )
        self.defect_entry.grid(row=9, column=1, pady=5)

        self.rework_label = ttk.Label(main_frame, text="返工时长(分钟):")
        self.rework_label.grid(row=10, column=0, sticky=tk.W, pady=5)
        self.rework_entry = ttk.Entry(
            main_frame, textvariable=self.rework_var, width=30
        )
        self.rework_entry.grid(row=10, column=1, pady=5)

        self._apply_type_fields()
        
        # 按钮框架
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=11, column=0, columnspan=2, pady=20)
        
        # 确定按钮
        btn_ok = ttk.Button(button_frame, text="确定", command=self._btn_ok, style="Primary.TButton")
        btn_ok.pack(side=tk.LEFT, padx=10)
        
        # 取消按钮
        btn_cancel = ttk.Button(button_frame, text="取消", command=self._btn_cancel)
        btn_cancel.pack(side=tk.LEFT, padx=10)
        
        # 聚焦到名称输入框
        name_entry.focus()

    def _apply_type_fields(self) -> None:
        """
        按生产类型显示/隐藏 V1.3 字段（标签与输入框成对隐藏）

        映射见 station_edit_fields()。
        """
        visible = station_edit_fields(self.line_type)
        fields = {
            'job_role': (self.job_role_label, self.job_role_combo),
            'machine_takt': (self.machine_takt_label, self.machine_takt_entry),
            'clean_time_minutes': (self.clean_time_label, self.clean_time_entry),
            'sampling_rate': (self.sampling_label, self.sampling_entry),
            'defect_rate': (self.defect_label, self.defect_entry),
            'rework_minutes': (self.rework_label, self.rework_entry),
        }
        for key, (label, entry) in fields.items():
            if key in visible:
                label.grid()
                entry.grid()
                entry.config(state=tk.NORMAL)
            else:
                label.grid_remove()
                entry.grid_remove()
    
    def _btn_ok(self) -> None:
        """确定按钮点击"""
        try:
            # 获取输入值
            name = self.name_var.get().strip()
            process_time = float(self.time_var.get())
            worker_count = int(self.workers_var.get())
            oee = float(self.oee_var.get())
            mode = CollaborationType(self.mode_var.get())
            job_role = JobRole(
                self._role_label_to_value.get(
                    self.job_role_var.get(), "general"
                )
            )

            # V1.3 可选字段
            machine_takt_text = self.machine_takt_var.get().strip()
            machine_takt = float(machine_takt_text) if machine_takt_text else None
            clean_time = float(self.clean_time_var.get() or 0)
            sampling = float(self.sampling_var.get() or 0)
            defect = float(self.defect_var.get() or 0)
            rework = float(self.rework_var.get() or 0)

            if machine_takt is not None and machine_takt <= 0:
                messagebox.showerror("错误", "机台节拍必须大于0")
                return
            if not 0 <= sampling <= 1 or not 0 <= defect <= 1:
                messagebox.showerror("错误", "抽检比例与缺陷率必须在0-1之间")
                return
            
            # 校验参数
            valid, error = validate_station(name, process_time, worker_count)
            if not valid:
                messagebox.showerror("错误", error)
                return
            
            # 校验OEE
            if not 0 < oee <= 1:
                messagebox.showerror("错误", "OEE必须在0-1之间")
                return
            
            # 已经导入了Station类，直接使用即可
            
            # 生成工序ID（如果是添加模式）
            if self.station is None:  # 添加模式
                if self.production_line and self.production_line.stations:
                    # 基于现有工序数量生成ID
                    max_id = max([int(s.id.replace('s', '')) for s in self.production_line.stations if s.id.startswith('s') and s.id[1:].isdigit()], default=0)
                    station_id = f"s{max_id + 1:02d}"
                else:
                    station_id = "s01"
            else:  # 编辑模式，使用原有ID
                station_id = self.station.id
            
            self.result = Station(
                id=station_id,
                name=name,
                process_time=process_time,
                worker_count=worker_count,
                collaboration_type=mode,
                job_role=job_role,
                oee=oee,
                machine_takt=machine_takt,
                clean_time_minutes=clean_time,
                sampling_rate=sampling,
                defect_rate=defect,
                rework_minutes=rework,
            )
            
            # 关闭对话框
            self.dialog.destroy()
            
        except ValueError as e:
            messagebox.showerror("错误", f"输入格式错误：{e}")
        except Exception as e:
            messagebox.showerror("错误", f"创建工序失败：{e}")
    
    def _btn_cancel(self) -> None:
        """取消按钮点击"""
        self.dialog.destroy()


class ShiftConfigDialog:
    """
    班次配置对话框 - 用于配置班次时间和工时成本
    
    使用方式：
        dialog = ShiftConfigDialog(parent, shift_hours=8, break_minutes=60, worker_hourly_wage=20.0)
        if dialog.result:
            config = dialog.result  # 获取配置字典
    """
    
    def __init__(
        self,
        parent,
        shift_hours: int = 8,
        break_minutes: int = 60,
        worker_hourly_wage: float = 20.0
    ):
        """
        初始化对话框
        
        Args:
            parent: 父窗口
            shift_hours: 班次时长（小时）
            break_minutes: 休息时间（分钟）
            worker_hourly_wage: 工人时薪（元/小时）
        """
        self.result: Optional[Dict[str, Any]] = None  # 对话框结果
        
        # 创建对话框窗口（更小巧）
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("班次配置")
        self.dialog.geometry("360x230")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # 创建输入字段
        self._create_widgets(shift_hours, break_minutes, worker_hourly_wage)
        # V3.0 键盘可达：Esc 取消，Enter 确定
        self.dialog.bind('<Escape>', lambda e: self._btn_cancel())
        self.dialog.bind('<Return>', lambda e: self._btn_ok())
        
        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        
        # 等待对话框关闭
        self.dialog.wait_window()
    
    def _create_widgets(self, shift_hours: int, break_minutes: int, worker_hourly_wage: float) -> None:
        """创建界面组件"""
        # 主框架（减小padding）
        main_frame = ttk.Frame(self.dialog, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 班次时长（缩短输入框，width改为10）
        ttk.Label(main_frame, text="班次时长(小时):").grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        self.shift_hours_var = tk.StringVar(value=str(shift_hours))
        shift_entry = ttk.Entry(main_frame, textvariable=self.shift_hours_var, width=10)
        shift_entry.grid(row=0, column=1, pady=5, padx=5, sticky=tk.W)
        
        # 休息时间
        ttk.Label(main_frame, text="休息时间(分钟):").grid(row=1, column=0, sticky=tk.W, pady=5, padx=5)
        self.break_minutes_var = tk.StringVar(value=str(break_minutes))
        break_entry = ttk.Entry(main_frame, textvariable=self.break_minutes_var, width=10)
        break_entry.grid(row=1, column=1, pady=5, padx=5, sticky=tk.W)
        
        # 工人时薪
        ttk.Label(main_frame, text="工人时薪(元/小时):").grid(row=2, column=0, sticky=tk.W, pady=5, padx=5)
        self.wage_var = tk.StringVar(value=str(worker_hourly_wage))
        wage_entry = ttk.Entry(main_frame, textvariable=self.wage_var, width=10)
        wage_entry.grid(row=2, column=1, pady=5, padx=5, sticky=tk.W)
        
        # 按钮框架（减小pady）
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=15)
        
        # 确定按钮
        btn_ok = ttk.Button(button_frame, text="确定", command=self._btn_ok, style="Primary.TButton")
        btn_ok.pack(side=tk.LEFT, padx=5)
        
        # 取消按钮
        btn_cancel = ttk.Button(button_frame, text="取消", command=self._btn_cancel)
        btn_cancel.pack(side=tk.LEFT, padx=5)
        
        # 聚焦到第一个输入框
        shift_entry.focus()

        # 日成本计算说明
        family = resolve_font_family()
        ttk.Label(
            main_frame,
            text="日成本 = 总人数 × 时薪 × 班次时长（不含休息扣减）",
            foreground=COLORS['text_secondary'],
            font=(family, 9),
        ).grid(row=4, column=0, columnspan=2, pady=(0, 5))
    
    def _btn_ok(self) -> None:
        """确定按钮点击"""
        try:
            # 获取输入值
            shift_hours = int(self.shift_hours_var.get())
            break_minutes = int(self.break_minutes_var.get())
            worker_hourly_wage = float(self.wage_var.get())
            
            # 校验参数
            if shift_hours <= 0 or shift_hours > 24:
                messagebox.showerror("错误", "班次时长必须在1-24小时之间")
                return
            
            if break_minutes < 0 or break_minutes >= shift_hours * 60:
                messagebox.showerror("错误", "休息时间不能超过班次时长")
                return
            
            if worker_hourly_wage <= 0:
                messagebox.showerror("错误", "工人时薪必须大于0")
                return
            
            # 保存结果
            self.result = {
                'shift_hours': shift_hours,
                'break_minutes': break_minutes,
                'worker_hourly_wage': worker_hourly_wage
            }
            
            # 关闭对话框
            self.dialog.destroy()
            
        except ValueError as e:
            messagebox.showerror("错误", f"输入格式错误：{e}")
        except Exception as e:
            messagebox.showerror("错误", f"配置失败：{e}")

    def _btn_cancel(self) -> None:
        """取消按钮点击：不保存，直接关闭对话框"""
        self.result = None
        self.dialog.destroy()


class SaveScenarioDialog:
    """
    保存方案对话框 - 用于保存当前产线配置为方案
    
    功能：
    - 输入方案名称（必填）
    - 输入方案描述（可选）
    - 验证名称是否重复
    - 检查方案数量限制（最多3个）
    
    使用方式：
        dialog = SaveScenarioDialog(parent, scenario_manager, production_line)
        if dialog.result:
            # 方案已保存
    """
    
    def __init__(self, parent, scenario_manager: ScenarioManager, production_line: ProductionLine):
        """
        初始化保存方案对话框
        
        Args:
            parent: 父窗口
            scenario_manager: 方案管理器
            production_line: 要保存的产线对象
        """
        self.scenario_manager = scenario_manager
        self.production_line = production_line
        self.result = None  # 保存结果：{'name': str, 'description': str} 或 None
        
        # 创建对话框窗口
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("保存方案")
        self.dialog.resizable(False, False)
        
        # 设置对话框为模态（阻塞父窗口）
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # 创建界面（先创建界面，再计算位置）
        self._create_widgets()
        
        # 绑定回车键
        self.dialog.bind('<Return>', lambda e: self._on_confirm())
        # V3.0 键盘可达：Esc 取消
        self.dialog.bind('<Escape>', lambda e: self._on_cancel())
        
        # 居中显示（相对于父窗口，而不是屏幕）
        # 必须在创建完所有widgets后调用update_idletasks()
        self.dialog.update_idletasks()
        
        # 获取父窗口位置和大小
        parent.update_idletasks()
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        
        # 获取对话框大小
        dialog_width = self.dialog.winfo_width()
        dialog_height = self.dialog.winfo_height()
        
        # 计算居中位置（相对于父窗口）
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        
        # 确保对话框不会超出屏幕
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        x = max(0, min(x, screen_width - dialog_width))
        y = max(0, min(y, screen_height - dialog_height))
        
        # 设置对话框位置
        self.dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
        
        # 等待对话框关闭
        self.dialog.wait_window()
    
    def _create_widgets(self) -> None:
        """创建界面组件"""
        # 主框架
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 方案名称输入
        ttk.Label(main_frame, text="方案名称:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.name_entry = ttk.Entry(main_frame, width=30)
        self.name_entry.grid(row=0, column=1, pady=5, padx=10)
        self.name_entry.focus()
        
        # 方案描述输入
        ttk.Label(main_frame, text="方案描述:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.description_text = tk.Text(main_frame, width=30, height=4)
        self.description_text.grid(row=1, column=1, pady=5, padx=10)

        # 自定义保存路径（可选）
        ttk.Label(main_frame, text="保存位置(可选):").grid(row=2, column=0, sticky=tk.W, pady=5)
        path_frame = ttk.Frame(main_frame)
        path_frame.grid(row=2, column=1, pady=5, padx=10, sticky=tk.W)
        self.path_var = tk.StringVar(value="")
        self.path_entry = ttk.Entry(path_frame, textvariable=self.path_var, width=22)
        self.path_entry.pack(side=tk.LEFT)
        ttk.Button(path_frame, text="浏览...", command=self._browse_path).pack(side=tk.LEFT, padx=5)
        
        # 提示信息
        info_label = ttk.Label(
            main_frame,
            text=f"当前已保存 {self.scenario_manager.get_scenario_count()}/{self.scenario_manager.MAX_SCENARIOS} 个方案",
            font=('Arial', 9),
            foreground='gray'
        )
        info_label.grid(row=3, column=0, columnspan=2, pady=10)
        
        # 按钮框架
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=4, column=0, columnspan=2, pady=10)
        
        ttk.Button(
            button_frame, text="确定", command=self._on_confirm, style="Primary.TButton"
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=self._on_cancel).pack(side=tk.LEFT, padx=5)

    def _browse_path(self) -> None:
        """选择自定义保存路径（JSON）"""
        path = filedialog.asksaveasfilename(
            title="选择方案保存位置",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.path_var.set(path)
    
    def _on_confirm(self) -> None:
        """确认保存方案"""
        # 获取输入
        name = self.name_entry.get().strip()
        description = self.description_text.get("1.0", tk.END).strip()
        
        # 验证方案名称
        if not name:
            messagebox.showwarning("警告", "方案名称不能为空")
            return
        
        # 尝试保存方案
        success, error_msg = self.scenario_manager.save_scenario(
            name, self.production_line, description
        )
        
        if success:
            # 保存成功
            export_path = self.path_var.get().strip()
            if export_path:
                if not self.scenario_manager.export_scenario(name, export_path):
                    messagebox.showwarning("警告", "方案已保存到内部，但导出到自定义路径失败")
            self.result = {
                'name': name,
                'description': description,
                'path': export_path or None,
            }
            self.dialog.destroy()
        else:
            # 保存失败，显示错误信息
            messagebox.showerror("错误", error_msg or "保存方案失败")
    
    def _on_cancel(self) -> None:
        """取消保存"""
        self.result = None
        self.dialog.destroy()


class ScenarioCompareDialog:
    """
    方案对比对话框 - 显示多个方案的KPI对比
    
    功能：
    - 显示所有方案的KPI对比表格
    - 计算并显示差异（相对于第一个方案）
    - 推荐最佳方案（单颗成本最低）
    
    使用方式：
        dialog = ScenarioCompareDialog(parent, scenario_manager, scenario_names)
        # 对话框会自动显示对比结果
    """
    
    def __init__(self, parent, scenario_manager: ScenarioManager, scenario_names: List[str]):
        """
        初始化方案对比对话框
        
        Args:
            parent: 父窗口
            scenario_manager: 方案管理器
            scenario_names: 要对比的方案名称列表（至少2个）
        """
        self.scenario_manager = scenario_manager
        self.scenario_names = scenario_names
        
        # 创建对话框窗口
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("方案对比")
        self.dialog.geometry("900x600")
        self.dialog.resizable(True, True)
        
        # 设置对话框为模态
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # 获取对比数据
        try:
            self.comparison_data = scenario_manager.compare_scenarios(scenario_names)
        except Exception as e:
            messagebox.showerror("错误", f"对比方案失败：{str(e)}")
            self.dialog.destroy()
            return
        self.export_rows: List[List[str]] = []

        first = self.scenario_manager.get_scenario(scenario_names[0])
        self.unit = first.production_line.get_unit() if first else "颗"
        
        # 创建界面（先创建界面，再计算位置）
        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self.dialog.destroy())
        
        # 居中显示（相对于父窗口）
        self.dialog.update_idletasks()
        
        # 获取父窗口位置和大小
        parent.update_idletasks()
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        
        # 获取对话框大小
        dialog_width = self.dialog.winfo_width()
        dialog_height = self.dialog.winfo_height()
        
        # 计算居中位置（相对于父窗口）
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        
        # 确保对话框不会超出屏幕
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        x = max(0, min(x, screen_width - dialog_width))
        y = max(0, min(y, screen_height - dialog_height))
        
        # 设置对话框位置
        self.dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
    
    def _create_widgets(self) -> None:
        """创建界面组件"""
        # 主框架
        main_frame = ttk.Frame(self.dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 标题
        title_label = ttk.Label(
            main_frame,
            text="方案对比",
            font=('Arial', 14, 'bold')
        )
        title_label.pack(pady=10)
        
        # 创建表格框架
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # 创建Treeview表格
        # 列：指标 + 每个方案 + 差异列（每个方案相对于第一个方案的差异）
        columns = ['指标'] + self.scenario_names
        # 为每个非基准方案添加差异列
        for i, name in enumerate(self.scenario_names[1:], 1):
            columns.append(f'差异{i}')
        
        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=15)
        
        # 设置列宽
        self.tree.column('指标', width=120, anchor=tk.W)
        for name in self.scenario_names:
            self.tree.column(name, width=100, anchor=tk.CENTER)
        for i in range(1, len(self.scenario_names)):
            self.tree.column(f'差异{i}', width=120, anchor=tk.W)
        
        # 设置列标题
        self.tree.heading('指标', text='指标')
        for name in self.scenario_names:
            self.tree.heading(name, text=name)
        # 差异列标题
        for i, name in enumerate(self.scenario_names[1:], 1):
            self.tree.heading(f'差异{i}', text=f'{name} vs {self.scenario_names[0]}')
        
        # 添加滚动条
        scrollbar_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        # 布局
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar_y.grid(row=0, column=1, sticky='ns')
        scrollbar_x.grid(row=1, column=0, sticky='ew')
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        
        # 填充数据
        self._populate_table()
        
        # 推荐方案标签
        if self.comparison_data.get('recommendation'):
            recommendation_label = ttk.Label(
                main_frame,
                text=f"💡 推荐方案：{self.comparison_data['recommendation']}",
                font=('Arial', 10, 'bold'),
                foreground='green'
            )
            recommendation_label.pack(pady=10)
        
        # 关闭按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10)
        ttk.Button(button_frame, text="导出Excel", command=self._export_excel).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(button_frame, text="关闭", command=self.dialog.destroy).pack()
        HelpSection(
            main_frame,
            "使用说明",
            "目的：对比 2-5 个保存方案的 KPI，并给出推荐。\n"
            "推荐依据：默认按单位成本最低推荐。\n"
            "差异列 = 各方案相对第一个方案（基准）的差值。",
        ).pack(fill=tk.X, pady=(0, 8))
    
    def _populate_table(self) -> None:
        """填充对比表格数据"""
        # KPI指标配置（指标键名, 显示名称, 单位, 格式化函数）
        unit = self.unit
        kpi_configs = [
            ('total_workers', '总人数', '人', lambda x: f"{int(x)}人"),
            ('bottleneck_capacity', '瓶颈产能', f'{unit}/h', lambda x: f"{x:.0f}"),
            ('daily_output', '日产量', unit, lambda x: f"{x:.0f}"),
            ('total_cost', '日成本', '元', lambda x: f"{x:.0f}"),
            ('unit_cost', '单位成本', f'元/{unit}', lambda x: f"{x:.2f}"),
            ('balance_rate', '产线平衡率', '%', lambda x: f"{x*100:.1f}%"),
            ('upph', 'UPPH', f'{unit}/人·h', lambda x: f"{x:.1f}")
        ]

        self.tree.tag_configure('even', background=COLORS['surface'])
        self.tree.tag_configure('odd', background=COLORS['bg'])
        
        # 遍历每个KPI指标
        for row_index, (metric_key, metric_name, unit, format_func) in enumerate(kpi_configs):
            # 查找对应的差异数据
            diff_data = next(
                (d for d in self.comparison_data['differences'] if d['metric_key'] == metric_key),
                None
            )
            
            if diff_data:
                # 构建行数据
                row_values = [f"{metric_name} ({unit})"]
                
                # 添加每个方案的值
                for scenario_data in self.comparison_data['scenarios']:
                    value = scenario_data['kpis'][metric_key]
                    row_values.append(format_func(value))
                
                # 添加差异列（每个非基准方案相对于第一个方案的差异）
                for i, diff_value in enumerate(diff_data['values'][1:], 1):
                    diff_abs = diff_value['diff_absolute']
                    diff_pct = diff_value['diff_percent']
                    
                    # 格式化差异文本
                    if abs(diff_abs) < 0.01:
                        diff_text = "无变化"
                    else:
                        sign = "+" if diff_abs > 0 else ""
                        diff_text = f"{sign}{format_func(diff_abs)} ({sign}{diff_pct:.1f}%)"
                    
                    row_values.append(diff_text)
                
                # 插入行
                self.tree.insert(
                    '', tk.END,
                    values=row_values,
                    tags=('even' if row_index % 2 == 0 else 'odd'),
                )
                
                # 根据差异设置颜色（改善=绿色，恶化=红色）
                # 注意：Treeview的颜色设置需要特殊处理，这里先不设置
                self.export_rows.append(row_values)

    def _export_excel(self) -> None:
        """按对比表格呈现方式导出 Excel（V3.3）"""
        try:
            import pandas as pd

            path = filedialog.asksaveasfilename(
                title="导出方案对比",
                defaultextension=".xlsx",
                initialfile="方案对比.xlsx",
                filetypes=[("Excel 文件", "*.xlsx")],
            )
            if not path:
                return
            headers = ['指标'] + list(self.scenario_names)
            for i in range(1, len(self.scenario_names)):
                headers.append(f'差异{i}（{self.scenario_names[i]} vs {self.scenario_names[0]}）')
            pd.DataFrame(self.export_rows, columns=headers).to_excel(path, index=False)
            messagebox.showinfo("成功", f"已导出：{path}")
        except Exception as e:
            messagebox.showerror("导出失败", f"{e}\n\n详细日志：logs/app.log")


class ScenarioManageDialog:
    """
    方案管理对话框（V3.3 合并方案对比）

    使用方式：
        dialog = ScenarioManageDialog(parent, scenario_manager, on_import=callback)
    - 导入方案：把所选方案加载到主界面
    - 删除方案：删除所选方案
    - 方案对比：切换对比模式 → 勾选最多 3 个 → 确认对比
    """

    def __init__(
        self,
        parent,
        scenario_manager: ScenarioManager,
        on_import=None,
    ):
        self.scenario_manager = scenario_manager
        self.on_import = on_import
        self.compare_mode = False
        self.checked: Dict[str, bool] = {}

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("方案管理 / 对比")
        self.dialog.geometry("780x480")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._img_unchecked = self._make_check_image(False)
        self._img_checked = self._make_check_image(True)

        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self.dialog.destroy())
        self.dialog.bind('<Button-1>', self._on_tree_click)

        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")

    @staticmethod
    def _make_check_image(filled: bool) -> tk.PhotoImage:
        """生成 22x22 复选框图片（V3.3：增大点选与视觉面积）"""
        size = 22
        pixels = [["#FFFFFF"] * size for _ in range(size)]
        for i in range(size):
            pixels[0][i] = "#666666"
            pixels[size - 1][i] = "#666666"
            pixels[i][0] = "#666666"
            pixels[i][size - 1] = "#666666"
        if filled:
            for y in range(2, size - 2):
                for x in range(2, size - 2):
                    pixels[y][x] = "#1F6FEB"
            # 白色对勾
            for i in range(6):
                pixels[8 + i][5 + i] = "#FFFFFF"
            for i in range(8):
                pixels[8 + i][12 - i] = "#FFFFFF"
                pixels[7 + i][12 - i] = "#FFFFFF"
        data = tuple(" ".join(row) for row in pixels)
        image = tk.PhotoImage(width=size, height=size)
        image.put(data)
        return image

    def _create_widgets(self) -> None:
        """创建界面组件"""
        main_frame = ttk.Frame(self.dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        columns = ('name', 'created_at', 'description', 'unit_cost')
        self.tree = ttk.Treeview(
            main_frame, columns=columns, show='tree headings', height=14
        )
        self.tree.heading('#0', text='选择')
        self.tree.heading('name', text='方案名称')
        self.tree.heading('created_at', text='创建时间')
        self.tree.heading('description', text='描述')
        self.tree.heading('unit_cost', text='单位成本')
        self.tree.column('#0', width=0, stretch=False, anchor=tk.CENTER)
        self.tree.column('name', width=140)
        self.tree.column('created_at', width=140)
        self.tree.column('description', width=260)
        self.tree.column('unit_cost', width=110)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.selected_index: Optional[int] = None
        self.tree.bind(
            "<<TreeviewSelect>>",
            lambda e: setattr(
                self,
                "selected_index",
                self.tree.index(self.tree.selection()[0])
                if self.tree.selection()
                else None,
            ),
        )

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(button_frame, text="导入方案", command=self._import_selected).pack(side=tk.LEFT)
        ttk.Button(button_frame, text="删除选中", command=self._delete_selected).pack(side=tk.LEFT)
        ttk.Button(button_frame, text="方案对比", command=self._toggle_compare).pack(side=tk.LEFT)
        self.btn_confirm_compare = ttk.Button(
            button_frame, text="确认对比", command=self._confirm_compare
        )
        ttk.Button(button_frame, text="关闭", command=self.dialog.destroy).pack(side=tk.RIGHT)

        self.compare_hint_var = tk.StringVar(value="")
        ttk.Label(
            main_frame,
            textvariable=self.compare_hint_var,
            foreground=COLORS["text_secondary"],
        ).pack(anchor=tk.W, pady=(6, 0))

        self._refresh()

    def _refresh(self) -> None:
        """刷新方案列表"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.tree.tag_configure('even', background=COLORS['surface'])
        self.tree.tag_configure('odd', background=COLORS['bg'])
        for idx, name in enumerate(self.scenario_manager.list_scenarios()):
            scenario = self.scenario_manager.get_scenario(name)
            kpis = scenario.get_kpis()
            check_image = self._img_checked if self.checked.get(name) else self._img_unchecked
            self.tree.insert('', tk.END, values=(
                name,
                scenario.created_at,
                scenario.description,
                f"{kpis['unit_cost']:.3f}",
            ), image=check_image, text="", tags=(name, 'even' if idx % 2 == 0 else 'odd'))

    def _on_tree_click(self, event: tk.Event) -> None:
        """点击复选框列切换勾选状态（仅对比模式）"""
        if not self.compare_mode:
            return
        if self.tree.identify_region(event.x, event.y) not in ("cell", "tree"):
            return
        column = self.tree.identify_column(event.x)
        if column not in ("#0", "#1"):
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        tags = self.tree.item(row_id, "tags")
        if not tags:
            return
        name = tags[0]
        self._toggle_check(name)

    def _toggle_check(self, name: str) -> None:
        """勾选/取消勾选，最多 3 个"""
        if self.checked.get(name):
            self.checked[name] = False
        else:
            if sum(1 for v in self.checked.values() if v) >= 3:
                messagebox.showwarning("提示", "最多只能对比 3 个方案")
                return
            self.checked[name] = True
        self._refresh()
        selected = self._selected_names()
        self.btn_confirm_compare.config(
            state=tk.NORMAL if len(selected) >= 2 else tk.DISABLED
        )
        self.compare_hint_var.set(
            f"已勾选 {len(selected)}/3：{'、'.join(selected) or '（点击左侧复选框勾选 2-3 个方案）'}"
        )

    def _selected_names(self) -> List[str]:
        return [
            name for name in self.scenario_manager.list_scenarios()
            if self.checked.get(name)
        ]

    def _toggle_compare(self) -> None:
        """切换对比模式：显示复选框与确认按钮"""
        self.compare_mode = not self.compare_mode
        width = 56 if self.compare_mode else 0
        self.tree.column('#0', width=width, stretch=False, anchor=tk.CENTER)
        if self.compare_mode:
            self.btn_confirm_compare.pack(side=tk.LEFT, padx=(4, 0))
            self.btn_confirm_compare.config(state=tk.DISABLED)
            self.compare_hint_var.set("点击左侧复选框勾选 2-3 个方案后点击「确认对比」")
        else:
            self.btn_confirm_compare.pack_forget()
            self.checked = {k: False for k in self.checked}
            self.compare_hint_var.set("")
        self._refresh()

    def _confirm_compare(self) -> None:
        """进入方案对比界面（最多 3 个）"""
        names = self._selected_names()
        if len(names) < 2:
            messagebox.showwarning("警告", "请至少勾选 2 个方案")
            return
        if len(names) > 3:
            messagebox.showwarning("警告", "最多只能对比 3 个方案")
            return
        ScenarioCompareDialog(self.dialog, self.scenario_manager, names)

    def _import_selected(self) -> None:
        """导入所选方案到主界面"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择一个方案")
            return
        tags = self.tree.item(selection[0], 'tags')
        if not tags:
            return
        name = tags[0]
        if self.on_import:
            self.on_import(name)
            messagebox.showinfo("成功", f"已导入方案：{name}")
        else:
            messagebox.showinfo("提示", f"选中方案：{name}")

    def _delete_selected(self) -> None:
        """删除选中的方案"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择一个方案")
            return
        item = selection[0]
        tags = self.tree.item(item, 'tags')
        if not tags:
            return
        name = tags[0]
        if messagebox.askyesno("确认", f"确定删除方案 '{name}' 吗？"):
            ok, err = self.scenario_manager.delete_scenario(name)
            if ok:
                self._refresh()
            else:
                messagebox.showerror("错误", err or "删除失败")


class GlossaryDialog:
    """
    术语说明对话框 - 展示供应链关键术语与意义梗概

    使用方式：
        GlossaryDialog(parent)
    """

    def __init__(self, parent):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("术语说明")
        self.dialog.geometry("600x520")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        main_frame = ttk.Frame(self.dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            main_frame,
            text="供应链关键术语速查",
            font=('Arial', 13, 'bold'),
        ).pack(anchor=tk.W, pady=(0, 8))

        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(search_frame, text="搜索:").pack(side=tk.LEFT, padx=(0, 4))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side=tk.LEFT)
        search_entry.bind("<Return>", lambda e: search_entry.focus_set())
        self.search_var.trace_add("write", lambda *a: self._render())
        self.count_var = tk.StringVar(value=f"共 {len(GLOSSARY)} 条")
        ttk.Label(
            search_frame,
            textvariable=self.count_var,
            foreground=COLORS["text_secondary"],
        ).pack(side=tk.RIGHT)
        ttk.Label(
            main_frame,
            text="支持模糊搜索：输入关键词即可实时筛选（如 “罐容” 或 “WIP”）",
            foreground=COLORS["text_secondary"],
        ).pack(anchor=tk.W, pady=(0, 6))

        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ('term', 'meaning')
        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=18)
        self.tree.heading('term', text='术语')
        self.tree.heading('meaning', text='意义梗概')
        self.tree.column('term', width=120, anchor=tk.W)
        self.tree.column('meaning', width=440, anchor=tk.W)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._render()

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(button_frame, text="关闭", command=self.dialog.destroy).pack(side=tk.RIGHT)

        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - self.dialog.winfo_width()) // 2
        y = (self.dialog.winfo_screenheight() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _render(self) -> None:
        """按关键词模糊筛选术语（V3.3.3）"""
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip()
        count = 0
        for term, meaning in GLOSSARY:
            if fuzzy_contains(f"{term} {meaning}", query):
                self.tree.insert("", tk.END, values=(term, meaning))
                count += 1
        self.count_var.set(f"共 {count} / {len(GLOSSARY)} 条")


class AboutDialog:
    """
    关于对话框（V3.1）

    包含：版本/署名、隐私声明、免责声明、Bug 反馈与资助入口。
    """

    def __init__(self, parent):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("关于")
        self.dialog.geometry("600x660")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        family = resolve_font_family()
        main = ttk.Frame(self.dialog, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            main,
            text=f"{PRODUCT_NAME} v{__version__}",
            font=(family, 15, 'bold'),
        )
        title.pack(anchor=tk.W, pady=(0, 4))

        ttk.Label(main, text="开发者：Max Hou", font=(family, 11)).pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(
            main,
            text="基于 SimPy 的离散事件仿真引擎，用于产线设计与人力优化。",
            font=(family, 10),
            foreground=COLORS['text_secondary'],
        ).pack(anchor=tk.W, pady=(0, 10))

        text = tk.Text(main, height=10, wrap=tk.WORD, font=(family, 10))
        text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        text.insert('1.0', (
            "【隐私声明】\n"
            "本工具完全本地运行，不上传任何数据；产线配置、方案、UI 设置与日志均保存在您的设备上。\n\n"
            "【免责声明】\n"
            "仿真结果仅供产线设计与人力规划参考，不构成产能或成本承诺；实际投产前请以现场验证为准。"
            "作者不对因参考仿真结果做出的决策损失承担责任。本工具按“现状”提供。\n\n"
            "【Bug 反馈】\n"
            f"GitHub Issues：{BUG_REPORT_URL}\n\n"
            "【资助作者】\n"
            "扫描上方二维码即可支持作者一杯咖啡的钱，感谢你的资助！\n"
        ))
        text.config(state=tk.DISABLED)
        self.about_text = text

        # 内嵌微信赞赏码图片（如存在）
        self._qr_image = None
        display_path = (
            WECHAT_QR_SMALL_PATH
            if WECHAT_QR_SMALL_PATH and os.path.exists(WECHAT_QR_SMALL_PATH)
            else WECHAT_QR_PATH
        )
        if display_path and os.path.exists(display_path):
            try:
                self._qr_image = tk.PhotoImage(file=display_path)
                ttk.Label(main, image=self._qr_image).pack(pady=(0, 8))
            except Exception:
                self._qr_image = None

        button_frame = ttk.Frame(main)
        button_frame.pack(fill=tk.X)
        ttk.Button(button_frame, text="报告 Bug", command=self._open_bug).pack(side=tk.LEFT, padx=2)
        ttk.Button(button_frame, text="微信赞赏码", command=self._show_wechat).pack(side=tk.LEFT, padx=2)
        ttk.Button(button_frame, text="关闭", command=self.dialog.destroy).pack(side=tk.RIGHT, padx=2)

        self.dialog.bind('<Escape>', lambda e: self.dialog.destroy())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - self.dialog.winfo_width()) // 2
        y = (self.dialog.winfo_screenheight() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _open_bug(self) -> None:
        webbrowser.open(BUG_REPORT_URL)

    def _show_wechat(self) -> None:
        """打开微信赞赏码图片"""
        if not WECHAT_QR_PATH or not os.path.exists(WECHAT_QR_PATH):
            messagebox.showinfo(
                "微信赞赏码",
                "尚未配置赞赏码图片，请检查 assets/wechat_qr.png。",
            )
            return
        webbrowser.open("file://" + os.path.abspath(WECHAT_QR_PATH))


class HrPlanningDialog:
    """
    人力规划对话框（V3.2） - 面向 HRBP/HR

    输入目标产量、班次、成本、爬坡与招聘计划，输出岗位人数、
    成本、招聘缺口与达产天数，支持导出 Excel/PDF。
    """

    def __init__(self, parent, production_line: Optional[ProductionLine]):
        self.line = production_line
        self.summary: Optional[Dict[str, Any]] = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("人力规划（HRBP/HR）")
        self.dialog.geometry("720x760")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self._cancel())

        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        line = self.line
        station_roles = {s.job_role.value for s in (line.stations if line else [])}
        labor_roles = set(line.labor_config.keys()) if line else set()
        self._single_role = len(station_roles | labor_roles) <= 1
        daily_default = (
            str(int(line.calculate_daily_output()))
            if line and line.stations
            else "0"
        )
        shift_default = str(line.shift_hours) if line else "8"
        break_default = str(line.break_minutes) if line else "60"
        wage_default = str(line.worker_hourly_wage) if line else "20.0"

        main = ttk.Frame(self.dialog, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        def _row(row, col, label, var, width=12, padx=(0, 0), hint=None):
            label_widget = ttk.Label(main, text=label)
            label_widget.grid(
                row=row, column=col, sticky=tk.W, pady=4
            )
            if hint:
                ToolTip(label_widget, hint)
            ttk.Entry(main, textvariable=var, width=width).grid(
                row=row, column=col + 1, sticky=tk.W, pady=4, padx=padx
            )

        self.target_var = tk.StringVar(value=daily_default)
        self.shifts_var = tk.StringVar(value="1")
        self.shift_hours_var = tk.StringVar(value=shift_default)
        self.break_var = tk.StringVar(value=break_default)
        self.overtime_var = tk.StringVar(value="0")
        self.wage_var = tk.StringVar(value=wage_default)
        self.social_var = tk.StringVar(value="0.30")
        self.absence_var = tk.StringVar(value="0")
        self.turnover_var = tk.StringVar(value="0")
        self.ramp_var = tk.StringVar(value="90")

        _row(0, 0, "目标日产量:", self.target_var,
             hint="产线每天需要达到的产出数量（单位随生产类型：颗/千克/袋）")
        _row(0, 2, "班次数:", self.shifts_var, padx=(16, 0),
             hint="每天开几个班次，如 1/2/3 班")
        _row(1, 0, "班次时长(小时):", self.shift_hours_var,
             hint="每个班次的计划时长（含休息），通常 8-12 小时")
        _row(1, 2, "休息(分钟):", self.break_var, padx=(16, 0),
             hint="班次内休息时间，影响每日有效工时")
        _row(2, 0, "加班(小时/班):", self.overtime_var,
             hint="每个班次额外加班小时数，按加班倍率计薪")
        _row(2, 2, "时薪(元/h):", self.wage_var, padx=(16, 0),
             hint="基本时薪（不含加班/社保）")
        _row(3, 0, "社保/福利比例:", self.social_var,
             hint="公司承担的社保/福利占工资比例（如 0.30 = 30%）")
        _row(3, 2, "缺勤率(0-1):", self.absence_var, padx=(16, 0),
             hint="日常缺勤比例，用于放大在岗覆盖人数（如 0.1 = 10%）")
        _row(4, 0, "月离职率(0-1):", self.turnover_var,
             hint="月离职比例，用于估算招聘/培训替换成本")
        _row(4, 2, "爬坡天数:", self.ramp_var, padx=(16, 0),
             hint="新员工从入职到满效率所需天数（默认 90 天线性爬坡）")

        current_label = (
            "当前在岗（人数；留空按 0 计，可用「按当前产线填充」带入）:"
            if self._single_role
            else "当前在岗（每行 工种:人数；留空按 0 计）:"
        )
        ttk.Label(main, text=current_label).grid(
            row=5, column=0, columnspan=4, sticky=tk.W, pady=(12, 2)
        )
        self.current_text = tk.Text(main, height=3, width=60)
        self.current_text.grid(row=6, column=0, columnspan=4, sticky=tk.W)
        hiring_label = (
            "招聘计划（每行 周,人数，如 1,2；也可写 周,工种,人数）:"
            if self._single_role
            else "招聘计划（每行 周,工种,人数，如 1,filling_operator,2）:"
        )
        ttk.Label(
            main, text=hiring_label
        ).grid(row=7, column=0, columnspan=4, sticky=tk.W, pady=(8, 2))
        self.hiring_text = tk.Text(main, height=3, width=60)
        self.hiring_text.grid(row=8, column=0, columnspan=4, sticky=tk.W)

        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=9, column=0, columnspan=4, pady=10, sticky=tk.W)
        ttk.Button(btn_frame, text="计算", command=self._compute).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="按当前产线填充", command=self._fill_current).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="导出Excel", command=self._export_excel).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="导出PDF", command=self._export_pdf).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="关闭", command=self._cancel).pack(side=tk.LEFT, padx=4)

        self.result_text = tk.Text(main, height=16, width=70, state=tk.DISABLED)
        self.result_text.grid(row=10, column=0, columnspan=4, sticky=tk.NSEW, pady=(4, 0))
        main.rowconfigure(10, weight=1)
        HelpSection(
            main,
            "使用说明",
            "目的：按目标产量估算产线需要的人数、排班与人力成本，"
            "供 HRBP/HR 做预算、招聘与爬坡规划。\n"
            "需求口径：达成目标产量的最低人数；"
            "每工序需求 = 每小时目标 ÷ 单人产能（向上取整）。\n"
            "单人产能公式：3600÷单件耗时×OEE×效率×(1−切换占比)（并联时÷人数）。\n"
            "当前在岗：留空按 0 人计（视为无在岗人员）；"
            "点「按当前产线填充」可把产线现有配置带入。\n"
            "缺口口径：缺口 = 需求 − 当前在岗 − 已计划到岗，"
            "指“还缺多少人”（累计），不是每周都要招这么多人；"
            "本周新增 = 较上周的新增缺口。\n"
            "成本 = 在岗人数 ×（基本工资 + 加班 + 社保福利）。\n"
            "术语：UPPH=每人每小时产出；缺勤率/离职率/爬坡天数见字段提示。\n"
            "结果仅供规划参考，不构成用工承诺。",
        ).grid(row=11, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))

    def _parse_current(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        raw = self.current_text.get("1.0", tk.END).strip()
        if raw and ":" not in raw and "," not in raw and "\n" not in raw:
            return {"general": int(raw.strip())}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                role, count = line.split(":", 1)
            elif "," in line:
                role, count = line.split(",", 1)
            else:
                continue
            result[role.strip()] = int(count.strip())
        return result

    def _parse_hiring(self):
        result = []
        for line in self.hiring_text.get("1.0", tk.END).strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.replace("，", ",").split(",")]
            if len(parts) == 2:
                result.append((int(parts[0]), "general", int(parts[1])))
                continue
            if len(parts) != 3:
                continue
            result.append((int(parts[0]), parts[1], int(parts[2])))
        return result

    def _compute(self) -> bool:
        try:
            target = float(self.target_var.get())
            shifts = int(self.shifts_var.get())
            shift_hours = float(self.shift_hours_var.get())
            break_minutes = int(self.break_var.get())
            overtime = float(self.overtime_var.get())
            wage = float(self.wage_var.get())
            social = float(self.social_var.get())
            absence = float(self.absence_var.get())
            turnover = float(self.turnover_var.get())
            ramp_days = int(self.ramp_var.get())

            if target <= 0:
                messagebox.showerror("错误", "目标日产量必须大于 0")
                return False
            if shift_hours <= 0 or break_minutes >= shift_hours * 60:
                messagebox.showerror("错误", "班次时长/休息时间不合理")
                return False

            shift_plan = ShiftPlan(
                shifts_per_day=shifts,
                shift_hours=shift_hours,
                break_minutes=break_minutes,
                overtime_hours_per_shift=overtime,
            )
            cost = LaborCostConfig(
                base_hourly_wage=wage,
                social_insurance_rate=social,
                absence_rate=absence,
                monthly_turnover_rate=turnover,
            )
            learning = LearningCurveConfig(ramp_days=ramp_days)
            current = self._parse_current()
            self.summary = build_hr_summary(
                self.line,
                target,
                shift_plan,
                cost,
                learning,
                current,
                self._parse_hiring(),
            )
            self._render()
            self._open_result_table()
            return True
        except Exception as e:
            messagebox.showerror("计算失败", f"{e}\n\n详细日志：logs/app.log")
            return False

    def _fill_current(self) -> None:
        """按当前产线在岗配置填充（V3.3）"""
        if self.line:
            self.current_text.delete("1.0", tk.END)
            current = current_headcount_by_role(self.line)
            if self._single_role:
                self.current_text.insert("1.0", str(sum(current.values())))
            else:
                self.current_text.insert(
                    "1.0",
                    "\n".join(f"{role}:{count}" for role, count in current.items()),
                )

    def _open_result_table(self) -> None:
        """以表格展示人力规划结果（V3.3）"""
        if not self.summary:
            return
        s = self.summary
        c = s["costs"]
        rows = []
        rows.append({
            "category": "计算口径",
            "item": f"目标日产量 {s['daily_target']:.0f} ÷ 有效工时 "
                    f"{s['effective_hours_per_day']:.1f}h",
            "value": f"每小时需求 {s['required_hourly']:.2f}",
        })
        for d in s["station_derivation"]:
            rows.append({
                "category": "计算明细",
                "item": (
                    f"{d['name']}{'（瓶颈）' if d['is_bottleneck'] else ''}："
                    f"配置 {d['configured']} 人 / 单人产能 {d['per_head']}/h"
                    f"（单件耗时 {d['process_time']}s × OEE {d['oee']} × "
                    f"效率 {d['efficiency']} × 切换{d['changeover_minutes']}min）"
                ),
                "value": f"需求 {d['needed']} 人",
            })
        if self._single_role:
            rows.append({
                "category": "人力需求（按目标反推）",
                "item": "通用（合计）",
                "value": f"{s['total_headcount']} 人",
            })
        else:
            for role, count in s["headcount_by_role"].items():
                rows.append({
                    "category": "人力需求（按目标反推）",
                    "item": role,
                    "value": f"{count} 人",
                })
            for role, count in s["current_headcount"].items():
                rows.append({
                    "category": "当前在岗", "item": role, "value": f"{count} 人"
                })
        rows.append({
            "category": "当前在岗", "item": "在岗合计",
            "value": f"{s['current_total']} 人",
        })
        rows.append({
            "category": "招聘缺口", "item": "首周净缺口（需求−在岗−到岗）",
            "value": f"{s['initial_gap']} 人",
        })
        rows.append({
            "category": "招聘缺口",
            "item": "一次性招聘建议（若需补齐）",
            "value": f"{s['one_time_hiring']} 人",
        })
        rows.append({
            "category": "招聘缺口", "item": "富余人数（在岗−需求）",
            "value": f"{s['excess_headcount']} 人",
        })
        rows += [
            {"category": "成本", "item": "在岗覆盖人数（含缺勤）",
             "value": f"{c['covered_headcount']} 人"},
            {"category": "成本", "item": "日人力成本", "value": f"{c['daily_labor_cost']} 元"},
            {"category": "成本", "item": "月总成本", "value": f"{c['monthly_total']} 元"},
            {"category": "成本", "item": "单位人力成本",
             "value": f"{c['per_unit_labor_cost']} 元/单位"},
            {"category": "达产", "item": "达产天数", "value": f"{s['days_to_full']} 天"},
        ]
        for week in s["weekly_gap"]:
            rows.append({
                "category": "招聘缺口",
                "item": f"第 {week['week']} 周底还缺",
                "value": f"{week['total_gap']} 人（本周新增 {week['new_gap']}）",
            })
        ResultTableDialog(
            self.dialog,
            "人力规划结果",
            [
                ("category", "类别", 90, tk.W),
                ("item", "项目", 220, tk.W),
                ("value", "数值", 160, tk.W),
            ],
            rows,
        )

    def _render(self) -> None:
        s = self.summary
        c = s["costs"]
        lines = [
            f"目标日产量：{s['daily_target']:.0f}　"
            f"每日有效工时：{s['effective_hours_per_day']:.1f} 小时　"
            f"每小时需求：{s['required_hourly']:.2f}",
            "",
            "【计算明细】",
            "产能公式：3600÷单件耗时×人数×OEE×效率×(1−切换占比)",
        ]
        lines += [
            f"  {d['name']}{'（瓶颈）' if d['is_bottleneck'] else ''}："
            f"单件耗时{d['process_time']}s×OEE{d['oee']}×效率{d['efficiency']}"
            f"×切换{d['changeover_minutes']}min → 产能 {d['capacity']}/h，"
            f"单人 {d['per_head']}/h → 需求 {d['needed']} 人"
            for d in s["station_derivation"]
        ]
        lines += [
            "",
            "【人力需求（按目标反推，最低人数）】",
        ]
        if self._single_role:
            lines.append(f"  通用（合计）：{s['total_headcount']} 人")
        else:
            lines += [
                f"  {role}: {n} 人" for role, n in s["headcount_by_role"].items()
            ]
            lines.append(f"总人数：{s['total_headcount']} 人")
        lines += [
            "",
            "【当前在岗（按产线配置）】",
        ]
        if self._single_role:
            lines.append(f"  通用（合计）：{s['current_total']} 人")
        else:
            lines += [
                f"  {role}: {n} 人" for role, n in s["current_headcount"].items()
            ]
        lines.append(
            f"在岗合计：{s['current_total']} 人　"
            f"首周净缺口（需求−在岗−到岗）：{s['initial_gap']} 人　"
            f"富余：{s['excess_headcount']} 人"
        )
        lines += [
            "",
            "【成本测算】",
            f"  在岗覆盖人数（含缺勤）：{c['covered_headcount']} 人",
            f"  日人力成本：{c['daily_labor_cost']} 元",
            f"  月总成本：{c['monthly_total']} 元",
            f"  单位人力成本：{c['per_unit_labor_cost']} 元/单位",
            "",
            "【招聘缺口（前 12 周）】",
            "口径：缺口=截至该周末还缺的人数（累计）；"
            "本周新增=较上周的新增缺口；无新增时不会每周重复招聘。",
        ]
        lines += [
            f"  第 {w['week']} 周底还缺：{w['total_gap']} 人"
            f"（本周新增 {w['new_gap']}）"
            for w in s["weekly_gap"][:12]
        ]
        lines += [
            "",
            f"【一次性招聘建议】{s['one_time_hiring']} 人"
            f"（按首周一次性补齐，之后无新增则无需重复招聘）",
            f"【达产预测】约 {s['days_to_full']} 天达产",
        ]

        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", "\n".join(lines))
        self.result_text.config(state=tk.DISABLED)

    def _export_excel(self) -> None:
        if self.summary is None:
            messagebox.showinfo("提示", "请先点击「计算」")
            return
        path = filedialog.asksaveasfilename(
            title="导出人力规划 Excel",
            defaultextension=".xlsx",
            initialfile="人力规划报告.xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
        )
        if not path:
            return
        if export_hr_report(self.summary, path):
            messagebox.showinfo("成功", f"已导出：{path}")
        else:
            messagebox.showerror("失败", "导出失败，请查看 logs/app.log")

    def _export_pdf(self) -> None:
        if self.summary is None:
            messagebox.showinfo("提示", "请先点击「计算」")
            return
        path = filedialog.asksaveasfilename(
            title="导出人力规划 PDF",
            defaultextension=".pdf",
            initialfile="人力规划摘要.pdf",
            filetypes=[("PDF 文件", "*.pdf")],
        )
        if not path:
            return
        if export_hr_pdf(self.summary, path):
            messagebox.showinfo("成功", f"已导出：{path}")
        else:
            messagebox.showerror("失败", "导出失败，请查看 logs/app.log")

    def _cancel(self) -> None:
        self.dialog.destroy()


class HistoryDialog:
    """
    KPI 历史趋势对话框（V3.2 P1）

    展示跨运行 KPI 记录（表格 + 简易折线图），可记录当前产线快照。
    """

    KPI_LABELS = {
        "bottleneck_capacity": "瓶颈产能",
        "daily_output": "日产量",
        "total_cost": "日成本",
        "unit_cost": "单位成本",
        "balance_rate": "平衡率",
        "upph": "UPPH",
        "batch_cycle_min": "批次周期",
        "batch_pass_rate": "批次合格率",
        "yield_rate": "收率",
        "machine_oee": "机台OEE",
        "shippable_quantity": "可发运产出",
        "rejected_batches": "隔离批次",
    }

    def __init__(
        self,
        parent,
        production_line: Optional[ProductionLine],
        result=None,
    ):
        self.line = production_line
        self.result = result
        self.history = load_history()

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("KPI 历史趋势")
        self.dialog.geometry("760x620")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self._close())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self.dialog, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(btn_frame, text="记录当前产线", command=self._record).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="刷新", command=self._refresh).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="导出历史", command=self._export_history).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="清空历史", command=self._clear_history).pack(
            side=tk.LEFT, padx=4
        )

        selector_frame = ttk.Frame(main)
        selector_frame.pack(fill=tk.X, pady=(0, 6))
        self._label_to_key = {
            label: key for key, label in self.KPI_LABELS.items()
        }
        ttk.Label(selector_frame, text="KPI:").pack(side=tk.LEFT, padx=(0, 4))
        self.kpi_var = tk.StringVar(value=self.KPI_LABELS["daily_output"])
        self.kpi_combo = ttk.Combobox(
            selector_frame,
            textvariable=self.kpi_var,
            values=list(self.KPI_LABELS.values()),
            state="readonly",
            width=24,
        )
        self.kpi_combo.pack(side=tk.LEFT)
        self.kpi_combo.bind("<<ComboboxSelected>>", lambda e: self._draw_chart())
        ttk.Label(selector_frame, text="对比KPI:").pack(side=tk.LEFT, padx=(20, 4))
        self.compare_var = tk.StringVar(value="无对比")
        self.compare_combo = ttk.Combobox(
            selector_frame,
            textvariable=self.compare_var,
            values=["无对比"] + list(self.KPI_LABELS.values()),
            state="readonly",
            width=24,
        )
        self.compare_combo.pack(side=tk.LEFT)
        self.compare_combo.bind("<<ComboboxSelected>>", lambda e: self._draw_chart())

        columns = ("time", "line", "daily", "unit_cost", "balance", "upph")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=12)
        self.tree.heading("time", text="时间")
        self.tree.heading("line", text="产线")
        self.tree.heading("daily", text="日产量")
        self.tree.heading("unit_cost", text="单位成本")
        self.tree.heading("balance", text="平衡率")
        self.tree.heading("upph", text="UPPH")
        self.tree.column("time", width=140)
        self.tree.column("line", width=140)
        self.tree.column("daily", width=80, anchor=tk.E)
        self.tree.column("unit_cost", width=90, anchor=tk.E)
        self.tree.column("balance", width=80, anchor=tk.E)
        self.tree.column("upph", width=90, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self.canvas = tk.Canvas(main, height=200, bg="#FFFFFF", highlightthickness=1)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._refresh()
        self._draw_chart()
        HelpSection(
            self.dialog,
            "使用说明",
            "目的：查看跨运行 KPI 变化趋势，评估优化效果。\n"
            "记录时机：点击「记录当前产线」立即记录；"
            "GUI 中停止仿真时自动记录。\n"
            "历史保存在 configs/run_history.json（最多 200 条，不入库）。",
        ).pack(fill=tk.X, pady=(6, 0))

    def _refresh(self) -> None:
        self.history = load_history()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for record in reversed(self.history):
            k = record.get("kpis", {})
            self.tree.insert("", tk.END, values=(
                record.get("timestamp", ""),
                record.get("line_name", ""),
                round(k.get("daily_output", 0), 1),
                round(k.get("unit_cost", 0), 3),
                f"{k.get('balance_rate', 0) * 100:.1f}%",
                round(k.get("upph", 0), 1),
            ))

    def _draw_chart(self) -> None:
        self.canvas.delete("all")
        kpi = self._label_to_key.get(self.kpi_var.get(), "daily_output")
        series = kpi_series(self.history, kpi)
        compare_kpi = self._label_to_key.get(self.compare_var.get(), "")
        compare_series = (
            kpi_series(self.history, compare_kpi) if compare_kpi else []
        )
        values = [v for _, v in series] + [v for _, v in compare_series]
        reference = None
        if self.line is not None:
            reference = build_snapshot(self.line).get("kpis", {}).get(kpi, None)
            if reference is not None:
                values.append(reference)
        if not series:
            self.canvas.create_text(
                260, 100, text="暂无记录，点击「记录当前产线」",
                fill="#888888",
            )
            return
        vmin, vmax = min(values), max(values)
        span = (vmax - vmin) or 1.0
        width, height = 740, 200
        pad = 30
        points = []
        for i, (label, value) in enumerate(series):
            x = pad + i * (width - 2 * pad) / max(len(series) - 1, 1)
            y = height - pad - (value - vmin) / span * (height - 2 * pad)
            points.append((x, y))
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            self.canvas.create_line(x1, y1, x2, y2, fill="#1F6FEB", width=2)
        for x, y in points:
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#1F6FEB")
        if compare_series:
            compare_points = []
            for i, (_, value) in enumerate(compare_series):
                x = pad + i * (width - 2 * pad) / max(len(compare_series) - 1, 1)
                y = height - pad - (value - vmin) / span * (height - 2 * pad)
                compare_points.append((x, y))
            for (x1, y1), (x2, y2) in zip(compare_points, compare_points[1:]):
                self.canvas.create_line(x1, y1, x2, y2, fill="#2DA44E", width=2)
        if reference is not None:
            ref_y = height - pad - (reference - vmin) / span * (height - 2 * pad)
            self.canvas.create_line(
                pad, ref_y, width - pad, ref_y,
                fill="#B3261E", dash=(4, 3),
            )
        self.canvas.create_text(
            8, 8, anchor=tk.NW,
            text=(
                f"{self.KPI_LABELS.get(kpi, kpi)}"
                f"{' vs ' + self.KPI_LABELS.get(compare_kpi, compare_kpi) if compare_kpi else ''}"
                f"  最高 {vmax:.2f} / 最低 {vmin:.2f}"
                f"（红虚线=当前值 {reference:.2f}）" if reference is not None else ""
            ),
            fill="#555555",
        )

    def _record(self) -> None:
        if self.line is None:
            messagebox.showwarning("警告", "没有当前产线")
            return
        record = build_snapshot(self.line, self.result)
        if append_snapshot(record):
            show_toast(self.dialog, "已记录本次 KPI 快照")
        else:
            messagebox.showerror("失败", "记录失败，请查看 logs/app.log")
        self._refresh()
        self._draw_chart()

    def _close(self) -> None:
        self.dialog.destroy()

    def _export_history(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出历史",
            defaultextension=".xlsx",
            initialfile="KPI历史.xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
        )
        if not path:
            return
        if export_history(path, self.history):
            messagebox.showinfo("成功", f"已导出：{path}")
        else:
            messagebox.showerror("失败", "无历史记录或导出失败")

    def _clear_history(self) -> None:
        if not messagebox.askyesno("确认", "确定清空全部 KPI 历史记录？"):
            return
        if clear_history():
            self._refresh()
            self._draw_chart()
            show_toast(self.dialog, "历史已清空")


class SweepDialog:
    """
    批量试算对话框（V3.2 P1）

    对同一参数的一组取值批量仿真对比（工人数/机台节拍/OEE/班次时长）。
    """

    PARAM_LABELS = {
        "worker_count": "工人数",
        "machine_takt": "机台节拍(秒)",
        "oee": "OEE",
        "shift_hours": "班次时长(小时)",
    }
    METRIC_LABELS = {
        "total_output": "总产出",
        "unit_cost": "单位成本",
        "upph": "UPPH",
    }

    def __init__(self, parent, production_line: Optional[ProductionLine]):
        self.line = production_line
        self.rows = []
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("批量试算")
        self.dialog.geometry("640x520")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self._close())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self.dialog, padding=14)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="参数:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self._param_label_to_key = {
            label: key for key, label in self.PARAM_LABELS.items()
        }
        self._metric_label_to_key = {
            label: key for key, label in self.METRIC_LABELS.items()
        }
        self.param_var = tk.StringVar(value=self.PARAM_LABELS["worker_count"])
        self.param_combo = ttk.Combobox(
            main,
            textvariable=self.param_var,
            values=list(self.PARAM_LABELS.values()),
            state="readonly",
            width=22,
        )
        self.param_combo.grid(row=0, column=1, sticky=tk.W, pady=4)
        self.param_combo.bind("<<ComboboxSelected>>", lambda e: self._on_param_change())

        ttk.Label(main, text="作用工序（空=瓶颈）:").grid(
            row=1, column=0, sticky=tk.W, pady=4
        )
        station_options = ["（空=瓶颈）"]
        if self.line:
            station_options += [
                f"{s.id} {s.name}" for s in self.line.stations
            ]
        self.station_var = tk.StringVar(value=station_options[0])
        self.station_combo = ttk.Combobox(
            main,
            textvariable=self.station_var,
            values=station_options,
            state="readonly",
            width=28,
        )
        self.station_combo.grid(row=1, column=1, sticky=tk.W, pady=4)

        ttk.Label(main, text="取值列表（逗号分隔）:").grid(
            row=2, column=0, sticky=tk.W, pady=4
        )
        self.values_var = tk.StringVar(value="1,2,3")
        ttk.Entry(main, textvariable=self.values_var, width=28).grid(
            row=2, column=1, sticky=tk.W, pady=4
        )
        ttk.Label(
            main,
            text="每个取值运行一次仿真，如 1,2,3 表示试三个档位",
            foreground=COLORS["text_secondary"],
        ).grid(row=2, column=2, sticky=tk.W, padx=(8, 0), pady=4)

        ttk.Label(main, text="仿真时长(小时):").grid(
            row=3, column=0, sticky=tk.W, pady=4
        )
        self.duration_var = tk.StringVar(
            value=str(self.line.shift_hours) if self.line else "8"
        )
        ttk.Entry(main, textvariable=self.duration_var, width=10).grid(
            row=3, column=1, sticky=tk.W, pady=4
        )

        ttk.Button(main, text="开始试算", command=self._run).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=8
        )

        columns = ("label", "output", "daily", "unit_cost", "upph")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=10)
        self.tree.heading("label", text="参数")
        self.tree.heading("output", text="总产出")
        self.tree.heading("daily", text="日产量")
        self.tree.heading("unit_cost", text="单位成本")
        self.tree.heading("upph", text="UPPH")
        for col, width in (("label", 140), ("output", 100), ("daily", 100),
                           ("unit_cost", 100), ("upph", 100)):
            self.tree.column(col, width=width, anchor=tk.E if col != "label" else tk.W)
        self.tree.grid(row=5, column=0, columnspan=2, sticky=tk.NSEW, pady=(4, 0))

        ttk.Label(main, text="图表指标:").grid(
            row=6, column=0, sticky=tk.W, pady=8
        )
        self.metric_var = tk.StringVar(value=self.METRIC_LABELS["total_output"])
        self.metric_combo = ttk.Combobox(
            main,
            textvariable=self.metric_var,
            values=list(self.METRIC_LABELS.values()),
            state="readonly",
            width=16,
        )
        self.metric_combo.grid(row=6, column=1, sticky=tk.W, pady=8)
        self.metric_combo.bind("<<ComboboxSelected>>", lambda e: self._draw_chart())
        ttk.Button(main, text="导出Excel", command=self._export_excel).grid(
            row=6, column=2, sticky=tk.E, pady=8, padx=(8, 0)
        )
        ttk.Button(main, text="关闭", command=self._close).grid(
            row=6, column=3, sticky=tk.E, pady=8, padx=(8, 0)
        )
        main.rowconfigure(5, weight=1)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=0)
        main.columnconfigure(3, weight=0)
        self.canvas = tk.Canvas(main, height=180, bg="#FFFFFF", highlightthickness=1)
        self.canvas.grid(row=8, column=0, columnspan=3, sticky=tk.NSEW, pady=(4, 0))
        HelpSection(
            main,
            "使用说明",
            "目的：对同一参数的一组取值批量仿真，观察产出/成本/UPPH 变化。\n"
            "参数：工序人数 / 机台节拍（秒）/ OEE / 班次时长（小时），"
            "选择后自动给出常用取值建议。\n"
            "作用工序：下拉选择要试算的工序（列表显示 工序ID+名称，"
            "如 s01 镭雕）；选“空=瓶颈”则自动作用于瓶颈工序。\n"
            "取值列表：该参数要试的几个档位，每个取值运行一次仿真，"
            "如 1,2,3 表示三档。\n"
            "仿真时长：每次仿真的模拟时长，默认取当前产线班次。\n"
            "图表指标：展示每次试算的结果指标（总产出/单位成本/UPPH），"
            "用于观察参数取值与结果的关系；导出 Excel 可进一步分析。",
        ).grid(row=7, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))

    def _run(self) -> None:
        try:
            param = self._param_label_to_key.get(
                self.param_var.get(), "worker_count"
            )
            station_text = self.station_var.get().strip()
            station_id = (
                None
                if not station_text or station_text.startswith("（空")
                else station_text.split(" ")[0]
            )
            values = [
                float(v.strip())
                for v in self.values_var.get().replace("，", ",").split(",")
                if v.strip()
            ]
            duration = float(self.duration_var.get())
            if not values:
                messagebox.showwarning("警告", "请填写取值列表")
                return
            self.dialog.config(cursor="watch")
            self.dialog.update_idletasks()
            base = SimulationEngine(copy.deepcopy(self.line)).run_sync(
                duration_hours=duration
            )
            base_row = {
                "label": "基线（当前）",
                "total_output": base.total_output,
                "daily_output": round(base.kpis.get("daily_output", 0.0), 1),
                "unit_cost": base.kpis.get("unit_cost", 0.0),
                "upph": base.kpis.get("upph", 0.0),
            }
            sweep_rows = run_sweep(
                self.line, param, values,
                station_id=station_id, duration_hours=duration,
            )
            self.rows = [base_row] + sweep_rows
            for item in self.tree.get_children():
                self.tree.delete(item)
            for index, row in enumerate(self.rows):
                item = self.tree.insert("", tk.END, values=(
                    row["label"],
                    row["total_output"],
                    row["daily_output"],
                    round(row["unit_cost"], 3),
                    round(row["upph"], 1),
                ))
                if index == 0:
                    self.tree.item(item, tags=("baseline",))
            self.tree.tag_configure("baseline", background="#EAF2FF")
            self._draw_chart()
        except Exception as e:
            messagebox.showerror("试算失败", f"{e}\n\n详细日志：logs/app.log")
        finally:
            self.dialog.config(cursor="")

    def _draw_chart(self) -> None:
        """批量试算折线图（V3.3）"""
        self.canvas.delete("all")
        if len(self.rows) < 2:
            self.canvas.create_text(
                300, 80, text="运行试算后显示对比曲线", fill="#888888"
            )
            return
        metric = self._metric_label_to_key.get(
            self.metric_var.get(), "total_output"
        )
        series = [row.get(metric, 0.0) for row in self.rows[1:]]
        base_value = self.rows[0].get(metric, 0.0)
        values = series + [base_value]
        vmin, vmax = min(values), max(values)
        span = (vmax - vmin) or 1.0
        width, height = 640, 180
        pad = 34
        points = []
        for i, value in enumerate(series):
            x = pad + i * (width - 2 * pad) / max(len(series) - 1, 1)
            y = height - pad - (value - vmin) / span * (height - 2 * pad)
            points.append((x, y))
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            self.canvas.create_line(x1, y1, x2, y2, fill="#1F6FEB", width=2)
        for x, y in points:
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#1F6FEB")
        baseline_y = height - pad - (base_value - vmin) / span * (height - 2 * pad)
        self.canvas.create_line(
            pad, baseline_y, width - pad, baseline_y,
            fill="#B3261E", dash=(4, 3),
        )
        self.canvas.create_text(
            8, 6, anchor=tk.NW,
            text=(
                f"{self.METRIC_LABELS.get(metric, metric)} 曲线"
                f"（红虚线=基线 {base_value:.2f}）"
            ),
            fill="#555555",
        )

    def _on_param_change(self) -> None:
        """切换参数时给出常用取值建议（V3.3.1）"""
        param = self._param_label_to_key.get(
            self.param_var.get(), "worker_count"
        )
        bottleneck = self.line.find_bottleneck() if self.line else None
        if param == "worker_count":
            n = bottleneck.worker_count if bottleneck else 1
            self.values_var.set(f"{max(1, n - 1)},{n},{n + 1}")
        elif param == "machine_takt":
            takt = bottleneck.machine_takt if bottleneck and bottleneck.machine_takt else 2.0
            self.values_var.set(f"{max(0.5, takt - 1):g},{takt:g},{takt + 1:g}")
        elif param == "oee":
            self.values_var.set("0.80,0.85,0.90")
        elif param == "shift_hours":
            self.values_var.set("8,10,12")

    def _export_excel(self) -> None:
        """导出试算结果（V3.3.1）"""
        try:
            import pandas as pd

            if not self.rows:
                messagebox.showinfo("提示", "请先点击「开始试算」")
                return
            path = filedialog.asksaveasfilename(
                title="导出批量试算结果",
                defaultextension=".xlsx",
                initialfile="批量试算结果.xlsx",
                filetypes=[("Excel 文件", "*.xlsx")],
            )
            if not path:
                return
            df = pd.DataFrame([
                {
                    "方案": row["label"],
                    "总产出": row["total_output"],
                    "日产量": row["daily_output"],
                    "单位成本": row["unit_cost"],
                    "UPPH": row["upph"],
                }
                for row in self.rows
            ])
            df.to_excel(path, index=False)
            messagebox.showinfo("成功", f"已导出：{path}")
        except Exception as e:
            messagebox.showerror("导出失败", f"{e}\n\n详细日志：logs/app.log")

    def _close(self) -> None:
        self.dialog.destroy()


class OptimizeDialog:
    """
    智能优化对话框（V3.2 P2）

    以单位成本最小或总产出最大为目标，遗传算法搜索 TOP 方案。
    """

    OBJECTIVE_LABELS = {
        "unit_cost": "单位成本最小",
        "output": "总产出最大",
    }

    def __init__(
        self,
        parent,
        production_line: Optional[ProductionLine],
        on_apply=None,
    ):
        self.line = production_line
        self.on_apply = on_apply
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("智能优化（遗传算法）")
        self.dialog.geometry("580x560")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self._close())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self.dialog, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="优化目标:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self._objective_label_to_key = {
            label: key for key, label in self.OBJECTIVE_LABELS.items()
        }
        self.objective_var = tk.StringVar(value=self.OBJECTIVE_LABELS["unit_cost"])
        self.objective_combo = ttk.Combobox(
            main,
            textvariable=self.objective_var,
            values=list(self.OBJECTIVE_LABELS.values()),
            state="readonly",
            width=22,
        )
        self.objective_combo.grid(row=0, column=1, sticky=tk.W, pady=5)

        ttk.Label(main, text="迭代代数:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.generations_var = tk.StringVar(value="8")
        ttk.Entry(main, textvariable=self.generations_var, width=12).grid(
            row=1, column=1, sticky=tk.W, pady=5
        )

        ttk.Label(main, text="种群大小:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.population_var = tk.StringVar(value="6")
        ttk.Entry(main, textvariable=self.population_var, width=12).grid(
            row=2, column=1, sticky=tk.W, pady=5
        )

        ttk.Label(main, text="仿真时长(小时):").grid(
            row=3, column=0, sticky=tk.W, pady=5
        )
        self.duration_var = tk.StringVar(
            value=str(self.line.shift_hours) if self.line else "8"
        )
        ttk.Entry(main, textvariable=self.duration_var, width=12).grid(
            row=3, column=1, sticky=tk.W, pady=5
        )

        ttk.Label(main, text="锁定工序（可多选，Cmd/Ctrl 点选）:").grid(
            row=4, column=0, sticky=tk.W, pady=5
        )
        self.locked_listbox = tk.Listbox(
            main, height=4, selectmode=tk.EXTENDED, exportselection=False
        )
        if self.line:
            for station in self.line.stations:
                self.locked_listbox.insert(tk.END, f"{station.id} {station.name}")
        self.locked_listbox.grid(row=4, column=1, sticky=tk.W, pady=5)

        ttk.Button(main, text="开始优化", command=self._run).grid(
            row=5, column=0, columnspan=2, sticky=tk.W, pady=10
        )
        self.progress_var = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.progress_var).grid(
            row=6, column=0, columnspan=2, sticky=tk.W
        )
        ttk.Label(
            main,
            text="搜索范围：工序人数 1-10、缓冲区 10-500、机台节拍 ±50%",
            foreground=COLORS["text_secondary"],
        ).grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        HelpSection(
            main,
            "使用说明",
            "目标：让产线总产出最大或单位成本最小。\n"
            "搜索参数：每道工序的人数（1-10）、缓冲区容量（10-500）、"
            "机台节拍（仅节拍工序，0.5s ~ 当前×1.5）。\n"
            "迭代代数：遗传算法进化的轮数；代数越多搜索越充分、越接近最优，"
            "但计算时间成倍增加（每代 = 种群大小次仿真）。\n"
            "种群大小：每一代同时评估的方案个数；越大搜索覆盖面越广，"
            "每代计算越慢。总仿真次数 ≈ 种群 × 代数。\n"
            "锁定工序：在列表中选中工序（可多选）后，该工序参数保持当前值"
            "不参与搜索，适合固定设备/不可变工位。\n"
            "结果口径：理论单位成本=人力成本÷理论日产量；"
            "实际单位成本=总成本÷本次仿真产出。\n"
            "方案运用：在结果表选中某个 TOP 方案，点「应用选中方案」，"
            "该方案的人数/缓冲区/节拍会写入当前产线并刷新主界面，"
            "之后可保存方案或继续仿真验证。",
        ).grid(row=8, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

    def _run(self) -> None:
        try:
            objective = self._objective_label_to_key.get(
                self.objective_var.get(), "unit_cost"
            )
            generations = int(self.generations_var.get())
            population = int(self.population_var.get())
            duration = float(self.duration_var.get())
            locked = [
                self.locked_listbox.get(index).split(" ")[0]
                for index in self.locked_listbox.curselection()
            ]
            self.dialog.config(cursor="watch")
            self.dialog.update_idletasks()

            base = SimulationEngine(copy.deepcopy(self.line)).run_sync(
                duration_hours=duration
            )
            base_row = {
                "rank": "基线",
                "output": base.total_output,
                "unit_cost": round(base.kpis.get("unit_cost", 0.0), 3),
                "actual_unit_cost": round(
                    base.kpis.get("total_cost", 0.0) / base.total_output
                    if base.total_output > 0 else 0.0,
                    3,
                ),
                "total_cost": round(base.kpis.get("total_cost", 0.0), 1),
                "params": "当前配置",
            }

            def _progress(generation, total, best):
                self.progress_var.set(
                    f"正在优化：第 {generation}/{total} 代，"
                    f"当前最优得分 {best:.1f}"
                )
                self.dialog.update_idletasks()

            top = optimize(
                self.line,
                objective=objective,
                generations=generations,
                population=population,
                duration_hours=duration,
                locked_stations=locked,
                progress_callback=_progress,
            )
            if not top:
                messagebox.showwarning("提示", "未找到可行方案")
                return
            self._top = top

            rows = [base_row]
            for s in top:
                rows.append({
                    "rank": f"TOP{s['rank']}",
                    "output": s["total_output"],
                    "unit_cost": round(s["unit_cost"], 3),
                    "actual_unit_cost": round(s["actual_unit_cost"], 3),
                    "total_cost": round(s["total_cost"], 1),
                    "params": _params_text(s["params"]),
                })
            columns = [
                ("rank", "方案", 60, tk.CENTER),
                ("output", "总产出", 90, tk.E),
                ("unit_cost", "理论单位成本", 110, tk.E),
                ("actual_unit_cost", "实际单位成本", 110, tk.E),
                ("total_cost", "总成本", 90, tk.E),
                ("params", "参数", 420, tk.W),
            ]
            dialog = ResultTableDialog(
                self.dialog,
                f"智能优化结果（{self.OBJECTIVE_LABELS.get(objective, objective)}）",
                columns,
                rows,
                highlight_key="rank",
                highlight_value="基线",
                actions=[("应用选中方案", lambda: self._apply_selected(dialog))],
            )
            self.progress_var.set("优化完成")
        except Exception as e:
            messagebox.showerror("优化失败", f"{e}\n\n详细日志：logs/app.log")
        finally:
            self.dialog.config(cursor="")

    def _apply_selected(self, dialog) -> None:
        """把选中的 TOP 方案应用到当前产线（V3.3.1）"""
        row = dialog.get_selected_row()
        if row is None:
            messagebox.showwarning("提示", "请先在结果表选中一个方案")
            return
        rank = row.get("rank", "")
        if rank == "基线":
            messagebox.showinfo("提示", "基线为当前配置，无需应用")
            return
        entry = next(
            (t for t in getattr(self, "_top", []) if f"TOP{t['rank']}" == rank),
            None,
        )
        if entry is None:
            return
        for station in self.line.stations:
            params = entry["params"].get(station.name)
            if not params:
                continue
            station.worker_count = params["worker_count"]
            station.buffer_capacity = params["buffer_capacity"]
            if station.machine_takt and params.get("machine_takt"):
                station.machine_takt = params["machine_takt"]
        if self.on_apply:
            self.on_apply()
        messagebox.showinfo(
            "已应用",
            f"已应用「{rank}」到当前产线，主界面工序配置已刷新；"
            "可保存为方案或继续仿真验证。",
        )

    def _close(self) -> None:
        self.dialog.destroy()


class HelpSection:
    """折叠式「使用说明」区块（V3.3）"""

    def __init__(self, parent, title: str = "使用说明", text: str = ""):
        self._title = title
        self.frame = ttk.Frame(parent)
        self._expanded = False
        self.btn = ttk.Button(
            self.frame, text=f"▶ {title}", command=self._toggle
        )
        self.btn.pack(anchor=tk.W)
        height = max(3, min(10, text.count("\n") + 2))
        self.body = tk.Text(
            self.frame, height=height, wrap=tk.WORD,
            font=(resolve_font_family(), 10),
            foreground=COLORS["text_secondary"],
            state=tk.DISABLED,
        )
        self.body.configure(state=tk.NORMAL)
        self.body.insert("1.0", text)
        self.body.configure(state=tk.DISABLED)

    def pack(self, *args, **kwargs):
        self.frame.pack(*args, **kwargs)
        return self

    def grid(self, *args, **kwargs):
        self.frame.grid(*args, **kwargs)
        return self

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.btn.config(text=("▼ " if self._expanded else "▶ ") + self._title)
        if self._expanded:
            self.body.pack(fill=tk.X, expand=False, padx=(16, 0), pady=(2, 6))
        else:
            self.body.pack_forget()


def build_result_rows(columns, rows, highlight_key=None, highlight_value=None):
    """
    生成结果表格行（V3.3.3 回归修复）

    Returns:
        List[(values, is_baseline)]，供 ResultTableDialog 插入。
    """
    keys = [c[0] for c in columns]
    result = []
    for row in rows:
        result.append((
            [row.get(key, "") for key in keys],
            highlight_key is not None
            and row.get(highlight_key) == highlight_value,
        ))
    return result


class ResultTableDialog:
    """
    通用结果表格对话框（V3.3）

    columns: [(key, 列名, 宽度, anchor)]；rows: [{key: value}]
    highlight_key/highlight_value：满足条件的行高亮为基线行。
    actions: [(按钮文字, 回调)]
    """

    def __init__(
        self,
        parent,
        title: str,
        columns,
        rows,
        highlight_key: Optional[str] = None,
        highlight_value=None,
        actions=None,
        help_text: Optional[str] = None,
    ):
        self.columns = list(columns)
        self.rows = list(rows)
        self.highlight_key = highlight_key
        self.highlight_value = highlight_value
        self.actions = actions or []
        self.help_text = help_text

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("820x520")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self._close())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self.dialog, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(btn_frame, text="导出Excel", command=self._export_excel).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="复制文本", command=self._copy_text).pack(
            side=tk.LEFT, padx=4
        )
        for label, callback in self.actions:
            ttk.Button(btn_frame, text=label, command=callback).pack(
                side=tk.LEFT, padx=4
            )
        ttk.Button(btn_frame, text="关闭", command=self._close).pack(
            side=tk.RIGHT, padx=4
        )

        keys = [c[0] for c in self.columns]
        self.tree = ttk.Treeview(main, columns=keys, show="headings", height=16)
        for key, label, width, anchor in self.columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.pack(fill=tk.BOTH, expand=True)

        if self.help_text:
            HelpSection(self.dialog, "结果说明", self.help_text).pack(
                fill=tk.X, pady=(6, 0)
            )

        for values, is_baseline in build_result_rows(
            self.columns,
            self.rows,
            self.highlight_key,
            self.highlight_value,
        ):
            item = self.tree.insert("", tk.END, values=values)
            if is_baseline:
                self.tree.item(item, tags=("baseline",))
        self.tree.tag_configure("baseline", background="#EAF2FF")

    def get_selected_row(self):
        """返回当前选中行（V3.3.1）"""
        if self.selected_index is None or not (0 <= self.selected_index < len(self.rows)):
            return None
        return self.rows[self.selected_index]

    def _export_excel(self) -> None:
        try:
            import pandas as pd

            path = filedialog.asksaveasfilename(
                title="导出结果",
                defaultextension=".xlsx",
                initialfile="分析结果.xlsx",
                filetypes=[("Excel 文件", "*.xlsx")],
            )
            if not path:
                return
            df = pd.DataFrame([
                {label: row.get(key, "") for key, label, _, _ in self.columns}
                for row in self.rows
            ])
            df.to_excel(path, index=False)
            messagebox.showinfo("成功", f"已导出：{path}")
        except Exception as e:
            messagebox.showerror("导出失败", f"{e}\n\n详细日志：logs/app.log")

    def _copy_text(self) -> None:
        lines = ["\t".join(label for _, label, _, _ in self.columns)]
        for row in self.rows:
            lines.append("\t".join(str(row.get(key, "")) for key, _, _, _ in self.columns))
        text = "\n".join(lines)
        self.dialog.clipboard_clear()
        self.dialog.clipboard_append(text)
        show_toast(self.dialog, "已复制到剪贴板")

    def _close(self) -> None:
        self.dialog.destroy()


class HelpDialog:
    """使用说明对话框（V3.3.3：与术语/分析指南一致的可滚动窗口）"""

    HELP_TEXT = """【操作步骤】
1. 用「文件 → 快速配置向导」一键生成产线，或从「方案」导入已有方案
2. 在主界面添加/编辑工序（参数随生产类型自动适配；
   烟油/尼古丁袋可设置工种）
3. 点击「开始仿真」，观察画布、KPI 与报警；
   运行中可用「停机切换（换型）」模拟更换口味/规格/配方
4. 保存方案（主界面或「文件 → 保存方案」）；
   「方案」菜单用于方案管理 / 对比与 KPI 历史趋势
5. 分析试算：「分析」菜单提供敏感性试算、批量试算、
   智能优化、人力规划
6. 需要存档时用「报告 → 导出报告」（Excel / PDF）
7. 快捷键：Ctrl+S 保存方案、空格 开始/暂停

【亮点与价值】
• 把产线设计从"2 周试错"压缩到"2 小时仿真"
• 支持烟弹组装 / 烟油灌装（千克）/ 尼古丁袋三种生产类型
• 智能报警：瓶颈、WIP、饥饿/堵塞、预测预警、根因建议
• 人力规划：目标产量反推人数、成本、招聘缺口与爬坡达产
• 统一浅色主题、2026 桌面级交互体验
• 完整本地运行，数据不出设备"""

    def __init__(self, parent):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("使用说明")
        self.dialog.geometry("560x560")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        main = ttk.Frame(self.dialog, padding=14)
        main.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(main, wrap=tk.WORD, font=(resolve_font_family(), 11))
        text.insert("1.0", self.HELP_TEXT)
        text.config(state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(main, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.dialog.bind('<Escape>', lambda e: self.dialog.destroy())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")


class AnalysisGuideDialog:
    """分析指南（V3.3）：何时用哪个分析功能"""

    GUIDE_TEXT = """【如何选择分析功能】

1. 想看某个参数变化的影响 → 敏感性试算
   操作：分析 → 敏感性试算...；结果可一键应用最优建议。

2. 想看同一参数多档取值 → 批量试算
   操作：分析 → 批量试算...；填参数与取值列表（如 1,2,3），
   蓝色曲线为方案，红色虚线为当前基线。

3. 想同时调多个参数找最优组合 → 智能优化
   操作：分析 → 智能优化...；选目标（产出最大/成本最小），
   可锁定工序；TOP 方案与基线同表对比。

4. 想预估人力/成本/招聘 → 人力规划
   操作：分析 → 人力规划...；输入目标日产量与班次，
   结果含工种人数、成本、招聘缺口与达产天数，可导出。

5. 想看多次运行的趋势 → KPI 历史趋势
   停止仿真或点击“记录当前产线”后查看；可多 KPI 对比与导出。

6. 想对比几个保存方案 → 方案对比 / 方案管理
   先“保存方案”，再进入方案对比查看推荐。

7. 想要完整存档 → 导出报告
   自动仿真当前产线并导出 Excel（10+ Sheet）/ PDF。

【通用建议】
- 先跑一次仿真，再看报警与失衡分析定位瓶颈；
- 用敏感性/批量试算验证方向，用智能优化做组合寻优；
- 所有结果均可导出 Excel 或复制文本。"""

    def __init__(self, parent):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("分析指南")
        self.dialog.geometry("520x560")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        main = ttk.Frame(self.dialog, padding=14)
        main.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(main, wrap=tk.WORD, font=(resolve_font_family(), 11))
        text.insert("1.0", self.GUIDE_TEXT)
        text.config(state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(main, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.dialog.bind('<Escape>', lambda e: self.dialog.destroy())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")


class ChangeoverDialog:
    """
    停机切换对话框（V3.3.1）

    通过工序下拉选择要换型的工序，避免用户在画布/工序列表中寻找。
    """

    def __init__(self, parent, production_line: Optional[ProductionLine]):
        self.line = production_line
        self.result: Optional[Dict[str, Any]] = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("停机切换（换型）")
        self.dialog.geometry("480x330")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._create_widgets()
        self.dialog.bind('<Escape>', lambda e: self.dialog.destroy())
        self.dialog.bind('<Return>', lambda e: self._confirm())
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self.dialog, padding=14)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="选择要停机的工序:").grid(
            row=0, column=0, sticky=tk.W, pady=5
        )
        station_options = []
        if self.line:
            station_options = [
                f"{s.id} {s.name}" for s in self.line.stations
            ]
        self.station_var = tk.StringVar(
            value=station_options[0] if station_options else ""
        )
        self.station_combo = ttk.Combobox(
            main,
            textvariable=self.station_var,
            values=station_options,
            state="readonly",
            width=30,
        )
        self.station_combo.grid(row=0, column=1, sticky=tk.W, pady=5)

        ttk.Label(main, text="停机时长(分钟):").grid(
            row=1, column=0, sticky=tk.W, pady=5
        )
        self.minutes_var = tk.StringVar(value="45")
        ttk.Entry(main, textvariable=self.minutes_var, width=10).grid(
            row=1, column=1, sticky=tk.W, pady=5
        )
        ttk.Label(
            main,
            text="默认取所选工序的切换时间",
            foreground=COLORS["text_secondary"],
        ).grid(row=1, column=2, sticky=tk.W, padx=(8, 0), pady=5)

        ttk.Button(main, text="确认停机切换", command=self._confirm).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=10
        )
        HelpSection(
            main,
            "使用说明",
            "作用：仿真运行中模拟一次换型停机（如更换口味/规格/配方），"
            "观察对上下游与产出的影响。\n"
            "选择工序：下拉已列出全部工序（ID+名称），无需在画布或"
            "工序列表中寻找；确认后该工序会在画布上高亮。",
        ).grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))

    def _confirm(self) -> None:
        text = self.station_var.get()
        if not text:
            messagebox.showwarning("警告", "请先选择工序")
            return
        try:
            minutes = int(float(self.minutes_var.get()))
        except ValueError:
            messagebox.showerror("错误", "停机时长必须是整数分钟")
            return
        if minutes <= 0:
            messagebox.showerror("错误", "停机时长必须大于 0")
            return
        self.result = {
            "station_id": text.split(" ")[0],
            "station_name": text.split(" ", 1)[1] if " " in text else text,
            "minutes": minutes,
        }
        self.dialog.destroy()


class WizardDialog:
    """
    快速配置向导 - 分步引导新手完成产线配置

    步骤：
    1. 选择模板（简单/标准/复杂/空白）
    2. 选择数据来源（模板 / Excel 导入）
    3. 设置班次
    4. 完成（可选立即开始仿真）

    使用方式：
        dialog = WizardDialog(parent)
        if dialog.result:
            line = dialog.result['production_line']
            auto_start = dialog.result['auto_start']
    """

    TEMPLATES = {
        "simple": [
            ("注油", 25.0, 2, "parallel"),
            ("焊接", 30.0, 3, "parallel"),
            ("包装", 15.0, 2, "parallel"),
        ],
        "standard": [
            ("注油", 25.0, 2, "parallel"),
            ("焊接", 30.0, 3, "parallel"),
            ("棉芯安装", 20.0, 2, "collaborative"),
            ("组装", 22.0, 2, "parallel"),
            ("包装", 15.0, 2, "parallel"),
        ],
        "complex": [
            ("镭雕", 25.0, 5, "parallel"),
            ("注油", 47.0, 1, "parallel"),
            ("预压合", 47.0, 1, "parallel"),
            ("底座压合", 48.0, 1, "parallel"),
            ("阻值&适配测试", 45.0, 1, "parallel"),
            ("异物检查", 232.0, 1, "parallel"),
            ("组装", 22.0, 2, "parallel"),
            ("包装", 15.0, 2, "parallel"),
        ],
    }

    def __init__(self, parent):
        """初始化向导对话框"""
        self.result = None
        self.parent = parent
        self.step_index = 0
        self.production_line: Optional[ProductionLine] = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("快速配置向导")
        self.dialog.geometry("680x480")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.template_var = tk.StringVar(value="standard")
        self.excel_path_var = tk.StringVar(value="")
        self.shift_hours_var = tk.StringVar(value="8")
        self.break_minutes_var = tk.StringVar(value="60")
        self.wage_var = tk.StringVar(value="20.0")
        self.auto_start_var = tk.BooleanVar(value=True)
        # V3.3.5：模板/类型变更追踪（Excel 导入保护 + 产线重建）
        self.excel_imported = False
        self._loading_template_var = False
        self._last_template = "standard"
        self._last_production_type = "assembly"

        self._create_widgets()
        self.template_var.trace_add("write", self._on_template_change)
        self.production_type_var.trace_add("write", self._on_template_change)
        self.dialog.bind('<Escape>', lambda e: self.dialog.destroy())
        self._show_step(0)

        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - self.dialog.winfo_width()) // 2
        y = (self.dialog.winfo_screenheight() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

        self.dialog.wait_window()

    def _create_widgets(self) -> None:
        """创建界面组件"""
        self.main_frame = ttk.Frame(self.dialog, padding=15)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # 步骤提示
        self.step_label = ttk.Label(self.main_frame, font=('Arial', 12, 'bold'))
        self.step_label.pack(anchor=tk.W, pady=(0, 10))

        # 步骤内容容器
        self.step_container = ttk.Frame(self.main_frame)
        self.step_container.pack(fill=tk.BOTH, expand=True)

        # 创建四个步骤的页面
        self.page_template = ttk.Frame(self.step_container)
        self.page_source = ttk.Frame(self.step_container)
        self.page_shift = ttk.Frame(self.step_container)
        self.page_finish = ttk.Frame(self.step_container)

        self._build_page_template()
        self._build_page_source()
        self._build_page_shift()
        self._build_page_finish()

        # 底部按钮
        button_frame = ttk.Frame(self.main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        self.btn_prev = ttk.Button(button_frame, text="上一步", command=self._prev)
        self.btn_prev.pack(side=tk.LEFT)
        self.btn_cancel = ttk.Button(button_frame, text="取消", command=self._cancel)
        self.btn_cancel.pack(side=tk.LEFT, padx=5)
        self.btn_next = ttk.Button(button_frame, text="下一步", command=self._next)
        self.btn_next.pack(side=tk.RIGHT)

    def _build_page_template(self) -> None:
        """步骤 1：模板选择"""
        ttk.Label(
            self.page_template,
            text="生产类型决定计量单位与关键参数；新手建议先选「标准产线」体验完整流程。",
            foreground="gray",
            wraplength=620,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 6))

        ttk.Label(self.page_template, text="选择生产类型：", font=('Arial', 10)).pack(
            anchor=tk.W, pady=(0, 5)
        )
        self.production_type_var = tk.StringVar(value="assembly")
        type_hints = {
            "assembly": "离散装配，计量单位：颗",
            "liquid_filling": "液体/批量生产，计量单位：千克",
            "pouch_packaging": "高速机台包装，计量单位：袋",
        }
        for key, label in [
            ("assembly", "烟弹组装（离散装配）"),
            ("liquid_filling", "烟油灌装（液体/批量）"),
            ("pouch_packaging", "尼古丁袋包装（高速机台）"),
        ]:
            radio = ttk.Radiobutton(
                self.page_template,
                text=label,
                variable=self.production_type_var,
                value=key,
            )
            radio.pack(anchor=tk.W, pady=2)
            ToolTip(radio, type_hints[key])
        ttk.Separator(self.page_template, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        ttk.Label(self.page_template, text="选择产线模板：", font=('Arial', 10)).pack(anchor=tk.W, pady=5)
        template_hints = {
            "simple": "3 工序快速体验，适合首次了解流程",
            "standard": "5 工序均衡示例，推荐新手（默认）",
            "complex": "8 工序贴近真实参数，适合正式评估",
            "blank": "从零手动添加，适合已有明确配置",
        }
        for key, label in [
            ("simple", "简单产线（3 工序）"),
            ("standard", "标准产线（5 工序）"),
            ("complex", "复杂产线（8 工序，真实参数）"),
            ("blank", "空白产线（手动添加）"),
        ]:
            radio = ttk.Radiobutton(
                self.page_template,
                text=label,
                variable=self.template_var,
                value=key,
            )
            radio.pack(anchor=tk.W, pady=3)
            ToolTip(radio, template_hints[key])
        ttk.Button(
            self.page_template,
            text="术语速查",
            command=lambda: GlossaryDialog(self.dialog),
        ).pack(anchor=tk.W, pady=8)

    def _build_page_source(self) -> None:
        """步骤 2：数据来源"""
        ttk.Label(self.page_source, text="数据来源：", font=('Arial', 10)).pack(anchor=tk.W, pady=5)
        ttk.Label(
            self.page_source,
            text="默认使用所选模板；也可以额外从 Excel 导入替换模板数据。",
            foreground="gray",
        ).pack(anchor=tk.W, pady=(0, 10))

        source_frame = ttk.Frame(self.page_source)
        source_frame.pack(fill=tk.X)
        ttk.Button(source_frame, text="选择 Excel 文件...", command=self._pick_excel).pack(side=tk.LEFT)
        self.excel_label = ttk.Label(source_frame, text="未选择", foreground="gray")
        self.excel_label.pack(side=tk.LEFT, padx=10)

    def _build_page_shift(self) -> None:
        """步骤 3：班次设置"""
        ttk.Label(self.page_shift, text="班次设置：", font=('Arial', 10)).pack(anchor=tk.W, pady=5)
        ttk.Label(
            self.page_shift,
            text="以下参数用于计算日产能与人力成本。",
            foreground="gray",
        ).pack(anchor=tk.W, pady=(0, 6))
        form = ttk.Frame(self.page_shift)
        form.pack(fill=tk.X, pady=10)

        ttk.Label(form, text="班次时长(小时):").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(form, textvariable=self.shift_hours_var, width=10).grid(row=0, column=1, padx=10)
        ttk.Label(form, text="每班计划工作小时数", foreground="gray").grid(
            row=0, column=2, sticky=tk.W
        )

        ttk.Label(form, text="休息时间(分钟):").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(form, textvariable=self.break_minutes_var, width=10).grid(row=1, column=1, padx=10)
        ttk.Label(form, text="班内休息总分钟（含用餐）", foreground="gray").grid(
            row=1, column=2, sticky=tk.W
        )

        ttk.Label(form, text="工人时薪(元/小时):").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(form, textvariable=self.wage_var, width=10).grid(row=2, column=1, padx=10)
        ttk.Label(form, text="用于成本与人力规划估算", foreground="gray").grid(
            row=2, column=2, sticky=tk.W
        )

    def _build_page_finish(self) -> None:
        """步骤 4：完成"""
        ttk.Label(
            self.page_finish,
            text="完成后可在左侧「工序配置」继续编辑工序细节。",
            foreground="gray",
        ).pack(anchor=tk.W, pady=(0, 5))
        self.summary_text = tk.Text(self.page_finish, height=12, width=70, state=tk.DISABLED)
        self.summary_text.pack(fill=tk.BOTH, expand=True, pady=5)
        ttk.Checkbutton(
            self.page_finish,
            text="配置完成后立即开始仿真",
            variable=self.auto_start_var,
        ).pack(anchor=tk.W, pady=5)

    def _pick_excel(self) -> None:
        """选择并导入 Excel 配置"""
        from tkinter import filedialog
        from src.utils import import_from_excel

        path = filedialog.askopenfilename(
            title="导入 Excel 配置",
            filetypes=[("Excel 文件", "*.xlsx *.xls"), ("所有文件", "*.*")],
        )
        if not path:
            return
        line, error = import_from_excel(path)
        if line:
            self.production_line = line
            self.excel_imported = True
            self.excel_path_var.set(path)
            self.excel_label.config(text=f"已导入：{path}（{len(line.stations)} 个工序）", foreground="green")
            # 同步第 1 步的选择状态，避免界面与实际产线不一致
            type_keys = {
                ProductionType.ASSEMBLY: "assembly",
                ProductionType.LIQUID_FILLING: "liquid_filling",
                ProductionType.POUCH_PACKAGING: "pouch_packaging",
            }
            self._loading_template_var = True
            try:
                self.production_type_var.set(type_keys.get(line.production_type, "assembly"))
                self.template_var.set("blank")
                self._last_production_type = self.production_type_var.get()
                self._last_template = "blank"
            finally:
                self._loading_template_var = False
        else:
            messagebox.showerror("导入失败", error or "未知错误")

    def _on_template_change(self, *_args) -> None:
        """模板/生产类型变更：重置已生成的产线，避免旧配置残留。

        若已导入 Excel，需用户确认后才会用新配置覆盖导入数据。
        """
        if self._loading_template_var:
            return
        if not resolve_template_reset(
            self.excel_imported,
            lambda: messagebox.askyesno(
                "切换模板",
                "已导入 Excel 产线。切换模板/生产类型将使用新配置并覆盖导入数据，是否继续？",
            ),
        ):
            # 用户取消：恢复变更前的选择
            self._loading_template_var = True
            try:
                self.template_var.set(self._last_template)
                self.production_type_var.set(self._last_production_type)
            finally:
                self._loading_template_var = False
            return
        self._last_template = self.template_var.get()
        self._last_production_type = self.production_type_var.get()
        self.excel_imported = False
        self.production_line = None
        if hasattr(self, "excel_label"):
            self.excel_label.config(text="未选择", foreground="gray")

    def _make_template_line(self) -> ProductionLine:
        """根据所选模板创建产线"""
        production_type = self.production_type_var.get()
        if production_type == "liquid_filling":
            return create_liquid_line()
        if production_type == "pouch_packaging":
            return create_pouch_line()
        return build_template_line(self.template_var.get())

    def _show_step(self, index: int) -> None:
        """切换步骤页面"""
        self.step_index = index
        pages = [self.page_template, self.page_source, self.page_shift, self.page_finish]
        titles = ["第 1 步 / 共 4 步：选择模板", "第 2 步 / 共 4 步：数据来源", "第 3 步 / 共 4 步：班次设置", "第 4 步 / 共 4 步：完成"]
        for i, page in enumerate(pages):
            page.pack_forget()
        pages[index].pack(fill=tk.BOTH, expand=True)
        self.step_label.config(text=titles[index])
        self.btn_prev.config(state=tk.NORMAL if index > 0 else tk.DISABLED)
        self.btn_next.config(text="完成" if index == len(pages) - 1 else "下一步")
        if index == len(pages) - 1:
            self._refresh_summary()

    def _refresh_summary(self) -> None:
        """刷新完成页摘要"""
        if self.production_line is None:
            self.production_line = self._make_template_line()
        line = self.production_line
        summary = (
            f"产线名称：{line.name}\n"
            f"工序数量：{len(line.stations)}\n"
            f"总人数：{sum(s.worker_count for s in line.stations)} 人\n"
            f"班次：{self.shift_hours_var.get()} 小时 / 休息 {self.break_minutes_var.get()} 分钟\n"
            f"时薪：{self.wage_var.get()} 元/小时\n"
        )
        if line.stations:
            unit = line.get_unit()
            summary += f"瓶颈产能：{line.get_bottleneck_capacity():.0f} {unit}/h\n"
            summary += f"预计日产量：{line.calculate_daily_output():.0f} {unit}\n"
        self.summary_text.config(state=tk.NORMAL)
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert("1.0", summary)
        self.summary_text.config(state=tk.DISABLED)

    def _prev(self) -> None:
        if self.step_index > 0:
            self._show_step(self.step_index - 1)

    def _next(self) -> None:
        if self.step_index < 3:
            # 进入步骤 3 前确保产线已就绪
            if self.step_index == 1 and self.production_line is None:
                self.production_line = self._make_template_line()
            self._show_step(self.step_index + 1)
            return

        # 完成：应用班次配置
        try:
            shift_hours, break_minutes, wage = parse_shift_params(
                self.shift_hours_var.get(),
                self.break_minutes_var.get(),
                self.wage_var.get(),
            )
        except ValueError as e:
            messagebox.showerror("参数错误", str(e))
            return

        if self.production_line is None:
            self.production_line = self._make_template_line()
        self.production_line.shift_hours = shift_hours
        self.production_line.break_minutes = break_minutes
        self.production_line.worker_hourly_wage = wage

        self.result = {
            "production_line": self.production_line,
            "auto_start": self.auto_start_var.get(),
        }
        self.dialog.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.dialog.destroy()
