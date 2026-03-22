"""历史K线定时调度器"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.data.history_backfill import backfill_kline_range
from app.data.history_repository import (
    create_scheduler_run,
    finish_scheduler_run,
    has_successful_scheduler_run,
    list_scheduler_runs,
    list_stocks_all,
    now_iso,
    update_subscription_result,
)

TIMEFRAMES = ['1d', '1h', '30m', '5m', '1m']
WINDOW_DAYS = {
    '1d': 10,
    '1h': 3,
    '30m': 3,
    '5m': 3,
    '1m': 2,
}


class HistoryScheduler:
    def __init__(self, tz_name: str = 'Asia/Shanghai', run_hour: int = 8, run_minute: int = 0, poll_seconds: int = 30):
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
        self._thread = threading.Thread(target=self._loop, name='history-scheduler', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def status(self) -> Dict[str, Any]:
        now = datetime.now(self.tz)
        next_run = now.replace(hour=self.run_hour, minute=self.run_minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return {
            'running': self._running,
            'last_started_at': self._last_started_at,
            'next_run_at': next_run.isoformat(timespec='seconds'),
            'timeframes': TIMEFRAMES,
            'recent_runs': list_scheduler_runs(limit=10),
            'subscriptions': list_stocks_all(enabled_only=True),
        }

    def run_once(self, trigger_type: str = 'manual', host: str = '127.0.0.1', port: int = 11111) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {'status': 'busy', 'message': 'scheduler is already running'}

        run_id = str(uuid.uuid4())
        now_local = datetime.now(self.tz)
        target_date = now_local.date().isoformat()
        create_scheduler_run(run_id, trigger_type, target_date, status='running')
        self._running = True
        self._last_started_at = now_iso()
        self._last_attempted_date = target_date

        subscriptions = list_stocks_all(enabled_only=True)
        results: List[Dict[str, Any]] = []
        failed = 0
        try:
            for sub in subscriptions:
                symbol = sub['symbol']
                source_hint = sub.get('source_hint')  # stocks table has no source_hint; None = auto-detect
                symbol_result = {'symbol': symbol, 'timeframes': []}
                symbol_ok = True

                for timeframe in TIMEFRAMES:
                    try:
                        start_date, end_date = self._compute_window(now_local, timeframe)
                        result = backfill_kline_range(
                            symbol=symbol,
                            timeframe=timeframe,
                            start_date=start_date,
                            end_date=end_date,
                            host=host,
                            port=port,
                            source=source_hint,
                        )
                        symbol_result['timeframes'].append({
                            'timeframe': timeframe,
                            'source': result['source'],
                            'written': result['written'],
                            'status': 'success',
                        })
                    except Exception as e:
                        symbol_ok = False
                        failed += 1
                        symbol_result['timeframes'].append({
                            'timeframe': timeframe,
                            'status': 'failed',
                            'error': str(e),
                        })

                update_subscription_result(symbol, 'success' if symbol_ok else 'error', None if symbol_ok else '部分周期补数失败')
                results.append(symbol_result)

            summary = json.dumps({'symbols': len(subscriptions), 'failed_timeframes': failed, 'results': results}, ensure_ascii=False)
            finish_scheduler_run(run_id, 'success' if failed == 0 else 'partial', summary=summary)
            return {'run_id': run_id, 'status': 'success' if failed == 0 else 'partial', 'results': results}
        except Exception as e:
            finish_scheduler_run(run_id, 'failed', error_message=str(e))
            return {'run_id': run_id, 'status': 'failed', 'error': str(e)}
        finally:
            self._running = False
            self._run_lock.release()

    def _compute_window(self, now_local: datetime, timeframe: str):
        days = WINDOW_DAYS.get(timeframe, 3)
        start = now_local - timedelta(days=days)
        end = now_local
        return start.isoformat(timespec='seconds'), end.isoformat(timespec='seconds')

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
