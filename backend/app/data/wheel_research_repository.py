"""Append-only research evidence. Never stores credentials or broker orders."""
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from app.data.database import get_db

VERSION = 'wheel-research-v1'


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def encode(value):
    return json.dumps(clean(value), ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def ensure_tables(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS wheel_research_events (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT, observed_at TEXT NOT NULL,
      version TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_research_kind_time ON wheel_research_events(kind, observed_at);
    CREATE TABLE IF NOT EXISTS wheel_execution_observations (
      id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wheel_nav_observations (
      id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, equity REAL NOT NULL, starting_cash REAL NOT NULL);
    ''')


def insert_event(conn, kind, payload, symbol=None):
    body = encode(payload)
    event_id = str(uuid.uuid4())
    conn.execute('INSERT INTO wheel_research_events VALUES (?,?,?,?,?,?,?)',
                 (event_id, kind, symbol, datetime.now(timezone.utc).isoformat(), VERSION,
                  hashlib.sha256(body.encode()).hexdigest(), body))
    return event_id


def append_event(kind, payload, symbol=None):
    conn = get_db()
    try:
        ensure_tables(conn)
        event_id = insert_event(conn, kind, payload, symbol)
        conn.commit()
        return event_id
    finally:
        conn.close()


def list_events(kind=None, limit=50, before=None, include_payload=False):
    conn = get_db()
    try:
        ensure_tables(conn)
        where, args = [], []
        if kind:
            where.append('kind=?'); args.append(kind)
        if before:
            where.append('observed_at<?'); args.append(before)
        sql = 'SELECT * FROM wheel_research_events'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        rows = conn.execute(sql + ' ORDER BY observed_at DESC, id DESC LIMIT ?',
                            (*args, min(max(int(limit), 1), 500))).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            payload = json.loads(r.pop('payload'))
            r['candidate_count'] = len(payload.get('candidates', payload.get('contracts', [])))
            if include_payload:
                r['payload'] = payload
            result.append(r)
        return result
    finally:
        conn.close()


def get_event(event_id):
    conn = get_db()
    try:
        ensure_tables(conn)
        row = conn.execute('SELECT * FROM wheel_research_events WHERE id=?', (event_id,)).fetchone()
        if not row:
            return None
        result = dict(row); result['payload'] = json.loads(result['payload'])
        return result
    finally:
        conn.close()


def observe_nav(nav):
    conn = get_db()
    try:
        ensure_tables(conn)
        starting = float(nav['starting_cash'])
        if not nav.get('valuation_incomplete') and not nav.get('reconciliation_required') and starting > 0:
            conn.execute('INSERT INTO wheel_nav_observations(observed_at,equity,starting_cash) VALUES (?,?,?)',
                         (datetime.now(timezone.utc).isoformat(), nav['equity'], starting))
            conn.commit()
        row = conn.execute('SELECT MAX(equity) peak,COUNT(*) samples FROM wheel_nav_observations WHERE starting_cash=?', (starting,)).fetchone()
        peak = max(starting, float(row['peak'] or starting))
        return {'peak_equity': peak, 'samples': row['samples'],
                'drawdown_pct': max(0, (peak-nav['equity'])/peak*100) if peak > 0 else None,
                'note': '仅从有效估值开始采样；变更起始现金后独立计算，不还原采样前回撤'}
    finally:
        conn.close()
