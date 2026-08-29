"""可成交报价 + 接货/买回/Roll 三列对比.

持仓树仍用第一命中出 action_code; 本模块提供:
- 点差/保守买回,禁止用不可成交的 ask 宣称 50% 止盈
- 三列数字,让交易员先看数再选动作
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.wheel_roll import _mid, _tick, spread_pct

WIDE_SPREAD_PCT = 8.0


def quote_quality(
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    last: Optional[float] = None,
    wide_pct: float = WIDE_SPREAD_PCT,
) -> Dict[str, Any]:
    try:
        bid_f = float(bid) if bid is not None else 0.0
    except (TypeError, ValueError):
        bid_f = 0.0
    try:
        ask_f = float(ask) if ask is not None else 0.0
    except (TypeError, ValueError):
        ask_f = 0.0
    try:
        last_f = float(last) if last is not None else 0.0
    except (TypeError, ValueError):
        last_f = 0.0

    mid = _mid(bid_f, ask_f) if bid_f > 0 and ask_f > 0 else (ask_f or last_f or bid_f)
    cons = (ask_f + _tick(ask_f)) if ask_f > 0 else (last_f if last_f > 0 else 0.0)
    sp = spread_pct(bid_f, ask_f) if bid_f > 0 and ask_f > 0 else None
    wide = bool(sp is not None and sp > float(wide_pct))
    # 无双边报价时不硬判不可成交,沿用 ask
    fillable = bool(cons > 0 and not wide)
    return {
        "bid": round(bid_f, 4) if bid_f > 0 else None,
        "ask": round(ask_f, 4) if ask_f > 0 else None,
        "last": round(last_f, 4) if last_f > 0 else None,
        "mid": round(mid, 4) if mid else None,
        "conservative": round(cons, 4) if cons else None,
        "spread_pct": sp,
        "wide_spread": wide,
        "fillable": fillable,
    }


def profit_from(open_px: Optional[float], close_px: Optional[float]) -> Optional[float]:
    try:
        o = float(open_px or 0)
        c = float(close_px) if close_px is not None else None
    except (TypeError, ValueError):
        return None
    if o <= 0 or c is None or c < 0:
        return None
    return round((o - c) / o * 100, 1)


def close_claimable(
    *,
    profit_pct: Optional[float],
    profit_target: float,
    quote: Dict[str, Any],
    profit_conservative: Optional[float],
) -> bool:
    """能宣称止盈平仓:点差未过宽;有双边报价时保守买回也要达标."""
    if not quote.get("fillable"):
        return False
    two_sided = quote.get("bid") and quote.get("ask")
    if two_sided and profit_conservative is not None:
        return profit_conservative >= float(profit_target)
    if profit_pct is None:
        return False
    return profit_pct >= float(profit_target)


def build_paths(
    item: Dict[str, Any],
    *,
    quote: Dict[str, Any],
    action_code: str,
    branch: str,
    stance: str = "acquire",
    profit_mid: Optional[float] = None,
    profit_conservative: Optional[float] = None,
    close_claimable_flag: bool = True,
) -> Dict[str, Any]:
    side = str(item.get("side") or "PUT").upper()
    strike = float(item.get("strike") or 0)
    spot = float(item.get("spot") or 0)
    qty = float(item.get("qty") or 1)
    size = float(item.get("contract_size") or 100)
    open_px = float(item.get("open_price") or 0)
    floor_price = item.get("floor_price")
    try:
        floor_price = float(floor_price) if floor_price is not None else None
    except (TypeError, ValueError):
        floor_price = None
    sell_above = item.get("sell_above")
    try:
        sell_above = float(sell_above) if sell_above is not None else None
    except (TypeError, ValueError):
        sell_above = None
    cb = item.get("cost_basis")
    if cb is None:
        cb = item.get("share_cost")
    try:
        cb = float(cb) if cb is not None else None
    except (TypeError, ValueError):
        cb = None

    cons = quote.get("conservative") or quote.get("ask") or item.get("buyback_ask") or 0
    try:
        cons_f = float(cons or 0)
    except (TypeError, ValueError):
        cons_f = 0.0
    close_usd = round(cons_f * size * qty, 2)
    pnl_usd = round((open_px - cons_f) * size * qty, 2) if open_px and cons_f else None
    wide = bool(quote.get("wide_spread"))
    fillable = bool(quote.get("fillable"))

    if side == "PUT":
        eff_cost = round(strike - open_px, 4) if strike and open_px else strike
        vs_spot = round(eff_cost - spot, 4) if spot and eff_cost is not None else None
        in_plan = bool(floor_price is None or floor_price <= 0 or strike <= floor_price)
        cash_due = round(strike * size * qty, 2)
        assign = {
            "label": "接货",
            "effective_cost": eff_cost,
            "vs_spot": vs_spot,
            "floor_ok": in_plan,
            "cash_due": cash_due,
            "note": (
                f"成本 {eff_cost:g} vs 现价 {spot:g}"
                + (" · 在愿接内" if in_plan else " · 超愿接")
            ),
        }
        roll_cap = floor_price
        roll_note = (
            f"买回硬成本 ${close_usd:.0f}/张档;新卖需 ≥ {cons_f:g} 才净贷方"
            + (f";新 strike ≤ 愿接 {floor_price:g}" if floor_price else "")
        )
        freed = cash_due
    else:
        proceeds = round(strike * size * qty, 2)
        vs_cost = round(strike - cb, 4) if cb else None
        ok_sell = bool(
            (sell_above is not None and strike >= sell_above)
            or (cb is not None and strike >= cb)
        )
        assign = {
            "label": "交货",
            "effective_cost": strike,
            "vs_spot": round(strike - spot, 4) if spot else None,
            "floor_ok": ok_sell,
            "cash_due": proceeds,
            "note": (
                f"按 {strike:g} 卖出"
                + (" · 已在愿卖/成本上" if ok_sell else " · 低于成本/愿卖")
            ),
        }
        roll_cap = sell_above if sell_above is not None else cb
        roll_note = f"买回硬成本 ${close_usd:.0f};想继续持股则 Roll 调高"
        freed = 0.0

    close_col = {
        "label": "买回",
        "price": cons_f or None,
        "fillable": fillable,
        "spread_pct": quote.get("spread_pct"),
        "pnl_usd": pnl_usd,
        "pnl_pct_mid": profit_mid,
        "pnl_pct_conservative": profit_conservative,
        "freed": freed,
        "note": (
            f"点差 {quote.get('spread_pct')}% 过宽,不能按中间价宣称止盈"
            if wide
            else (f"保守买回 {cons_f:g}" if cons_f else "无买回价")
        ),
    }
    roll_col = {
        "label": "Roll",
        "close_cost": close_usd,
        "min_new_premium": cons_f or None,
        "strike_cap": roll_cap,
        "note": roll_note,
    }

    rec, rec_why = _recommend(
        side=side,
        stance=str(stance or "acquire"),
        action_code=action_code,
        branch=branch,
        assign=assign,
        close_claimable_flag=close_claimable_flag,
        fillable=fillable,
        wide=wide,
        itm=bool(item.get("itm")),
    )
    return {
        "quote": {
            "bid": quote.get("bid"),
            "ask": quote.get("ask"),
            "mid": quote.get("mid"),
            "conservative": quote.get("conservative"),
            "spread_pct": quote.get("spread_pct"),
            "wide_spread": wide,
            "fillable": fillable,
        },
        "assign": assign,
        "close": close_col,
        "roll": roll_col,
        "recommend": rec,
        "recommend_reason": rec_why,
    }


def _recommend(
    *,
    side: str,
    stance: str,
    action_code: str,
    branch: str,
    assign: Dict[str, Any],
    close_claimable_flag: bool,
    fillable: bool,
    wide: bool,
    itm: bool,
) -> tuple:
    code = (action_code or "").upper()
    income = stance in ("income", "只收租")

    if wide and code in ("CLOSE", "REPLACE") and branch in (
        "close_profit", "close_velocity", "replace_soft",
    ):
        return "hold", "点差过宽,止盈不能按当前 ask 成交,挂中间价等"

    if code == "PREPARE_ASSIGN" or branch in (
        "deep_itm_acquire", "deep_itm_call_deliver", "prepare_assign",
    ):
        if assign.get("floor_ok") is False and side == "PUT":
            return "roll", "超愿接,接货偏离计划,优先 Roll 调低"
        return "assign", assign.get("note") or "按计划接货/交货"

    if code == "ROLL_ADJUST" or code == "ROLL":
        return "roll", "结构要改 strike 或展期"

    if code in ("CLOSE", "REPLACE"):
        if not fillable and code == "CLOSE":
            return "hold", "买回价不可靠,限价再平"
        return "close", "买回释放/揭盖"

    if income and itm and side == "PUT":
        return "roll", "不愿接货"

    if code == "HOLD_THETA":
        return "hold", "吃 θ,三列仅供对照"
    return "hold", "观察,对照三列后选"


def apply_fill_gate(
    *,
    code: str,
    branch: str,
    close_claimable_flag: bool,
    wide: bool,
    hold_for_theta: bool,
) -> Dict[str, Any]:
    """宽点差时撤销「按 50% 宣称 CLOSE」."""
    if code == "CLOSE" and branch in ("close_profit", "close_velocity") and not close_claimable_flag:
        if hold_for_theta:
            return {
                "action_code": "HOLD_THETA",
                "decision_branch": "wide_spread_theta",
                "action_hint": "点差过宽,不能按 ask 止盈,继续吃θ或挂限价",
                "prefer_card": "no_roll",
            }
        return {
            "action_code": "NONE",
            "decision_branch": "wide_spread_wait",
            "action_hint": "点差过宽,不能按 ask 宣称止盈,挂中间价",
            "prefer_card": "no_roll",
        }
    return {}
