#!/bin/bash
echo "== LEAPS 候选列表(来自股票池 美股/港股) =="
curl -s http://127.0.0.1:8000/api/leaps/watchlist/candidates | python3 -m json.tool 2>/dev/null | head -30
echo ""
echo "== 当前白名单 =="
curl -s http://127.0.0.1:8000/api/leaps/watchlist | python3 -m json.tool 2>/dev/null | head -20
echo ""
read -n 1 -s -p "按任意键关闭..."
