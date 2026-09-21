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
        else:
            return {"ok": False, "error": "请明确选择实际成交/指派/到期事件;建议不等于成交"}

    cycle_id = item.get("cycle_id")
    symbol = item.get("symbol")
    qty = float(item.get("qty") or 1)
    size = int(item.get("contract_size") or 100)
    strike = item.get("strike")
    expiry = item.get("expiry")
    explicit_fill_price = buyback_price is not None
    px = buyback_price
    if px is None:
        px = item.get("buyback_ask") or item.get("current_price") or 0
    # A displayed ask is only a hint unless it carries a fresh two-sided
    # quote.  Keep it in the preview for context, but require the user to
    # supply the actual fill before this draft can be posted to the ledger.
    from app.core.wheel_quotes import executable_quote
    quote_ok = executable_quote(item)
    # Assignment/expiry are event confirmations and do not need an option
    # buyback quote.  Only actions that post an option close fill require a
    # user supplied price when the displayed quote is not executable.
    requires_fill_price = action in ("close", "roll") and not explicit_fill_price and not quote_ok

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

    if requires_fill_price and action in ("close", "roll"):
        notes.append("⚠ 当前买回价不是新鲜双边可成交行情，登记前必须填写实际成交价")

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
        "requires_fill_price": requires_fill_price,
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
    px = opp.get("bid") or 0
    from app.core.wheel_quotes import executable_quote
    requires_fill_price = not executable_quote(opp)
    steps = [{
        "trade_type": tt,
        "symbol": symbol,
        "new_cycle": side == "PUT",
        "cycle_id": opp.get("cycle_id") if side == "CALL" else None,
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
    if requires_fill_price:
        notes.append("⚠ 当前报价不是新鲜双边可成交行情，登记前必须填写实际成交价")
    return {
        "ok": True,
        "kind": "open",
        "action": "open",
        "title": f"开仓 {symbol} 卖{side} ${opp.get('strike')}",
        "symbol": symbol,
        "steps": steps,
        "notes": notes,
        "requires_fill_price": requires_fill_price,
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
    if draft.get("requires_fill_price"):
        raise WheelError("报价不是新鲜双边可成交行情,请填实际成交价后再记账")
    for step in steps:
        if step.get("trade_type") in ("SELL_PUT", "SELL_CALL", "BUY_PUT_CLOSE", "BUY_CALL_CLOSE"):
            if float(step.get("price") or 0) <= 0:
                raise WheelError("期权成交价必须大于 0")
    result = repo.record_trades(steps, execution_id=draft.get("execution_id"), mode=draft.get("mode", "recorded"))
    cycle = result["cycle"]

    hint = None
    if cycle and cycle.get("status") == "HOLDING":
        hint = post_assign_hint(cycle)
    return {
        "ok": True,
        "cycle": cycle,
        "post_assign": hint,
        "applied_steps": len(steps),
        "risk": result["risk"],
        "execution_id": result["execution_id"],
    }


def register_roll_draft(body):
    """Resolve the selected leg, then record both fills in one transaction.

    Partial Put rolls open a separate cycle so the original put and shares stay.
    Retries resolve from stored payload rather than the now-changed position.
    """
    import json
    from app.data import wheel_repository as repo
    from app.data.database import get_db
    request = dict(body)
    execution_id = body.get("execution_id")
    if not execution_id:
        raise repo.WheelError("Roll 需要 execution_id")
    conn = get_db()
    try:
        previous = conn.execute("SELECT result_json FROM wheel_executions WHERE id=?", (execution_id,)).fetchone()
        if previous:
            result = json.loads(previous["result_json"])
            if result.get("roll_request") != request:
                raise repo.WheelError("execution_id 已用于不同内容")
            return result
    finally:
        conn.close()
    c = repo.get_cycle(body["cycle_id"])
    from app.core.wheel_cc_legs import expand_open_option_rows
    rows = expand_open_option_rows([c] if c else [])
    code = body.get("close_contract_code")
    if code:
        rows = [r for r in rows if (r.get("open_contract_code") or "").removeprefix("US.") == code.removeprefix("US.")]
    if len(rows) != 1:
        raise repo.WheelError("请选择唯一要 Roll 的在场合约")
    leg = rows[0]
    side = leg["open_option_type"]
    close = dict(symbol=c["symbol"], cycle_id=c["id"],
        trade_type="BUY_PUT_CLOSE" if side == "PUT" else "BUY_CALL_CLOSE",
        contract_code=leg["open_contract_code"], strike=leg["open_strike"], expiry=leg["open_expiry"],
        qty=body.get("qty", 1), contract_size=body.get("contract_size", 100),
        price=body["buyback_price"], fee=body.get("fee_close", 0), note="Roll 平仓腿")
    opening = dict(symbol=c["symbol"], cycle_id=c["id"],
        trade_type="SELL_PUT" if side == "PUT" else "SELL_CALL",
        contract_code=body["sell_contract_code"], strike=body["sell_strike"], expiry=body["sell_expiry"],
        qty=close["qty"], contract_size=close["contract_size"], price=body["sell_price"],
        fee=body.get("fee_open", 0), note="Roll 开仓腿")
    if side == "PUT" and (close["qty"] < leg["open_qty"] or c.get("shares", 0) > 0):
        opening.update(cycle_id=None, new_cycle=True)
    return repo.record_trades([close, opening], execution_id=execution_id,
        mode=body.get("mode", "recorded"), request_context={"roll_request": request})
