"""数据库模块"""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path(__file__).parent.parent / "tradeforge.db"


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now().isoformat()


def _build_visual_demo() -> Dict[str, Any]:
    return {
        "version": "1.0",
        "strategy_id": "",
        "mode": "visual",
        "name": "MA Volume Demo",
        "symbols": ["AAPL"],
        "timeframe": "1d",
        "indicators": [
            {"name": "ma_fast", "type": "MA", "period": 5, "source": "close"},
            {"name": "ma_slow", "type": "MA", "period": 20, "source": "close"},
            {"name": "vol_ma", "type": "MA", "period": 20, "source": "volume"},
        ],
        "variables": {
            "vol_ratio": {
                "op": "/",
                "left": "volume",
                "right": "vol_ma",
            }
        },
        "conditions": {
            "entry": {
                "type": "AND",
                "rules": [
                    {"id": "entry_1", "type": "crossover", "op": "cross_above", "left": "ma_fast", "right": "ma_slow"},
                    {"id": "entry_2", "type": "binary", "op": ">", "left": "vol_ratio", "right": 1.0},
                ],
            },
            "exit": {
                "type": "OR",
                "rules": [
                    {"id": "exit_1", "type": "crossover", "op": "cross_below", "left": "ma_fast", "right": "ma_slow"}
                ],
            },
        },
        "position_sizing": {"type": "fixed_amount", "value": 10000},
        "risk_rules": {
            "initial_capital": 100000,
            "fee_rate": 0.0003,
            "slippage": 0.001,
            "max_position_pct": 0.5,
        },
    }


def _build_formula_demo() -> Dict[str, Any]:
    source_code = '''strategy("Formula Demo", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
vol_ratio = volume / MA(volume, 20)

entry = cross_above(ma_fast, ma_slow) and vol_ratio > 1.2
exit = cross_below(ma_fast, ma_slow)

if entry:
    buy(100)

if exit:
    sell_all()
'''
    return {
        "version": "1.0",
        "strategy_id": "",
        "mode": "formula",
        "name": "Formula Demo",
        "symbols": ["TSLA"],
        "timeframe": "1d",
        "source_code": source_code,
        "parameters": [
            {"name": "fast", "label": "快线周期", "default": 5, "min": 2, "max": 50},
            {"name": "slow", "label": "慢线周期", "default": 20, "min": 5, "max": 200},
        ],
        "indicators": [
            {"name": "ma_fast", "type": "MA", "source": "close", "period_ref": "fast"},
            {"name": "ma_slow", "type": "MA", "source": "close", "period_ref": "slow"},
            {"name": "vol_ma", "type": "MA", "source": "volume", "period": 20},
        ],
        "variables": {
            "vol_ratio": {"op": "/", "left": "volume", "right": "vol_ma"}
        },
        "conditions": {
            "entry": {
                "type": "AND",
                "rules": [
                    {"id": "entry_1", "type": "crossover", "op": "cross_above", "left": "ma_fast", "right": "ma_slow"},
                    {"id": "entry_2", "type": "binary", "op": ">", "left": "vol_ratio", "right": 1.2},
                ],
            },
            "exit": {
                "type": "OR",
                "rules": [
                    {"id": "exit_1", "type": "crossover", "op": "cross_below", "left": "ma_fast", "right": "ma_slow"}
                ],
            },
        },
        "position_sizing": {"type": "fixed_amount", "value": 10000},
        "risk_rules": {
            "initial_capital": 100000,
            "fee_rate": 0.0003,
            "slippage": 0.001,
            "max_position_pct": 0.5,
        },
    }


def seed_demo_strategies(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(1) AS cnt FROM strategies")
    row = cursor.fetchone()
    if row and row["cnt"]:
        return

    now = _now_iso()
    demos: List[Dict[str, Any]] = [
        {"name": "MA Volume Demo", "mode": "visual", "config": _build_visual_demo()},
        {"name": "Formula Demo", "mode": "formula", "config": _build_formula_demo()},
    ]

    for demo in demos:
        strategy_id = str(uuid.uuid4())
        config = dict(demo["config"])
        config["strategy_id"] = strategy_id
        cursor.execute(
            """
            INSERT INTO strategies (id, name, mode, config, status, version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                demo["name"],
                demo["mode"],
                json.dumps(config, ensure_ascii=False),
                "ready",
                1,
                now,
                now,
            ),
        )

    conn.commit()


def init_db():
    """初始化数据库表"""
    conn = get_db()
    cursor = conn.cursor()

    # 策略表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'visual',
            config TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            version INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # 策略版本表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategy_versions (
            id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            config TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        )
    """)

    # 回测记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            fee_rate REAL NOT NULL,
            slippage REAL NOT NULL,
            status TEXT DEFAULT 'running',
            metrics TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        )
    """)

    # 成交记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            backtest_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_time TEXT,
            exit_price REAL,
            quantity REAL NOT NULL,
            pnl REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (backtest_id) REFERENCES backtest_runs(id)
        )
    """)

    # 标的基础信息
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            symbol TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            source_symbol TEXT NOT NULL,
            name TEXT,
            currency TEXT,
            lot_size INTEGER,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # 历史K线
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kline_bars (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL DEFAULT 0,
            turnover REAL DEFAULT 0,
            source TEXT NOT NULL,
            adjusted TEXT DEFAULT 'none',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, timeframe, ts)
        )
    """)

    # K线同步状态
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kline_sync_state (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            source TEXT NOT NULL,
            earliest_ts TEXT,
            latest_ts TEXT,
            last_sync_at TEXT,
            last_success_at TEXT,
            status TEXT DEFAULT 'idle',
            error_message TEXT,
            PRIMARY KEY (symbol, timeframe, source)
        )
    """)

    # K线补数任务
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kline_backfill_jobs (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            source TEXT NOT NULL,
            start_ts TEXT NOT NULL,
            end_ts TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 5,
            retry_count INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT
        )
    """)

    # 数据订阅表（定时更新列表）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS data_subscriptions (
            symbol TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            name TEXT,
            source_hint TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_scheduled_sync_at TEXT,
            last_scheduled_status TEXT,
            last_error TEXT
        )
    """)

    # 调度运行记录
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_scheduler_runs (
            id TEXT PRIMARY KEY,
            trigger_type TEXT NOT NULL,
            target_date TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            summary TEXT,
            error_message TEXT
        )
    """)

    # 风险事件记录
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            order_value REAL NOT NULL,
            risk_score REAL NOT NULL,
            details TEXT,
            blocked INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kline_symbol_tf_ts ON kline_bars(symbol, timeframe, ts)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kline_jobs_status ON kline_backfill_jobs(status, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_enabled ON data_subscriptions(enabled, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scheduler_runs_target ON history_scheduler_runs(target_date, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_events_symbol ON risk_events(symbol, created_at)")

    seed_demo_strategies(conn)
    conn.commit()
    conn.close()
