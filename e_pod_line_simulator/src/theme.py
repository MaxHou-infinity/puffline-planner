"""
设计令牌与主题 - V1.2.0 视觉体系唯一来源

所有颜色、字体、间距、状态色、报警图标集中定义在这里，
GUI 组件通过引用令牌保持视觉一致，避免散落硬编码。
"""

import platform
import tkinter as tk


# ==================== 设计令牌 ====================

COLORS = {
    'bg': '#F4F6F9',          # 应用背景
    'surface': '#FFFFFF',     # 卡片/面板表面
    'border': '#E2E7EE',      # 边框
    'text': '#202B3C',        # 主文本
    'text_secondary': '#637084',  # 次要文本
    'primary': '#285DE5',     # 主色
    'primary_hover': '#204EC3',
    'primary_soft': '#EDF2FF',
    'success': '#34C759',
    'danger': '#FF3B30',
    'warning': '#FF9F0A',
    'info': '#0A84FF',
    'idle': '#E5E5EA',
    'danger_dark': '#B3261E',
    'connection': '#637084',
    'chart_primary': '#1F6FEB',
    'chart_secondary': '#2DA44E',
    'chart_reference': '#B3261E',
    'chart_text': '#555555',
    'chart_muted': '#888888',
    'chart_baseline': '#EAF2FF',
    'button_primary': '#285DE5',
    'button_primary_hover': '#204EC3',
    'danger_soft': '#FDEBEA',
    'danger_soft_hover': '#F8D7D5',
    'disabled_text': '#B9C4D4',
}

# 工序状态色（V1.2：柔和、语义清晰）
STATUS_COLORS = {
    'idle': COLORS['idle'],
    'running': COLORS['success'],
    'blocked': COLORS['danger'],
    'waiting': COLORS['warning'],
    'changeover': COLORS['info'],
}
STATUS_LABELS = {
    'idle': '空闲',
    'running': '运行',
    'blocked': '堵塞',
    'waiting': '等待',
    'changeover': '换型',
}
STATUS_SYMBOLS = {
    'idle': '○',
    'running': '▶',
    'blocked': '■',
    'waiting': '…',
    'changeover': '↻',
}

# 工序状态浅色填充（节点卡片底色，避免整块高饱和）
STATUS_SOFT = {
    'idle': '#F0F1F3',
    'running': '#E7F8EE',
    'blocked': '#FDEBEA',
    'waiting': '#FFF3E0',
    'changeover': '#E6F2FE',
}

# 报警级别图标
ALERT_ICONS = {
    'critical': '⛔',
    'warning': '⚠️',
    'info': 'ℹ️',
}

ALERT_COLORS = {
    'critical': COLORS['danger'],
    'warning': COLORS['warning'],
    'info': COLORS['info'],
}

# 间距刻度（4/8/12/16/24）
SPACING = {
    'xs': 4,
    'sm': 8,
    'md': 12,
    'lg': 16,
    'xl': 24,
}

# V3.0 Design Tokens 2.0
RADIUS = {'sm': 8, 'md': 12, 'lg': 16}
SHADOW = {'card': (0, 1, 2), 'popup': (0, 4, 12)}
MOTION = {'fast': 120, 'normal': 180}

# 统一浅色主题色板（V3.0，深色模式已取消）
THEMES = {
    'light': {
        **COLORS,
        'canvas': '#FFFFFF',
        'grid': '#EDEFF3',
        'hover': '#F0F3F8',
        'focus': '#285DE5',
        'toast_bg': '#202B3C',
        'toast_fg': '#FFFFFF',
    },
}


def status_color(status: str) -> str:
    """返回工序状态色"""
    return STATUS_COLORS.get(status, STATUS_COLORS['idle'])


def status_soft(status: str) -> str:
    """返回工序状态浅色填充"""
    return STATUS_SOFT.get(status, STATUS_SOFT['idle'])


def alert_color(severity: str) -> str:
    """返回报警级别色"""
    return ALERT_COLORS.get(severity, '#CCCCCC')


def resolve_font_family() -> str:
    """
    按平台解析中文字体族

    Returns:
        str: 优先 PingFang SC（macOS）/ 微软雅黑（Windows），回退 Arial
    """
    system = platform.system()
    if system == 'Darwin':
        return 'PingFang SC'
    if system == 'Windows':
        return 'Microsoft YaHei'
    return 'Arial'


def apply_theme(root) -> dict:
    """
    将设计令牌应用到 Tk 根窗口

    - 使用固定版本ttkbootstrap现代主题，保留原生Tk业务交互
    - 配置字体/颜色/间距/按钮/Treeview 等基础组件

    Args:
        root: tk.Tk 根窗口

    Returns:
        dict: 生效的主题色板（统一浅色）
    """
    global COLORS
    palette = THEMES['light']
    COLORS.update(palette)  # 就地更新模块级 COLORS，兼容既有引用

    from tkinter import ttk
    from ttkbootstrap import Style
    from ttkbootstrap.style import ThemeDefinition

    family = resolve_font_family()
    style = Style(theme='litera')
    if 'puffline' not in style.theme_names():
        style.register_theme(ThemeDefinition(
            name='puffline', themetype='light', colors={
                'primary': COLORS['primary'], 'secondary': COLORS['text_secondary'],
                'success': COLORS['success'], 'info': COLORS['info'],
                'warning': COLORS['warning'], 'danger': COLORS['danger_dark'],
                'light': COLORS['bg'], 'dark': COLORS['text'],
                'bg': COLORS['surface'], 'fg': COLORS['text'],
                'selectbg': COLORS['primary_soft'], 'selectfg': COLORS['primary'],
                'border': COLORS['border'], 'inputfg': COLORS['text'],
                'inputbg': COLORS['surface'], 'active': COLORS['hover'],
            }))
    style.theme_use('puffline')
    root._puffline_style = style  # 保持主题图像资源与当前Tk解释器同生命周期。
    style.configure(
        '.',
        font=(family, -14),
        background=COLORS['surface'],
        foreground=COLORS['text'],
    )
    style.configure('TFrame', background=COLORS['surface'])
    style.configure('TLabel', background=COLORS['surface'], foreground=COLORS['text'])
    style.configure('TLabelframe', background=COLORS['surface'],
                    bordercolor=COLORS['surface'], borderwidth=0, relief='flat')
    style.configure(
        'TLabelframe.Label',
        background=COLORS['surface'],
        foreground=COLORS['text'],
        font=(family, -14, 'bold'),
    )
    style.configure('TButton', font=(family, -14), padding=(12, 8),
                    background=COLORS['surface'], foreground=COLORS['text'],
                    bordercolor=COLORS['border'], borderwidth=1, relief='flat')
    style.map('TButton', background=[('disabled', COLORS['bg']), ('active', COLORS['hover'])],
              foreground=[('disabled', COLORS['text_secondary'])],
              bordercolor=[('active', COLORS['border'])])
    # 主操作使用蓝色，其余操作保持轻量。
    style.configure(
        'Primary.TButton',
        background=COLORS['button_primary'],
        foreground=COLORS['surface'],
        font=(family, 11),
        padding=(12, 6),
    )
    style.map(
        'Primary.TButton',
        background=[('active', COLORS['button_primary_hover'])],
        foreground=[('disabled', COLORS['disabled_text'])],
    )
    # 危险按钮：软红色底 + 深红文字，避免文字/背景冲突
    style.configure(
        'Danger.TButton',
        background=COLORS['danger_soft'],
        foreground=COLORS['danger_dark'],
        bordercolor=COLORS['danger_dark'],
        padding=(12, 6),
    )
    style.map('Danger.TButton', background=[('active', COLORS['danger_soft_hover'])])
    style.configure(
        'Secondary.TButton',
        background=COLORS['surface'],
        foreground=COLORS['text'],
        padding=(12, 6),
    )
    style.map('Secondary.TButton', background=[('active', COLORS['hover'])])
    style.configure(
        'Accent.TButton', background=COLORS['primary'], foreground=COLORS['surface']
    )
    style.map(
        'Accent.TButton',
        background=[('active', COLORS['primary_hover'])],
        foreground=[('disabled', COLORS['disabled_text'])],
    )
    style.configure(
        'Treeview',
        background=COLORS['surface'],
        fieldbackground=COLORS['surface'],
        foreground=COLORS['text'],
        rowheight=40,
        borderwidth=0,
        relief='flat',
        font=(family, -14),
        selectbackground=COLORS['primary_soft'],
        selectforeground=COLORS['primary'],
    )
    style.configure(
        'Treeview.Heading',
        font=(family, -12),
        relief='flat',
        padding=(8, 10),
        background=COLORS['surface'],
        foreground=COLORS['text'],
    )
    style.configure(
        'TEntry',
        fieldbackground=COLORS['surface'],
        bordercolor=COLORS['border'],
        padding=(9, 7),
        relief='flat',
    )
    style.map(
        'TEntry',
        bordercolor=[('focus', COLORS['focus'])],
        fieldbackground=[('disabled', COLORS['bg'])],
    )
    style.configure('TCombobox', fieldbackground=COLORS['surface'], bordercolor=COLORS['border'])
    style.map('TCombobox', bordercolor=[('focus', COLORS['focus'])])

    style.configure('TCombobox', padding=(9, 7), arrowsize=14)
    style.map('TCombobox', fieldbackground=[('readonly', COLORS['surface'])],
              foreground=[('readonly', COLORS['text'])])
    style.map('Treeview', background=[('selected', COLORS['primary_soft'])],
              foreground=[('selected', COLORS['primary'])])
    style.configure('TSeparator', background=COLORS['border'])
    style.configure('Heading.TLabel', font=(family, -19, 'bold'))
    style.configure('Section.TLabel', font=(family, -13, 'bold'))
    from src.ui_controls import install_control_elements
    install_control_elements(root, style, COLORS)
    root.configure(bg=COLORS['bg'])
    root.option_add('*Background', COLORS['surface'])
    root.option_add('*Foreground', COLORS['text'])
    root.option_add('*Text.background', COLORS['surface'])
    root.option_add('*Text.foreground', COLORS['text'])
    root.option_add('*Canvas.background', COLORS['surface'])
    root.option_add('*Listbox.background', COLORS['surface'])
    root.option_add('*selectBackground', COLORS['primary_soft'])
    root.option_add('*selectForeground', COLORS['primary'])
    root.option_add('*Font', (family, 11))
    return palette



class ToolTip:
    """
    轻量 Tooltip：悬停显示提示文本

    使用方式：
        ToolTip(button, "开始仿真（空格）")
    """

    def __init__(self, widget, text: str, delay_ms: int = 400):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip_window = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self) -> None:
        self._after_id = None
        if self._tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip_window = tk.Toplevel(self.widget)
        self._tip_window.wm_overrideredirect(True)
        self._tip_window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip_window,
            text=self.text,
            justify='left',
            bg=COLORS['text'],
            fg=COLORS['surface'],
            padx=6,
            pady=3,
            font=(resolve_font_family(), 10),
        ).pack()

    def _hide(self, event=None) -> None:
        self._cancel()
        if self._tip_window is not None:
            self._tip_window.destroy()
            self._tip_window = None

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


class Toast:
    """
    轻量 Toast 反馈（V3.0）

    使用方式：
        show_toast(root, "已保存")
    """

    def __init__(self, root, text: str, duration_ms: int = 2200):
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        label = tk.Label(
            top,
            text=text,
            bg=COLORS['toast_bg'],
            fg=COLORS['toast_fg'],
            padx=16,
            pady=10,
            font=(resolve_font_family(), 11),
        )
        label.pack()
        top.update_idletasks()
        x = root.winfo_rootx() + root.winfo_width() - top.winfo_width() - 24
        y = root.winfo_rooty() + root.winfo_height() - top.winfo_height() - 48
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.after(duration_ms, top.destroy)


def show_toast(root, text: str, duration_ms: int = 2200) -> None:
    """显示 Toast 反馈"""
    Toast(root, text, duration_ms)
