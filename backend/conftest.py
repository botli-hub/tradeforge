"""backend/ 根目录的 test_*.py 是手工联调脚本,不是单测。

它们在模块级连接 OpenD / 本地后端 / Finnhub;被 pytest 收集(例如在 backend/ 或仓库根目录
直接跑 `pytest`)时,futu OpenQuoteContext 在无 OpenD 时会无限重连,整个测试挂起。
显式点名运行(`pytest test_futu_option_snapshot.py`)不受影响。
"""
collect_ignore_glob = ["test_*.py"]
