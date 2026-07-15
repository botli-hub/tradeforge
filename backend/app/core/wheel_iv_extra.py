"""IV 期限结构与 Skew(从已加载的多到期日期权链计算,尽量不额外请求)。"""
from typing import Any, Dict, List, Optional


def term_structure_from_chains(
    chains: List[Dict[str, Any]],
    spot: float,
) -> Dict[str, Any]:
    """chains: [{expiry, dte, contracts}, ...]
    返回近月/次月 ATM IV 与 contango/backwardation。
    """
    from app.core.volatility import atm_iv_from_chain

    points = []
    for ch in chains:
        iv = atm_iv_from_chain(ch.get("contracts") or [], spot)
        if iv is None:
            continue
        points.append({
            "expiry": ch.get("expiry"),
            "dte": ch.get("dte"),
            "atm_iv": iv,
        })
    points.sort(key=lambda x: x.get("dte") or 0)
    near = points[0] if points else None
    next_m = points[1] if len(points) > 1 else None
    shape = None
    spread = None
    if near and next_m and near.get("atm_iv") and next_m.get("atm_iv"):
        spread = round(next_m["atm_iv"] - near["atm_iv"], 2)
        if spread > 1.0:
            shape = "contango"  # 远月更高,卖近月 theta 相对一般
        elif spread < -1.0:
            shape = "backwardation"  # 近月紧张,权利金更肥但风险事件?
        else:
            shape = "flat"
    return {
        "points": points,
        "near": near,
        "next": next_m,
        "term_spread": spread,
        "shape": shape,
        "hint": {
            "contango": "远月IV更高:短周期卖方尚可,注意不要过度追远月",
            "backwardation": "近月IV抬升:短周期权利金更肥,核对是否事件驱动",
            "flat": "期限结构平坦",
        }.get(shape or "", None),
    }


def skew_from_chain(contracts: List[Dict[str, Any]], spot: float) -> Dict[str, Any]:
    """OTM put IV − ATM IV;call 翼简要。"""
    from app.core.volatility import atm_iv_from_chain

    atm = atm_iv_from_chain(contracts, spot)
    if not atm or not spot:
        return {"atm_iv": atm, "put_skew": None, "call_skew": None}

    def _norm_iv(iv: float) -> float:
        return iv * 100 if iv < 3 else iv

    put_ivs = []
    call_ivs = []
    for c in contracts:
        iv = c.get("iv") or 0
        if iv <= 0:
            continue
        ivn = _norm_iv(iv)
        k = c.get("strike") or 0
        ot = c.get("option_type")
        # 约 5-15% OTM
        if ot == "PUT" and 0.85 * spot <= k <= 0.97 * spot:
            put_ivs.append(ivn)
        if ot == "CALL" and 1.03 * spot <= k <= 1.15 * spot:
            call_ivs.append(ivn)

    put_avg = sum(put_ivs) / len(put_ivs) if put_ivs else None
    call_avg = sum(call_ivs) / len(call_ivs) if call_ivs else None
    put_skew = round(put_avg - atm, 2) if put_avg is not None else None
    call_skew = round(call_avg - atm, 2) if call_avg is not None else None

    warn = None
    if put_skew is not None and put_skew > 8:
        warn = "Put skew 偏陡:市场对下行保护要价高,近 delta put 更贵也更险"

    return {
        "atm_iv": atm,
        "otm_put_iv": round(put_avg, 2) if put_avg else None,
        "otm_call_iv": round(call_avg, 2) if call_avg else None,
        "put_skew": put_skew,
        "call_skew": call_skew,
        "warn": warn,
    }
