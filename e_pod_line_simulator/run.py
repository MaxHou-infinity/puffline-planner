#!/usr/bin/env python3
"""
程序启动脚本

快速启动程序的便捷脚本
可以直接运行：python run.py
"""

import sys
import os

# 确保项目根目录在模块搜索路径中（源码运行时）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 运行主程序
if __name__ == '__main__':
    from src.main import main
    main()
