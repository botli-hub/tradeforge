"""开仓张数建议(suggest_qty)。

只会把张数往下压,不会突破 wheel_sizing.max_contracts 硬顶:
  base   = PUT: floor(标的资金余量 / (strike×size));  CALL: floor(未覆盖股数 / size)
  × IV 档 size_mult(apply_iv_size_mult)
  ∧ 风险预算 suggested_max_qty(wheel_risk_budget.enabled 且 use_risk_budget_cap)
  ∧ max_contracts(默认 1 → 默认行为与旧版一致:每条机会 1 张)
不自动下单;exec-signals 通道 qty 固定 1,不受影响。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

DEFAULT_SIZING = {"max_contracts": 1, "apply_iv_size_mult": True, "use_risk_budget_cap": True}


def sizing_cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(DEFAULT_SIZING)
    out.update((cfg or {}).get("wheel_sizing") or {})
    try:
        out["max_contracts"] = max(1, int(out.get("max_contracts") or 1))
    except (TypeError, ValueError):
        out["max_contracts"] = 1
    return out


def suggest_qty(
    opp: Dict[str, Any],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    headroom: Optional[float] = None,
    uncovered_shares: Optional[float] = None,
    size_mult: Optional[float] = None,
    risk_budget: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """返回 {suggest_qty, uncapped, capped_by[], inputs}。suggest_qty=0 仅在风险预算启用且给出 0 时。"""
    sc = sizing_cfg(cfg)
    side = str(opp.get("side") or "PUT").upper()
    size = float(opp.get("contract_size") or 100)
    strike = float(opp.get("strike") or 0)
    capped_by = []

    base: Optional[int] = None
    if side == "PUT" and headroom is not None and strike > 0:
        base = max(0, math.floor(float(headroom) / (strike * size)))
        capped_by.append("capital_headroom")
    elif side == "CALL" and uncovered_shares is not None:
        base = max(0, math.floor(float(uncovered_shares) / size))
        capped_by.append("uncovered_shares")
    if base is None:
        base = sc["max_contracts"]

    mult = 1.0
    if sc.get("apply_iv_size_mult") and size_mult:
        try:
            mult = max(0.0, float(size_mult))
        except (TypeError, ValueError):
            mult = 1.0
    scaled = math.floor(base * mult + 1e-9)
    if mult != 1.0:
        capped_by.append(f"iv_size_mult×{mult:g}")

    qty = min(scaled, sc["max_contracts"])
    if scaled > sc["max_contracts"]:
        capped_by.append("max_contracts")

    risk_max = None
    if sc.get("use_risk_budget_cap") and risk_budget and risk_budget.get("enabled"):
        risk_max = risk_budget.get("suggested_max_qty")
        if risk_max is not None:
            if int(risk_max) < qty:
                capped_by.append("risk_budget")
            qty = min(qty, int(risk_max))
            if qty <= 0:
                return {
                    "suggest_qty": 0, "uncapped": scaled, "capped_by": capped_by,
                    "inputs": {"base": base, "size_mult": mult, "risk_max": risk_max,
                               "max_contracts": sc["max_contracts"]},
                }

    # 资金/持股不足不在此处拦截(已有 exceeds_capital / 风险校验旗标),最少建议 1 张
    qty = max(1, qty)
    return {
        "suggest_qty": int(qty), "uncapped": int(scaled), "capped_by": capped_by,
        "inputs": {"base": base, "size_mult": mult, "risk_max": risk_max,
                   "max_contracts": sc["max_contracts"]},
    }


def current_size_mult(cfg: Optional[Dict[str, Any]]) -> float:
    reg = (cfg or {}).get("_iv_regime") or {}
    if reg.get("size_mult") is not None:
        try:
            return float(reg["size_mult"])
        except (TypeError, ValueError):
            return 1.0
    try:
        from app.core.wheel_iv_regime import resolve_regime
        return float(resolve_regime(cfg).get("size_mult") or 1.0)
    except Exception:
        return 1.0
