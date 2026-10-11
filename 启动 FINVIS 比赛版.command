#!/bin/bash
set -eu
TASK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$TASK_DIR"
if ! command -v python3 >/dev/null 2>&1; then
  echo "无法启动比赛版：请先安装 Python 3。"; read -r -p "按回车关闭"; exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1
exec python3 -B -m finvis.competition_launcher "$@"
