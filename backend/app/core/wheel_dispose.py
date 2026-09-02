"""过线分叉 + CSP 平仓机会成本.

不改 POSITION_QUANT / profit_target_pct 数值.
过线(已校准权利金 + profit_pct>=现有止盈线)必须进「必须处理」.
HOLD_THETA / 吃θ观察不能藏;续拿文案必须写明是方向赌注.
不自动下单.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

CLAIM_WORDS = ("已止盈", "过线")


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def profit_target_of(result: Dict[str, Any], default: float = 50.0) -> float:
    """现有止盈线字段: quant_thresholds.profit_target_pct. 不改数值."""
    q = result.get("quant_thresholds") or {}
    v = _f(q.get("profit_target_pct"))
    if v is not None and v > 0:
        return v
    tree = result.get("decision_tree") or {}
    q2 = tree.get("quant") or {}
    v2 = _f(q2.get("profit_target_pct"))
    return v2 if v2 is not None and v2 > 0 else float(default)


def premium_is_calibrated(prem: Optional[Dict[str, Any]], result: Optional[Dict[str, Any]] = None) -> bool:
    if prem is not None and "calibrated" in prem:
        return bool(prem.get("calibrated"))
    if result and result.get("premium_uncalibrated"):
        return False
    p = (result or {}).get("premium") or {}
    if "calibrated" in p:
        return bool(p.get("calibrated"))
    return True


def past_take_profit_line(result: Dict[str, Any], prem: Optional[Dict[str, Any]] = None) -> bool:
    """过现有止盈线. 未校准权利金永远不算过线."""
    if not premium_is_calibrated(prem, result):
        return False
    tree = result.get("decision_tree") or {}
    if tree.get("profit_hit"):
        return True
    pp = _f((result.get("profit_pct") if "profit_pct" in result else None) or tree.get("profit_pct"))
    if pp is None:
        return False
    return pp >= profit_target_of(result)


def strip_take_profit_claims(text: Optional[str]) -> str:
    s = str(text or "")
    for w in CLAIM_WORDS:
        s = s.replace(w, "未校准")
    if "止盈" in s and "未校准" not in s:
        s = s.replace("止盈平仓", "权利金未校准").replace("止盈", "未校准")
    return s


def build_dispose_fork(item: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """落袋 vs 续拿. 续拿=方向赌注,不是默认吃θ."""
    tgt = profit_target_of(result)
    tree = result.get("decision_tree") or {}
    pp = tree.get("profit_pct_conservative")
    if pp is None:
        pp = item.get("profit_pct")
    side = str(item.get("side") or result.get("side") or "").upper()
    stance = (result.get("stance") or tree.get("stance") or item.get("stance") or "acquire")
    bag_copy = (
        f"浮盈已过现有止盈线({tgt:g}%),买回落袋"
        + ("、释放 CSP 担保" if side != "CALL" else "、结束 Call 义务")
        + "。决策辅助,不自动下单。"
    )
    hold_copy = (
        "续拿是方向赌注,不是默认继续吃θ。"
        "只有你愿意对股价方向下注时才续拿;HOLD_THETA/吃θ观察不能代替这次选择。"
    )
    if str(stance).lower() in ("income", "只收租"):
        hold_copy = "只收租立场下续拿更偏离收租纪律。" + hold_copy
    return {
        "kind": "dispose_fork",
        "must_manage": True,
        "past_line": True,
        "profit_target_pct": tgt,
        "profit_pct": pp,
        "stance": stance,
        "bag": {
            "label": "落袋",
            "action": "close",
            "copy": bag_copy,
        },
        "hold": {
            "label": "续拿",
            "action": "hold",
            "directional_bet": True,
            "copy": hold_copy,
        },
        "note": "分叉强制进今日必须处理;续拿不是默认吃θ",
    }


def csp_opportunity_cost(item: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """CSP 平仓机会成本:释放担保 + 可再开对照. 决策辅助. 缺字段不崩溃."""
    try:
        side = str((item or {}).get("side") or "").upper()
        if side != "PUT":
            return None
        result = result or {}
        tree = result.get("decision_tree") or {}
        strike = _f(item.get("strike"))
        qty = _f(item.get("qty")) or 1.0
        size = _f(item.get("contract_size")) or 100.0
        collateral = round(strike * qty * size, 2) if strike and strike > 0 else None
        remaining_ann = result.get("remaining_annualized")
        if remaining_ann is None:
            remaining_ann = tree.get("remaining_ann")
        captured_ann = tree.get("captured_annualized")
        q = result.get("quant_thresholds") or {}
        min_ann = _f(q.get("min_annualized"))
        close_notional = _f(tree.get("close_notional"))
        open_px = _f(item.get("open_price"))
        close_px = _f(item.get("buyback_ask")) or _f(item.get("current_price"))
        captured_usd = None
        if open_px is not None and close_px is not None:
            captured_usd = round((open_px - close_px) * qty * size, 2)
        dte = item.get("dte")
        try:
            dte_i = int(dte) if dte is not None else None
        except (TypeError, ValueError):
            dte_i = None
        reopen_credit = None
        if collateral and min_ann and dte_i and dte_i > 0:
            reopen_credit = round(collateral * float(min_ann) / 100.0 * dte_i / 365.0, 2)
        compare = None
        if remaining_ann is not None and min_ann is not None:
            if float(remaining_ann) < float(min_ann):
                compare = (
                    f"当前剩余年化 {remaining_ann}% < 再开目标 {min_ann:g}%"
                    " — 落袋周转可能更优(仅决策辅助)"
                )
            else:
                compare = (
                    f"当前剩余年化 {remaining_ann}% vs 再开目标 {min_ann:g}%"
                    " (仅决策辅助,不自动下单)"
                )
        elif min_ann is not None:
            compare = f"释放担保后可对照再开目标年化 {min_ann:g}%(仅决策辅助)"
        return {
            "aid_only": True,
            "side": "PUT",
            "collateral_released": collateral,
            "captured_if_close_usd": captured_usd,
            "close_cost_usd": close_notional,
            "remaining_ann_if_hold": remaining_ann,
            "captured_ann": captured_ann,
            "reopen": {
                "label": "释放担保后可再开同类 CSP",
                "target_ann": min_ann,
                "est_credit_same_dte": reopen_credit,
                "dte": dte_i,
                "note": compare,
            },
            "note": "决策辅助,不自动下单,不改止盈阈值",
        }
    except Exception:
        return {
            "aid_only": True,
            "error": True,
            "note": "机会成本暂不可算(缺字段已忽略,未崩溃)",
        }


def item_must_manage(item: Optional[Dict[str, Any]]) -> bool:
    """今日必须处理:过线分叉 / 显式 must_manage / 已校准 profit_hit."""
    if not item:
        return False
    if item.get("must_manage") or item.get("dispose_fork"):
        return True
    if item.get("premium_uncalibrated"):
        return False
    prem = item.get("premium") or {}
    if prem.get("calibrated") is False:
        return False
    tree = item.get("decision_tree") or {}
    if tree.get("profit_hit"):
        return True
    return False


def apply_dispose(
    result: Dict[str, Any],
    item: Dict[str, Any],
    prem: Optional[Dict[str, Any]] = None,
    pos_cfg: Optional[Dict[str, Any]] = None,  # noqa: ARG001 — 与 decide_position 签名对齐
) -> Dict[str, Any]:
    """决策树后处理:未校准禁宣称;过线强制分叉进必须处理."""
    prem = prem or result.get("premium") or item.get("premium") or {}
    result = dict(result)
    tree = dict(result.get("decision_tree") or {})
    result["premium"] = prem
    calibrated = premium_is_calibrated(prem, result)

    if not calibrated:
        tree["profit_hit"] = False
        tree["premium_calibrated"] = False
        result["decision_tree"] = tree
        result["premium_uncalibrated"] = True
        result["must_manage"] = False
        result["dispose_fork"] = None
        result["close_claimable"] = False
        hint = result.get("action_hint") or ""
        branch = result.get("decision_branch") or tree.get("branch") or ""
        claimed = (
            branch in ("close_profit", "close_velocity")
            or any(w in hint for w in CLAIM_WORDS)
            or "止盈" in hint
        )
        if claimed:
            result["action_code"] = "NONE"
            result["action_hint"] = "权利金未校准,不能宣称止盈/过线"
            result["decision_branch"] = "premium_uncalibrated"
            tree["branch"] = "premium_uncalibrated"
            result["prefer_card"] = "no_roll"
            result["action_priority"] = max(int(result.get("action_priority") or 9), 6)
        else:
            result["action_hint"] = strip_take_profit_claims(hint) or hint
        reasons: List[str] = []
        for r in result.get("reasons") or []:
            if any(w in r for w in CLAIM_WORDS) or "硬止盈" in r:
                continue
            reasons.append(r)
        tag = "未校准:台账无开仓成交,止盈数学不可用"
        if tag not in reasons:
            reasons.append(tag)
        result["reasons"] = reasons
        result["decision_tree"] = tree
        try:
            oc = csp_opportunity_cost(item, result)
            if oc:
                result["csp_opportunity_cost"] = oc
        except Exception:
            result["csp_opportunity_cost"] = {"aid_only": True, "error": True}
        return result

    tree["premium_calibrated"] = True
    result["premium_uncalibrated"] = False
    result["decision_tree"] = tree

    if past_take_profit_line(result, prem):
        fork = build_dispose_fork(item, result)
        result["dispose_fork"] = fork
        result["must_manage"] = True
        prio = int(result.get("action_priority") or 9)
        result["action_priority"] = min(prio, 3)
        code = (result.get("action_code") or "").upper()
        if code in ("HOLD_THETA", "NONE"):
            extra = "过线须处理:续拿是方向赌注,不是默认吃θ"
            hint = result.get("action_hint") or ""
            if "方向赌注" not in hint:
                result["secondary_hint"] = extra
            reasons = list(result.get("reasons") or [])
            if extra not in reasons:
                reasons.append(extra)
            result["reasons"] = reasons
        if str(item.get("side") or "").upper() == "PUT":
            oc = csp_opportunity_cost(item, result)
            if oc:
                result["csp_opportunity_cost"] = oc
    else:
        result["must_manage"] = bool(result.get("must_manage"))
        code = (result.get("action_code") or "").upper()
        if str(item.get("side") or "").upper() == "PUT" and code in ("CLOSE", "REPLACE"):
            oc = csp_opportunity_cost(item, result)
            if oc:
                result["csp_opportunity_cost"] = oc

    return result
