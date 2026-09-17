"""完整轮子纸面账 (Sim Wheel) — 独立状态机,不碰实盘台账 / FirstTrade / POSITION_QUANT.

状态: IDLE → CSP_OPEN → HOLDING → CC_OPEN → IDLE/CLOSED
信号进账: Put触线 / Call触线 / 缠论 B/S

Touch Wheel(v1 纸面):
- 张数按周期: 1h=1 / 1d=2(EMA 不加倍);策略键细桶 put_1h_ema50 等
- CSP 止盈主路径=同标的 Call 触线买回对应张数;无 Call 信号不主动权利金止盈
- 破愿接 hold_to_assign;同批 1h+1d 默认 prefer_daily;不自动 FirstTrade

缠论正股(附录 A, chan_buy_mode=equity_long):
- 5m/30m/1d B/S → ±5/±30/±100 股;策略键 chan5m/chan30m/chan1d
- 与 CSP/CC 分账(share_pool=isolated);不允许裸空;不改 TG 缠论推送
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

STATUSES = ("IDLE", "CSP_OPEN", "HOLDING", "CC_OPEN", "CLOSED")
# 兼容旧键;触线细桶为 put_1h_ema50 / call_1d_ema200 等
STRATEGIES = (
    "put_touch", "call_touch", "chan5m", "chan30m", "chan1d",
    "put_1h_ema50", "put_1h_ema200", "put_1d_ema50", "put_1d_ema200",
    "call_1h_ema50", "call_1h_ema200", "call_1d_ema50", "call_1d_ema200",
)

DEFAULT_TOUCH_WHEEL: Dict[str, Any] = {
    "qty_by_timeframe": {"1h": 1, "1d": 2},
    "ema_does_not_scale_qty": True,
    "same_batch_1h_1d": "prefer_daily",
    "put_breach_floor": "hold_to_assign",
    "call_without_shares": "skip",
    "put_tp_mode": "call_touch",
    "premium_tp_override": False,
    "threat_exit": False,
    "put_touch_closes_call": True,
    "cc_force_days": 0,
    "cooldown_calendar_days": 1,
    "max_open_csp_per_symbol": 0,
    "allow_parallel_csp": True,
}

DEFAULT_CHAN_EQUITY: Dict[str, Any] = {
    "share_pool": "isolated",  # v1 与 Touch Wheel CSP/CC 分账;后期可 shared
    "qty_by_timeframe": {"5m": 5, "30m": 30, "1d": 100},
}


DEFAULT_SIM: Dict[str, Any] = {
    "enabled": True,
    "chan_buy_mode": "equity_long",  # 附录 A: 缠论 B/S → 纸面买卖正股(不再映射卖 Put)
    "chan_equity_sim": dict(DEFAULT_CHAN_EQUITY),
    "call_without_shares": "skip",
    "levels": {"L1": 0.02, "L2": 0.04, "L3": 0.06},  # 仅缠论等非触线路径
    "cc_force_days": 0,  # Touch Wheel 默认关;显式 >0 才强挂
    "put_breach_floor": "hold_to_assign",
    "roll": "tag_only",
    "max_symbol_pct": 0.25,  # 缠论路径;触线不按单票 25% 硬顶
    "max_portfolio_pct": 0.80,
    "equity": 100_000.0,  # 纸面权益兜底;可被 cfg 覆盖
    "contract_size": 100,
    "dte_default": 30,
    # 纸面权利金止盈 — 仅 premium_tp_override / put_tp_mode 含 premium 时启用
    "hard_profit_pct": 42.0,
    "soft_profit_pct": 28.0,
    "min_remaining_ann": 12.0,
    "hard_roll_dte": 21,
    "threat_otm_buffer_pct": 5.0,
    "tg_summary": True,
    "touch_wheel": dict(DEFAULT_TOUCH_WHEEL),
}


def get_touch_wheel_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    tw = dict(DEFAULT_TOUCH_WHEEL)
    if not cfg:
        return tw
    overlay = cfg.get("touch_wheel") if isinstance(cfg, dict) else None
    if isinstance(overlay, dict):
        tw.update(overlay)
        if isinstance(overlay.get("qty_by_timeframe"), dict):
            q = dict(DEFAULT_TOUCH_WHEEL["qty_by_timeframe"])
            q.update(overlay["qty_by_timeframe"])
            tw["qty_by_timeframe"] = q
    # 允许 sim_wheel 覆盖同名键(测试便利)
    sim = cfg.get("sim_wheel") if isinstance(cfg, dict) else None
    if isinstance(sim, dict):
        for k in (
            "put_tp_mode", "premium_tp_override", "threat_exit",
            "put_touch_closes_call", "same_batch_1h_1d", "call_without_shares",
            "put_breach_floor", "cc_force_days", "allow_parallel_csp",
            "max_open_csp_per_symbol",
        ):
            if k in sim:
                tw[k] = sim[k]
        if isinstance(sim.get("qty_by_timeframe"), dict):
            q = dict(tw["qty_by_timeframe"])
            q.update(sim["qty_by_timeframe"])
            tw["qty_by_timeframe"] = q
    return tw


def get_sim_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_SIM)
    tw = get_touch_wheel_cfg(cfg)
    merged["touch_wheel"] = tw
    # 触线口径同步到顶层(引擎读写便利)
    merged["call_without_shares"] = tw.get("call_without_shares", merged["call_without_shares"])
    merged["put_breach_floor"] = tw.get("put_breach_floor", merged["put_breach_floor"])
    merged["cc_force_days"] = int(tw.get("cc_force_days", merged["cc_force_days"]) or 0)
    if cfg:
        overlay = cfg.get("sim_wheel") or {}
        if isinstance(overlay, dict):
            merged.update(overlay)
            if isinstance(overlay.get("levels"), dict):
                levels = dict(DEFAULT_SIM["levels"])
                levels.update(overlay["levels"])
                merged["levels"] = levels
            # 重新挂载 touch_wheel(不被 sim_wheel 整表冲掉)
            merged["touch_wheel"] = get_touch_wheel_cfg(cfg)
            tw = merged["touch_wheel"]
            # chan_equity_sim 深合并
            ce = dict(DEFAULT_CHAN_EQUITY)
            if isinstance(overlay.get("chan_equity_sim"), dict):
                ce.update(overlay["chan_equity_sim"])
                if isinstance(overlay["chan_equity_sim"].get("qty_by_timeframe"), dict):
                    q = dict(DEFAULT_CHAN_EQUITY["qty_by_timeframe"])
                    q.update(overlay["chan_equity_sim"]["qty_by_timeframe"])
                    ce["qty_by_timeframe"] = q
            merged["chan_equity_sim"] = ce
            if "cc_force_days" in overlay:
                merged["cc_force_days"] = int(overlay["cc_force_days"] or 0)
            else:
                merged["cc_force_days"] = int(tw.get("cc_force_days") or 0)
            if "call_without_shares" not in overlay:
                merged["call_without_shares"] = tw.get("call_without_shares", merged["call_without_shares"])
            if "put_breach_floor" not in overlay:
                merged["put_breach_floor"] = tw.get("put_breach_floor", merged["put_breach_floor"])
    # 顶层 chan_equity_sim 覆盖(与 touch_wheel 并列配置)
    if cfg and isinstance(cfg.get("chan_equity_sim"), dict):
        ce = dict(merged.get("chan_equity_sim") or DEFAULT_CHAN_EQUITY)
        ce.update(cfg["chan_equity_sim"])
        if isinstance(cfg["chan_equity_sim"].get("qty_by_timeframe"), dict):
            q = dict(ce.get("qty_by_timeframe") or DEFAULT_CHAN_EQUITY["qty_by_timeframe"])
            q.update(cfg["chan_equity_sim"]["qty_by_timeframe"])
            ce["qty_by_timeframe"] = q
        merged["chan_equity_sim"] = ce
    if not isinstance(merged.get("chan_equity_sim"), dict):
        merged["chan_equity_sim"] = dict(DEFAULT_CHAN_EQUITY)

    # 权益:优先 sim_wheel.equity;否则 wheel_portfolio.total_equity;否则默认
    if cfg and (not merged.get("equity") or float(merged.get("equity") or 0) <= 0):
        pe = (cfg.get("wheel_portfolio") or {}).get("total_equity")
        try:
            if pe and float(pe) > 0:
                merged["equity"] = float(pe)
        except (TypeError, ValueError):
            pass
    return merged


def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_day(s: Any) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    raw = str(s).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except Exception:
        return None


def trading_days_between(start: date, end: date) -> int:
    """简易交易日计数(跳过周末)。v1 足够测 cc_force_days。"""
    if end < start:
        return 0
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


# ── 级别映射 ─────────────────────────────────────────────────────────────────

def map_level(
    *,
    signal_kind: str,
    ema_type: Optional[str] = None,
    timeframe: Optional[str] = None,
    chan_kind: Optional[str] = None,
) -> str:
    """L1/L2/L3 映射(PRD §5)。

    EMA50、5m B1 → L1
    EMA200、5m B2/B3、30m B1 → L2
    30m B2/B3 → L3
    """
    kind = (signal_kind or "").upper()
    ema = (ema_type or "").upper()
    tf = (timeframe or "").lower()
    ck = (chan_kind or kind or "").upper()

    if kind in ("WHEEL_PUT", "WHEEL_CALL", "PUT_TOUCH", "CALL_TOUCH"):
        if ema in ("EMA200", "200"):
            return "L2"
        return "L1"  # EMA50 默认 L1

    if ck.startswith("B") or ck.startswith("S"):
        if tf in ("30m", "30min", "k_30m"):
            if ck in ("B1", "S1"):
                return "L2"
            return "L3"  # B2/B3/S2/S3
        # 5m
        if ck in ("B1", "S1"):
            return "L1"
        return "L2"  # B2/B3/S2/S3 on 5m

    return "L1"


def normalize_timeframe(tf: Any) -> str:
    """归一到 1h / 1d(触线张数键)。未知偏保守按 1h。"""
    t = str(tf or "").strip().lower()
    if t in ("1h", "60m", "60min", "hour", "hourly", "k_1h"):
        return "1h"
    if t in ("1d", "d", "day", "daily", "1day", "k_1d"):
        return "1d"
    if "1h" in t or t.startswith("60"):
        return "1h"
    if "1d" in t or "day" in t:
        return "1d"
    return "1h"


def normalize_ema(ema: Any) -> str:
    e = str(ema or "").strip().upper().replace(" ", "")
    if e in ("EMA200", "200", "E200"):
        return "ema200"
    return "ema50"


def qty_from_timeframe(
    tf: Any,
    qty_by_timeframe: Optional[Dict[str, Any]] = None,
) -> int:
    """触线张数只跟周期: 1h→1, 1d→2。EMA 不加倍。"""
    m = qty_by_timeframe or DEFAULT_TOUCH_WHEEL["qty_by_timeframe"]
    nt = normalize_timeframe(tf)
    try:
        q = int(m.get(nt, m.get("1h", 1)))
    except (TypeError, ValueError):
        q = 1
    return max(0, q)


def is_touch_alert(alert: Dict[str, Any]) -> bool:
    """触线(非缠论)信号。"""
    cat = (alert.get("category") or alert.get("source") or "").lower()
    kind = str(alert.get("kind") or "").upper()
    level = str(alert.get("signal_level") or "").upper()
    if cat == "chan" or kind in {"B1", "B2", "B3", "S1", "S2", "S3"}:
        return False
    if level in ("WHEEL_PUT", "WHEEL_CALL", "PUT_TOUCH", "CALL_TOUCH"):
        return True
    return cat in ("put_touch", "timing_put", "call_touch", "timing_call")


def normalize_chan_timeframe(tf: Any) -> str:
    """缠论级别归一到 5m / 30m / 1d。"""
    t = str(tf or "").strip().lower()
    if t.startswith("30") or t in ("30m", "30min", "k_30m"):
        return "30m"
    if t in ("1d", "d", "day", "daily", "1day", "k_1d") or "1d" in t or "day" in t:
        return "1d"
    if t.startswith("5") or t in ("5m", "5min", "k_5m"):
        return "5m"
    return "5m"


def equity_qty_from_timeframe(
    tf: Any,
    qty_by_timeframe: Optional[Dict[str, Any]] = None,
) -> int:
    """附录 A: 5m→5 / 30m→30 / 1d→100 股。"""
    m = qty_by_timeframe or DEFAULT_CHAN_EQUITY["qty_by_timeframe"]
    nt = normalize_chan_timeframe(tf)
    try:
        q = int(m.get(nt, 0))
    except (TypeError, ValueError):
        q = 0
    return max(0, q)


def strategy_of_alert(alert: Dict[str, Any]) -> str:
    """策略维度: 触线细桶 put_1h_ema50…; 缠论 chan5m/chan30m/chan1d; 兼容旧 put_touch。"""
    cat = (alert.get("category") or alert.get("source") or "").lower()
    level = (alert.get("signal_level") or "").upper()
    tf = str(alert.get("timeframe") or "").lower()
    kind = str(alert.get("kind") or "").upper()

    if cat == "chan" or kind in {"B1", "B2", "B3", "S1", "S2", "S3"}:
        nt = normalize_chan_timeframe(tf)
        return {"5m": "chan5m", "30m": "chan30m", "1d": "chan1d"}.get(nt, "chan5m")

    side = "call" if (
        level == "WHEEL_CALL" or cat in ("call_touch", "timing_call")
        or str(alert.get("side") or "").upper() in ("CALL", "C")
    ) else "put"
    # 无周期/EMA 时退回笼统键(兼容旧测试)
    raw_tf = alert.get("timeframe")
    raw_ema = alert.get("ema_type")
    if raw_tf or raw_ema:
        return f"{side}_{normalize_timeframe(raw_tf)}_{normalize_ema(raw_ema)}"
    return "call_touch" if side == "call" else "put_touch"


def resolve_same_batch_1h_1d(
    signals: Sequence[Any],
    *,
    mode: str = "prefer_daily",
) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """同标的同侧同批 1h+1d: 默认只留日线(张数=2),1h 记 shadow_superseded_by_1d。

    输入宜已按 (symbol,side[,tf]) 择优。返回 (keepers, shadow_events)。
    mode=stack 则两周期都保留(各自张数,不叠成单笔 3 张)。
    """
    # 本地归一 timeframe / group key
    def _tf_of(sig: Any) -> str:
        if isinstance(sig, dict):
            return normalize_timeframe(sig.get("timeframe"))
        return normalize_timeframe(getattr(sig, "timeframe", None))

    def _gk(sig: Any) -> Tuple[str, str]:
        if isinstance(sig, dict):
            sym = str(sig.get("symbol") or "").upper()
            side = str(sig.get("side") or sig.get("signal_level") or "").upper()
            if "CALL" in side:
                side = "CALL"
            elif "PUT" in side:
                side = "PUT"
            else:
                from app.core.touch_best import signal_side
                side = signal_side(sig)
            return sym, side
        from app.core.touch_best import group_key as gk
        return gk(sig)

    mode_l = (mode or "prefer_daily").strip().lower()
    if mode_l in ("stack", "stack_intraday_and_daily", "both"):
        return list(signals or []), []

    # prefer_daily: 同 (symbol,side) 若有 1d 则丢弃 1h
    by_group: Dict[Tuple[str, str], Dict[str, List[Any]]] = {}
    order: List[Tuple[str, str]] = []
    for sig in signals or []:
        key = _gk(sig)
        if not key[0]:
            continue
        if key not in by_group:
            by_group[key] = {"1h": [], "1d": [], "other": []}
            order.append(key)
        nt = _tf_of(sig)
        if nt == "1d":
            by_group[key]["1d"].append(sig)
        elif nt == "1h":
            by_group[key]["1h"].append(sig)
        else:
            by_group[key]["other"].append(sig)

    keepers: List[Any] = []
    shadows: List[Dict[str, Any]] = []
    for key in order:
        buckets = by_group[key]
        if buckets["1d"] and buckets["1h"]:
            keepers.extend(buckets["1d"])
            keepers.extend(buckets["other"])
            for s in buckets["1h"]:
                shadows.append({
                    "signal": s,
                    "reason": "shadow_superseded_by_1d",
                    "symbol": key[0],
                    "side": key[1],
                })
        else:
            keepers.extend(buckets["1d"])
            keepers.extend(buckets["1h"])
            keepers.extend(buckets["other"])
    return keepers, shadows


def alert_fingerprint(alert: Dict[str, Any]) -> str:
    """与 TG 去重同语义:调用方可预置 fingerprint;否则按关键字段生成。"""
    if alert.get("fingerprint"):
        return str(alert["fingerprint"])
    cat = (alert.get("category") or alert.get("source") or "alert").lower()
    if cat == "chan" or alert.get("kind") in {"B1", "B2", "B3", "S1", "S2", "S3"}:
        raw = (
            f"{(alert.get('symbol') or '').upper()}|"
            f"{alert.get('timeframe')}|{alert.get('kind')}|{alert.get('ts')}"
        )
        return "chan:" + hashlib.sha1(raw.encode()).hexdigest()[:16]
    # timing put/call
    raw = (
        f"{(alert.get('symbol') or '').upper()}|"
        f"{alert.get('signal_level') or alert.get('side')}|"
        f"{alert.get('contract_code') or ''}|"
        f"{alert.get('ema_type') or ''}|"
        f"{_f(alert.get('trigger_price') or alert.get('price')):.2f}"
    )
    return "timing:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def size_contracts(
    level: str,
    spot: float,
    equity: float,
    *,
    used_symbol: float = 0.0,
    used_portfolio: float = 0.0,
    max_symbol_pct: float = 0.25,
    max_portfolio_pct: float = 0.80,
    levels: Optional[Dict[str, float]] = None,
    contract_size: int = 100,
    for_call_shares: Optional[float] = None,
) -> int:
    """按级别名义担保定张数;硬顶单票 25%。Call 时不超过持股/100。"""
    lv = levels or DEFAULT_SIM["levels"]
    pct = float(lv.get(level, lv.get("L1", 0.02)))
    if spot <= 0 or equity <= 0:
        return 0
    notional_budget = equity * pct
    # 单票硬顶
    symbol_room = max(0.0, equity * max_symbol_pct - used_symbol)
    port_room = max(0.0, equity * max_portfolio_pct - used_portfolio)
    budget = min(notional_budget, symbol_room, port_room)
    per_contract = spot * contract_size
    if per_contract <= 0:
        return 0
    qty = int(budget // per_contract)
    # 级别名义不足 1 张、但单票/组合硬顶仍够时:纸面最少 1 张(熟悉度样本)
    if qty < 1 and notional_budget > 0 and per_contract <= min(symbol_room, port_room):
        qty = 1
    if for_call_shares is not None:
        qty = min(qty, int(float(for_call_shares) // contract_size))
    return max(0, qty)


def familiarity_badge(closed_cycles: int, expectancy: float = 0.0) -> str:
    """Cold <5; Warm 5–19; Hot ≥20 且期望>0。"""
    n = int(closed_cycles or 0)
    if n >= 20 and expectancy > 0:
        return "Hot"
    if n >= 5:
        return "Warm"
    return "Cold"


# ── 纸面退出判定(CSP) ─────────────────────────────────────────────────────────

def put_profit_pct(open_price: float, mark: float) -> Optional[float]:
    """卖出权利金止盈%: (开仓prem − 买回mark) / 开仓prem * 100。"""
    if open_price is None or open_price <= 0 or mark is None:
        return None
    return (float(open_price) - float(mark)) / float(open_price) * 100.0


def remaining_ann(open_price: float, mark: float, dte: int, strike: float) -> Optional[float]:
    """剩余年化粗估: mark/strike * 365/dte。"""
    if dte is None or dte <= 0 or strike is None or strike <= 0 or mark is None:
        return None
    return float(mark) / float(strike) * (365.0 / float(dte)) * 100.0


def otm_buffer_pct(spot: float, strike: float, side: str = "PUT") -> Optional[float]:
    if spot is None or strike is None or spot <= 0:
        return None
    if (side or "").upper() == "PUT":
        return (float(spot) - float(strike)) / float(spot) * 100.0
    return (float(strike) - float(spot)) / float(spot) * 100.0


def is_timing_scan_alert(alert: Dict[str, Any]) -> bool:
    """同源触线信号(非缠论/force)。Sim 与 live 扫描共用 OTM 口径。"""
    level = str(alert.get("signal_level") or "").upper()
    cat = str(alert.get("category") or "").lower()
    if level in ("WHEEL_PUT", "WHEEL_CALL", "PUT_TOUCH", "CALL_TOUCH"):
        return True
    return cat in ("put_touch", "timing_put", "call_touch", "timing_call")


# ── 引擎 ─────────────────────────────────────────────────────────────────────

class SimWheelEngine:
    """纸面账引擎。依赖 sim_repository 接口;测试可注入 memory repo。"""

    def __init__(self, repo: Any, cfg: Optional[Dict[str, Any]] = None):
        self.repo = repo
        self.cfg = get_sim_cfg(cfg)

    def refresh_cfg(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        if cfg is not None:
            self.cfg = get_sim_cfg(cfg)

    # ── 信号入口 ──────────────────────────────────────────────────────────

    def on_alert(self, alert: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """TG 同指纹信号 → 纸面开仓/记 skip。不自动实盘下单。"""
        now = now or datetime.now()
        if not self.cfg.get("enabled", True):
            return {"ok": False, "reason": "disabled"}

        fp = alert_fingerprint(alert)
        if self.repo.fingerprint_used(fp):
            return {"ok": False, "reason": "dup_fingerprint", "fingerprint": fp}

        symbol = str(alert.get("symbol") or "").strip().upper()
        if not symbol:
            return {"ok": False, "reason": "no_symbol"}

        # 与 Wheel TG 共用信号桶冷却键(SYMBOL|PUT|1h|EMA50)
        if is_timing_scan_alert(alert):
            try:
                from app.data import leaps_repository as leaps_repo
                cd_key = leaps_repo.signal_cooldown_key_from_signal(alert)
                if leaps_repo.is_contract_in_cooldown(cd_key):
                    return {
                        "ok": False,
                        "reason": "signal_bucket_cooldown",
                        "fingerprint": fp,
                        "cooldown_key": cd_key,
                    }
            except Exception:
                pass

        strategy = strategy_of_alert(alert)
        kind = str(alert.get("kind") or alert.get("signal_level") or "").upper()

        # 附录 A: chan_buy_mode=equity_long → 缠论 B/S 纸面买卖正股(与 CSP/CC 分账)
        if self._is_chan_equity_mode(alert):
            return self._equity_long_from_alert(alert, fp, strategy, now)

        side = self._alert_side(alert)

        if side == "PUT":
            return self._open_csp_from_alert(alert, fp, strategy, now)
        if side == "CALL":
            return self._open_cc_from_alert(alert, fp, strategy, now)
        self.repo.add_event(
            cycle_id=None, symbol=symbol, event_type="ignored_unknown",
            fingerprint=fp, detail={"kind": kind}, created_at=_now_iso(now),
        )
        return {"ok": False, "reason": "unknown_side", "fingerprint": fp}

    def _is_chan_equity_mode(self, alert: Dict[str, Any]) -> bool:
        mode = str(self.cfg.get("chan_buy_mode") or "").strip().lower()
        if mode != "equity_long":
            return False
        cat = (alert.get("category") or alert.get("source") or "").lower()
        kind = str(alert.get("kind") or "").upper()
        return cat == "chan" or kind in {"B1", "B2", "B3", "S1", "S2", "S3"}

    def _chan_equity_cfg(self) -> Dict[str, Any]:
        ce = self.cfg.get("chan_equity_sim")
        return ce if isinstance(ce, dict) else dict(DEFAULT_CHAN_EQUITY)

    def _equity_fill_price(self, alert: Dict[str, Any]) -> float:
        return _f(
            alert.get("underlying_price")
            or alert.get("price")
            or alert.get("spot")
            or alert.get("trigger_price"),
            0.0,
        )

    def _equity_long_from_alert(
        self,
        alert: Dict[str, Any],
        fp: str,
        strategy: str,
        now: datetime,
    ) -> Dict[str, Any]:
        """缠论 B→买正股 / S→卖正股;不允许裸空;与 Touch Wheel 分账。"""
        symbol = str(alert.get("symbol") or "").strip().upper()
        kind = str(alert.get("kind") or "").upper()
        spot = self._equity_fill_price(alert)
        ce = self._chan_equity_cfg()
        signal_qty = equity_qty_from_timeframe(
            alert.get("timeframe"),
            ce.get("qty_by_timeframe"),
        )
        iso = _now_iso(now)

        if spot <= 0:
            self.repo.add_event(
                cycle_id=None, symbol=symbol, event_type="skipped_no_price",
                fingerprint=fp, detail={"kind": kind}, created_at=iso,
            )
            return {"ok": False, "reason": "no_price", "fingerprint": fp}

        if signal_qty <= 0:
            self.repo.add_event(
                cycle_id=None, symbol=symbol, event_type="ignored_unknown",
                fingerprint=fp, detail={"kind": kind, "qty": signal_qty}, created_at=iso,
            )
            return {"ok": False, "reason": "no_qty", "fingerprint": fp}

        if kind.startswith("B"):
            return self._equity_buy(
                symbol=symbol, strategy=strategy, qty=float(signal_qty),
                price=spot, fingerprint=fp, kind=kind, now=now, alert=alert,
            )
        if kind.startswith("S"):
            return self._equity_sell(
                symbol=symbol, strategy=strategy, signal_qty=float(signal_qty),
                price=spot, fingerprint=fp, kind=kind, now=now, alert=alert,
            )
        self.repo.add_event(
            cycle_id=None, symbol=symbol, event_type="ignored_unknown",
            fingerprint=fp, detail={"kind": kind}, created_at=iso,
        )
        return {"ok": False, "reason": "unknown_side", "fingerprint": fp}

    def _equity_buy(
        self,
        *,
        symbol: str,
        strategy: str,
        qty: float,
        price: float,
        fingerprint: str,
        kind: str,
        now: datetime,
        alert: Dict[str, Any],
    ) -> Dict[str, Any]:
        iso = _now_iso(now)
        pos = self.repo.get_equity_position(strategy, symbol) or {
            "strategy": strategy, "symbol": symbol,
            "shares": 0.0, "avg_cost": 0.0, "realized_pnl": 0.0,
        }
        old_shares = _f(pos.get("shares"), 0)
        old_avg = _f(pos.get("avg_cost"), 0)
        new_shares = old_shares + qty
        new_avg = (
            ((old_shares * old_avg) + (qty * price)) / new_shares
            if new_shares > 0 else 0.0
        )
        self.repo.upsert_equity_position({
            "strategy": strategy,
            "symbol": symbol,
            "shares": new_shares,
            "avg_cost": new_avg,
            "realized_pnl": _f(pos.get("realized_pnl"), 0),
            "updated_at": iso,
        })
        trade_id = str(uuid.uuid4())
        self.repo.insert_equity_trade({
            "id": trade_id,
            "strategy": strategy,
            "symbol": symbol,
            "side": "BUY",
            "qty": qty,
            "price": price,
            "fingerprint": fingerprint,
            "note": kind,
            "realized_pnl": None,
            "created_at": iso,
        })
        self.repo.add_event(
            cycle_id=None, symbol=symbol, event_type="equity_buy",
            fingerprint=fingerprint,
            detail={
                "strategy": strategy, "kind": kind, "qty": qty, "price": price,
                "shares_after": new_shares, "avg_cost": new_avg,
                "share_pool": (self._chan_equity_cfg().get("share_pool") or "isolated"),
                "trade_id": trade_id,
            },
            created_at=iso,
        )
        return {
            "ok": True,
            "action": "equity_buy",
            "fingerprint": fingerprint,
            "strategy": strategy,
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "shares": new_shares,
            "avg_cost": new_avg,
            "trade_id": trade_id,
        }

    def _equity_sell(
        self,
        *,
        symbol: str,
        strategy: str,
        signal_qty: float,
        price: float,
        fingerprint: str,
        kind: str,
        now: datetime,
        alert: Dict[str, Any],
    ) -> Dict[str, Any]:
        iso = _now_iso(now)
        pos = self.repo.get_equity_position(strategy, symbol)
        held = _f((pos or {}).get("shares"), 0)
        if held <= 0:
            self.repo.add_event(
                cycle_id=None, symbol=symbol, event_type="skipped_no_shares",
                fingerprint=fingerprint,
                detail={
                    "strategy": strategy, "kind": kind,
                    "signal_qty": signal_qty, "mode": "equity_long",
                },
                created_at=iso,
            )
            return {
                "ok": False,
                "reason": "skipped_no_shares",
                "fingerprint": fingerprint,
                "strategy": strategy,
                "symbol": symbol,
            }

        sell_qty = min(signal_qty, held)
        partial = sell_qty < signal_qty
        avg_cost = _f((pos or {}).get("avg_cost"), 0)
        pnl = (price - avg_cost) * sell_qty
        new_shares = held - sell_qty
        realized = _f((pos or {}).get("realized_pnl"), 0) + pnl
        self.repo.upsert_equity_position({
            "strategy": strategy,
            "symbol": symbol,
            "shares": new_shares,
            "avg_cost": avg_cost if new_shares > 0 else 0.0,
            "realized_pnl": realized,
            "updated_at": iso,
        })
        trade_id = str(uuid.uuid4())
        note = f"{kind}|partial_exit" if partial else kind
        self.repo.insert_equity_trade({
            "id": trade_id,
            "strategy": strategy,
            "symbol": symbol,
            "side": "SELL",
            "qty": sell_qty,
            "price": price,
            "fingerprint": fingerprint,
            "note": note,
            "realized_pnl": pnl,
            "created_at": iso,
        })
        ev = "partial_exit" if partial else "equity_sell"
        self.repo.add_event(
            cycle_id=None, symbol=symbol, event_type=ev,
            fingerprint=fingerprint,
            detail={
                "strategy": strategy, "kind": kind,
                "signal_qty": signal_qty, "qty": sell_qty, "price": price,
                "shares_after": new_shares, "realized_pnl": pnl,
                "partial": partial,
                "share_pool": (self._chan_equity_cfg().get("share_pool") or "isolated"),
                "trade_id": trade_id,
            },
            created_at=iso,
        )
        return {
            "ok": True,
            "action": ev,
            "fingerprint": fingerprint,
            "strategy": strategy,
            "symbol": symbol,
            "qty": sell_qty,
            "signal_qty": signal_qty,
            "price": price,
            "shares": new_shares,
            "realized_pnl": pnl,
            "partial": partial,
            "trade_id": trade_id,
        }

    def _alert_side(self, alert: Dict[str, Any]) -> Optional[str]:
        level = (alert.get("signal_level") or "").upper()
        kind = (alert.get("kind") or "").upper()
        side = (alert.get("side") or "").upper()
        cat = (alert.get("category") or "").lower()
        if level == "WHEEL_PUT" or side == "PUT" or cat in ("put_touch", "timing_put"):
            return "PUT"
        if level == "WHEEL_CALL" or side == "CALL" or cat in ("call_touch", "timing_call"):
            return "CALL"
        if kind.startswith("B"):
            mode = self.cfg.get("chan_buy_mode") or "sell_put"
            return "PUT" if mode == "sell_put" else None
        if kind.startswith("S"):
            return "CALL"
        return None

    def _touch_cfg(self) -> Dict[str, Any]:
        tw = self.cfg.get("touch_wheel")
        return tw if isinstance(tw, dict) else dict(DEFAULT_TOUCH_WHEEL)

    def _signal_qty(self, alert: Dict[str, Any], strategy: str) -> int:
        """触线 → qty_from_timeframe; 缠论仍走 L1/L2/L3 size_contracts。"""
        if is_touch_alert(alert) or strategy.startswith(("put_1", "call_1", "put_touch", "call_touch")):
            return qty_from_timeframe(
                alert.get("timeframe"),
                (self._touch_cfg().get("qty_by_timeframe") or DEFAULT_TOUCH_WHEEL["qty_by_timeframe"]),
            )
        return -1  # 哨兵:调用方走 size_contracts

    def _list_csp_open(self, symbol: str) -> List[Dict[str, Any]]:
        rows = self.repo.list_cycles(symbol=symbol, include_closed=False) or []
        return [c for c in rows if c.get("status") == "CSP_OPEN"]

    def _list_cc_open(self, symbol: str) -> List[Dict[str, Any]]:
        rows = self.repo.list_cycles(symbol=symbol, include_closed=False) or []
        return [c for c in rows if c.get("status") == "CC_OPEN"]

    def _holding_cycle(self, symbol: str) -> Optional[Dict[str, Any]]:
        rows = self.repo.list_cycles(symbol=symbol, include_closed=False) or []
        for st in ("HOLDING", "CC_OPEN"):
            for c in rows:
                if c.get("status") == st and _f(c.get("shares"), 0) > 0:
                    return c
        return None

    def _uncovered_shares(self, symbol: str) -> float:
        rows = self.repo.list_cycles(symbol=symbol, include_closed=False) or []
        size = int(self.cfg.get("contract_size") or 100)
        shares = 0.0
        covered = 0.0
        for c in rows:
            if c.get("status") in ("HOLDING", "CC_OPEN"):
                shares += _f(c.get("shares"), 0)
            if c.get("status") == "CC_OPEN":
                covered += _f(c.get("open_qty"), 0) * size
        return max(0.0, shares - covered)

    def _csp_close_sort_key(self, c: Dict[str, Any], as_of: Optional[date] = None):
        """多腿买回顺序: DTE 更短 → strike 更高 → 开仓更早。"""
        as_of = as_of or date.today()
        exp = _parse_day(c.get("open_expiry"))
        dte = (exp - as_of).days if exp else 10**9
        strike = _f(c.get("open_strike"), 0)
        started = str(c.get("started_at") or "")
        return (dte, -strike, started)

    def _put_mark_for_close(self, alert: Dict[str, Any], c: Dict[str, Any]) -> float:
        mark = _f(alert.get("put_close_mark") or alert.get("close_mark"), 0)
        if mark > 0:
            return mark
        # 纸面:按开仓权利金一定比例估算买回(不表示真实市价)
        return max(0.05, _f(c.get("open_price"), 0.5) * 0.4)

    def _buyback_puts(
        self,
        symbol: str,
        qty: int,
        *,
        now: datetime,
        reason: str,
        alert: Optional[Dict[str, Any]] = None,
        fingerprint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按优先级买回该标的开仓 Put 共 qty 张。"""
        alert = alert or {}
        want = max(0, int(qty))
        if want <= 0:
            return []
        cycles = sorted(self._list_csp_open(symbol), key=lambda c: self._csp_close_sort_key(c, now.date()))
        out: List[Dict[str, Any]] = []
        left = want
        for c in cycles:
            if left <= 0:
                break
            open_qty = int(_f(c.get("open_qty"), 0))
            if open_qty <= 0:
                continue
            take = min(left, open_qty)
            mark = self._put_mark_for_close(alert, c)
            out.append(self._close_put(c, mark, now, reason=reason, close_qty=take, fingerprint=fingerprint))
            left -= take
        return out

    def _buyback_calls(
        self,
        symbol: str,
        qty: int,
        *,
        now: datetime,
        reason: str,
        alert: Optional[Dict[str, Any]] = None,
        fingerprint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        alert = alert or {}
        want = max(0, int(qty))
        if want <= 0:
            return []
        cycles = self._list_cc_open(symbol)
        out: List[Dict[str, Any]] = []
        left = want
        for c in cycles:
            if left <= 0:
                break
            open_qty = int(_f(c.get("open_qty"), 0))
            if open_qty <= 0:
                continue
            take = min(left, open_qty)
            mark = _f(alert.get("call_close_mark") or alert.get("close_mark"), 0)
            if mark <= 0:
                mark = max(0.05, _f(c.get("open_price"), 0.5) * 0.4)
            out.append(self._close_call(c, mark, now, reason=reason, close_qty=take, fingerprint=fingerprint))
            left -= take
        return out

    def _open_csp_from_alert(
        self, alert: Dict[str, Any], fp: str, strategy: str, now: datetime,
    ) -> Dict[str, Any]:
        symbol = str(alert["symbol"]).strip().upper()
        tw = self._touch_cfg()

        # 对称规则: CC 期间 Put 触线 → 先平对应张数 Call
        put_closes_call = bool(tw.get("put_touch_closes_call", True))
        close_actions: List[Dict[str, Any]] = []
        if put_closes_call and is_touch_alert(alert) and self._list_cc_open(symbol):
            q_close = self._signal_qty(alert, strategy)
            if q_close < 0:
                q_close = qty_from_timeframe(alert.get("timeframe"), tw.get("qty_by_timeframe"))
            close_actions = self._buyback_calls(
                symbol, q_close, now=now, reason="put_touch_closes_call",
                alert=alert, fingerprint=fp,
            )

        # 并行 CSP:默认允许;可选 max_open_csp_per_symbol
        open_csp = self._list_csp_open(symbol)
        allow_parallel = bool(tw.get("allow_parallel_csp", True))
        max_csp = int(tw.get("max_open_csp_per_symbol") or 0)
        if is_touch_alert(alert):
            if (not allow_parallel) and open_csp:
                self.repo.add_event(
                    cycle_id=open_csp[0]["id"], symbol=symbol, event_type="ignored_stacked",
                    fingerprint=fp, detail={"status": "CSP_OPEN"}, created_at=_now_iso(now),
                )
                return {"ok": False, "reason": "already_csp_open", "fingerprint": fp,
                        "prior_actions": close_actions}
            if max_csp > 0 and len(open_csp) >= max_csp:
                self.repo.add_event(
                    cycle_id=open_csp[0]["id"], symbol=symbol, event_type="ignored_max_csp",
                    fingerprint=fp, detail={"max": max_csp, "open": len(open_csp)},
                    created_at=_now_iso(now),
                )
                return {"ok": False, "reason": "max_open_csp", "fingerprint": fp,
                        "prior_actions": close_actions}
        else:
            # 缠论:保留旧「同策略已开则忽略」
            stacked = self.repo.find_open_cycle(symbol, strategy, status="CSP_OPEN")
            if stacked:
                self.repo.add_event(
                    cycle_id=stacked["id"], symbol=symbol, event_type="ignored_stacked",
                    fingerprint=fp, detail={"status": "CSP_OPEN"}, created_at=_now_iso(now),
                )
                return {"ok": False, "reason": "already_csp_open", "fingerprint": fp}

        spot = _f(alert.get("underlying_price") or alert.get("spot") or alert.get("price"), 0)
        strike = _f(alert.get("strike"), 0)
        floor = _f(alert.get("floor_price") or alert.get("floor"), 0)
        if strike <= 0 and spot > 0:
            strike = round(spot * 0.95, 2)  # 纸面兜底:略 OTM
        if spot <= 0 and strike > 0:
            spot = strike
        if strike <= 0:
            return {"ok": False, "reason": "no_strike", "fingerprint": fp}

        # 同源触线:严格 OTM(strike < spot);ATM/ITM 不进纸面
        if is_timing_scan_alert(alert) and spot > 0:
            from app.core.wheel_timing_klines import is_otm_put
            if not is_otm_put(strike, spot):
                self.repo.add_event(
                    cycle_id=None, symbol=symbol, event_type="skipped_not_otm",
                    fingerprint=fp, detail={"side": "PUT", "strike": strike, "spot": spot},
                    created_at=_now_iso(now),
                )
                return {"ok": False, "reason": "not_otm", "fingerprint": fp}

        # 愿接过滤:strike ≤ 愿接(有 floor 时)
        if floor > 0 and strike > floor:
            self.repo.add_event(
                cycle_id=None, symbol=symbol, event_type="skipped_above_floor",
                fingerprint=fp, detail={"strike": strike, "floor": floor},
                created_at=_now_iso(now),
            )
            return {"ok": False, "reason": "above_floor", "fingerprint": fp}

        level = map_level(
            signal_kind=str(alert.get("signal_level") or alert.get("kind") or ""),
            ema_type=alert.get("ema_type"),
            timeframe=alert.get("timeframe"),
            chan_kind=alert.get("kind"),
        )
        usage = self.repo.capital_usage()
        used_sym = float((usage.get("per_symbol") or {}).get(symbol, {}).get("committed") or 0)
        used_port = float(usage.get("total_committed") or 0)
        equity = float(self.cfg["equity"])
        size = int(self.cfg.get("contract_size") or 100)

        qty = self._signal_qty(alert, strategy)
        if qty < 0:
            qty = size_contracts(
                level, spot or strike, equity,
                used_symbol=used_sym,
                used_portfolio=used_port,
                max_symbol_pct=float(self.cfg.get("max_symbol_pct") or 0.25),
                max_portfolio_pct=float(self.cfg.get("max_portfolio_pct") or 0.80),
                levels=self.cfg.get("levels"),
                contract_size=size,
            )
        else:
            # 触线:只受组合占用约束(无单票 25% 硬顶)
            need = qty * (spot or strike) * size
            port_room = max(0.0, equity * float(self.cfg.get("max_portfolio_pct") or 0.80) - used_port)
            if need > port_room + 1e-6:
                self.repo.add_event(
                    cycle_id=None, symbol=symbol, event_type="skipped_size_cap",
                    fingerprint=fp, detail={"qty": qty, "need": need, "port_room": port_room},
                    created_at=_now_iso(now),
                )
                return {"ok": False, "reason": "size_cap", "fingerprint": fp,
                        "prior_actions": close_actions}

        if qty <= 0:
            self.repo.add_event(
                cycle_id=None, symbol=symbol, event_type="skipped_size_cap",
                fingerprint=fp, detail={"level": level}, created_at=_now_iso(now),
            )
            return {"ok": False, "reason": "size_cap", "fingerprint": fp}

        premium = _f(alert.get("bid") or alert.get("premium") or alert.get("trigger_price"), 0)
        if premium <= 0:
            premium = max(0.05, strike * 0.01)  # 纸面兜底权利金

        dte = int(alert.get("dte") or self.cfg.get("dte_default") or 30)
        exp = alert.get("expiry")
        if not exp:
            exp = (now.date() + timedelta(days=dte)).isoformat()

        cycle_id = str(uuid.uuid4())
        cycle = {
            "id": cycle_id,
            "symbol": symbol,
            "strategy": strategy,
            "status": "CSP_OPEN",
            "level": level,
            "shares": 0.0,
            "share_cost": 0.0,
            "cost_basis": None,
            "total_premium": round(qty * premium * size, 4),
            "realized_pnl": None,
            "open_strike": strike,
            "open_expiry": str(exp)[:10],
            "open_qty": float(qty),
            "open_price": premium,
            "open_option_type": "PUT",
            "open_contract_code": alert.get("contract_code"),
            "floor_price": floor or None,
            "alert_fingerprint": fp,
            "holding_since": None,
            "cc_force_tagged": 0,
            "started_at": _now_iso(now),
            "closed_at": None,
            "updated_at": _now_iso(now),
        }
        self.repo.insert_cycle(cycle)
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": cycle_id,
            "leg_type": "SELL_PUT",
            "strike": strike,
            "expiry": str(exp)[:10],
            "qty": float(qty),
            "price": premium,
            "premium_net": cycle["total_premium"],
            "note": f"sim open tf={normalize_timeframe(alert.get('timeframe'))} qty={qty}",
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        self.repo.add_event(
            cycle_id=cycle_id, symbol=symbol, event_type="open_csp",
            fingerprint=fp,
            detail={
                "level": level, "qty": qty, "strike": strike, "premium": premium,
                "timeframe": alert.get("timeframe"),
                "strategy": strategy,
            },
            created_at=_now_iso(now),
        )
        return {
            "ok": True, "action": "open_csp", "cycle_id": cycle_id,
            "fingerprint": fp, "level": level, "qty": qty, "strategy": strategy,
            "prior_actions": close_actions,
        }

    def _open_cc_from_alert(
        self, alert: Dict[str, Any], fp: str, strategy: str, now: datetime,
        *, force: bool = False,
    ) -> Dict[str, Any]:
        symbol = str(alert["symbol"]).strip().upper()
        tw = self._touch_cfg()
        close_actions: List[Dict[str, Any]] = []

        # Call 触线止盈 Put:先按周期张数买回同标的 CSP
        put_tp_mode = str(tw.get("put_tp_mode") or "call_touch").lower()
        if (
            (not force)
            and is_touch_alert(alert)
            and put_tp_mode in ("call_touch", "both")
            and self._list_csp_open(symbol)
        ):
            q_close = self._signal_qty(alert, strategy)
            if q_close < 0:
                q_close = qty_from_timeframe(alert.get("timeframe"), tw.get("qty_by_timeframe"))
            # 实际买回 ≤ 当前开仓 Put 总张数
            total_put = sum(int(_f(c.get("open_qty"), 0)) for c in self._list_csp_open(symbol))
            q_close = min(q_close, total_put)
            close_actions = self._buyback_puts(
                symbol, q_close, now=now, reason="call_touch_closes_put",
                alert=alert, fingerprint=fp,
            )

        holding = self._holding_cycle(symbol)
        uncovered = self._uncovered_shares(symbol)
        size = int(self.cfg.get("contract_size") or 100)

        if not holding or uncovered < size:
            mode = self.cfg.get("call_without_shares") or tw.get("call_without_shares") or "skip"
            if close_actions:
                # 只完成 Put 减仓,不挂 CC
                return {
                    "ok": True, "action": "close_put_only", "fingerprint": fp,
                    "prior_actions": close_actions, "reason": "no_uncovered_after_put_close",
                }
            self.repo.add_event(
                cycle_id=None, symbol=symbol, event_type="skipped_no_shares",
                fingerprint=fp, detail={"mode": mode, "force": force, "uncovered": uncovered},
                created_at=_now_iso(now),
            )
            return {"ok": False, "reason": "skipped_no_shares", "fingerprint": fp}

        if holding.get("status") == "CC_OPEN" or self._list_cc_open(symbol):
            self.repo.add_event(
                cycle_id=holding["id"], symbol=symbol, event_type="ignored_cc_open",
                fingerprint=fp, detail={}, created_at=_now_iso(now),
            )
            return {
                "ok": False, "reason": "already_cc_open", "fingerprint": fp,
                "prior_actions": close_actions,
            }

        cycle = holding
        shares = _f(cycle.get("shares"), 0)
        cost_basis = _f(cycle.get("cost_basis") or cycle.get("share_cost"), 0)
        spot = _f(alert.get("underlying_price") or alert.get("spot") or alert.get("price"), 0)
        strike = _f(alert.get("strike"), 0)
        if strike <= 0:
            base = max(cost_basis, spot) if (cost_basis or spot) else 0
            strike = round(base, 2) if base else 0
        if strike <= 0:
            return {"ok": False, "reason": "no_strike", "fingerprint": fp}
        if cost_basis > 0 and strike < cost_basis and not force:
            strike = cost_basis

        if (not force) and is_timing_scan_alert(alert) and spot > 0:
            from app.core.wheel_timing_klines import is_otm_call
            if not is_otm_call(strike, spot):
                self.repo.add_event(
                    cycle_id=cycle.get("id"), symbol=symbol, event_type="skipped_not_otm",
                    fingerprint=fp,
                    detail={"side": "CALL", "strike": strike, "spot": spot, "cost_basis": cost_basis},
                    created_at=_now_iso(now),
                )
                return {
                    "ok": False, "reason": "not_otm", "fingerprint": fp,
                    "prior_actions": close_actions,
                }

        level = cycle.get("level") or map_level(
            signal_kind=str(alert.get("signal_level") or alert.get("kind") or ""),
            ema_type=alert.get("ema_type"),
            timeframe=alert.get("timeframe"),
            chan_kind=alert.get("kind"),
        )

        sig_qty = self._signal_qty(alert, strategy)
        if sig_qty < 0:
            qty = size_contracts(
                level, spot or strike, float(self.cfg["equity"]),
                for_call_shares=uncovered,
                levels=self.cfg.get("levels"),
                contract_size=size,
            )
            if qty <= 0:
                qty = max(1, int(uncovered // size))
        else:
            qty = min(sig_qty, int(uncovered // size))

        if qty <= 0:
            if close_actions:
                return {
                    "ok": True, "action": "close_put_only", "fingerprint": fp,
                    "prior_actions": close_actions,
                }
            return {"ok": False, "reason": "no_qty", "fingerprint": fp}

        premium = _f(alert.get("bid") or alert.get("premium") or alert.get("trigger_price"), 0)
        if premium <= 0:
            premium = max(0.05, strike * 0.008)
        dte = int(alert.get("dte") or self.cfg.get("dte_default") or 30)
        exp = alert.get("expiry") or (now.date() + timedelta(days=dte)).isoformat()
        prem_net = round(qty * premium * size, 4)

        cycle_id = cycle["id"]
        self.repo.update_cycle(cycle_id, {
            "status": "CC_OPEN",
            "open_strike": strike,
            "open_expiry": str(exp)[:10],
            "open_qty": float(qty),
            "open_price": premium,
            "open_option_type": "CALL",
            "open_contract_code": alert.get("contract_code"),
            "total_premium": round(_f(cycle.get("total_premium")) + prem_net, 4),
            "alert_fingerprint": fp if not force else cycle.get("alert_fingerprint"),
            "cc_force_tagged": 1 if force else int(cycle.get("cc_force_tagged") or 0),
            "updated_at": _now_iso(now),
        })
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": cycle_id,
            "leg_type": "SELL_CALL",
            "strike": strike,
            "expiry": str(exp)[:10],
            "qty": float(qty),
            "price": premium,
            "premium_net": prem_net,
            "note": "force_cc" if force else "sim sell call",
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        ev = "force_cc" if force else "open_cc"
        self.repo.add_event(
            cycle_id=cycle_id, symbol=symbol, event_type=ev,
            fingerprint=fp,
            detail={
                "qty": qty, "strike": strike, "premium": premium, "force": force,
                "timeframe": alert.get("timeframe"), "strategy": strategy,
            },
            created_at=_now_iso(now),
        )
        return {
            "ok": True, "action": ev, "cycle_id": cycle_id, "fingerprint": fp,
            "qty": qty, "prior_actions": close_actions,
        }

    # ── tick:行情推进 ─────────────────────────────────────────────────────

    def tick(
        self,
        spots: Dict[str, float],
        *,
        marks: Optional[Dict[str, float]] = None,
        now: Optional[datetime] = None,
        as_of: Optional[date] = None,
    ) -> Dict[str, Any]:
        """推进所有非 CLOSED 周期:止盈/威胁/指派/强挂 CC/到期。"""
        now = now or datetime.now()
        as_of = as_of or now.date()
        marks = marks or {}
        actions: List[Dict[str, Any]] = []
        cycles = self.repo.list_cycles(include_closed=False)
        for c in cycles:
            status = c.get("status")
            sym = c.get("symbol")
            spot = _f(spots.get(sym), 0)
            mark = marks.get(c.get("id") or "")
            if mark is None and c.get("open_contract_code"):
                mark = marks.get(str(c.get("open_contract_code")))
            if status == "CSP_OPEN":
                actions.extend(self._tick_csp(c, spot, mark, now, as_of))
            elif status == "HOLDING":
                actions.extend(self._tick_holding(c, spot, now, as_of))
            elif status == "CC_OPEN":
                actions.extend(self._tick_cc(c, spot, mark, now, as_of))
        return {"ok": True, "actions": actions, "as_of": as_of.isoformat()}

    def _tick_csp(
        self, c: Dict[str, Any], spot: float, mark: Optional[float],
        now: datetime, as_of: date,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        strike = _f(c.get("open_strike"))
        open_price = _f(c.get("open_price"))
        exp = _parse_day(c.get("open_expiry"))
        dte = (exp - as_of).days if exp else None
        hard = float(self.cfg.get("hard_profit_pct") or 42)
        soft = float(self.cfg.get("soft_profit_pct") or 28)
        min_ann = float(self.cfg.get("min_remaining_ann") or 12)
        roll_dte = int(self.cfg.get("hard_roll_dte") or 21)
        threat = float(self.cfg.get("threat_otm_buffer_pct") or 5)

        # 破愿接:不买回,扛到接货
        floor = _f(c.get("floor_price"), 0)
        if floor > 0 and spot > 0 and spot < floor:
            if self.cfg.get("put_breach_floor") == "hold_to_assign":
                self.repo.add_event(
                    cycle_id=c["id"], symbol=c["symbol"], event_type="breach_floor_hold",
                    fingerprint=None, detail={"spot": spot, "floor": floor},
                    created_at=_now_iso(now),
                )
                out.append({"cycle_id": c["id"], "action": "breach_floor_hold"})

        # 到期路径优先
        if exp and as_of >= exp:
            if spot > 0 and spot < strike:
                out.append(self._assign_put(c, now))
            else:
                out.append(self._expire_put(c, now))
            return out

        if mark is None:
            # 无期权标记价时,仅用现货粗估 ITM;止盈需 mark
            return out

        tw = self._touch_cfg()
        put_tp_mode = str(tw.get("put_tp_mode") or "call_touch").lower()
        premium_ok = bool(tw.get("premium_tp_override")) or put_tp_mode in ("premium_pct", "both")
        threat_exit = bool(tw.get("threat_exit"))

        profit = put_profit_pct(open_price, mark)
        rem = remaining_ann(open_price, mark, dte or 0, strike)
        buf = otm_buffer_pct(spot, strike, "PUT") if spot else None

        # 主路径 call_touch:无 Call 信号不主动权利金止盈
        if premium_ok:
            if profit is not None and profit >= hard:
                out.append(self._close_put(c, mark, now, reason="hard_tp"))
                return out
            if profit is not None and profit >= soft and rem is not None and rem < min_ann:
                out.append(self._close_put(c, mark, now, reason="soft_tp_low_ann"))
                return out
        if (
            threat_exit
            and dte is not None and dte <= roll_dte
            and buf is not None and buf < threat
            and (profit is None or profit < hard)
        ):
            self.repo.add_event(
                cycle_id=c["id"], symbol=c["symbol"], event_type="would_roll",
                fingerprint=None,
                detail={"dte": dte, "buffer": buf, "profit": profit},
                created_at=_now_iso(now),
            )
            out.append(self._close_put(c, mark, now, reason="threat_would_roll"))
            return out
        elif (
            dte is not None and dte <= roll_dte
            and buf is not None and buf < threat
            and (profit is None or profit < hard)
        ):
            # 默认威胁窗仍持有,只打标
            self.repo.add_event(
                cycle_id=c["id"], symbol=c["symbol"], event_type="would_roll",
                fingerprint=None,
                detail={"dte": dte, "buffer": buf, "profit": profit, "held": True},
                created_at=_now_iso(now),
            )
            out.append({"cycle_id": c["id"], "action": "would_roll_hold"})
        return out

    def _close_put(
        self, c: Dict[str, Any], mark: float, now: datetime, reason: str,
        *, close_qty: Optional[float] = None, fingerprint: Optional[str] = None,
    ) -> Dict[str, Any]:
        size = int(self.cfg.get("contract_size") or 100)
        open_qty = _f(c.get("open_qty"), 1)
        qty = open_qty if close_qty is None else min(open_qty, max(0.0, float(close_qty)))
        if qty <= 0:
            return {"cycle_id": c["id"], "action": "close_put", "reason": reason, "qty": 0}
        cost = qty * mark * size
        new_prem = round(_f(c.get("total_premium")) - cost, 4)
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": c["id"],
            "leg_type": "BUY_PUT_CLOSE",
            "strike": c.get("open_strike"),
            "expiry": c.get("open_expiry"),
            "qty": qty,
            "price": mark,
            "premium_net": -cost,
            "note": reason,
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        remaining = open_qty - qty
        if remaining > 1e-9:
            # 部分减仓,周期仍 CSP_OPEN
            self.repo.update_cycle(c["id"], {
                "status": "CSP_OPEN",
                "total_premium": new_prem,
                "open_qty": float(remaining),
                "updated_at": _now_iso(now),
            })
            # 同步内存,供同批后续腿使用
            c["open_qty"] = float(remaining)
            c["total_premium"] = new_prem
            self.repo.add_event(
                cycle_id=c["id"], symbol=c["symbol"], event_type="close_put",
                fingerprint=fingerprint,
                detail={"reason": reason, "qty": qty, "remaining": remaining, "partial": True},
                created_at=_now_iso(now),
            )
            return {
                "cycle_id": c["id"], "action": "close_put", "reason": reason,
                "qty": qty, "remaining": remaining, "partial": True,
            }

        pnl = new_prem  # 无股票腿
        self.repo.update_cycle(c["id"], {
            "status": "CLOSED",
            "total_premium": new_prem,
            "realized_pnl": round(pnl, 4),
            "open_strike": None,
            "open_expiry": None,
            "open_qty": 0,
            "open_price": 0,
            "open_option_type": None,
            "open_contract_code": None,
            "closed_at": _now_iso(now),
            "updated_at": _now_iso(now),
        })
        c["open_qty"] = 0
        c["status"] = "CLOSED"
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="close_put",
            fingerprint=fingerprint, detail={"reason": reason, "pnl": pnl, "qty": qty},
            created_at=_now_iso(now),
        )
        self.repo.record_closed_stats(c, pnl=pnl, assigned=False, called_away=False, now=now)
        return {"cycle_id": c["id"], "action": "close_put", "reason": reason, "pnl": pnl, "qty": qty}

    def _expire_put(self, c: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        pnl = _f(c.get("total_premium"))
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": c["id"],
            "leg_type": "EXPIRE",
            "strike": c.get("open_strike"),
            "expiry": c.get("open_expiry"),
            "qty": c.get("open_qty"),
            "price": 0,
            "premium_net": 0,
            "note": "OTM expire",
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        self.repo.update_cycle(c["id"], {
            "status": "CLOSED",
            "realized_pnl": round(pnl, 4),
            "open_strike": None,
            "open_expiry": None,
            "open_qty": 0,
            "open_price": 0,
            "open_option_type": None,
            "open_contract_code": None,
            "closed_at": _now_iso(now),
            "updated_at": _now_iso(now),
        })
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="expire_put",
            fingerprint=None, detail={"pnl": pnl}, created_at=_now_iso(now),
        )
        self.repo.record_closed_stats(c, pnl=pnl, assigned=False, called_away=False, now=now)
        return {"cycle_id": c["id"], "action": "expire_put", "pnl": pnl}

    def _assign_put(self, c: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        size = int(self.cfg.get("contract_size") or 100)
        qty = _f(c.get("open_qty"), 1)
        strike = _f(c.get("open_strike"))
        shares = qty * size
        premium = _f(c.get("total_premium"))
        # cost_basis = strike − premium_per_share
        prem_ps = premium / shares if shares else 0
        cost_basis = round(strike - prem_ps, 4)
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": c["id"],
            "leg_type": "ASSIGNED",
            "strike": strike,
            "expiry": c.get("open_expiry"),
            "qty": qty,
            "price": strike,
            "premium_net": 0,
            "note": "assigned to shares",
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        self.repo.update_cycle(c["id"], {
            "status": "HOLDING",
            "shares": shares,
            "share_cost": strike,
            "cost_basis": cost_basis,
            "open_strike": None,
            "open_expiry": None,
            "open_qty": 0,
            "open_price": 0,
            "open_option_type": None,
            "open_contract_code": None,
            "holding_since": _now_iso(now),
            "updated_at": _now_iso(now),
        })
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="assign",
            fingerprint=None,
            detail={"shares": shares, "share_cost": strike, "cost_basis": cost_basis},
            created_at=_now_iso(now),
        )
        return {
            "cycle_id": c["id"], "action": "assign",
            "shares": shares, "cost_basis": cost_basis, "share_cost": strike,
        }

    def _tick_holding(
        self, c: Dict[str, Any], spot: float, now: datetime, as_of: date,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        force_days = int(self.cfg.get("cc_force_days") or 0)
        if force_days <= 0:
            return out
        since = _parse_day(c.get("holding_since") or c.get("updated_at") or c.get("started_at"))
        if not since:
            return out
        held = trading_days_between(since, as_of)
        cost_basis = _f(c.get("cost_basis") or c.get("share_cost"))
        if held >= force_days and spot > 0 and cost_basis > 0 and spot >= cost_basis:
            if int(c.get("cc_force_tagged") or 0) == 1 and c.get("status") == "CC_OPEN":
                return out
            # 强挂 ATM/OTM1
            alert = {
                "symbol": c["symbol"],
                "side": "CALL",
                "strike": round(max(spot, cost_basis), 2),
                "underlying_price": spot,
                "premium": max(0.05, spot * 0.008),
                "fingerprint": f"force_cc:{c['id']}:{as_of.isoformat()}",
                "category": "force_cc",
            }
            r = self._open_cc_from_alert(alert, alert["fingerprint"], c.get("strategy") or "put_touch", now, force=True)
            out.append(r)
        return out

    def _tick_cc(
        self, c: Dict[str, Any], spot: float, mark: Optional[float],
        now: datetime, as_of: date,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        strike = _f(c.get("open_strike"))
        open_price = _f(c.get("open_price"))
        exp = _parse_day(c.get("open_expiry"))
        hard = float(self.cfg.get("hard_profit_pct") or 42)
        soft = float(self.cfg.get("soft_profit_pct") or 28)
        min_ann = float(self.cfg.get("min_remaining_ann") or 12)
        dte = (exp - as_of).days if exp else None

        if exp and as_of >= exp:
            if spot > 0 and spot > strike:
                out.append(self._called_away(c, now))
            else:
                out.append(self._expire_call(c, now))
            return out

        if mark is None:
            return out
        profit = put_profit_pct(open_price, mark)
        rem = remaining_ann(open_price, mark, dte or 0, strike)
        if profit is not None and profit >= hard:
            out.append(self._close_call(c, mark, now, reason="hard_tp"))
            return out
        if profit is not None and profit >= soft and rem is not None and rem < min_ann:
            out.append(self._close_call(c, mark, now, reason="soft_tp_low_ann"))
            return out
        return out

    def _close_call(
        self, c: Dict[str, Any], mark: float, now: datetime, reason: str,
        *, close_qty: Optional[float] = None, fingerprint: Optional[str] = None,
    ) -> Dict[str, Any]:
        size = int(self.cfg.get("contract_size") or 100)
        open_qty = _f(c.get("open_qty"), 1)
        qty = open_qty if close_qty is None else min(open_qty, max(0.0, float(close_qty)))
        if qty <= 0:
            return {"cycle_id": c["id"], "action": "close_call", "reason": reason, "qty": 0}
        cost = qty * mark * size
        new_prem = round(_f(c.get("total_premium")) - cost, 4)
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": c["id"],
            "leg_type": "BUY_CALL_CLOSE",
            "strike": c.get("open_strike"),
            "expiry": c.get("open_expiry"),
            "qty": qty,
            "price": mark,
            "premium_net": -cost,
            "note": reason,
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        remaining = open_qty - qty
        if remaining > 1e-9:
            self.repo.update_cycle(c["id"], {
                "status": "CC_OPEN",
                "total_premium": new_prem,
                "open_qty": float(remaining),
                "updated_at": _now_iso(now),
            })
            c["open_qty"] = float(remaining)
            c["total_premium"] = new_prem
            self.repo.add_event(
                cycle_id=c["id"], symbol=c["symbol"], event_type="close_call",
                fingerprint=fingerprint,
                detail={"reason": reason, "qty": qty, "remaining": remaining, "partial": True},
                created_at=_now_iso(now),
            )
            return {
                "cycle_id": c["id"], "action": "close_call", "reason": reason,
                "qty": qty, "remaining": remaining, "partial": True,
            }

        self.repo.update_cycle(c["id"], {
            "status": "HOLDING",
            "total_premium": new_prem,
            "open_strike": None,
            "open_expiry": None,
            "open_qty": 0,
            "open_price": 0,
            "open_option_type": None,
            "open_contract_code": None,
            "cc_force_tagged": 0,
            "holding_since": _now_iso(now),
            "updated_at": _now_iso(now),
        })
        c["open_qty"] = 0
        c["status"] = "HOLDING"
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="close_call",
            fingerprint=fingerprint, detail={"reason": reason, "qty": qty},
            created_at=_now_iso(now),
        )
        return {"cycle_id": c["id"], "action": "close_call", "reason": reason, "qty": qty}

    def _expire_call(self, c: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": c["id"],
            "leg_type": "EXPIRE",
            "strike": c.get("open_strike"),
            "expiry": c.get("open_expiry"),
            "qty": c.get("open_qty"),
            "price": 0,
            "premium_net": 0,
            "note": "CC OTM expire",
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        self.repo.update_cycle(c["id"], {
            "status": "HOLDING",
            "open_strike": None,
            "open_expiry": None,
            "open_qty": 0,
            "open_price": 0,
            "open_option_type": None,
            "open_contract_code": None,
            "cc_force_tagged": 0,
            "holding_since": _now_iso(now),
            "updated_at": _now_iso(now),
        })
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="expire_call",
            fingerprint=None, detail={}, created_at=_now_iso(now),
        )
        return {"cycle_id": c["id"], "action": "expire_call"}

    def _called_away(self, c: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        strike = _f(c.get("open_strike"))
        shares = _f(c.get("shares"))
        cost_basis = _f(c.get("cost_basis") or c.get("share_cost"))
        premium = _f(c.get("total_premium"))
        stock_pnl = (strike - cost_basis) * shares
        pnl = round(stock_pnl + premium, 4)
        self.repo.insert_leg({
            "id": str(uuid.uuid4()),
            "cycle_id": c["id"],
            "leg_type": "CALLED_AWAY",
            "strike": strike,
            "expiry": c.get("open_expiry"),
            "qty": c.get("open_qty"),
            "price": strike,
            "premium_net": 0,
            "note": "called away",
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        self.repo.update_cycle(c["id"], {
            "status": "CLOSED",
            "shares": 0,
            "realized_pnl": pnl,
            "open_strike": None,
            "open_expiry": None,
            "open_qty": 0,
            "open_price": 0,
            "open_option_type": None,
            "open_contract_code": None,
            "closed_at": _now_iso(now),
            "updated_at": _now_iso(now),
        })
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="called_away",
            fingerprint=None, detail={"pnl": pnl, "strike": strike, "cost_basis": cost_basis},
            created_at=_now_iso(now),
        )
        self.repo.record_closed_stats(c, pnl=pnl, assigned=True, called_away=True, now=now)
        return {"cycle_id": c["id"], "action": "called_away", "pnl": pnl}


def select_best_alerts_for_sim(
    alerts: List[Dict[str, Any]],
    *,
    same_batch_1h_1d: str = "prefer_daily",
) -> List[Dict[str, Any]]:
    """同 tick/batch:先按 (symbol,side,tf) 择优,再同批 1h+1d 冲突策略。"""
    from app.core.touch_best import select_best_touch_signals
    per_tf = list(select_best_touch_signals(alerts or [], group_by_timeframe=True))
    keepers, _shadows = resolve_same_batch_1h_1d(per_tf, mode=same_batch_1h_1d)
    return list(keepers)


def select_touch_batch_for_push(
    alerts: List[Any],
    *,
    same_batch_1h_1d: str = "prefer_daily",
) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """扫描出口:择优 + 1h/1d 冲突。返回 (push_list, shadow_meta)。"""
    from app.core.touch_best import select_best_touch_signals
    per_tf = list(select_best_touch_signals(alerts or [], group_by_timeframe=True))
    return resolve_same_batch_1h_1d(per_tf, mode=same_batch_1h_1d)


def sim_on_alert(alert: Dict[str, Any], *, cfg: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """模块级入口:供 TG/扫描同指纹挂钩。失败吞掉,不影响推送。"""
    try:
        from app.data import sim_repository as repo
        if cfg is None:
            try:
                from app.core.config import get_effective_config
                cfg = get_effective_config()
            except Exception:
                cfg = {}
        eng = SimWheelEngine(repo, cfg)
        return eng.on_alert(alert, now=now)
    except Exception as e:
        logger.info("sim_on_alert skip: %s", e)
        return {"ok": False, "reason": f"error:{e}"}


def alert_from_wheel_signal(sig: Any) -> Dict[str, Any]:
    """LeapsSignal / dict → sim alert。"""
    if isinstance(sig, dict):
        d = sig
    else:
        d = {
            "symbol": getattr(sig, "symbol", None),
            "signal_level": getattr(sig, "signal_level", None),
            "contract_code": getattr(sig, "contract_code", None),
            "strike": getattr(sig, "strike", None),
            "expiry": getattr(sig, "expiry", None),
            "dte": getattr(sig, "dte", None),
            "ema_type": getattr(sig, "ema_type", None),
            "trigger_price": getattr(sig, "trigger_price", None),
            "bid": getattr(sig, "bid", None),
            "underlying_price": getattr(sig, "underlying_price", None),
            "floor_price": getattr(sig, "floor_price", None),
            "iv_rank": getattr(sig, "iv_rank", None),
            "annualized": getattr(sig, "annualized", None),
            "theta": getattr(sig, "theta", None),
            "timeframe": getattr(sig, "timeframe", None),
        }
    level = (d.get("signal_level") or "").upper()
    d["category"] = "timing_call" if level == "WHEEL_CALL" else "timing_put"
    d["side"] = "CALL" if level == "WHEEL_CALL" else "PUT"
    d["fingerprint"] = alert_fingerprint(d)
    return d


def alert_from_chan_item(item: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(item)
    d["category"] = "chan"
    d["fingerprint"] = d.get("fingerprint") or alert_fingerprint(d)
    if d.get("price") and not d.get("underlying_price"):
        d["underlying_price"] = d["price"]
    return d
