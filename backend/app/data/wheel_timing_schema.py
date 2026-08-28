"""Wheel 触线档案 timeframe 分桶：价格缓存 / timing_history PK 迁移。"""
from __future__ import annotations

def _table_create_sql(cursor, name: str) -> str:
    row = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if not row:
        return ""
    try:
        return row["sql"] or ""
    except Exception:
        return (row[0] if row[0] else "") or ""


def _pk_has_timeframe(cursor, name: str) -> bool:
    sql = _table_create_sql(cursor, name).upper()
    if "PRIMARY KEY" not in sql:
        return False
    return "TIMEFRAME" in sql.split("PRIMARY KEY", 1)[-1]


def _migrate_price_cache_timeframe(cursor) -> None:
    """leaps_option_price_cache PK → (contract_code, timeframe, date)。"""
    if _pk_has_timeframe(cursor, "leaps_option_price_cache"):
        return
    cursor.execute("""
        CREATE TABLE leaps_option_price_cache_tf (
            contract_code TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '1d',
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL, iv REAL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (contract_code, timeframe, date)
        )
    """)
    names = [r[1] for r in cursor.execute("PRAGMA table_info(leaps_option_price_cache)")]
    if "timeframe" in names:
        cursor.execute("""
            INSERT OR IGNORE INTO leaps_option_price_cache_tf
                (contract_code, timeframe, date, open, high, low, close, volume, iv, created_at)
            SELECT contract_code, COALESCE(NULLIF(timeframe,''),'1d'), date,
                   open, high, low, close, volume, iv, created_at
            FROM leaps_option_price_cache
        """)
    else:
        cursor.execute("""
            INSERT OR IGNORE INTO leaps_option_price_cache_tf
                (contract_code, timeframe, date, open, high, low, close, volume, iv, created_at)
            SELECT contract_code, '1d', date, open, high, low, close, volume, iv, created_at
            FROM leaps_option_price_cache
        """)
    cursor.execute("DROP TABLE leaps_option_price_cache")
    cursor.execute("ALTER TABLE leaps_option_price_cache_tf RENAME TO leaps_option_price_cache")


def _migrate_timing_history_timeframe(cursor) -> None:
    """wheel_timing_history PK → (contract_code, timeframe)。Put 日K / Call 1h 分档。"""
    if _pk_has_timeframe(cursor, "wheel_timing_history"):
        return
    cursor.execute("""
        CREATE TABLE wheel_timing_history_tf (
            contract_code TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '1d',
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            strike REAL, expiry TEXT, ema_type TEXT, ema_value REAL,
            trigger_price REAL, iv_rank REAL, underlying_price REAL,
            delta REAL, bid REAL, annualized REAL, dte INTEGER,
            below_floor INTEGER DEFAULT 0,
            times_triggered INTEGER DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            PRIMARY KEY (contract_code, timeframe)
        )
    """)
    names = [r[1] for r in cursor.execute("PRAGMA table_info(wheel_timing_history)")]
    tf_expr = "COALESCE(NULLIF(timeframe,''),'1d')" if "timeframe" in names else "'1d'"
    extras = []
    for c, dflt in (("delta", "NULL"), ("bid", "NULL"), ("annualized", "NULL"),
                    ("dte", "NULL"), ("below_floor", "0")):
        extras.append(c if c in names else dflt)
    cursor.execute(
        f"""INSERT OR IGNORE INTO wheel_timing_history_tf
            (contract_code, timeframe, symbol, side, strike, expiry, ema_type, ema_value,
             trigger_price, iv_rank, underlying_price, delta, bid, annualized, dte,
             below_floor, times_triggered, first_seen, last_seen)
            SELECT contract_code, {tf_expr}, symbol, side, strike, expiry, ema_type, ema_value,
                   trigger_price, iv_rank, underlying_price, {", ".join(extras)},
                   times_triggered, first_seen, last_seen
            FROM wheel_timing_history"""
    )
    cursor.execute("DROP TABLE wheel_timing_history")
    cursor.execute("ALTER TABLE wheel_timing_history_tf RENAME TO wheel_timing_history")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_wheel_timing_last_seen ON wheel_timing_history(last_seen DESC)"
    )


def apply_timeframe_alters(cursor) -> None:
    """幂等 ALTER：signals/cooldowns/history 记录 timeframe。"""
    for ddl in [
        "ALTER TABLE wheel_timing_history ADD COLUMN timeframe TEXT DEFAULT '1d'",
        "ALTER TABLE leaps_signals ADD COLUMN timeframe TEXT DEFAULT '1d'",
        "ALTER TABLE leaps_cooldowns ADD COLUMN timeframe TEXT DEFAULT '1d'",
    ]:
        try:
            cursor.execute(ddl)
        except Exception:
            pass
