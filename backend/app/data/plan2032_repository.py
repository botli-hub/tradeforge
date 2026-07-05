"""2032 Plan 持仓数据访问层

前端每次保存都发送完整持仓列表(replace-all 语义,并按顺序重排 sort_order),
所以 replace_holdings 在单个事务里清空后整表重写,保证刷新后从数据库读回一致。
"""
from typing import Any, Dict, List

from app.data.database import get_db, _now_iso

_COLUMNS = (
    "symbol", "name", "shares", "target2032", "dividend_yield", "category",
    "currency", "pe", "moat", "risk", "note", "sort_order",
)


def get_holdings() -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM plan2032_holdings ORDER BY sort_order, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def replace_holdings(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """整表替换。传入列表的下标作为 sort_order 兜底(前端也会带 sort_order)。"""
    now = _now_iso()
    conn = get_db()
    try:
        conn.execute("DELETE FROM plan2032_holdings")
        for index, h in enumerate(holdings):
            data = {c: h.get(c) for c in _COLUMNS}
            data["symbol"] = (data.get("symbol") or "").strip().upper()
            if not data["symbol"]:
                continue  # 跳过空标的
            data["name"] = data.get("name") or data["symbol"]
            data["category"] = data.get("category") or "growth"
            data["currency"] = data.get("currency") or "USD"
            if data.get("sort_order") is None:
                data["sort_order"] = index
            conn.execute(
                """
                INSERT INTO plan2032_holdings
                    (symbol, name, shares, target2032, dividend_yield, category, currency,
                     pe, moat, risk, note, sort_order, created_at, updated_at)
                VALUES (:symbol, :name, :shares, :target2032, :dividend_yield, :category,
                        :currency, :pe, :moat, :risk, :note, :sort_order, :now, :now)
                """,
                {**data, "now": now},
            )
        conn.commit()
    finally:
        conn.close()
    return get_holdings()
