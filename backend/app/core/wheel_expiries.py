"""到期日选取:核心 DTE 优先,覆盖 21–45 舒适区。

触线扫描另有近端范围:约 3 周周权 + 约 3 个月月权。
从 leaps_monitor 抽出,避免测试/建议扫描依赖 OpenD/pandas。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# 触线期权链:近端周权 + 近端月权。LEAPS / 旧 DTE 窗走 dte_window。
NEAR_WEEKLY_MONTHLY = "near_weekly_monthly"
DTE_WINDOW = "dte_window"


def select_expiries(
    eligible: List[Tuple[Any, int]],
    max_n: int = 6,
    core_dte_min: Optional[int] = None,
    core_dte_max: Optional[int] = None,
    prefer_core: bool = True,
) -> Tuple[List[Tuple[Any, int]], List[Tuple[Any, int]]]:
    """从 DTE 窗口内的到期日中选取最多 max_n 个。

    优先核心带(core_dte_min~max,通常=标的 dte 无 pad),再按 DTE 近→远补齐。
    返回 (selected, skipped)。
    """
    if not eligible:
        return [], []
    max_n = max(1, int(max_n or 6))
    ordered = sorted(eligible, key=lambda x: x[1])
    if not prefer_core or core_dte_min is None or core_dte_max is None:
        selected = ordered[:max_n]
        skipped = ordered[max_n:]
        return selected, skipped
    core = [e for e in ordered if core_dte_min <= e[1] <= core_dte_max]
    outer = [e for e in ordered if e not in core]
    selected: List[Tuple[Any, int]] = []
    for e in core:
        if len(selected) >= max_n:
            break
        selected.append(e)
    for e in outer:
        if len(selected) >= max_n:
            break
        selected.append(e)
    selected.sort(key=lambda x: x[1])
    sel_set = set(selected)
    skipped = [e for e in ordered if e not in sel_set]
    return selected, skipped


def pick_windowed_expiries(
    expirations: List[Any],
    dte_min: int,
    dte_max: int,
    *,
    today: Optional[date] = None,
    max_n: int = 6,
    prefer_core: bool = True,
    pad_days: int = 7,
) -> Tuple[List[Tuple[Any, int]], List[Tuple[Any, int]], List[Tuple[Any, int]]]:
    """给建议扫描选到期日: pad 扩窗 → 核心 DTE 优先 → 最多 max_n。

    旧逻辑 `in_range[:3]` 会按近月截断,周期权下 21/28/35 占满前 3 而丢掉 42。
    返回 (selected, skipped, eligible)。
    """
    today = today or date.today()
    pad = max(0, int(pad_days or 0))
    core_lo = max(1, int(dte_min))
    core_hi = max(core_lo, int(dte_max))
    win_lo = max(1, core_lo - pad)
    win_hi = core_hi + pad
    eligible: List[Tuple[Any, int]] = []
    seen = set()
    for exp in expirations or []:
        raw = exp
        label = str(exp)[:10] if exp is not None else ""
        try:
            dte = (date.fromisoformat(label) - today).days
        except Exception:
            continue
        if not (win_lo <= dte <= win_hi):
            continue
        key = (label, dte)
        if key in seen:
            continue
        seen.add(key)
        eligible.append((raw, dte))
    selected, skipped = select_expiries(
        eligible,
        max_n=max_n,
        core_dte_min=core_lo,
        core_dte_max=core_hi,
        prefer_core=prefer_core,
    )
    return selected, skipped, eligible


def _expiry_date(exp: Any) -> Optional[date]:
    label = str(exp)[:10] if exp is not None else ""
    try:
        return date.fromisoformat(label)
    except Exception:
        return None


def _cycle_token(cycle: Any) -> str:
    if cycle is None:
        return ""
    text = str(cycle).strip().upper()
    if not text or text in {"NONE", "N/A", "NAN", "NULL"}:
        return ""
    if "." in text:
        text = text.split(".")[-1]
    return text


def classify_expiry_cycle(cycle: Any, exp: date) -> str:
    """'weekly' | 'monthly'.

    优先用行情源 expiration_cycle(WEEK/MONTH)。缺省时美股标准月权=当月第三个周五,
    其余到期日按周权。
    """
    token = _cycle_token(cycle)
    if token:
        if "MONTH" in token or token in {"M", "QUARTER", "QUARTERLY"}:
            return "monthly"
        if "WEEK" in token or token in {"W"}:
            return "weekly"
    # 第三个周五:日期落在 15–21 且为周五
    if exp.weekday() == 4 and 15 <= exp.day <= 21:
        return "monthly"
    return "weekly"


def select_near_term_expiries(
    expirations: Sequence[Any],
    *,
    today: Optional[date] = None,
    cycles: Optional[Mapping[str, Any]] = None,
    weekly_weeks: int = 3,
    monthly_months: int = 3,
    min_dte: int = 1,
) -> Tuple[List[Tuple[Any, int, str]], List[Tuple[Any, int, str]]]:
    """触线扫描到期日:近 weekly_weeks 周的周权 + 近 monthly_months 张月权。

    月权不重复计入周权。返回 (selected, skipped),元素为 (raw, dte, kind)。
    月权 DTE 上限约为 monthly_months 个日历月(31*N+7),避免把 LEAP 误当近月。
    """
    today = today or date.today()
    weeks = max(1, int(weekly_weeks or 3))
    months = max(1, int(monthly_months or 3))
    weekly_hi = weeks * 7
    monthly_hi = months * 31 + 7
    floor = max(0, int(min_dte))
    cycles = cycles or {}

    parsed: List[Tuple[Any, int, str, str]] = []
    seen = set()
    for exp in expirations or []:
        exp_date = _expiry_date(exp)
        if exp_date is None:
            continue
        label = exp_date.isoformat()
        if label in seen:
            continue
        seen.add(label)
        dte = (exp_date - today).days
        if dte < floor:
            continue
        cycle = cycles.get(label)
        if cycle is None:
            cycle = cycles.get(str(exp)[:10])
        kind = classify_expiry_cycle(cycle, exp_date)
        parsed.append((exp, dte, kind, label))

    weeklies = [p for p in parsed if p[2] == "weekly" and p[1] <= weekly_hi]
    monthlies = [p for p in parsed if p[2] == "monthly" and p[1] <= monthly_hi]
    monthlies = sorted(monthlies, key=lambda p: p[1])[:months]
    chosen = {(p[3]) for p in weeklies} | {p[3] for p in monthlies}
    selected = [(p[0], p[1], p[2]) for p in sorted(weeklies + monthlies, key=lambda p: p[1])]
    skipped = [(p[0], p[1], p[2]) for p in parsed if p[3] not in chosen]
    skipped.sort(key=lambda p: p[1])
    return selected, skipped


def chain_scope_kwargs(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从 wheel_timing 读触线期权链范围。缺省=近 3 周周权 + 近 3 个月月权。"""
    wt = {}
    if isinstance(cfg, dict):
        raw = cfg.get("wheel_timing") or {}
        if isinstance(raw, dict):
            wt = raw
    mode = str(wt.get("expiry_mode") or NEAR_WEEKLY_MONTHLY).strip() or NEAR_WEEKLY_MONTHLY
    try:
        weeks = int(wt["weekly_weeks"]) if wt.get("weekly_weeks") is not None else 3
    except (TypeError, ValueError):
        weeks = 3
    try:
        months = int(wt["monthly_months"]) if wt.get("monthly_months") is not None else 3
    except (TypeError, ValueError):
        months = 3
    return {
        "expiry_mode": mode,
        "weekly_weeks": max(1, weeks),
        "monthly_months": max(1, months),
    }
