"""同标的多合约触线去重:一批扫描内按年化→theta 择优,仅赢家进 TG / Sim。

规则(与 PR 正文一致):
1. 按 (symbol, side PUT|CALL) 分组(同一 scan batch;含 1d/1h Call)。
2. 主排序:年化收益率%(annualized / annualized_yield)更高者胜。
3. 平手:绝对 theta 更高(缺省按 0,即更差)。
4. 再平手:权利金(premium/bid)更高 → DTE 更近 → strike 更高。
5. 每组仅 1 个赢家;落选仍可由扫描侧写历史,但不推 TG、不进 sim_on_alert。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

SignalLike = Union[Dict[str, Any], Any]


def _get(sig: SignalLike, key: str, default: Any = None) -> Any:
    if isinstance(sig, dict):
        return sig.get(key, default)
    return getattr(sig, key, default)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def signal_side(sig: SignalLike) -> str:
    """归一化为 PUT / CALL。"""
    side = str(_get(sig, "side") or "").upper()
    if side in ("PUT", "P"):
        return "PUT"
    if side in ("CALL", "C"):
        return "CALL"
    level = str(_get(sig, "signal_level") or "").upper()
    if "CALL" in level:
        return "CALL"
    if "PUT" in level:
        return "PUT"
    cat = str(_get(sig, "category") or "").lower()
    if "call" in cat:
        return "CALL"
    if "put" in cat:
        return "PUT"
    return "PUT"


def annualized_of(sig: SignalLike) -> float:
    """年化%(已有字段优先;否则 premium/strike×365/DTE×100)。"""
    for key in ("annualized", "annualized_yield"):
        v = _get(sig, key)
        if v is not None:
            return _f(v, 0.0)
    prem = premium_of(sig)
    strike = _f(_get(sig, "strike"), 0.0)
    dte = dte_of(sig)
    if prem > 0 and strike > 0 and dte > 0:
        return round(prem / strike * 365 / dte * 100, 2)
    return 0.0


def theta_abs_of(sig: SignalLike) -> float:
    """绝对 theta;缺失视为 0(更差)。"""
    for key in ("theta", "option_theta", "theta_abs"):
        v = _get(sig, key)
        if v is not None:
            return abs(_f(v, 0.0))
    return 0.0


def premium_of(sig: SignalLike) -> float:
    for key in ("bid", "premium", "trigger_price", "open_price"):
        v = _get(sig, key)
        if v is not None and _f(v, 0.0) > 0:
            return _f(v, 0.0)
    return 0.0


def dte_of(sig: SignalLike) -> int:
    try:
        v = _get(sig, "dte")
        if v is None:
            return 10**9  # 缺 DTE 视为极远,排序靠后
        return int(v)
    except (TypeError, ValueError):
        return 10**9


def strike_of(sig: SignalLike) -> float:
    return _f(_get(sig, "strike"), 0.0)


def group_key(sig: SignalLike) -> Tuple[str, str]:
    sym = str(_get(sig, "symbol") or "").upper()
    return (sym, signal_side(sig))


def rank_tuple(sig: SignalLike) -> Tuple[float, float, float, int, float]:
    """越大越好的排序键(供 reverse sort)。DTE 取负使更近者更大。"""
    return (
        annualized_of(sig),
        theta_abs_of(sig),
        premium_of(sig),
        -dte_of(sig),
        strike_of(sig),
    )


def pick_best(signals: Sequence[SignalLike]) -> Optional[SignalLike]:
    if not signals:
        return None
    return max(signals, key=rank_tuple)


def select_best_touch_signals(
    signals: Iterable[SignalLike],
    *,
    group_by_timeframe: bool = False,
) -> List[SignalLike]:
    """一批触线信号 → 每组仅保留最优合约。

    默认按 (symbol, side) 分组。若 group_by_timeframe=True,再按 timeframe 分桶
    (一般同批次 Call 1h+1d 仍只推一个最优合约,故默认 False)。
    稳定:同键内相对顺序不影响结果(纯 key 比较)。
    """
    buckets: Dict[Tuple[Any, ...], List[SignalLike]] = {}
    for sig in signals or []:
        sym, side = group_key(sig)
        if not sym:
            continue
        if group_by_timeframe:
            tf = str(_get(sig, "timeframe") or "")
            key: Tuple[Any, ...] = (sym, side, tf)
        else:
            key = (sym, side)
        buckets.setdefault(key, []).append(sig)

    winners: List[SignalLike] = []
    # 保持首次出现组的顺序,便于日志对照
    for key in buckets:
        best = pick_best(buckets[key])
        if best is not None:
            winners.append(best)
    return winners
