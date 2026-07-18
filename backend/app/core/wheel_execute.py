"""一键执行草稿:管理动作 / 开仓机会 → 可确认的记账载荷。

不自动下单到券商,只生成台账草稿,减少切屏填表。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def draft_from_manage(
    item: Dict[str, Any],
    *,
    action: Optional[str] = None,
    buyback_price: Optional[float] = None,
    roll: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """持仓管理 → 草稿。

    action: close | expire | assign | roll | auto(按 action_code)
    """
    code = (item.get("action_code") or "NONE").upper()
    side = (item.get("side") or "").upper()
    if not action or action == "auto":
        if code in ("CLOSE", "REPLACE"):
            action = "close"
        elif code in ("ROLL", "ROLL_ADJUST"):
            action = "roll"
        elif code == "PREPARE_ASSIGN":
            action = "assign"
        elif code == "HOLD_THETA":
            action = "expire"
        else:
            action = "close" if item.get("profit_hit") else "expire"

    cycle_id = item.get("cycle_id")
    symbol = item.get("symbol")
    qty = float(item.get("qty") or 1)
    size = int(item.get("contract_size") or 100)
    strike = item.get("strike")
    expiry = item.get("expiry")
    px = buyback_price
    if px is None:
        px = item.get("buyback_ask") or item.get("current_price") or 0

    steps: List[Dict[str, Any]] = []
    title = ""
    notes: List[str] = []

    if action == "close":
        tt = "BUY_PUT_CLOSE" if side == "PUT" else "BUY_CALL_CLOSE"
        title = f"买回平仓 {symbol} {side}"
        steps.append({
            "trade_type": tt,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "contract_code": item.get("contract_code"),
            "strike": strike,
            "expiry": expiry,
            "qty": qty,
            "price": float(px or 0),
            "fee": 0,
            "contract_size": size,
            "note": f"一键执行·{code or 'CLOSE'}",
        })
        if side == "PUT":
            notes.append("平仓后释放 CSP 担保,可开下一腿")
        else:
            notes.append("平仓后结束 Call 义务,持股仍在")

    elif action == "expire":
        title = f"到期作废 {symbol} {side}"
        steps.append({
            "trade_type": "EXPIRE",
            "symbol": symbol,
            "cycle_id": cycle_id,
            "contract_code": item.get("contract_code"),
            "strike": strike,
            "expiry": expiry,
            "qty": qty,
            "price": 0,
            "fee": 0,
            "contract_size": size,
            "note": "一键执行·EXPIRE",
        })
        notes.append("仅当确认 OTM 作废时使用;ITM 应用接货/交货")

    elif action == "assign":
        if side == "PUT":
            title = f"指派接货 {symbol}"
            steps.append({
                "trade_type": "ASSIGNED",
                "symbol": symbol,
                "cycle_id": cycle_id,
                "contract_code": item.get("contract_code"),
                "strike": strike,
                "expiry": expiry,
                "qty": qty,
                "price": float(strike or 0),
                "fee": 0,
                "contract_size": size,
                "note": "一键执行·ASSIGNED",
            })
            notes.append("接货后成本基础=strike−累计净权利金;下一步卖 CC")
            cl = item.get("assign_checklist") or {}
            if cl.get("floor_ok") is False:
                notes.append("⚠ strike 高于愿接价")
            if cl.get("over_symbol_cap"):
                notes.append("⚠ 接货后可能超标的上限")
        else:
            title = f"被 Call 走 {symbol}"
            steps.append({
                "trade_type": "CALLED_AWAY",
                "symbol": symbol,
                "cycle_id": cycle_id,
                "contract_code": item.get("contract_code"),
                "strike": strike,
                "expiry": expiry,
                "qty": qty,
                "price": float(strike or 0),
                "fee": 0,
                "contract_size": size,
                "note": "一键执行·CALLED_AWAY",
            })
            notes.append("交货后周期结束,可重开 CSP")

    elif action == "roll":
        title = f"Roll {symbol} {side}"
        if not roll:
            return {
                "ok": False,
                "error": "Roll 需要 roll 候选(strike/expiry/price/contract_code)",
                "action": action,
            }
        close_tt = "BUY_PUT_CLOSE" if side == "PUT" else "BUY_CALL_CLOSE"
        open_tt = "SELL_PUT" if side == "PUT" else "SELL_CALL"
        steps.append({
            "trade_type": close_tt,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "contract_code": item.get("contract_code"),
            "strike": strike,
            "expiry": expiry,
            "qty": qty,
            "price": float(px or 0),
            "fee": float(roll.get("fee_close") or 0),
            "contract_size": size,
            "note": "一键Roll·平仓",
        })
        steps.append({
            "trade_type": open_tt,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "contract_code": roll.get("contract_code") or roll.get("sell_contract_code"),
            "strike": roll.get("strike") or roll.get("sell_strike"),
            "expiry": roll.get("expiry") or roll.get("sell_expiry"),
            "qty": qty,
            "price": float(roll.get("price") or roll.get("sell_price") or 0),
            "fee": float(roll.get("fee_open") or 0),
            "contract_size": size,
            "note": "一键Roll·开仓",
        })
        notes.append("两腿同一 cycle;确认新 strike 仍在愿接区内")
    else:
        return {"ok": False, "error": f"未知 action: {action}"}

    return {
        "ok": True,
        "kind": "manage",
        "action": action,
        "title": title,
        "symbol": symbol,
        "cycle_id": cycle_id,
        "steps": steps,
        "notes": notes,
        "source_action_code": code,
        "created_at": _now(),
    }


def draft_from_opportunity(opp: Dict[str, Any], *, qty: Optional[float] = None) -> Dict[str, Any]:
    """开仓机会 → 卖 Put/Call 草稿。"""
    side = (opp.get("side") or "PUT").upper()
    tt = "SELL_PUT" if side == "PUT" else "SELL_CALL"
    symbol = opp.get("symbol")
    q = qty if qty is not None else float(opp.get("suggest_qty") or opp.get("qty") or 1)
    if q < 1:
        q = 1
    px = opp.get("bid") or opp.get("mid") or opp.get("last") or 0
    steps = [{
        "trade_type": tt,
        "symbol": symbol,
        "new_cycle": True,
        "contract_code": opp.get("contract_code"),
        "strike": opp.get("strike"),
        "expiry": opp.get("expiry"),
        "qty": q,
        "price": float(px or 0),
        "fee": 0,
        "contract_size": int(opp.get("contract_size") or 100),
        "note": f"一键开仓·score={opp.get('score')}",
        "entry_score": opp.get("score"),
    }]
    notes = []
    if opp.get("covers_earnings"):
        notes.append("⚠ 存续覆盖财报")
    if opp.get("exceeds_capital"):
        notes.append("⚠ 可能超资金上限")
    if opp.get("high_corr_warn"):
        notes.append(f"⚠ 高相关: {opp.get('high_corr_warn')}")
    return {
        "ok": True,
        "kind": "open",
        "action": "open",
        "title": f"开仓 {symbol} 卖{side} ${opp.get('strike')}",
        "symbol": symbol,
        "steps": steps,
        "notes": notes,
        "created_at": _now(),
    }


def apply_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """执行草稿记账(台账),返回最后 cycle + 指派后提示。"""
    from app.data import wheel_repository as repo
    from app.data.wheel_repository import WheelError
    from app.core.wheel_post_assign import post_assign_hint

    if not draft.get("ok"):
        raise WheelError(draft.get("error") or "无效草稿")
    steps = draft.get("steps") or []
    if not steps:
        raise WheelError("草稿无步骤")
    cycle = None
    for st in steps:
        cycle = repo.record_trade(
            symbol=st["symbol"],
            trade_type=st["trade_type"],
            contract_code=st.get("contract_code"),
            strike=st.get("strike"),
            expiry=st.get("expiry"),
            qty=float(st.get("qty") or 1),
            price=float(st.get("price") or 0),
            fee=float(st.get("fee") or 0),
            contract_size=int(st.get("contract_size") or 100),
            note=st.get("note"),
            cycle_id=st.get("cycle_id"),
            new_cycle=bool(st.get("new_cycle")),
        )
        # 写 entry_score
        if st.get("entry_score") is not None and cycle:
            try:
                from app.data.database import get_db, _now_iso
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE wheel_cycles SET entry_score = ? WHERE id = ?",
                        (float(st["entry_score"]), cycle["id"]),
                    )
                    conn.commit()
                    cycle["entry_score"] = float(st["entry_score"])
                finally:
                    conn.close()
            except Exception:
                pass

    hint = None
    if cycle and cycle.get("status") == "HOLDING":
        hint = post_assign_hint(cycle)
    return {
        "ok": True,
        "cycle": cycle,
        "post_assign": hint,
        "applied_steps": len(steps),
    }
