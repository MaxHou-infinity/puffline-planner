"""
版本号定义 - 全项目唯一版本入口

所有显示版本号的位置（窗口标题、关于对话框、README）都应引用此文件，
避免多处维护导致版本不一致。
"""

__version__ = "3.3.5"
VERSION_STRING = f"v{__version__}"
PRODUCT_NAME = "PuffLine Planner"

# Bug 反馈与资助入口（可在发布前替换为个人链接）
BUG_REPORT_URL = "https://github.com/MaxHou-infinity/puffline-planner/issues"
WECHAT_QR_PATH = "assets/wechat_qr.png"  # 微信赞赏码（PNG/GIF）
WECHAT_QR_SMALL_PATH = "assets/wechat_qr_small.png"  # 关于对话框内嵌小图
