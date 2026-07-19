"""IV 环境档位:自动(组合中位 IVR)优先,驱动开仓/持仓参数叠加。

关注点:组合年化(周转) — 低 IV 更快止盈、更严 min 年化、更低仓位;
高 IV 略提高仓位与止盈弹性,但 floor/风险阈值不变。
"""
from __future__ import annotations

import json
import logging
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_KV_REGIME = "wheel_iv_regime_state"

# 进入/退出滞回,减少抖动
DEFAULT_REGIME_CFG: Dict[str, Any] = {
    "mode": "auto",  # auto | manual
    "manual_regime": "mid",  # low | mid | high
    "low_enter": 35.0,   # IVR < 此 → 可进 low
    "low_exit": 40.0,    # 已在 low 时 IVR > 此 → 离开 low
    "high_enter": 60.0,  # IVR >= 此 → 可进 high
    "high_exit": 55.0,   # 已在 high 时 IVR < 此 → 离开 high
    "min_symbols_with_ivr": 1,
    # 各档叠加(deep_merge 到 wheel_position / wheel_scan / wheel_portfolio)
    "overlays": {
        "low": {
            "wheel_position": {
                "profit_target_pct": 42,
                "soft_profit_pct": 28,
                "max_hold_profit_pct": 70,
                "hold_theta_min_remaining_ann": 14.0,
                "capital_tight_util_pct": 70.0,
            },
            "wheel_scan": {
                "buffer_atr_min": 1.0,
                "pop_weight": 0.45,
                "iv_rank_bonus": 0.10,
                "earnings_hard_filter": True,
            },
            "wheel_portfolio": {
                "max_portfolio_pct": 0.65,
            },
            # 机会流额外: min 年化乘数(相对标的 min)
            "min_annualized_mult": 1.25,
            "size_mult": 0.65,
            "label": "低IV",
            "hint": "权利金薄→严过滤、更快止盈、控仓,留子弹",
        },
        "mid": {
            "wheel_position": {
                "profit_target_pct": 50,
                "soft_profit_pct": 30,
                "max_hold_profit_pct": 80,
                "hold_theta_min_remaining_ann": 12.0,
                "capital_tight_util_pct": 75.0,
            },
            "wheel_scan": {
                "buffer_atr_min": 0.8,
                "pop_weight": 0.35,
                "iv_rank_bonus": 0.20,
            },
            "wheel_portfolio": {
                "max_portfolio_pct": 0.80,
            },
            "min_annualized_mult": 1.0,
            "size_mult": 1.0,
            "label": "中性",
            "hint": "均衡:50%止盈优先周转,组合年化优先",
        },
        "high": {
            "wheel_position": {
                "profit_target_pct": 55,
                "soft_profit_pct": 32,
                "max_hold_profit_pct": 85,
                "hold_theta_min_remaining_ann": 11.0,
                "capital_tight_util_pct": 78.0,
            },
            "wheel_scan": {
                "buffer_atr_min": 0.7,
                "pop_weight": 0.30,
                "iv_rank_bonus": 0.28,
            },
            "wheel_portfolio": {
                "max_portfolio_pct": 0.80,
            },
            "min_annualized_mult": 0.95,
            "size_mult": 1.1,
            "label": "高IV",
            "hint": "保费厚→可略进攻,仍守愿接价;止盈略放宽但仍重周转",
        },
    },
}


def _load_regime_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app.core.config import deep_merge
    if cfg is None:
        from app.core.config import get_effective_config
        cfg = get_effective_config()
    user = (cfg.get("wheel_iv_regime") or {}) if cfg else {}
    return deep_merge(DEFAULT_REGIME_CFG, user)


def collect_symbol_ivr(symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """启用标的的 IVR 列表(brief_profile,本地库,不打 OpenD)。"""
    from app.data import wheel_repository as repo
    from app.core.volatility import brief_profile

    if symbols is None:
        symbols = [t["symbol"] for t in repo.get_targets() if t.get("enabled")]
    out: List[Dict[str, Any]] = []
    for sym in symbols:
        try:
            bp = brief_profile(sym)
            rank = bp.get("iv_rank")
            out.append({
                "symbol": sym,
                "iv_rank": float(rank) if rank is not None else None,
                "source": bp.get("iv_rank_source"),
                "atm_iv": bp.get("atm_iv"),
                "hv20": bp.get("hv20"),
            })
        except Exception as e:
            logger.debug("ivr %s: %s", sym, e)
            out.append({"symbol": sym, "iv_rank": None, "source": None})
    return out


def median_ivr(rows: List[Dict[str, Any]]) -> Tuple[Optional[float], int]:
    vals = [r["iv_rank"] for r in rows if r.get("iv_rank") is not None]
    if not vals:
        return None, 0
    return round(float(statistics.median(vals)), 1), len(vals)


def classify_regime(
    median: Optional[float],
    prev: Optional[str],
    rc: Dict[str, Any],
) -> str:
    """滞回分类 low|mid|high。median 缺失 → mid。"""
    if median is None:
        return prev if prev in ("low", "mid", "high") else "mid"
    prev = prev if prev in ("low", "mid", "high") else "mid"
    low_enter = float(rc.get("low_enter", 35))
    low_exit = float(rc.get("low_exit", 40))
    high_enter = float(rc.get("high_enter", 60))
    high_exit = float(rc.get("high_exit", 55))

    if prev == "low":
        if median > low_exit:
            # 离开 low
            if median >= high_enter:
                return "high"
            return "mid"
        return "low"
    if prev == "high":
        if median < high_exit:
            if median < low_enter:
                return "low"
            return "mid"
        return "high"
    # mid
    if median < low_enter:
        return "low"
    if median >= high_enter:
        return "high"
    return "mid"


def _read_prev_state() -> Dict[str, Any]:
    try:
        from app.data.wheel_repository import get_kv
        raw = get_kv(_KV_REGIME)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        from app.data.wheel_repository import set_kv
        set_kv(_KV_REGIME, json.dumps(state, ensure_ascii=False))
    except Exception as e:
        logger.warning("save iv regime state: %s", e)


def resolve_regime(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """解析当前 IV 档 + 叠加参数。"""
    if cfg is None:
        from app.core.config import get_effective_config
        cfg = get_effective_config()
    rc = _load_regime_cfg(cfg)
    mode = (rc.get("mode") or "auto").lower()
    rows = collect_symbol_ivr()
    med, n = median_ivr(rows)
    prev_state = _read_prev_state()
    prev = prev_state.get("regime")

    if mode == "manual":
        regime = (rc.get("manual_regime") or "mid").lower()
        if regime not in ("low", "mid", "high"):
            regime = "mid"
        source = "manual"
    else:
        min_n = int(rc.get("min_symbols_with_ivr") or 1)
        if n < min_n or med is None:
            regime = prev if prev in ("low", "mid", "high") else "mid"
            source = "auto_fallback_mid" if med is None else "auto"
        else:
            regime = classify_regime(med, prev, rc)
            source = "auto"

    overlays = (rc.get("overlays") or {}).get(regime) or DEFAULT_REGIME_CFG["overlays"]["mid"]
    state = {
        "regime": regime,
        "median_ivr": med,
        "n_with_ivr": n,
        "mode": mode,
        "source": source,
        "updated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    _write_state(state)

    return {
        **state,
        "label": overlays.get("label") or regime,
        "hint": overlays.get("hint") or "",
        "min_annualized_mult": float(overlays.get("min_annualized_mult") or 1.0),
        "size_mult": float(overlays.get("size_mult") or 1.0),
        "overlay": {
            k: v for k, v in overlays.items()
            if k in ("wheel_position", "wheel_scan", "wheel_portfolio")
        },
        "per_symbol": rows,
        "thresholds": {
            "low_enter": rc.get("low_enter"),
            "low_exit": rc.get("low_exit"),
            "high_enter": rc.get("high_enter"),
            "high_exit": rc.get("high_exit"),
        },
    }


def apply_regime_to_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生效配置 = 设置页配置 ← IV 档叠加。"""
    from app.core.config import deep_merge, get_effective_config
    base = dict(cfg or get_effective_config())
    info = resolve_regime(base)
    overlay = info.get("overlay") or {}
    merged = deep_merge(base, overlay)
    merged["_iv_regime"] = {
        "regime": info.get("regime"),
        "label": info.get("label"),
        "hint": info.get("hint"),
        "median_ivr": info.get("median_ivr"),
        "n_with_ivr": info.get("n_with_ivr"),
        "mode": info.get("mode"),
        "source": info.get("source"),
        "min_annualized_mult": info.get("min_annualized_mult"),
        "size_mult": info.get("size_mult"),
    }
    return merged


def effective_min_annualized(base_min: float, cfg: Optional[Dict[str, Any]] = None) -> float:
    """标的 min_annualized × 档位乘数(组合年化优先:低 IV 抬高门槛)。"""
    info = resolve_regime(cfg)
    mult = float(info.get("min_annualized_mult") or 1.0)
    try:
        b = float(base_min or 0)
    except (TypeError, ValueError):
        b = 0.0
    if b <= 0:
        # 无标的 min 时低 IV 用绝对地板
        if info.get("regime") == "low":
            return 18.0
        if info.get("regime") == "high":
            return 12.0
        return 15.0
    return round(b * mult, 2)
