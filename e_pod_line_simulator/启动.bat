@echo off
chcp 65001 >nul
rem ============================================================
rem PuffLine Planner - 普通用户启动器（Windows）
rem 双击本文件即可启动；首次运行会自动安装依赖（需网络）。
rem ============================================================
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
  echo 未找到 Python，请先安装 Python 3.8+：https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import simpy, pandas, openpyxl, reportlab, ttkbootstrap; from PIL import Image; from importlib.metadata import version; assert version('ttkbootstrap') == '1.10.1'; Image.Resampling.LANCZOS" >nul 2>nul
if errorlevel 1 (
  echo 正在安装或更新运行依赖，请稍候...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

python run.py
