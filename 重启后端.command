#!/bin/bash
echo "停止旧的后端进程..."
pkill -f "uvicorn app.main:app" 2>/dev/null
sleep 1
exec "$(dirname "$0")/启动后端.command"
