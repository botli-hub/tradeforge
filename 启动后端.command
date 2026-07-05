#!/bin/bash
cd "$(dirname "$0")/backend"

# 寻找 Python 3.10+
PY=""
for c in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null)
    if [ -n "$v" ] && [ "$v" -ge 310 ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo ""
  echo "❌ 没有找到 Python 3.10 或更高版本(当前系统默认是 3.8,太老)。"
  echo "请打开 https://www.python.org/downloads/ 下载安装最新版 Python,"
  echo "安装完成后重新双击本脚本即可。"
  echo ""
  read -n 1 -s -p "按任意键关闭..."
  exit 1
fi
echo "使用 $PY ($("$PY" --version))"

# 旧虚拟环境版本太低则重建
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; assert sys.version_info>=(3,10)' 2>/dev/null; then
  echo "检测到旧虚拟环境版本过低,正在重建..."
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  echo "首次运行:创建虚拟环境并安装依赖(需几分钟)..."
  "$PY" -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt || { echo "❌ 依赖安装失败,请把上面的报错发给 Claude"; read -n 1 -s -p "按任意键关闭..."; exit 1; }
echo ""
echo "正在启动后端(看到 Uvicorn running 才算成功): http://127.0.0.1:8000"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
