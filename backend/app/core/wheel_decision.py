"""Wheel 持仓动态决策树(量化阈值版).

核心树在 wheel_decision_tree(默认允许接货);
本模块叠加只收租偏离守卫、浮盈两本账、台账权利金、过线分叉与持仓期事件日分叉.
不改 POSITION_QUANT 数值,不自动下单.
"""
from app.core.wheel_decision_lib import *  # noqa: F401,F403
from app.core.wheel_decision_tree import format_alert_line  # noqa: F401
from app.core.wheel_decision_tree import decide_position as _decide_position_core
from app.core.wheel_stance import decorate


def decide_position(item, min_annualized, profit_target, pos_cfg=None):
    item = dict(item or {})
    from app.core.premium_ledger import apply_premium_to_item, resolve_premium
    prem = resolve_premium(item)
    item = apply_premium_to_item(item, prem)
    result = _decide_position_core(item, min_annualized, profit_target, pos_cfg)
    result = decorate(result, item, pos_cfg)
    try:
        from app.core.wheel_dispose import apply_dispose
        result = apply_dispose(result, item, prem, pos_cfg)
    except Exception:
        result["premium"] = prem
        if not prem.get("calibrated"):
            result["premium_uncalibrated"] = True
    try:
        from app.core.wheel_event_dispose import apply_event_dispose
        result = apply_event_dispose(result, item, pos_cfg)
    except Exception:
        pass
    try:
        from app.core.wheel_paths import build_paths
        from app.core.wheel_stance import resolve_stance
        tree = result.get("decision_tree") or {}
        result["paths"] = build_paths(
            item,
            quote=result.get("quote") or {},
            action_code=result.get("action_code") or "NONE",
            branch=result.get("decision_branch") or tree.get("branch") or "",
            stance=tree.get("stance") or resolve_stance(item),
            profit_mid=tree.get("profit_pct_mid"),
            profit_conservative=tree.get("profit_pct_conservative"),
            close_claimable_flag=bool(result.get("close_claimable")),
        )
    except Exception:
        pass
    return result
