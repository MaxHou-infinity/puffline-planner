"""
GUI画布视图 - 2D产线可视化

这个文件包含画布视图类，负责：
1. 绘制产线布局（工序节点、连接线）
2. 显示实时状态（颜色、数值）
3. 物料流动动画
4. 交互操作（双击编辑、拖拽等）

Canvas绘制原理：
- Tkinter的Canvas是2D绘图画布
- 使用create_rectangle、create_text等绘制图形
- 使用itemconfig更新已有图形
- 使用tags管理图形元素
"""

import tkinter as tk
from typing import Optional, Dict, Any, List, Callable

from src.models import ProductionLine, Station, SimulationState
from src.utils import get_status_color, CANVAS_WIDTH, CANVAS_HEIGHT, STATION_WIDTH, STATION_HEIGHT
from src.theme import COLORS, STATUS_LABELS, STATUS_SYMBOLS, status_soft, resolve_font_family


def format_capacity_text(capacity: float, unit: str) -> str:
    """产能文本（单位动态，V3.3 修复写死'颗'的问题）"""
    return f"⚡ {capacity:.0f} {unit}/h"


def compute_reorder_order(
    order_ids: List[str],
    station_id: str,
    positions: Dict[str, tuple],
    final_x: float,
    final_y: float,
) -> Optional[List[str]]:
    """
    计算拖拽后的新工序顺序（纯函数，便于单元测试）

    将拖拽工序插入到距离其最终位置最近工序的索引处。

    Returns:
        Optional[List[str]]: 新顺序；无法计算时返回 None
    """
    if station_id not in order_ids or station_id not in positions:
        return None

    nearest_id = None
    nearest_dist = float("inf")
    for other_id, (ox, oy) in positions.items():
        if other_id == station_id:
            continue
        dist = (final_x - ox) ** 2 + (final_y - oy) ** 2
        if dist < nearest_dist:
            nearest_dist = dist
            nearest_id = other_id

    if nearest_id is None:
        return None

    new_order = list(order_ids)
    current_index = new_order.index(station_id)
    target_index = new_order.index(nearest_id)
    new_order.pop(current_index)
    new_order.insert(target_index, station_id)
    return new_order


def _round_rect(canvas, x1, y1, x2, y2, radius=12, **kwargs):
    """
    在 Canvas 上绘制圆角矩形（多边形平滑近似）

    Returns:
        int: 图形 item id
    """
    r = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    r = max(1, r)
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class CanvasView(tk.Canvas):
    """
    画布视图类 - 2D产线可视化
    
    继承自tk.Canvas，提供产线的2D可视化功能
    
    绘制内容：
    - 工序节点：矩形框，显示工序信息
    - 连接线：箭头，表示物料流向
    - WIP显示：数字，显示在制品数量
    - 物料粒子：小圆点，模拟物料流动
    
    使用方式：
        canvas = CanvasView(parent, width=1000, height=500)
        canvas.update_production_line(production_line)
    """
    
    def __init__(
        self,
        parent,
        width: int = CANVAS_WIDTH,
        height: int = CANVAS_HEIGHT,
        on_reorder: Optional[Callable[[List[str]], None]] = None,
        on_station_edit: Optional[Callable[[str], None]] = None,
        on_station_select: Optional[Callable[[str], None]] = None,
        on_station_menu: Optional[Callable[[str, int, int], None]] = None,
        **kwargs
    ):
        """
        初始化画布视图
        
        Args:
            parent: 父组件
            width: 画布宽度（像素）
            height: 画布高度（像素）
        """
        super().__init__(parent, width=width, height=height, bg=COLORS['canvas'], **kwargs)
        
        # 产线数据
        self.production_line: Optional[ProductionLine] = None
        self.simulation_state: Optional[SimulationState] = None

        # 交互回调
        self.on_reorder = on_reorder
        self.on_station_edit = on_station_edit
        self.on_station_select = on_station_select
        self.on_station_menu = on_station_menu
        
        # 图形元素存储（用于更新）
        self.station_rects: Dict[str, int] = {}  # 工序矩形ID
        self.station_texts: Dict[str, List[int]] = {}  # 工序文本ID列表
        self.connection_lines: Dict[str, int] = {}  # 连接线ID
        self.wip_labels: Dict[str, int] = {}  # WIP标签ID
        self.station_positions: Dict[str, tuple] = {}  # 工序中心坐标
        self.station_cards: Dict[str, int] = {}  # 节点卡片ID
        self.status_strips: Dict[str, int] = {}  # 顶部状态条ID
        self.status_texts: Dict[str, int] = {}
        self.bottleneck_badges: Dict[str, int] = {}  # 瓶颈徽标ID
        self.type_badge_id: Optional[int] = None
        self.batch_summary_ids: List[int] = []

        # 拖拽状态
        self._drag_station_id: Optional[str] = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_last_x = 0
        self._drag_last_y = 0
        self._drag_total_dx = 0
        self._drag_total_dy = 0
        self._drag_moved = False
        self._drop_indicator: Optional[int] = None
        self.drag_enabled = True  # 仿真运行中由主窗口置 False
        self._hover_station_id: Optional[str] = None
        self.selected_station_id: Optional[str] = None
        self._resize_after_id = None
        self._bottleneck_id: Optional[str] = None
        self._bottleneck_capacity = 0.0
        
        # 布局参数
        self.station_spacing = 200  # 工序间距（像素）
        self.station_y = height // 2  # 工序Y坐标（居中）
        self.start_x = 100  # 起始X坐标
        self.station_row_height = 150  # 每行工序的垂直间距（像素）
        self.max_stations_per_row = 0  # 每行最大工序数（自动计算）
        
        # 绑定事件
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<ButtonPress-1>", self._on_drag_start)
        self.bind("<B1-Motion>", self._on_drag_move)
        self.bind("<ButtonRelease-1>", self._on_drag_end)
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<Motion>", self._on_motion)
        
        # 绑定窗口大小变化事件（当画布大小变化时重新布局）
        self.bind("<Configure>", self._on_canvas_resize)
    
    def update_production_line(self, production_line: ProductionLine) -> None:
        """
        更新产线显示
        
        当产线数据变化时，重新绘制整个画布
        支持蛇形布局：当工序超出画布宽度时，自动换行显示
        
        Args:
            production_line: 产线对象
        """
        self.production_line = production_line
        
        # 清空画布
        self.delete("all")
        self.station_rects.clear()
        self.station_texts.clear()
        self.connection_lines.clear()
        self.wip_labels.clear()
        self.station_positions.clear()
        self.station_cards.clear()
        self.status_strips.clear()
        self.status_texts.clear()
        self.bottleneck_badges.clear()
        self.type_badge_id = None
        self.batch_summary_ids = []
        
        if not production_line or not production_line.stations:
            # 空状态：显示引导提示，避免空白画布无反馈
            self.delete("all")
            self.station_rects.clear()
            self.station_texts.clear()
            self.connection_lines.clear()
            self.wip_labels.clear()
            self.station_positions.clear()
            family = resolve_font_family()
            width = self.winfo_width() if self.winfo_width() > 1 else CANVAS_WIDTH
            height = self.winfo_height() if self.winfo_height() > 1 else CANVAS_HEIGHT
            self.create_text(
                width // 2,
                height // 2,
                text="暂无工序\n\n请点击左侧「添加」按钮，或使用「文件 → 快速配置向导」",
                font=(family, 14),
                fill=COLORS['text_secondary'],
                justify='center',
                tags=("empty_state",),
            )
            return

        self._draw_grid()
        
        # 计算每行最大工序数（基于画布宽度）
        # 使用实际画布宽度，而不是固定值
        canvas_width = self.winfo_width()
        if canvas_width <= 1:
            requested_width = self.winfo_reqwidth()
            canvas_width = requested_width if requested_width > 1 else CANVAS_WIDTH
        
        available_width = canvas_width - 2 * self.start_x  # 可用宽度（减去左右边距）
        self.max_stations_per_row = max(1, int(available_width / self.station_spacing))
        
        # 计算需要多少行（蛇形布局）
        num_stations = len(production_line.stations)
        num_rows = (num_stations + self.max_stations_per_row - 1) // self.max_stations_per_row
        
        # 计算起始Y坐标（居中显示所有行）
        # 使用实际画布高度
        canvas_height = self.winfo_height()
        if canvas_height <= 1:
            requested_height = self.winfo_reqheight()
            canvas_height = requested_height if requested_height > 1 else CANVAS_HEIGHT
        
        total_height = (num_rows - 1) * self.station_row_height
        start_y = (canvas_height - total_height) // 2
        start_y = max(75, start_y)  # leave room for the status legend
        
        # 蛇形布局：先计算所有工序的位置
        station_positions = {}  # 存储每个工序的坐标
        
        # 第一步：计算所有工序的位置（不绘制）
        # 蛇形布局逻辑：
        # - 偶数行（0, 2, 4...）：从左到右，col = 0, 1, 2, ...
        # - 奇数行（1, 3, 5...）：从右到左，col = max-1, max-2, ..., 0
        for i, station in enumerate(production_line.stations):
            # 计算当前工序所在的行和列（在行内的位置）
            row = i // self.max_stations_per_row
            col_in_row = i % self.max_stations_per_row  # 在行内的列索引（0-based）
            
            # 如果是奇数行，需要反向排列（蛇形效果）
            if row % 2 == 1:
                # 奇数行：从右到左
                # 例如：如果max_stations_per_row=4，col_in_row=0,1,2,3
                # 应该映射为：3,2,1,0
                actual_col = self.max_stations_per_row - 1 - col_in_row
            else:
                # 偶数行：从左到右
                # 例如：如果max_stations_per_row=4，col_in_row=0,1,2,3
                # 应该映射为：0,1,2,3
                actual_col = col_in_row
            
            # 计算坐标
            x = self.start_x + actual_col * self.station_spacing
            y = start_y + row * self.station_row_height
            
            # 保存位置
            station_positions[station.id] = (x, y)
        self.station_positions = station_positions
        
        # One bottleneck calculation per full redraw, rather than per station.
        bottleneck = production_line.find_bottleneck()
        self._bottleneck_id = bottleneck.id if bottleneck else None
        self._bottleneck_capacity = bottleneck.get_capacity() if bottleneck else 0.0

        # 第二步：绘制所有工序节点
        for station in production_line.stations:
            pos = station_positions[station.id]
            self._draw_station(station, pos[0], pos[1])
        
        # 第三步：按顺序绘制连接线（确保箭头方向正确）
        for i in range(len(production_line.stations) - 1):
            current_station = production_line.stations[i]
            next_station = production_line.stations[i + 1]
            
            current_pos = station_positions[current_station.id]
            next_pos = station_positions[next_station.id]
            
            # 绘制连接线（从当前工序到下一个工序）
            self._draw_connection_curved(current_station.id, next_station.id, current_pos, next_pos)

        self._draw_type_badge()
        self._draw_status_legend(canvas_width)
        if self.selected_station_id in self.station_cards:
            self.highlight_station(self.selected_station_id)
        self._draw_batch_summary()

    def _draw_type_badge(self) -> None:
        """绘制生产类型徽标（V1.3 流程型布局提示）"""
        if not self.production_line:
            return
        family = resolve_font_family()
        label_map = {
            "assembly": "类型：烟弹组装",
            "liquid_filling": "类型：烟油灌装（罐→灌装→QC）",
            "pouch_packaging": "类型：尼古丁袋高速包装",
        }
        text = label_map.get(self.production_line.production_type.value, "")
        self.type_badge_id = self.create_text(
            12, 12,
            text=text,
            anchor=tk.NW,
            font=(family, 11, 'bold'),
            fill=COLORS['text_secondary'],
            tags=("type_badge",),
        )

    def _draw_batch_summary(self) -> None:
        """绘制批次状态摘要（简化批次甘特信息）"""
        if not self.production_line or not self.production_line.batches:
            return
        family = resolve_font_family()
        canvas_width = self.winfo_width() if self.winfo_width() > 1 else CANVAS_WIDTH
        lines = [f"{b.id}: {b.status.value}" for b in self.production_line.batches]
        text = "\n".join(lines)
        self.batch_summary_ids = [
            self.create_text(
                canvas_width - 12, 12,
                text=text,
                anchor=tk.NE,
                font=(family, 10),
                fill=COLORS['text'],
                justify='right',
                tags=("batch_summary",),
            )
        ]

    def refresh_batch_summary(self) -> None:
        """仿真后刷新批次状态摘要（清洗状态高亮一并体现）"""
        if not self.production_line or not self.production_line.batches:
            return
        family = resolve_font_family()
        canvas_width = self.winfo_width() if self.winfo_width() > 1 else CANVAS_WIDTH
        lines = [f"{b.id}: {b.status.value}" for b in self.production_line.batches]
        text = "\n".join(lines)
        if self.batch_summary_ids:
            self.itemconfig(self.batch_summary_ids[0], text=text)
        else:
            self.batch_summary_ids = [
                self.create_text(
                    canvas_width - 12, 12,
                    text=text,
                    anchor=tk.NE,
                    font=(family, 10),
                    fill=COLORS['text'],
                    justify='right',
                    tags=("batch_summary",),
                )
            ]

    def apply_palette(self, palette: dict) -> None:
        """按主题色板刷新画布底色（V3.0）"""
        self.configure(bg=palette['canvas'])
        if self.production_line and self.production_line.stations:
            self._redraw_production_line()
    
    def _draw_station(self, station: Station, x: int, y: int) -> None:
        """
        绘制一个工序节点
        
        Args:
            station: 工序对象
            x: X坐标
            y: Y坐标（中心点）
        """
        # 计算矩形坐标
        rect_x1 = x - STATION_WIDTH // 2
        rect_y1 = y - STATION_HEIGHT // 2
        rect_x2 = x + STATION_WIDTH // 2
        rect_y2 = y + STATION_HEIGHT // 2

        # 获取状态颜色
        color = get_status_color(station.current_status)
        soft_color = status_soft(station.current_status)
        family = resolve_font_family()

        # 节点阴影（偏移浅色描边，模拟弱阴影）
        _round_rect(
            self,
            rect_x1 + 2, rect_y1 + 3, rect_x2 + 2, rect_y2 + 3,
            radius=10,
            fill=COLORS['border'],
            outline='',
            tags=(f"station_{station.id}", "station"),
        )

        # 节点卡片：浅色填充 + 圆角 + 边框
        card_id = _round_rect(
            self,
            rect_x1, rect_y1, rect_x2, rect_y2,
            radius=10,
            fill=soft_color,
            outline=COLORS['border'],
            width=1.5,
            tags=(f"station_{station.id}", "station"),
        )
        self.station_rects[station.id] = card_id
        self.station_cards[station.id] = card_id

        # 顶部状态条：实心状态色
        strip_id = _round_rect(
            self,
            rect_x1 + 4, rect_y1 + 4, rect_x2 - 4, rect_y1 + 14,
            radius=5,
            fill=color,
            outline='',
            tags=(f"station_{station.id}", "station"),
        )
        self.status_strips[station.id] = strip_id
        self.status_texts[station.id] = self.create_text(
            rect_x1 + 15, rect_y1 + 9,
            text=STATUS_SYMBOLS.get(station.current_status, '○'),
            font=(family, 8, 'bold'),
            fill=COLORS['text'],
            tags=(f"station_{station.id}", "station"),
        )

        # 绘制文本信息
        text_ids = []

        # 工序名称（第一行）
        text_id1 = self.create_text(
            x, y - 24,
            text=station.name,
            font=(family, 12, 'bold'),
            fill=COLORS['text'],
            tags=(f"station_{station.id}", "station")
        )
        text_ids.append(text_id1)

        # 工人数量（第二行）
        text_id2 = self.create_text(
            x, y - 4,
            text=f"👥 {station.worker_count}人",
            font=(family, 10),
            fill=COLORS['text'],
            tags=(f"station_{station.id}", "station")
        )
        text_ids.append(text_id2)

        # 产能（第三行）
        capacity = station.get_capacity()
        unit = self.production_line.get_unit() if self.production_line else "颗"
        text_id3 = self.create_text(
            x, y + 15,
            text=format_capacity_text(capacity, unit),
            font=(family, 10),
            fill=COLORS['text'],
            tags=(f"station_{station.id}", "station")
        )
        text_ids.append(text_id3)

        # 负荷率（第四行）
        if self.production_line:
            utilization = station.get_utilization(self._bottleneck_capacity)
            text_id4 = self.create_text(
                x, y + 35,
                text=f"📊 {utilization:.0%}",
                font=(family, 10),
                fill=COLORS['text'],
                tags=(f"station_{station.id}", "station")
            )
            text_ids.append(text_id4)

        self.station_texts[station.id] = text_ids

        # 瓶颈徽标：右上角红色圆形
        if self._bottleneck_id == station.id:
            badge_id = self.create_oval(
                rect_x2 - 18, rect_y1 + 18, rect_x2 - 4, rect_y1 + 32,
                fill=COLORS['danger_dark'],
                outline='',
                tags=(f"station_{station.id}", "station"),
            )
            self.create_text(
                rect_x2 - 11, rect_y1 + 25,
                text="!",
                fill=COLORS['surface'],
                font=(family, 10, 'bold'),
                tags=(f"station_{station.id}", "station"),
            )
            self.bottleneck_badges[station.id] = badge_id
    
    def _draw_connection_curved(self, from_station_id: str, to_station_id: str, pos1: tuple, pos2: tuple) -> None:
        """
        绘制连接线（支持曲线，用于蛇形布局）
        
        优化点：
        1. 连接线从节点边缘开始，不穿过节点
        2. 根据方向确定连接点（左/右/上/下边缘）
        3. 跨行时使用平滑曲线
        
        Args:
            from_station_id: 上游工序ID
            to_station_id: 下游工序ID
            pos1: 起始位置 (x, y) - 节点中心
            pos2: 结束位置 (x, y) - 节点中心
        """
        x1, y1 = pos1  # 上游工序中心
        x2, y2 = pos2  # 下游工序中心
        
        # 判断连接方向，确定连接点位置
        # 计算方向向量
        dx = x2 - x1
        dy = y2 - y1
        
        # 确定连接点（从节点边缘开始，避免穿过节点）
        # 根据方向确定从哪个边缘连接
        edge_offset_x = STATION_WIDTH // 2 + 5  # 边缘偏移量（多加5像素避免贴边）
        edge_offset_y = STATION_HEIGHT // 2 + 5
        
        if abs(dx) > abs(dy):
            # 主要是水平方向
            if dx > 0:
                # 从左到右：从右边缘连接到左边缘
                start_x = x1 + edge_offset_x
                start_y = y1
                end_x = x2 - edge_offset_x
                end_y = y2
            else:
                # 从右到左：从左边缘连接到右边缘
                start_x = x1 - edge_offset_x
                start_y = y1
                end_x = x2 + edge_offset_x
                end_y = y2
        else:
            # 主要是垂直方向
            if dy > 0:
                # 从上到下：从下边缘连接到上边缘
                start_x = x1
                start_y = y1 + edge_offset_y
                end_x = x2
                end_y = y2 - edge_offset_y
            else:
                # 从下到上：从上边缘连接到下边缘
                start_x = x1
                start_y = y1 - edge_offset_y
                end_x = x2
                end_y = y2 + edge_offset_y
        
        # 判断是否需要绘制曲线（跨行时）
        if abs(y2 - y1) > STATION_HEIGHT // 2:  # 跨行，需要曲线
            # 计算控制点，使曲线更平滑
            # 使用二次贝塞尔曲线，控制点在中间位置
            mid_x = (start_x + end_x) // 2
            mid_y = (start_y + end_y) // 2
            
            # 创建平滑的曲线路径
            points = []
            num_points = 30  # 曲线平滑度（增加点数使曲线更平滑）
            
            for i in range(num_points + 1):
                t = i / num_points
                # 二次贝塞尔曲线公式
                x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * mid_x + t ** 2 * end_x
                y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * mid_y + t ** 2 * end_y
                points.extend([x, y])
            
            # 绘制曲线
            line_id = self.create_line(
                *points,
                fill=COLORS['connection'],
                width=2,
                smooth=True,  # 平滑曲线
                tags=(f"connection_{from_station_id}_{to_station_id}", "connection")
            )
            
            # 在终点绘制箭头（计算箭头方向）
            arrow_length = 15
            arrow_width = 5
            
            # 计算箭头方向（指向下游工序）
            arrow_dx = end_x - points[-2]
            arrow_dy = end_y - points[-3]
            arrow_len = (arrow_dx ** 2 + arrow_dy ** 2) ** 0.5
            
            if arrow_len > 0:
                # 归一化方向向量
                arrow_dx = arrow_dx / arrow_len * arrow_length
                arrow_dy = arrow_dy / arrow_len * arrow_length
                
                # 计算箭头两个端点（垂直于箭头方向）
                perp_x = -arrow_dy / arrow_len * arrow_width
                perp_y = arrow_dx / arrow_len * arrow_width
                
                arrow_id = self.create_line(
                    end_x - arrow_dx + perp_x, end_y - arrow_dy + perp_y,
                    end_x, end_y,
                    end_x - arrow_dx - perp_x, end_y - arrow_dy - perp_y,
                    fill=COLORS['connection'],
                    width=2,
                    tags=(f"connection_{from_station_id}_{to_station_id}", "connection")
                )
        else:
            # 同一行或同一列，绘制直线
            line_id = self.create_line(
                start_x, start_y,
                end_x, end_y,
                fill=COLORS['connection'],
                width=2,
                arrow=tk.LAST,  # 在终点绘制箭头
                arrowshape=(10, 12, 3),
                tags=(f"connection_{from_station_id}_{to_station_id}", "connection")
            )
        
        self.connection_lines[f"{from_station_id}_{to_station_id}"] = line_id
        
        # 智能定位WIP数量，避免覆盖箭头
        # 计算连接线的主方向
        abs_dx = abs(end_x - start_x)
        abs_dy = abs(end_y - start_y)
        
        if abs_dy > abs_dx:  # 垂直方向连接（包括曲线连接）
            # 垂直连接，WIP显示在连接线旁边
            # 计算中间点
            mid_x = (start_x + end_x) // 2
            mid_y = (start_y + end_y) // 2
            
            # WIP显示在连接线右侧，避免覆盖箭头
            wip_x = mid_x + 20  # 向右偏移20像素
            wip_y = mid_y
        else:  # 水平方向连接
            # 水平连接，WIP显示在连接线上方，距离连接线有一定间距
            wip_x = (start_x + end_x) // 2
            wip_y = (start_y + end_y) // 2 - 25  # 向上偏移25像素，增加间距
        
        wip_id = self.create_text(
            wip_x, wip_y,
            text=f"WIP: 0 {self.production_line.get_unit() if self.production_line else '颗'}",
            font=(resolve_font_family(), 9),
            fill=COLORS['chart_primary'],
            tags=(f"wip_{from_station_id}_{to_station_id}", "wip")
        )
        self.wip_labels[f"{from_station_id}_{to_station_id}"] = wip_id
    
    def update_simulation_state(self, state: SimulationState) -> None:
        """
        更新仿真状态显示
        
        当仿真运行时，定期调用此方法更新显示
        只更新变化的部分，而不是重绘整个画布（性能优化）
        
        Args:
            state: 仿真状态对象
        """
        self.simulation_state = state
        
        if not self.production_line:
            return
        
        # 更新各工序的状态
        for station in self.production_line.stations:
            station_state = state.station_states.get(station.id, {})

            # 更新状态颜色
            status = station_state.get('status', 'idle')
            color = get_status_color(status)

            # 更新卡片底色（浅色）与顶部状态条（实色）
            if station.id in self.station_cards:
                self.itemconfig(
                    self.station_cards[station.id],
                    fill=status_soft(status),
                )
            if station.id in self.status_strips:
                self.itemconfig(self.status_strips[station.id], fill=color)
            if station.id in self.status_texts:
                self.itemconfig(
                    self.status_texts[station.id],
                    text=STATUS_SYMBOLS.get(status, status),
                    fill=COLORS['text'],
                )

            # 更新产能文本（如果有变化）
            if station.id in self.station_texts and len(self.station_texts[station.id]) >= 3:
                capacity = station_state.get('capacity', station.get_capacity())
                unit = self.production_line.get_unit() if self.production_line else "颗"
                self.itemconfig(
                    self.station_texts[station.id][2],
                    text=format_capacity_text(capacity, unit),
                )
        
        # 更新WIP显示
        for i in range(len(self.production_line.stations) - 1):
            from_station = self.production_line.stations[i]
            to_station = self.production_line.stations[i + 1]
            
            key = f"{from_station.id}_{to_station.id}"
            if key in self.wip_labels:
                wip = state.station_states.get(to_station.id, {}).get('wip', 0)
                unit = self.production_line.get_unit() if self.production_line else "颗"
                self.itemconfig(self.wip_labels[key], text=f"WIP: {wip} {unit}")

        # 刷新批次状态（烟油）
        if self.production_line.production_type and self.production_line.production_type.value == "liquid_filling":
            self.refresh_batch_summary()

    def _draw_status_legend(self, canvas_width: int) -> None:
        """A compact legend that does not rely on color alone."""
        family = resolve_font_family()
        x = max(12, canvas_width - 340)
        for status in ('idle', 'running', 'waiting', 'blocked', 'changeover'):
            self.create_oval(x, 11, x + 9, 20, fill=get_status_color(status), outline='')
            self.create_text(
                x + 13, 15, anchor=tk.W, text=STATUS_LABELS[status],
                font=(family, 8), fill=COLORS['text_secondary'],
            )
            x += 66
    
    def highlight_station(self, station_id: str) -> None:
        """
        高亮显示指定工序
        
        Args:
            station_id: 工序ID
        """
        self.selected_station_id = station_id
        # 先取消所有高亮
        for station in self.production_line.stations if self.production_line else []:
            if station.id in self.station_cards:
                self.itemconfig(
                    self.station_cards[station.id],
                    outline=COLORS['border'],
                    width=1.5,
                )

        # 高亮指定工序
        if station_id in self.station_cards:
            self.itemconfig(
                self.station_cards[station_id],
                outline=COLORS['primary'],
                width=3,
            )
    
    def _on_double_click(self, event: tk.Event) -> None:
        """
        双击事件处理
        
        双击工序节点时，触发编辑对话框
        
        Args:
            event: 鼠标事件
        """
        station_id = self._station_id_at(event.x, event.y)
        if station_id and self.on_station_edit:
            self.on_station_edit(station_id)

    def _station_id_at(self, x: int, y: int) -> Optional[str]:
        """获取坐标处的工序 ID（通过 Canvas tags 查找）"""
        items = self.find_overlapping(x - 2, y - 2, x + 2, y + 2)
        if not items:
            closest = self.find_closest(x, y)
            if closest:
                items = [closest[0]]
        for item in items:
            for tag in self.gettags(item):
                if tag.startswith("station_"):
                    return tag.replace("station_", "")
        return None

    def _on_drag_start(self, event: tk.Event) -> None:
        """拖拽开始：记录被拖动的工序与起始位置"""
        if not self.drag_enabled:
            return
        station_id = self._station_id_at(event.x, event.y)
        self._drag_station_id = station_id
        self._drag_start_x = event.x
        self._drag_start_y = event.y
        self._drag_last_x = event.x
        self._drag_last_y = event.y
        self._drag_total_dx = 0
        self._drag_total_dy = 0
        self._drag_moved = False

    def _on_drag_move(self, event: tk.Event) -> None:
        """拖拽移动：移动工序节点及其文本，提供视觉反馈"""
        if not self._drag_station_id:
            return
        dx = event.x - self._drag_last_x
        dy = event.y - self._drag_last_y
        self._drag_last_x = event.x
        self._drag_last_y = event.y
        self._drag_total_dx += dx
        self._drag_total_dy += dy
        if abs(self._drag_total_dx) > 5 or abs(self._drag_total_dy) > 5:
            self._drag_moved = True
        self.move(f"station_{self._drag_station_id}", dx, dy)
        self._update_drop_indicator(event.x, event.y)

    def _update_drop_indicator(self, x: int, y: int) -> None:
        """绘制拖拽落点指示（高亮最近目标工序卡片）"""
        if self._drop_indicator is not None:
            self.delete(self._drop_indicator)
            self._drop_indicator = None
        if not self._drag_station_id or not self.station_positions:
            return

        nearest_id = None
        nearest_dist = float("inf")
        for other_id, (ox, oy) in self.station_positions.items():
            if other_id == self._drag_station_id:
                continue
            dist = (x - ox) ** 2 + (y - oy) ** 2
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_id = other_id
        if nearest_id is None or nearest_id not in self.station_cards:
            return

        x1, y1, x2, y2 = self.bbox(self.station_cards[nearest_id])
        self._drop_indicator = self.create_rectangle(
            x1 - 3, y1 - 3, x2 + 3, y2 + 3,
            outline=COLORS['primary'],
            width=2,
            dash=(4, 4),
            tags=("drop_indicator",),
        )

    def _on_drag_end(self, event: tk.Event) -> None:
        """拖拽结束：若发生位移则计算新顺序并触发重排回调"""
        if self._drop_indicator is not None:
            self.delete(self._drop_indicator)
            self._drop_indicator = None
        station_id = self._drag_station_id
        moved = self._drag_moved
        self._drag_station_id = None

        if not station_id or not moved or not self.on_reorder:
            # 未发生拖拽视为单击选择
            if station_id and not moved and self.on_station_select:
                self.on_station_select(station_id)
            return

        if station_id not in self.station_positions or not self.production_line:
            return

        old_x, old_y = self.station_positions[station_id]
        final_x = old_x + self._drag_total_dx
        final_y = old_y + self._drag_total_dy

        order_ids = [s.id for s in self.production_line.stations]
        new_order = compute_reorder_order(
            order_ids, station_id, self.station_positions, final_x, final_y
        )
        if new_order and new_order != order_ids:
            self.on_reorder(new_order)

    def _on_motion(self, event: tk.Event) -> None:
        """鼠标悬停反馈：工序节点上显示手型光标"""
        station_id = self._station_id_at(event.x, event.y)
        self.configure(cursor='hand2' if station_id and self.drag_enabled else 'arrow')
        if station_id == self._hover_station_id:
            return
        # 重置上一悬停节点
        if self._hover_station_id and self._hover_station_id in self.station_cards:
            selected = self._hover_station_id == self.selected_station_id
            self.itemconfig(
                self.station_cards[self._hover_station_id],
                outline=COLORS['primary'] if selected else COLORS['border'],
                width=3 if selected else 1.5,
            )
        self._hover_station_id = station_id
        if (station_id and station_id in self.station_cards and self.drag_enabled
                and station_id != self.selected_station_id):
            self.itemconfig(
                self.station_cards[station_id],
                outline=COLORS['focus'],
                width=2,
            )

    def _draw_grid(self) -> None:
        """绘制画布网格底纹（V3.0）"""
        width = self.winfo_width() if self.winfo_width() > 1 else CANVAS_WIDTH
        height = self.winfo_height() if self.winfo_height() > 1 else CANVAS_HEIGHT
        spacing = 40
        color = COLORS['grid']
        for x in range(0, width, spacing):
            self.create_line(x, 0, x, height, fill=color, tags=("grid",))
        for y in range(0, height, spacing):
            self.create_line(0, y, width, y, fill=color, tags=("grid",))

    def _on_right_click(self, event: tk.Event) -> None:
        """右键菜单：弹出工序操作菜单"""
        station_id = self._station_id_at(event.x, event.y)
        if station_id and self.on_station_menu:
            self.on_station_menu(station_id, event.x_root, event.y_root)
    
    def _on_canvas_resize(self, event: tk.Event) -> None:
        """
        画布大小变化事件处理
        
        当画布大小变化时（窗口调整大小），重新计算布局并重绘
        
        Args:
            event: Configure事件，包含新的画布大小信息
        """
        # 只有当画布实际大小发生变化时才重绘
        # 避免初始化时的多次重绘
        if event.width <= 1 or event.height <= 1:
            return
        
        # 如果已有产线数据，重新绘制
        if self.production_line:
            if self._resize_after_id is not None:
                self.after_cancel(self._resize_after_id)
            self._resize_after_id = self.after(120, self._redraw_production_line)
    
    def _redraw_production_line(self) -> None:
        """
        重新绘制产线（内部方法，用于窗口大小变化时）
        
        这个方法与update_production_line类似，但专门用于窗口大小变化时的重绘
        """
        self._resize_after_id = None
        if not self.production_line:
            return
        
        # 重新绘制产线（会重新计算布局）
        self.update_production_line(self.production_line)
        
        # 如果有仿真状态，也更新状态显示
        if self.simulation_state:
            self.update_simulation_state(self.simulation_state)
