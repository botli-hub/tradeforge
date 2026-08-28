"""到期日选取:核心 DTE 优先,覆盖 21–45 舒适区。

从 leaps_monitor 抽出,避免测试/建议扫描依赖 OpenD/pandas。
"""
from __future__ import annotations

from datetime import date
from typing import Any, List, Optional, Tuple


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
