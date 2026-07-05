#!/bin/bash
cd "$(dirname "$0")/frontend"

# 补全常见的 Node 安装路径
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
[ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh"

if ! command -v npm >/dev/null 2>&1; then
  echo ""
  echo "❌ 没有找到 Node.js(npm 命令不存在)。"
  echo "请打开 https://nodejs.org 下载安装 LTS 版本,装完后重新双击本脚本。"
  echo ""
  read -n 1 -s -p "按任意键关闭..."
  exit 1
fi
echo "使用 Node $(node --version)"

if [ ! -d node_modules ]; then
  echo "首次运行:安装依赖..."
  npm install || { echo "❌ 依赖安装失败,请把上面的报错发给 Claude"; read -n 1 -s -p "按任意键关闭..."; exit 1; }
fi
echo "正在启动前端,成功后浏览器打开 http://localhost:1420"
npm run dev
