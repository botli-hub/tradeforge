"""股票池管理 API"""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.data.database import get_db

router = APIRouter()


class StockOut(BaseModel):
    symbol: str
    name: str
    market: str
    enabled: bool
    subscribed: bool
    asset_type: Optional[str] = None
    source_symbol: Optional[str] = None
    currency: Optional[str] = None
    lot_size: Optional[int] = None
    status: Optional[str] = None


class StockIn(BaseModel):
    symbol: str
    name: str
    market: str  # US | HK | CN


def _now() -> str:
    return datetime.now().isoformat()


@router.get("", response_model=List[StockOut])
def list_stocks(
    market: Optional[str] = Query(None),
    enabled_only: bool = Query(False),
):
    conn = get_db()
    cursor = conn.cursor()
    sql = "SELECT symbol, name, market, enabled, subscribed, asset_type, source_symbol, currency, lot_size, status FROM stocks WHERE 1=1"
    params: list = []
    if enabled_only:
        sql += " AND enabled = 1"
    if market:
        sql += " AND market = ?"
        params.append(market.upper())
    sql += " ORDER BY market, symbol"
    rows = cursor.execute(sql, params).fetchall()
    conn.close()
    return [
        StockOut(
            symbol=r["symbol"],
            name=r["name"],
            market=r["market"],
            enabled=bool(r["enabled"]),
            subscribed=bool(r["subscribed"]),
            asset_type=r["asset_type"],
            source_symbol=r["source_symbol"],
            currency=r["currency"],
            lot_size=r["lot_size"],
            status=r["status"],
        )
        for r in rows
    ]


@router.post("", response_model=StockOut)
def add_stock(body: StockIn):
    conn = get_db()
    cursor = conn.cursor()
    now = _now()
    try:
        cursor.execute(
            """
            INSERT INTO stocks (symbol, name, market, enabled, subscribed, asset_type, source_symbol, currency, lot_size, status, created_at, updated_at)
            VALUES (?, ?, ?, 1, 0, 'STOCK', ?, ?, NULL, 'active', ?, ?)
            """,
            (
                body.symbol,
                body.name,
                body.market.upper(),
                body.symbol,
                'USD' if body.market.upper() == 'US' else ('HKD' if body.market.upper() == 'HK' else 'CNY'),
                now,
                now,
            ),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    row = cursor.execute("SELECT * FROM stocks WHERE symbol = ?", (body.symbol,)).fetchone()
    conn.close()
    return StockOut(
        symbol=row["symbol"],
        name=row["name"],
        market=row["market"],
        enabled=bool(row["enabled"]),
        subscribed=bool(row["subscribed"]),
        asset_type=row["asset_type"],
        source_symbol=row["source_symbol"],
        currency=row["currency"],
        lot_size=row["lot_size"],
        status=row["status"],
    )


@router.delete("/{symbol}")
def delete_stock(symbol: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM stocks WHERE symbol = ?", (symbol,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/{symbol}/enable")
def set_enabled(symbol: str, enabled: bool = Query(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE stocks SET enabled = ?, updated_at = ? WHERE symbol = ?",
        (1 if enabled else 0, _now(), symbol),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="股票不存在")
    conn.commit()
    conn.close()
    return {"ok": True, "symbol": symbol, "enabled": enabled}


@router.post("/{symbol}/subscribe")
def set_subscribed(symbol: str, subscribed: bool = Query(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE stocks SET subscribed = ?, updated_at = ? WHERE symbol = ?",
        (1 if subscribed else 0, _now(), symbol),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="股票不存在")
    conn.commit()
    conn.close()
    return {"ok": True, "symbol": symbol, "subscribed": subscribed}
