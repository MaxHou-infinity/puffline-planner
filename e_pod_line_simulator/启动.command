#!/bin/bash
# ============================================================
# PuffLine Planner - 普通用户启动器（macOS）
# 双击本文件即可启动；首次运行会自动安装依赖（需网络）。
# 如提示"无法打开"，请在访达中右键本文件 → 打开。
# ============================================================

cd "$(dirname "$0")"

PY=""
for cand in \
  "/opt/homebrew/Caskroom/miniconda/base/bin/python3" \
  "$HOME/miniconda3/bin/python3" \
  "$HOME/anaconda3/bin/python3" \
  "/usr/local/bin/python3" \
  "python3"
do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c "import simpy, pandas, openpyxl, reportlab, ttkbootstrap; from PIL import Image; from importlib.metadata import version; assert version('ttkbootstrap') == '1.10.1'; Image.Resampling.LANCZOS" >/dev/null 2>&1; then
      PY="$cand"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  # 没有可用环境：找任意 Python 3 并尝试安装依赖
  for cand in \
    "/opt/homebrew/Caskroom/miniconda/base/bin/python3" \
    "$HOME/miniconda3/bin/python3" \
    "$HOME/anaconda3/bin/python3" \
    "python3"
  do
    if command -v "$cand" >/dev/null 2>&1; then
      PY="$cand"
      break
    fi
  done

  if [ -z "$PY" ]; then
    osascript -e 'display dialog "未找到 Python 3。请先安装 Miniconda 或 Python 3.8+，再双击本文件启动。" buttons {"知道了"} default button "知道了"' >/dev/null 2>&1
    exit 1
  fi

  osascript -e 'display dialog "启动需要安装或更新依赖（仿真、报表及现代界面控件），是否继续？" buttons {"取消", "安装"} default button "安装"' >/dev/null 2>&1 || exit 1
  "$PY" -m pip install -r requirements.txt || {
    osascript -e 'display dialog "依赖安装失败，请检查网络后重试。" buttons {"知道了"} default button "知道了"' >/dev/null 2>&1
    exit 1
  }
fi

exec "$PY" run.py
