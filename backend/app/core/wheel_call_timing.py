"""持股卖 Call 时机层。

池 = HOLDING(已持股待挂 CC), 不是全市场。
发现 = 合约 1h K 穿自身 EMA(WHEEL_CALL 触线)。无触线不算时机到。
立场只影响触线之后的升档,不单独当发现。
action / hint 仅参考, 不自动下单, 不改 POSITION_QUANT。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

STANCE_INCOME = "income"
STANCE_ACQUIRE = "acquire"

# 允许接货:现价相对成本基础的缓冲%(配置可覆盖;不是止盈规则)
DEFAULT_ACQUIRE_CUSHION_PCT = 3.0
DEFAULT_IV_LIFT_RANK = 50.0
DEFAULT_MAX_SPREAD_PCT = 8.0

GRADE_SKIP = "skip"
GRADE_WATCH = "watch"
GRADE_READY = "ready"
GRADE_PRIORITY = "priority"

_GRADE_RANK = {
    GRADE_PRIORITY: 0,
    GRADE_READY: 1,
    GRADE_WATCH: 2,
    GRADE_SKIP: 3,
}


def normalize_stance(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in (STANCE_INCOME, "只收租"):
        return STANCE_INCOME
    return STANCE_ACQUIRE


def call_timing_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """只读 wheel_timing.call_* ; 缺省不碰 wheel_position 止盈数字。"""
    wt = (cfg or {}).get("wheel_timing") or {}
    def _f(key: str, default: float) -> float:
        try:
            return float(wt.get(key, default) if wt.get(key) is not None else default)
        except (TypeError, ValueError):
            return float(default)
    return {
        "acquire_cushion_pct": _f("call_acquire_cushion_pct", DEFAULT_ACQUIRE_CUSHION_PCT),
        "iv_lift_rank": _f("call_iv_lift_min_rank", DEFAULT_IV_LIFT_RANK),
        "max_spread_pct": _f("call_max_spread_pct", DEFAULT_MAX_SPREAD_PCT),
    }


def cushion_pct(spot: Optional[float], cost_basis: Optional[float]) -> Optional[float]:
    """现价相对成本基础的离开幅度%。正数 = 现价在成本之上。"""
    try:
        sp = float(spot) if spot is not None else None
        cb = float(cost_basis) if cost_basis is not None else None
    except (TypeError, ValueError):
        return None
    if sp is None or cb is None or cb <= 0 or sp <= 0:
        return None
    return round((sp - cb) / cb * 100, 2)


def strike_floor(cost_basis: Optional[float], sell_above: Optional[float] = None) -> Optional[float]:
    """CC strike 锚: max(成本基础, 愿卖价)。被 call 走是计划内(acquire)。"""
    vals: List[float] = []
    for v in (cost_basis, sell_above):
        try:
            if v is not None and float(v) > 0:
                vals.append(float(v))
        except (TypeError, ValueError):
            pass
    if not vals:
        return None
    return round(max(vals), 2)


def _positive_or_none(v) -> Optional[float]:
    """0/None/非法 = 未设。"""
    try:
        if v is None:
            return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def ensure_sell_above_column() -> None:
    """wheel_targets.sell_above: 愿卖价(CC strike 锚). 幂等 ALTER。"""
    try:
        from app.data.database import get_db
        conn = get_db()
        try:
            conn.execute("ALTER TABLE wheel_targets ADD COLUMN sell_above REAL")
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
    except Exception:
        pass


def get_target_sell_above(symbol: str) -> Optional[float]:
    """读愿卖价. 兼容遗留 call_floor; 0/None=未设。"""
    if not symbol:
        return None
    ensure_sell_above_column()
    try:
        from app.data.database import get_db
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM wheel_targets WHERE symbol=?", (symbol,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            return _positive_or_none(d.get("sell_above")) or _positive_or_none(d.get("call_floor"))
        finally:
            conn.close()
    except Exception:
        return None


def set_target_sell_above(symbol: str, value) -> Optional[float]:
    """写入愿卖价. None/0=清空。"""
    ensure_sell_above_column()
    v = _positive_or_none(value)
    from datetime import datetime
    from app.data.database import get_db
    conn = get_db()
    try:
        conn.execute(
            "UPDATE wheel_targets SET sell_above=?, updated_at=? WHERE symbol=?",
            (v, datetime.now().isoformat(), symbol),
        )
        conn.commit()
    finally:
        conn.close()
    return v


def _candidate_ok(
    *,
    min_annualized: Optional[float],
    dte_min: Optional[int],
    dte_max: Optional[int],
    max_spread: float,
    candidate_ann: Optional[float],
    candidate_dte: Optional[int],
    candidate_spread: Optional[float],
) -> bool:
    if candidate_ann is None and candidate_dte is None and candidate_spread is None:
        return False
    if min_annualized and candidate_ann is not None:
        try:
            if float(candidate_ann) < float(min_annualized):
                return False
        except (TypeError, ValueError):
            return False
    if candidate_dte is not None:
        try:
            d = int(candidate_dte)
            if dte_min is not None and d < int(dte_min):
                return False
            if dte_max is not None and d > int(dte_max):
                return False
        except (TypeError, ValueError):
            return False
    if candidate_spread is not None:
        try:
            if float(candidate_spread) > float(max_spread):
                return False
        except (TypeError, ValueError):
            return False
    return True


def evaluate_cc_timing(
    *,
    stance: Any = None,
    spot: Optional[float] = None,
    cost_basis: Optional[float] = None,
    sell_above: Optional[float] = None,
    shares: float = 0,
    uncovered_days: Optional[int] = None,
    iv_rank: Optional[float] = None,
    min_annualized: Optional[float] = None,
    dte_min: Optional[int] = None,
    dte_max: Optional[int] = None,
    candidate_ann: Optional[float] = None,
    candidate_dte: Optional[int] = None,
    candidate_spread: Optional[float] = None,
    ema_touch: bool = False,
    ema_type: Optional[str] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """纯函数。返回 grade/hint/reasons, 不含交易结论。

    ema_touch: 该标的近期有 WHEEL_CALL 1h 触线。无触线 → 待时机,不给找 Call。
    """
    st = normalize_stance(stance)
    tc = call_timing_cfg(cfg)
    cushion = cushion_pct(spot, cost_basis)
    floor = strike_floor(cost_basis, sell_above)
    contracts = int(float(shares or 0) // 100)
    reasons: List[str] = []
    iv_lift = False
    try:
        if iv_rank is not None and float(iv_rank) >= tc["iv_lift_rank"]:
            iv_lift = True
            need_iv = tc["iv_lift_rank"]
            reasons.append(f"IV分位 {float(iv_rank):.0f}≥{need_iv:.0f}(可选抬升)")
    except (TypeError, ValueError):
        iv_lift = False

    cand_ok = _candidate_ok(
        min_annualized=min_annualized,
        dte_min=dte_min,
        dte_max=dte_max,
        max_spread=tc["max_spread_pct"],
        candidate_ann=candidate_ann,
        candidate_dte=candidate_dte,
        candidate_spread=candidate_spread,
    )
    if cand_ok:
        bits = []
        if candidate_ann is not None:
            bits.append(f"年化{float(candidate_ann):g}%")
        if candidate_dte is not None:
            bits.append(f"DTE{int(candidate_dte)}")
        if candidate_spread is not None:
            bits.append(f"点差{float(candidate_spread):g}%")
        reasons.append("候选过线(" + ",".join(bits) + ")")

    if contracts < 1:
        return {
            "stance": st,
            "grade": GRADE_SKIP,
            "timing_ready": False,
            "show_find_call": False,
            "cushion_pct": cushion,
            "strike_floor": floor,
            "cc_contracts": contracts,
            "iv_lift": iv_lift,
            "candidate_ok": cand_ok,
            "ema_touch": False,
            "ema_type": ema_type,
            "hint": "持股不足一张,暂不能标准 CC",
            "tag": "不足一张",
            "reasons": reasons + ["持股不足 100 股"],
        }

    if cushion is not None:
        reasons.append(f"现价相对成本 {cushion:+.1f}%")
    elif cost_basis:
        reasons.append("缺现价,无法判断是否离开成本")

    touched = bool(ema_touch)
    if touched:
        reasons.append(f"1h 触线{(' · ' + ema_type) if ema_type else ''}".strip())
    else:
        reasons.append("无近期 1h 触线,不发现卖 Call")

    grade = GRADE_WATCH
    hint = "待触线(1h EMA)"
    tag = "待触线"

    if not touched:
        if st == STANCE_INCOME:
            hint = "只收租·等待 1h 触线"
            reasons.append("只收租:发现仍走触线,不因持股就挂")
        else:
            hint = "允许接货·等待 1h 触线"
    else:
        if st == STANCE_INCOME:
            grade = GRADE_READY
            hint = "只收租·触线可挂 CC"
            tag = "时机到"
            reasons.append("只收租:触线后应挂 CC")
            if cand_ok or (uncovered_days is not None and int(uncovered_days) >= 3):
                grade = GRADE_PRIORITY
                hint = "只收租·优先找 CC"
                tag = "优先挂CC"
                if uncovered_days is not None and int(uncovered_days) >= 3:
                    reasons.append(f"已裸奔 {int(uncovered_days)} 天")
        else:
            grade = GRADE_READY
            hint = "允许接货·触线可挂 CC"
            tag = "时机到"
            need = tc["acquire_cushion_pct"]
            left = cushion is not None and cushion >= need
            if left:
                reasons.append(f"现价已离开成本≥{need:g}%")
            if left or iv_lift or cand_ok:
                grade = GRADE_PRIORITY
                hint = "允许接货·优先找 CC"
                tag = "优先挂CC"

    return {
        "stance": st,
        "grade": grade,
        "timing_ready": grade in (GRADE_READY, GRADE_PRIORITY),
        "show_find_call": grade in (GRADE_READY, GRADE_PRIORITY),
        "cushion_pct": cushion,
        "strike_floor": floor,
        "cc_contracts": contracts,
        "iv_lift": iv_lift,
        "candidate_ok": cand_ok,
        "ema_touch": touched,
        "ema_type": ema_type,
        "hint": hint,
        "tag": tag,
        "reasons": reasons,
    }


def attach_cc_timing(hint: Dict[str, Any], timing: Dict[str, Any]) -> Dict[str, Any]:
    """把时机层并进 post_assign 条目。不覆盖 cycle 身份字段。"""
    out = dict(hint)
    out["cc_timing"] = timing
    out["stance"] = timing.get("stance")
    out["cc_grade"] = timing.get("grade")
    out["cc_tag"] = timing.get("tag")
    out["cc_hint"] = timing.get("hint")
    out["timing_ready"] = bool(timing.get("timing_ready"))
    out["show_find_call"] = bool(timing.get("show_find_call"))
    if timing.get("strike_floor") is not None:
        out["min_call_strike"] = timing["strike_floor"]
    # 排序:priority 数字越小越前
    out["priority"] = {GRADE_PRIORITY: 1, GRADE_READY: 2, GRADE_WATCH: 4, GRADE_SKIP: 6}.get(
        timing.get("grade"), 5
    )
    notes = list(out.get("notes") or [])
    if timing.get("hint") and timing["hint"] not in notes:
        notes.append(str(timing["hint"]))
    out["notes"] = notes
    # 不再一律 SELL_CALL
    if not timing.get("show_find_call"):
        if timing.get("grade") == GRADE_SKIP:
            out["next_step"] = "HOLD_OR_BUY_MORE"
        else:
            out["next_step"] = "WAIT_CC_TIMING"
        out["suggest_side"] = None
        out["next_step_hint"] = timing.get("hint") or out.get("next_step_hint")
    else:
        out["next_step"] = "SELL_CALL"
        out["suggest_side"] = "call"
        fl = timing.get("strike_floor") or out.get("min_call_strike") or "成本"
        n_cc = timing.get("cc_contracts") or out.get("cc_contracts")
        out["next_step_hint"] = f"找 Call: strike≥{fl} · 约{n_cc}张(参考)"
    return out


def split_holding_cc(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """今日页:优先挂 / 时机到 / 待时机。"""
    buckets = {"priority": [], "ready": [], "watch": [], "skip": []}
    for r in rows:
        g = r.get("cc_grade") or ((r.get("cc_timing") or {}).get("grade")) or GRADE_WATCH
        if g == GRADE_PRIORITY:
            buckets["priority"].append(r)
        elif g == GRADE_READY:
            buckets["ready"].append(r)
        elif g == GRADE_SKIP:
            buckets["skip"].append(r)
        else:
            buckets["watch"].append(r)
    return buckets


def is_holding_call_opp(item: Dict[str, Any]) -> bool:
    """机会流里 CALL 只保留持股待挂。"""
    if (item.get("side") or "").upper() != "CALL":
        return True
    ctx = item.get("context") or {}
    stage = str(ctx.get("stage") or item.get("stage") or "").upper()
    return stage == "HOLDING"


def grade_rank(grade: Optional[str]) -> int:
    return _GRADE_RANK.get(grade or "", 9)
