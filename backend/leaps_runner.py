#!/usr/bin/env python3
"""LEAPS Put 权利金卖出信号监控独立运行脚本

用法:
  python leaps_runner.py              # EOD 全量扫描（默认）
  python leaps_runner.py --intraday   # 盘中增量扫描（用最新价判断 S1）
  python leaps_runner.py --symbol AAPL  # 仅扫描指定标的

运行时机:
  - EOD: 美股收盘后（ET 16:30 后）运行，用当日最高价判断 S1
  - 盘中: 每 30 分钟 cron 触发，用最新价判断 S1（建议仅一级信号 EMA50 启用）

Crontab 示例（服务器时区为 UTC）：
  # EOD 扫描（ET 16:45 = UTC 21:45）
  45 21 * * 1-5 cd /path/to/tradeforge/backend && python leaps_runner.py >> logs/leaps_eod.log 2>&1

  # 盘中扫描（ET 09:30~16:00 = UTC 13:30~20:00）每 30 分钟
  30 13-20 * * 1-5 cd /path/to/tradeforge/backend && python leaps_runner.py --intraday >> logs/leaps_intraday.log 2>&1
"""
import argparse
import logging
import sys
from pathlib import Path

# 确保 backend 目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("leaps_runner")


def load_config() -> dict:
    """配置统一从本地数据库读取(设置页保存),不再依赖 yaml 文件。"""
    from app.api.leaps import _load_config
    return _load_config()


def main():
    parser = argparse.ArgumentParser(description="LEAPS Put 权利金卖出信号监控")
    parser.add_argument("--intraday", action="store_true", help="盘中模式（用最新价）")
    parser.add_argument("--symbol", type=str, default=None, help="仅扫描指定标的")
    parser.add_argument("--dry-run", action="store_true", help="仅输出信号，不推送 Telegram")
    args = parser.parse_args()

    # 初始化 DB（确保表存在）
    from app.data.database import init_db
    init_db()

    cfg = load_config()

    from app.core.leaps_monitor import LeapsMonitor
    from app.services.notifier import TelegramNotifier
    from app.data import leaps_repository as repo

    monitor = LeapsMonitor(cfg)
    notifier = TelegramNotifier.from_config(cfg) if not args.dry_run else None

    mode = "盘中增量" if args.intraday else "EOD 全量"
    logger.info("启动 LEAPS 扫描 — %s", mode + (f"（{args.symbol}）" if args.symbol else ""))

    if args.symbol:
        item = repo.get_watchlist_item(args.symbol)
        if not item:
            logger.error("%s 不在监控白名单中", args.symbol)
            sys.exit(1)
        signals = monitor.scan_symbol(args.symbol, item["floor_price"], is_intraday=args.intraday)
    else:
        signals = monitor.scan_all(is_intraday=args.intraday)

    if not signals:
        logger.info("本次扫描无信号触发")
    else:
        logger.info("触发信号 %d 条", len(signals))
        for sig in signals:
            logger.info(
                "[%s] %s %s%dP — IV rank %.1f, 触发价 %.2f 上穿 %s(%.2f)",
                sig.signal_level, sig.symbol, sig.expiry,
                int(sig.strike), sig.iv_rank,
                sig.trigger_price, sig.ema_type, sig.ema_value,
            )
            if notifier:
                notifier.send_signal(sig)

    logger.info("扫描结束")


if __name__ == "__main__":
    main()
