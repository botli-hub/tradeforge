"""完整轮子纸面账 (Sim Wheel) — 独立状态机,不碰实盘台账 / FirstTrade / POSITION_QUANT.

状态: IDLE → CSP_OPEN → HOLDING → CC_OPEN → IDLE/CLOSED
信号进账: Put触线 / Call触线 / 缠论 B/S(默认 B→卖Put)
v1: put_breach_floor=hold_to_assign; roll 只打 would_roll; 无持股 Call → skip.
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
STRATEGIES = ("put_touch", "call_touch", "chan5m", "chan30m")

DEFAULT_SIM: Dict[str, Any] = {
    "enabled": True,
    "chan_buy_mode": "sell_put",  # v1 不做 equity_long
    "call_without_shares": "skip",
    "levels": {"L1": 0.02, "L2": 0.04, "L3": 0.06},
    "cc_force_days": 5,
    "put_breach_floor": "hold_to_assign",
    "roll": "tag_only",
    "max_symbol_pct": 0.25,
    "max_portfolio_pct": 0.80,
    "equity": 100_000.0,  # 纸面权益兜底;可被 cfg 覆盖
    "contract_size": 100,
    "dte_default": 30,
    # 纸面止盈 — 读 sim 配置,绝不改 wheel_position / POSITION_QUANT
    "hard_profit_pct": 42.0,
    "soft_profit_pct": 28.0,
    "min_remaining_ann": 12.0,
    "hard_roll_dte": 21,
    "threat_otm_buffer_pct": 5.0,
    "tg_summary": True,
}


def get_sim_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_SIM)
    if cfg:
        overlay = cfg.get("sim_wheel") or {}
        if isinstance(overlay, dict):
            merged.update(overlay)
            if isinstance(overlay.get("levels"), dict):
                levels = dict(DEFAULT_SIM["levels"])
                levels.update(overlay["levels"])
                merged["levels"] = levels
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


def strategy_of_alert(alert: Dict[str, Any]) -> str:
    """策略维度: put_touch / call_touch / chan5m / chan30m。"""
    cat = (alert.get("category") or alert.get("source") or "").lower()
    level = (alert.get("signal_level") or "").upper()
    tf = str(alert.get("timeframe") or "").lower()
    kind = str(alert.get("kind") or "").upper()

    if cat == "chan" or kind in {"B1", "B2", "B3", "S1", "S2", "S3"}:
        return "chan30m" if tf.startswith("30") else "chan5m"
    if level == "WHEEL_CALL" or cat in ("call_touch", "timing_call"):
        return "call_touch"
    return "put_touch"


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

        strategy = strategy_of_alert(alert)
        kind = str(alert.get("kind") or alert.get("signal_level") or "").upper()
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

    def _open_csp_from_alert(
        self, alert: Dict[str, Any], fp: str, strategy: str, now: datetime,
    ) -> Dict[str, Any]:
        symbol = str(alert["symbol"]).strip().upper()
        # 同策略×标的已有 CSP_OPEN → 忽略
        open_csp = self.repo.find_open_cycle(symbol, strategy, status="CSP_OPEN")
        if open_csp:
            self.repo.add_event(
                cycle_id=open_csp["id"], symbol=symbol, event_type="ignored_stacked",
                fingerprint=fp, detail={"status": "CSP_OPEN"}, created_at=_now_iso(now),
            )
            return {"ok": False, "reason": "already_csp_open", "fingerprint": fp}

        # 同标的 HOLDING/CC_OPEN 时不再开新 Put
        holding = self.repo.find_symbol_active(symbol, statuses=("HOLDING", "CC_OPEN", "CSP_OPEN"))
        if holding:
            self.repo.add_event(
                cycle_id=holding["id"], symbol=symbol, event_type="ignored_occupied",
                fingerprint=fp, detail={"status": holding.get("status")}, created_at=_now_iso(now),
            )
            return {"ok": False, "reason": "symbol_occupied", "fingerprint": fp}

        spot = _f(alert.get("underlying_price") or alert.get("spot") or alert.get("price"), 0)
        strike = _f(alert.get("strike"), 0)
        floor = _f(alert.get("floor_price") or alert.get("floor"), 0)
        if strike <= 0 and spot > 0:
            strike = round(spot * 0.95, 2)  # 纸面兜底:略 OTM
        if spot <= 0 and strike > 0:
            spot = strike
        if strike <= 0:
            return {"ok": False, "reason": "no_strike", "fingerprint": fp}

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
        qty = size_contracts(
            level, spot or strike, float(self.cfg["equity"]),
            used_symbol=used_sym,
            used_portfolio=float(usage.get("total_committed") or 0),
            max_symbol_pct=float(self.cfg.get("max_symbol_pct") or 0.25),
            max_portfolio_pct=float(self.cfg.get("max_portfolio_pct") or 0.80),
            levels=self.cfg.get("levels"),
            contract_size=int(self.cfg.get("contract_size") or 100),
        )
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
            "total_premium": round(qty * premium * int(self.cfg.get("contract_size") or 100), 4),
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
            "note": f"sim open L{level[-1] if level else '?'}",
            "traded_at": _now_iso(now),
            "created_at": _now_iso(now),
        })
        self.repo.add_event(
            cycle_id=cycle_id, symbol=symbol, event_type="open_csp",
            fingerprint=fp,
            detail={"level": level, "qty": qty, "strike": strike, "premium": premium},
            created_at=_now_iso(now),
        )
        return {"ok": True, "action": "open_csp", "cycle_id": cycle_id, "fingerprint": fp, "level": level}

    def _open_cc_from_alert(
        self, alert: Dict[str, Any], fp: str, strategy: str, now: datetime,
        *, force: bool = False,
    ) -> Dict[str, Any]:
        symbol = str(alert["symbol"]).strip().upper()
        holding = self.repo.find_symbol_active(symbol, statuses=("HOLDING",))
        if not holding:
            # 无持股 → skip(默认)
            mode = self.cfg.get("call_without_shares") or "skip"
            self.repo.add_event(
                cycle_id=None, symbol=symbol, event_type="skipped_no_shares",
                fingerprint=fp, detail={"mode": mode, "force": force},
                created_at=_now_iso(now),
            )
            return {"ok": False, "reason": "skipped_no_shares", "fingerprint": fp}

        if holding.get("status") == "CC_OPEN" or self.repo.find_open_cycle(
            symbol, holding.get("strategy") or strategy, status="CC_OPEN",
        ):
            # 已有 CC
            self.repo.add_event(
                cycle_id=holding["id"], symbol=symbol, event_type="ignored_cc_open",
                fingerprint=fp, detail={}, created_at=_now_iso(now),
            )
            return {"ok": False, "reason": "already_cc_open", "fingerprint": fp}

        # 使用持股所在 cycle(策略沿用开 Put 的策略)
        cycle = holding
        shares = _f(cycle.get("shares"), 0)
        cost_basis = _f(cycle.get("cost_basis") or cycle.get("share_cost"), 0)
        spot = _f(alert.get("underlying_price") or alert.get("spot") or alert.get("price"), 0)
        strike = _f(alert.get("strike"), 0)
        if strike <= 0:
            # ATM / 成本上方
            base = max(cost_basis, spot) if (cost_basis or spot) else 0
            strike = round(base, 2) if base else 0
        if strike <= 0:
            return {"ok": False, "reason": "no_strike", "fingerprint": fp}
        # strike ≥ 成本基础
        if cost_basis > 0 and strike < cost_basis and not force:
            strike = cost_basis

        level = cycle.get("level") or map_level(
            signal_kind=str(alert.get("signal_level") or alert.get("kind") or ""),
            ema_type=alert.get("ema_type"),
            timeframe=alert.get("timeframe"),
            chan_kind=alert.get("kind"),
        )
        qty = size_contracts(
            level, spot or strike, float(self.cfg["equity"]),
            for_call_shares=shares,
            levels=self.cfg.get("levels"),
            contract_size=int(self.cfg.get("contract_size") or 100),
        )
        if qty <= 0:
            qty = max(1, int(shares // int(self.cfg.get("contract_size") or 100)))
        premium = _f(alert.get("bid") or alert.get("premium") or alert.get("trigger_price"), 0)
        if premium <= 0:
            premium = max(0.05, strike * 0.008)
        dte = int(alert.get("dte") or self.cfg.get("dte_default") or 30)
        exp = alert.get("expiry") or (now.date() + timedelta(days=dte)).isoformat()
        size = int(self.cfg.get("contract_size") or 100)
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
            detail={"qty": qty, "strike": strike, "premium": premium, "force": force},
            created_at=_now_iso(now),
        )
        return {"ok": True, "action": ev, "cycle_id": cycle_id, "fingerprint": fp}

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

        profit = put_profit_pct(open_price, mark)
        rem = remaining_ann(open_price, mark, dte or 0, strike)
        buf = otm_buffer_pct(spot, strike, "PUT") if spot else None

        if profit is not None and profit >= hard:
            out.append(self._close_put(c, mark, now, reason="hard_tp"))
            return out
        if profit is not None and profit >= soft and rem is not None and rem < min_ann:
            out.append(self._close_put(c, mark, now, reason="soft_tp_low_ann"))
            return out
        if (
            dte is not None and dte <= roll_dte
            and buf is not None and buf < threat
            and (profit is None or profit < hard)
        ):
            # v1 不 Roll,只打标;可选买回 — PRD:威胁买回 IDLE + would_roll
            self.repo.add_event(
                cycle_id=c["id"], symbol=c["symbol"], event_type="would_roll",
                fingerprint=None,
                detail={"dte": dte, "buffer": buf, "profit": profit},
                created_at=_now_iso(now),
            )
            out.append(self._close_put(c, mark, now, reason="threat_would_roll"))
            return out
        return out

    def _close_put(self, c: Dict[str, Any], mark: float, now: datetime, reason: str) -> Dict[str, Any]:
        size = int(self.cfg.get("contract_size") or 100)
        qty = _f(c.get("open_qty"), 1)
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
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="close_put",
            fingerprint=None, detail={"reason": reason, "pnl": pnl},
            created_at=_now_iso(now),
        )
        self.repo.record_closed_stats(c, pnl=pnl, assigned=False, called_away=False, now=now)
        return {"cycle_id": c["id"], "action": "close_put", "reason": reason, "pnl": pnl}

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
        force_days = int(self.cfg.get("cc_force_days") or 5)
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

    def _close_call(self, c: Dict[str, Any], mark: float, now: datetime, reason: str) -> Dict[str, Any]:
        size = int(self.cfg.get("contract_size") or 100)
        qty = _f(c.get("open_qty"), 1)
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
        self.repo.add_event(
            cycle_id=c["id"], symbol=c["symbol"], event_type="close_call",
            fingerprint=None, detail={"reason": reason}, created_at=_now_iso(now),
        )
        return {"cycle_id": c["id"], "action": "close_call", "reason": reason}

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
