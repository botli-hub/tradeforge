"""标的立场 overlay + 浮盈两本账.

不改 POSITION_QUANT. 默认 acquire 保持旧 action_code.
decide_position 由 wheel_decision 包装调用 decorate().
"""
from __future__ import annotations

from typing import Any, Dict, Optional

STANCE_INCOME = "income"
STANCE_ACQUIRE = "acquire"

ACTION_CLOSE = "CLOSE"
ACTION_REPLACE = "REPLACE"
ACTION_HOLD_THETA = "HOLD_THETA"
ACTION_PREPARE_ASSIGN = "PREPARE_ASSIGN"
ACTION_ROLL_ADJUST = "ROLL_ADJUST"
ACTION_NONE = "NONE"


def normalize_stance(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ("income", "只收租", "rent", "premium"):
        return STANCE_INCOME
    return STANCE_ACQUIRE


def ensure_stance_column() -> None:
    try:
        from app.data.database import get_db
        conn = get_db()
        try:
            conn.execute("ALTER TABLE wheel_targets ADD COLUMN stance TEXT DEFAULT 'acquire'")
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
    except Exception:
        pass


def resolve_stance(item: Dict[str, Any]) -> str:
    raw = item.get("stance")
    if raw:
        return normalize_stance(raw)
    sym = item.get("symbol")
    if not sym:
        return STANCE_ACQUIRE
    try:
        ensure_stance_column()
        from app.data.database import get_db
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT stance FROM wheel_targets WHERE symbol=?", (sym,)
            ).fetchone()
            if row:
                return normalize_stance(row["stance"] if not isinstance(row, tuple) else row[0])
        finally:
            conn.close()
    except Exception:
        pass
    return STANCE_ACQUIRE


def set_target_stance(symbol: str, stance: str) -> str:
    ensure_stance_column()
    st = normalize_stance(stance)
    from app.data.database import get_db
    from datetime import datetime
    conn = get_db()
    try:
        conn.execute(
            "UPDATE wheel_targets SET stance=?, updated_at=? WHERE symbol=?",
            (st, datetime.now().isoformat(), symbol),
        )
        conn.commit()
    finally:
        conn.close()
    return st


def build_books(item: Dict[str, Any], result: Dict[str, Any], stance: str) -> Optional[Dict[str, Any]]:
    code = result.get("action_code") or ACTION_NONE
    tree = result.get("decision_tree") or {}
    profit_pct = item.get("profit_pct")
    if profit_pct is None:
        profit_pct = tree.get("profit_pct")
    soft_profit = float(tree.get("soft_profit_pct") or 30)
    branch = result.get("decision_branch") or tree.get("branch") or ""
    close_notional = float(tree.get("close_notional") or 0)
    remaining_ann = result.get("remaining_annualized")
    strike = float(item.get("strike") or 0)
    qty = float(item.get("qty") or 1)
    size = float(item.get("contract_size") or 100)
    side = item.get("side")
    floor_price = item.get("floor_price")
    try:
        floor_price = float(floor_price) if floor_price is not None else None
    except (TypeError, ValueError):
        floor_price = None

    soft_hit = profit_pct is not None and profit_pct >= soft_profit
    replace_soft = branch == "replace_soft"
    attach = bool(
        (soft_hit and code in (ACTION_HOLD_THETA, ACTION_NONE, ACTION_REPLACE))
        or replace_soft
        or (code == ACTION_HOLD_THETA and profit_pct is not None and profit_pct > 0)
    )
    if not attach:
        return None

    if side == "PUT":
        capital_tied = round(strike * qty * size, 2)
        freed_if_close = capital_tied
    else:
        sh = item.get("shares")
        try:
            sh = float(sh) if sh is not None else qty * size
        except (TypeError, ValueError):
            sh = qty * size
        cb = item.get("cost_basis")
        if cb is None:
            cb = item.get("share_cost")
        try:
            capital_tied = round(float(cb) * sh, 2) if cb is not None else None
        except (TypeError, ValueError):
            capital_tied = None
        freed_if_close = 0.0

    if stance == STANCE_INCOME:
        assign_means = "接货是偏离只收租，不是成功路径"
    elif floor_price is not None and floor_price > 0:
        assign_means = f"接的是 strike（愿接 floor={floor_price:g}）"
    else:
        assign_means = "接的是 strike（未设 floor）"

    return {
        "seller": {
            "premium_captured_pct": profit_pct,
            "remaining_premium_usd": round(close_notional, 2),
            "remaining_ann": remaining_ann,
            "capital_tied": capital_tied,
            "freed_if_close": freed_if_close,
        },
        "owner": {
            "holding_is_price_bet": code in (ACTION_HOLD_THETA, ACTION_NONE),
            "assign_strike": strike,
            "floor_price": floor_price,
            "stance": stance,
            "assign_means": assign_means,
        },
    }


def decorate(result: Dict[str, Any], item: Dict[str, Any], pos_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Post-process core decide_position. Does not change global thresholds."""
    stance = resolve_stance(item)
    tree = dict(result.get("decision_tree") or {})
    code = result.get("action_code") or ACTION_NONE
    hint = result.get("action_hint")
    reasons = list(result.get("reasons") or [])
    cl = result.get("assign_checklist")
    side = item.get("side")
    profit_pct = item.get("profit_pct")
    soft_profit = float(tree.get("soft_profit_pct") or 30)
    profit_hit = bool(tree.get("profit_hit"))
    if not profit_hit and profit_pct is not None:
        try:
            tgt = float((result.get("quant_thresholds") or {}).get("profit_target_pct") or 50)
            profit_hit = profit_pct >= tgt
        except (TypeError, ValueError):
            profit_hit = False
    soft_hit = bool(tree.get("soft_profit_hit"))
    if not soft_hit and profit_pct is not None:
        soft_hit = profit_pct >= soft_profit

    if stance == STANCE_INCOME and code == ACTION_HOLD_THETA and (profit_hit or soft_hit):
        tree["hold_for_theta"] = False
        if profit_hit:
            code = ACTION_CLOSE
            hint = "只收租·浮盈达硬止盈,更早腾仓"
            tree["branch"] = "close_profit"
            result["decision_branch"] = "close_profit"
            result["prefer_card"] = "no_roll"
            result["action_priority"] = min(int(result.get("action_priority") or 9), 2)
        else:
            code = ACTION_REPLACE
            hint = "只收租·软止盈,更早腾仓"
            tree["branch"] = "replace_soft"
            result["decision_branch"] = "replace_soft"
            result["prefer_card"] = "no_roll"
            result["action_priority"] = min(int(result.get("action_priority") or 9), 3)
        result["action_code"] = code
        result["action_hint"] = hint

    if stance == STANCE_INCOME and side == "PUT" and code == ACTION_PREPARE_ASSIGN:
        code = ACTION_ROLL_ADJUST
        hint = "只收租·接货是偏离,Roll 或买回"
        result["action_code"] = code
        result["action_hint"] = hint
        result["prefer_card"] = "adjust_strike"
        tree["branch"] = "income_assign_warn"
        result["decision_branch"] = "income_assign_warn"
        if isinstance(cl, dict):
            notes = list(cl.get("notes") or [])
            if not any("只收租" in n or "偏离" in n for n in notes):
                notes.insert(0, "标的只收租，接货视为偏离")
            cl = {**cl, "notes": notes}
            result["assign_checklist"] = cl

    if stance == STANCE_INCOME and isinstance(cl, dict):
        notes = list(cl.get("notes") or [])
        if not any("只收租" in n or "偏离" in n for n in notes):
            notes.insert(0, "标的只收租，接货视为偏离")
            cl = {**cl, "notes": notes}
            result["assign_checklist"] = cl

    tree["stance"] = stance
    result["decision_tree"] = tree
    result["stance"] = stance

    books = build_books(item, result, stance)
    result["books"] = books
    if books and "浮盈对账" not in reasons:
        reasons.append("浮盈对账")
    result["reasons"] = reasons
    return result
