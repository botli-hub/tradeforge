"""触线扫描 / 缠论状态监控的标的宇宙。

与 wheel_targets.enabled 解耦: enabled 仍表示轮子目标(账本 / UI / 轮子 / 机会扫描)。
本模块只决定哪些启用标的进入触线扫描和缠论监控。
排除名单来自配置 monitor_universe,不在扫描器里写死代码。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence


def _as_symbols(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, Iterable):
        parts = list(raw)
    else:
        return []
    out: List[str] = []
    seen = set()
    for item in parts:
        sym = str(item or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def monitor_exclude(cfg: Optional[Dict[str, Any]], kind: str) -> List[str]:
    """kind: 'touch' | 'chan'. 共享 exclude 再加该侧名单。"""
    mu = {}
    if isinstance(cfg, dict):
        raw = cfg.get("monitor_universe") or {}
        if isinstance(raw, dict):
            mu = raw
    shared = _as_symbols(mu.get("exclude"))
    side = _as_symbols(mu.get(f"{kind}_exclude"))
    out: List[str] = []
    seen = set()
    for sym in shared + side:
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def filter_monitor_symbols(
    symbols: Sequence[str],
    cfg: Optional[Dict[str, Any]],
    kind: str,
) -> List[str]:
    """保持原顺序,去掉空值、重复项和监控排除名单。"""
    ban = set(monitor_exclude(cfg, kind))
    out: List[str] = []
    seen = set()
    for item in symbols or []:
        sym = str(item or "").strip().upper()
        if not sym or sym in ban or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def touch_scan_targets(
    targets: Optional[Sequence[Dict[str, Any]]],
    cfg: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """启用的 wheel 标的,再去掉触线排除。不改 enabled。"""
    ban = set(monitor_exclude(cfg, "touch"))
    out: List[Dict[str, Any]] = []
    for t in targets or []:
        if not t.get("enabled"):
            continue
        sym = str(t.get("symbol") or "").strip().upper()
        if not sym or sym in ban:
            continue
        out.append(t)
    return out
