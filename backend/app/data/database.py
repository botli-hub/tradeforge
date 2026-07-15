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

    # 股票池 + 标的主数据（统一主表）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            subscribed INTEGER DEFAULT 0,
            asset_type TEXT DEFAULT 'STOCK',
            source_symbol TEXT,
            currency TEXT,
            lot_size INTEGER,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # 兼容旧库：为 stocks 增补主数据 + 调度订阅字段
    for ddl in [
        "ALTER TABLE stocks ADD COLUMN asset_type TEXT DEFAULT 'STOCK'",
        "ALTER TABLE stocks ADD COLUMN source_symbol TEXT",
        "ALTER TABLE stocks ADD COLUMN currency TEXT",
        "ALTER TABLE stocks ADD COLUMN lot_size INTEGER",
        "ALTER TABLE stocks ADD COLUMN status TEXT DEFAULT 'active'",
        "ALTER TABLE stocks ADD COLUMN source_hint TEXT",
        "ALTER TABLE stocks ADD COLUMN last_scheduled_sync_at TEXT",
        "ALTER TABLE stocks ADD COLUMN last_scheduled_status TEXT",
        "ALTER TABLE stocks ADD COLUMN last_error TEXT",
    ]:
        try:
            cursor.execute(ddl)
        except Exception:
            pass

    # 从旧 instruments 表一次性回填到 stocks；若旧表不存在则自动跳过
    try:
        cursor.execute(
            "SELECT symbol, market, asset_type, source_symbol, name, currency, lot_size, status, created_at, updated_at FROM instruments"
        )
        rows = cursor.fetchall()
        for row in rows:
            cursor.execute(
                """
                INSERT INTO stocks (symbol, name, market, enabled, subscribed, asset_type, source_symbol, currency, lot_size, status, created_at, updated_at)
                VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=COALESCE(NULLIF(stocks.name, stocks.symbol), excluded.name, stocks.name),
                    market=COALESCE(stocks.market, excluded.market),
                    asset_type=COALESCE(stocks.asset_type, excluded.asset_type),
                    source_symbol=COALESCE(stocks.source_symbol, excluded.source_symbol),
                    currency=COALESCE(stocks.currency, excluded.currency),
                    lot_size=COALESCE(stocks.lot_size, excluded.lot_size),
                    status=COALESCE(stocks.status, excluded.status),
                    updated_at=excluded.updated_at
                """,
                (
                    row["symbol"],
                    row["name"] or row["symbol"],
                    row["market"],
                    row["asset_type"],
                    row["source_symbol"],
                    row["currency"],
                    row["lot_size"],
                    row["status"] or 'active',
                    row["created_at"],
                    row["updated_at"],
                ),
            )
    except Exception:
        pass

    # 从旧 data_subscriptions 表回填到 stocks；若旧表不存在则自动跳过
    try:
        cursor.execute(
            "SELECT symbol, market, name, source_hint, enabled, created_at, updated_at, last_scheduled_sync_at, last_scheduled_status, last_error FROM data_subscriptions"
        )
        rows = cursor.fetchall()
        for row in rows:
            cursor.execute(
                """
                INSERT INTO stocks (
                    symbol, name, market, enabled, subscribed, asset_type, source_symbol, currency, lot_size, status,
                    source_hint, last_scheduled_sync_at, last_scheduled_status, last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, 'STOCK', ?, ?, NULL, 'active', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=COALESCE(NULLIF(stocks.name, stocks.symbol), excluded.name, stocks.name),
                    market=COALESCE(stocks.market, excluded.market),
                    enabled=excluded.enabled,
                    source_hint=COALESCE(stocks.source_hint, excluded.source_hint),
                    last_scheduled_sync_at=COALESCE(excluded.last_scheduled_sync_at, stocks.last_scheduled_sync_at),
                    last_scheduled_status=COALESCE(excluded.last_scheduled_status, stocks.last_scheduled_status),
                    last_error=COALESCE(excluded.last_error, stocks.last_error),
                    updated_at=excluded.updated_at
                """,
                (
                    row["symbol"],
                    row["name"] or row["symbol"],
                    row["market"],
                    row["enabled"],
                    row["symbol"],
                    'USD' if row['market'] == 'US' else ('HKD' if row['market'] == 'HK' else 'CNY'),
                    row["source_hint"],
                    row["last_scheduled_sync_at"],
                    row["last_scheduled_status"],
                    row["last_error"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
    except Exception:
        pass

    # 主数据清洗：规范 market / source_symbol / currency / name
    cursor.execute(
        """
        UPDATE stocks
        SET market = CASE
            WHEN market = 'SH' OR symbol LIKE '%.SH' OR symbol LIKE '%.SZ' THEN 'CN'
            WHEN market = 'SZ' THEN 'CN'
            WHEN market = 'HK' OR symbol LIKE '%.HK' THEN 'HK'
            WHEN symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' THEN 'CN'
            ELSE 'US'
        END
        """
    )
    cursor.execute(
        """
        UPDATE stocks
        SET currency = CASE
            WHEN market = 'CN' THEN 'CNY'
            WHEN market = 'HK' THEN 'HKD'
            ELSE 'USD'
        END
        WHERE currency IS NULL OR currency = ''
        """
    )
    cursor.execute(
        "UPDATE stocks SET source_symbol = symbol WHERE source_symbol IS NULL OR source_symbol = ''"
    )
    cursor.execute(
        "UPDATE stocks SET asset_type = 'STOCK' WHERE asset_type IS NULL OR asset_type = ''"
    )
    cursor.execute(
        "UPDATE stocks SET status = 'active' WHERE status IS NULL OR status = ''"
    )
    cursor.execute(
        "UPDATE stocks SET name = symbol WHERE name IS NULL OR TRIM(name) = ''"
    )

    # 逻辑上停用旧表：迁移后尝试删除；若不存在则跳过
    for old_table in ['instruments', 'data_subscriptions']:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {old_table}")
        except Exception:
            pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kline_symbol_tf_ts ON kline_bars(symbol, timeframe, ts)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kline_jobs_status ON kline_backfill_jobs(status, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stocks_enabled ON stocks(enabled, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stocks_subscribed ON stocks(subscribed, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scheduler_runs_target ON history_scheduler_runs(target_date, status)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plan2032_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            shares REAL DEFAULT 0,
            target2032 REAL DEFAULT 0,
            dividend_yield REAL DEFAULT 0,
            category TEXT NOT NULL,
            currency TEXT NOT NULL,
            pe REAL,
            moat TEXT,
            risk INTEGER DEFAULT 3,
            note TEXT,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_events_symbol ON risk_events(symbol, created_at)")

    # LEAPS 监控表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaps_watchlist (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            floor_price REAL NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaps_option_price_cache (
            contract_code TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            iv REAL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (contract_code, date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaps_iv_history (
            contract_code TEXT NOT NULL,
            date TEXT NOT NULL,
            iv REAL NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (contract_code, date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaps_signals (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            contract_code TEXT NOT NULL,
            signal_level TEXT NOT NULL,
            trigger_price REAL NOT NULL,
            ema_value REAL NOT NULL,
            ema_type TEXT NOT NULL,
            iv_rank REAL NOT NULL,
            underlying_price REAL NOT NULL,
            floor_price REAL NOT NULL,
            suggestions TEXT,
            is_intraday INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaps_cooldowns (
            contract_code TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            cooldown_until TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leaps_signals_symbol ON leaps_signals(symbol, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leaps_cooldowns_symbol ON leaps_cooldowns(symbol, cooldown_until)")

    # ── Wheel 策略表 ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_targets (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'US',
            floor_price REAL NOT NULL,
            max_capital REAL DEFAULT 0,
            delta_min REAL DEFAULT 0.15,
            delta_max REAL DEFAULT 0.30,
            dte_min INTEGER DEFAULT 21,
            dte_max INTEGER DEFAULT 45,
            min_annualized REAL DEFAULT 15.0,
            min_open_interest INTEGER DEFAULT 100,
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_cycles (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'IDLE',
            shares REAL DEFAULT 0,
            share_cost REAL DEFAULT 0,
            total_premium REAL DEFAULT 0,
            total_fees REAL DEFAULT 0,
            realized_pnl REAL,
            open_contract_code TEXT,
            open_option_type TEXT,
            open_strike REAL,
            open_expiry TEXT,
            open_qty REAL DEFAULT 0,
            open_price REAL DEFAULT 0,
            open_contract_size INTEGER DEFAULT 100,
            started_at TEXT NOT NULL,
            closed_at TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_trades (
            id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            contract_code TEXT,
            strike REAL,
            expiry TEXT,
            qty REAL DEFAULT 1,
            price REAL DEFAULT 0,
            fee REAL DEFAULT 0,
            contract_size INTEGER DEFAULT 100,
            note TEXT,
            traded_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (cycle_id) REFERENCES wheel_cycles(id)
        )
    """)

    # 通用 KV(周报标记等)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_kv (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)

    # Wheel 开仓时机历史(按合约代码去重合并)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_timing_history (
            contract_code TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            strike REAL,
            expiry TEXT,
            ema_type TEXT,
            ema_value REAL,
            trigger_price REAL,
            iv_rank REAL,
            underlying_price REAL,
            times_triggered INTEGER DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wheel_timing_last_seen ON wheel_timing_history(last_seen DESC)")

    # 兼容旧库:为时机历史增补合约收益字段(delta/bid/年化/DTE/底线警告)
    for ddl in [
        "ALTER TABLE wheel_timing_history ADD COLUMN delta REAL",
        "ALTER TABLE wheel_timing_history ADD COLUMN bid REAL",
        "ALTER TABLE wheel_timing_history ADD COLUMN annualized REAL",
        "ALTER TABLE wheel_timing_history ADD COLUMN dte INTEGER",
        "ALTER TABLE wheel_timing_history ADD COLUMN below_floor INTEGER DEFAULT 0",
    ]:
        try:
            cursor.execute(ddl)
        except Exception:
            pass

    # 一次性回填:历史表为空时,从既有 WHEEL 信号导入(按合约合并)
    try:
        row = cursor.execute("SELECT COUNT(1) AS c FROM wheel_timing_history").fetchone()
        if row and row["c"] == 0:
            cursor.execute("""
                INSERT INTO wheel_timing_history
                    (contract_code, symbol, side, strike, expiry, ema_type, ema_value,
                     trigger_price, iv_rank, underlying_price, times_triggered, first_seen, last_seen)
                SELECT contract_code, symbol,
                       CASE WHEN signal_level LIKE '%CALL%' THEN 'CALL' ELSE 'PUT' END,
                       NULL, NULL, ema_type, ema_value,
                       trigger_price, iv_rank, underlying_price,
                       COUNT(1), MIN(created_at), MAX(created_at)
                FROM leaps_signals
                WHERE signal_level LIKE 'WHEEL%'
                GROUP BY contract_code
            """)
    except Exception:
        pass

    # 标的层面 ATM IV 快照(用于 IV Rank 历史积累)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS underlying_iv_history (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            iv REAL NOT NULL,
            spot REAL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (symbol, date)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wheel_cycles_symbol ON wheel_cycles(symbol, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wheel_trades_cycle ON wheel_trades(cycle_id, traded_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wheel_trades_symbol ON wheel_trades(symbol, traded_at)")

    # Wheel 扫描建议快照(归因/复盘)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_suggestion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # 事件封锁日(财报外的手动 block: FOMC/拆分等)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_event_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            event_date TEXT NOT NULL,
            label TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # 兼容旧库:wheel_targets 增补行业/标签;cycles 增补入场分
    for ddl in [
        "ALTER TABLE wheel_targets ADD COLUMN sector TEXT",
        "ALTER TABLE wheel_targets ADD COLUMN tags TEXT",
        "ALTER TABLE wheel_cycles ADD COLUMN entry_score REAL",
        "ALTER TABLE wheel_cycles ADD COLUMN entry_meta TEXT",
        "ALTER TABLE wheel_trades ADD COLUMN is_roll INTEGER DEFAULT 0",
    ]:
        try:
            cursor.execute(ddl)
        except Exception:
            pass

    seed_demo_strategies(conn)
    conn.commit()
    conn.close()
