"""历史K线定时调度器

优化说明（v1.2）：
- 并发补数：使用 ThreadPoolExecutor 并行处理不同标的，提升多标的场景下的调度效率
- 精确订阅过滤：改用 list_subscriptions(enabled_only=True) 替代 list_stocks_all，
  仅对 subscribed=1 AND enabled=1 的标的进行补数，避免扫描整个股票池
- 重试机制：单个标的/周期补数失败时最多重试 2 次，每次间隔 3 秒
- 错失任务补偿：启动时检查当天是否已成功运行，若未运行则立即补跑
- 引入 logging 替换 print，消除裸 except
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.data.history_backfill import backfill_kline_range
from app.data.history_repository import (
    create_scheduler_run,
    finish_scheduler_run,
    has_successful_scheduler_run,
    list_scheduler_runs,
    list_subscriptions,
    now_iso,
    update_subscription_result,
)

logger = logging.getLogger(__name__)

TIMEFRAMES = ['1d', '1h', '30m', '5m', '1m']
WINDOW_DAYS = {
    '1d': 10,
    '1h': 3,
    '30m': 3,
    '5m': 3,
    '1m': 2,
}

# 并发补数的最大线程数（避免对行情源造成过大压力）
MAX_WORKERS = 4
# 单个补数任务的最大重试次数
MAX_RETRIES = 2
# 重试间隔（秒）
RETRY_DELAY = 3


def _backfill_with_retry(
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    host: str,
    port: int,
    source_hint: Optional[str],
) -> Dict[str, Any]:
    """
    带重试的单周期补数。

    最多重试 MAX_RETRIES 次，每次失败后等待 RETRY_DELAY 秒。
    最终仍失败则返回带 status='failed' 的结果字典。
    """
    last_error: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 2):  # 1 次正常 + MAX_RETRIES 次重试
        try:
            result = backfill_kline_range(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                host=host,
                port=port,
                source=source_hint,
            )
            return {
                'timeframe': timeframe,
                'source': result['source'],
                'written': result['written'],
                'status': 'success',
                'attempts': attempt,
            }
        except Exception as e:
            last_error = str(e)
            if attempt <= MAX_RETRIES:
                logger.warning(
                    "补数 %s/%s 失败（第 %d 次），%d 秒后重试: %s",
                    symbol, timeframe, attempt, RETRY_DELAY, last_error,
                )
                time.sleep(RETRY_DELAY)
            else:
                logger.error(
                    "补数 %s/%s 最终失败（共 %d 次）: %s",
                    symbol, timeframe, attempt, last_error,
                )

    return {
        'timeframe': timeframe,
        'status': 'failed',
        'error': last_error,
        'attempts': MAX_RETRIES + 1,
    }


def _process_symbol(
    sub: Dict[str, Any],
    now_local: datetime,
    host: str,
    port: int,
) -> Dict[str, Any]:
    """
    处理单个标的的所有时间周期补数（串行处理各周期，标的间并发）。
    """
    symbol = sub['symbol']
    source_hint = sub.get('source_hint')
    symbol_result: Dict[str, Any] = {'symbol': symbol, 'timeframes': []}
    symbol_ok = True

    for timeframe in TIMEFRAMES:
        start_date, end_date = _compute_window(now_local, timeframe)
        tf_result = _backfill_with_retry(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            host=host,
            port=port,
            source_hint=source_hint,
        )
        symbol_result['timeframes'].append(tf_result)
        if tf_result['status'] != 'success':
            symbol_ok = False

    symbol_result['ok'] = symbol_ok
    return symbol_result


def _compute_window(now_local: datetime, timeframe: str):
    days = WINDOW_DAYS.get(timeframe, 3)
    start = now_local - timedelta(days=days)
    end = now_local
    return start.isoformat(timespec='seconds'), end.isoformat(timespec='seconds')


class HistoryScheduler:
    def __init__(
        self,
        tz_name: str = 'Asia/Shanghai',
        run_hour: int = 8,
        run_minute: int = 0,
        poll_seconds: int = 30,
    ):
        self.tz = ZoneInfo(tz_name)
        self.run_hour = run_hour
        self.run_minute = run_minute
        self.poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._run_lock = threading.Lock()
        self._running = False
        self._last_started_at: Optional[str] = None
        self._last_attempted_date: Optional[str] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name='history-scheduler', daemon=True
        )
        self._thread.start()
        logger.info("[HistoryScheduler] 调度器已启动，每日 %02d:%02d 执行", self.run_hour, self.run_minute)

        # 错失任务补偿：启动时检查当天是否已成功运行
        self._check_missed_run()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.info("[HistoryScheduler] 调度器已停止")

    def status(self) -> Dict[str, Any]:
        now = datetime.now(self.tz)
        next_run = now.replace(
            hour=self.run_hour, minute=self.run_minute, second=0, microsecond=0
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        return {
            'running': self._running,
            'last_started_at': self._last_started_at,
            'next_run_at': next_run.isoformat(timespec='seconds'),
            'timeframes': TIMEFRAMES,
            'recent_runs': list_scheduler_runs(limit=10),
            # 仅展示已订阅的标的
            'subscriptions': list_subscriptions(enabled_only=True),
        }

    def run_once(
        self,
        trigger_type: str = 'manual',
        host: str = '127.0.0.1',
        port: int = 11111,
    ) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {'status': 'busy', 'message': 'scheduler is already running'}

        run_id = str(uuid.uuid4())
        now_local = datetime.now(self.tz)
        target_date = now_local.date().isoformat()
        create_scheduler_run(run_id, trigger_type, target_date, status='running')
        self._running = True
        self._last_started_at = now_iso()
        self._last_attempted_date = target_date

        # 仅获取 subscribed=1 AND enabled=1 的标的，不扫描整个股票池
        subscriptions = list_subscriptions(enabled_only=True)
        logger.info(
            "[HistoryScheduler] 开始补数，共 %d 个订阅标的，触发方式: %s",
            len(subscriptions), trigger_type,
        )

        results: List[Dict[str, Any]] = []
        failed_timeframes = 0

        try:
            # 使用线程池并发处理各标的
            with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='backfill') as executor:
                future_to_sub = {
                    executor.submit(_process_symbol, sub, now_local, host, port): sub
                    for sub in subscriptions
                }

                for future in as_completed(future_to_sub):
                    sub = future_to_sub[future]
                    symbol = sub['symbol']
                    try:
                        symbol_result = future.result()
                    except Exception:
                        logger.error("[HistoryScheduler] 处理标的 %s 时发生未预期异常", symbol, exc_info=True)
                        symbol_result = {
                            'symbol': symbol,
                            'timeframes': [],
                            'ok': False,
                        }

                    symbol_ok = symbol_result.get('ok', False)
                    failed_count = sum(
                        1 for tf in symbol_result.get('timeframes', [])
                        if tf.get('status') != 'success'
                    )
                    failed_timeframes += failed_count

                    update_subscription_result(
                        symbol,
                        'success' if symbol_ok else 'error',
                        None if symbol_ok else '部分周期补数失败',
                    )
                    results.append(symbol_result)
                    logger.info(
                        "[HistoryScheduler] %s 补数完成，失败周期数: %d",
                        symbol, failed_count,
                    )

            final_status = 'success' if failed_timeframes == 0 else 'partial'
            summary = json.dumps(
                {
                    'symbols': len(subscriptions),
                    'failed_timeframes': failed_timeframes,
                    'results': results,
                },
                ensure_ascii=False,
            )
            finish_scheduler_run(run_id, final_status, summary=summary)
            logger.info(
                "[HistoryScheduler] 补数完成，状态: %s，失败周期总数: %d",
                final_status, failed_timeframes,
            )
            return {
                'run_id': run_id,
                'status': final_status,
                'results': results,
            }

        except Exception:
            logger.error("[HistoryScheduler] 调度运行发生严重异常", exc_info=True)
            finish_scheduler_run(run_id, 'failed', error_message='调度运行发生严重异常')
            return {'run_id': run_id, 'status': 'failed', 'error': '调度运行发生严重异常'}
        finally:
            self._running = False
            self._run_lock.release()

    def _check_missed_run(self):
        """
        错失任务补偿：服务启动时检查当天是否已成功运行过。
        若未运行（如服务在计划时间前宕机），则立即触发一次补数。
        """
        now_local = datetime.now(self.tz)
        target_date = now_local.date().isoformat()
        # 仅在当天计划时间已过、且尚未成功运行时触发
        scheduled_time = now_local.replace(
            hour=self.run_hour, minute=self.run_minute, second=0, microsecond=0
        )
        if now_local >= scheduled_time and not has_successful_scheduler_run(target_date):
            logger.info("[HistoryScheduler] 检测到当天补数未执行，触发错失任务补偿")
            threading.Thread(
                target=self.run_once,
                kwargs={'trigger_type': 'missed_run_recovery'},
                daemon=True,
            ).start()

    def _loop(self):
        while not self._stop_event.wait(self.poll_seconds):
            now_local = datetime.now(self.tz)
            if now_local.hour == self.run_hour and now_local.minute == self.run_minute:
                target_date = now_local.date().isoformat()
                if self._last_attempted_date == target_date:
                    continue
                if not has_successful_scheduler_run(target_date):
                    self.run_once(trigger_type='daily')


_scheduler_singleton: Optional[HistoryScheduler] = None


def get_history_scheduler() -> HistoryScheduler:
    global _scheduler_singleton
    if _scheduler_singleton is None:
        _scheduler_singleton = HistoryScheduler()
    return _scheduler_singleton
