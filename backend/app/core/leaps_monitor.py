"""LEAPS Put 权利金卖出信号核心引擎

信号三条件（S1 & S2 & S3 同时满足）：
  S1: 合约当日最高价（盘中用最新价）≥ EMA50（一级）或 ≥ EMA200（二级强信号）
  S2: 合约当前 IV ≥ 自身 52 周 IV 70 分位
  S3: 标的现价 > 接货底线价
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.data import leaps_repository as repo

logger = logging.getLogger(__name__)

# ── 富途接口节流:到期日/期权链/快照类接口限频约 10 次/30 秒 ─────────────────────
_QUOTA_LOCK = threading.Lock()
_LAST_QUOTA_CALL = {"t": 0.0}


def _throttle(min_interval: float = 3.2):
    """保证相邻 quota 类请求间隔 >= min_interval 秒(线程安全)"""
    with _QUOTA_LOCK:
        now = time.monotonic()
        wait = _LAST_QUOTA_CALL["t"] + min_interval - now
        if wait > 0:
            time.sleep(wait)
        _LAST_QUOTA_CALL["t"] = time.monotonic()


@dataclass
class LeapsSuggestion:
    contract_code: str
    strike: float
    expiry: str
    premium: float
    delta: float
    annualized_yield: float
    cost_basis: float
    dte: int


@dataclass
class LeapsSignal:
    symbol: str
    contract_code: str
    expiry: str
    strike: float
    signal_level: str          # 'PRIMARY'(EMA50) | 'SECONDARY'(EMA200)
    trigger_price: float       # 合约触发价格
    ema_type: str              # 'EMA50' | 'EMA200'
    ema_value: float
    iv_rank: float             # 0-100
    underlying_price: float
    floor_price: float
    suggestions: List[LeapsSuggestion] = field(default_factory=list)
    is_intraday: bool = False
    delta: Optional[float] = None        # 合约 delta(绝对值)
    bid: Optional[float] = None          # 买价(卖方可成交价)
    annualized: Optional[float] = None   # 年化收益率%(bid/strike×365/DTE)
    dte: Optional[int] = None
    below_floor: bool = False            # 标的现价低于接货底线(软警告)


def _compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _iv_percentile(iv_history: List[float], current_iv: float) -> float:
    """返回 current_iv 在 iv_history 中的百分位（0-100）"""
    if not iv_history:
        return 0.0
    arr = np.array(iv_history)
    return float(np.sum(arr <= current_iv) / len(arr) * 100)


class WheelTimingMonitor:
    """Wheel 开仓时机扫描 —— 核心原则:期权合约价格受长期均线压制,
    合约价触及自身日K的 EMA50(一级)或 EMA200(强)即为卖出时机。

    卖 Put 时机(标的可开新轮:无活跃轮或有 IDLE 轮):
      - 标的现价 > 接货底线价
      - PUT 合约(DTE/strike 按 wheel_timing 配置)价格触及 EMA50/EMA200
    持股卖 Call 时机(轮子处于 HOLDING):
      - CALL 合约 strike ≥ cost basis
      - 合约价格触及 EMA50/EMA200
    IV Rank 默认仅记录不作硬条件(wheel_timing.iv_percentile_threshold 可改)。
    信号级别 WHEEL_PUT / WHEEL_CALL,ema_type 字段区分触的是哪条线;
    合约冷却复用 LEAPS 的 leaps_cooldowns。
    """

    def __init__(self, config: Dict[str, Any]):
        cfg = config.get("wheel_timing", {}) or {}
        self.monitor = LeapsMonitor(config)
        # 覆盖冷却天数(wheel 单独配置)
        cd = cfg.get("cooldown_trading_days")
        if cd:
            self.monitor.cooldown_days = cd
        self.dte_min = cfg.get("dte_min", 21)
        self.dte_max = cfg.get("dte_max", 60)
        self.iv_threshold = cfg.get("iv_percentile_threshold", 0)
        # strike 扫描区间(相对现价,非对称):[spot×(1−down), spot×(1+up)]
        self.strike_range_down = cfg.get("strike_range_down", 0.20)
        self.strike_range_up = cfg.get("strike_range_up", 0.10)
        self.align_target_dte = bool(cfg.get("align_target_dte", True))
        self.dte_pad_days = int(cfg.get("dte_pad_days", 7) or 0)
        # Wheel 扫描的每标的合约数上限(0 = 不限制;默认 30 控噪音)
        mc = cfg.get("contract_max_per_symbol")
        if mc is not None:
            self.monitor.max_contracts = mc

    def _dte_window(self, target: Dict[str, Any]) -> Tuple[int, int]:
        """标的级 DTE 窗口;align 时用标的设置 ± pad,否则用全局 wheel_timing。"""
        if self.align_target_dte:
            lo = int(target.get("dte_min") or self.dte_min)
            hi = int(target.get("dte_max") or self.dte_max)
            pad = self.dte_pad_days
            return max(1, lo - pad), hi + pad
        return int(self.dte_min), int(self.dte_max)

    def scan_all(self, symbol: Optional[str] = None, is_intraday: bool = True,
                 report: Optional[List[Dict[str, Any]]] = None) -> List[LeapsSignal]:
        """is_intraday=True(默认): 用合约最新价与 EMA 比较(现价 ≥ EMA 触发);
        False 则用当日最高价(盘中摸过均线也算)。"""
        from app.data import wheel_repository as wrepo
        from app.data import leaps_repository as lrepo

        def _prog(**kw):
            # 独立进度模块,禁止静默吞错导致前端一直「启动中」
            from app.core.wheel_timing_progress import update as _upd
            _upd(**kw)

        signals: List[LeapsSignal] = []
        targets = [t for t in wrepo.get_targets() if t.get("enabled")]
        if symbol:
            targets = [t for t in targets if t["symbol"] == symbol.upper()]
        n_targets = len(targets)
        _prog(
            phase="timing", target_n=n_targets, target_i=0,
            symbol=None, side=None, expiry=None, contract_i=0, contract_n=0,
            message=f"触线 · 共 {n_targets} 个标的",
        )
        for ti, t in enumerate(targets, start=1):
            sym = t["symbol"]
            try:
                cycles = wrepo.get_active_cycles(sym)
                holding = [c for c in cycles if c["status"] == "HOLDING"]
                dte_lo, dte_hi = self._dte_window(t)

                # 卖 Put:启用标的一律扫描(状态机支持多轮并行,是否开仓由用户决定);
                # 接货底线降级为软警告(信号带 below_floor 标记,不再硬性跳过)
                _prog(
                    target_i=ti, target_n=n_targets, symbol=sym, side="PUT",
                    expiry=None, contract_i=0, contract_n=0,
                    message=f"触线 · {sym} PUT · 标的 {ti}/{n_targets}",
                )
                rep: Dict[str, Any] = {"symbol": sym, "side": "PUT", "dte": f"{dte_lo}-{dte_hi}"}
                signals.extend(self.monitor.scan_symbol(
                    sym, t["floor_price"], is_intraday=is_intraday,
                    option_type="PUT",
                    dte_min=dte_lo, dte_max=dte_hi,
                    level_map={"EMA50": "WHEEL_PUT", "EMA200": "WHEEL_PUT"},
                    iv_threshold=self.iv_threshold,
                    respect_30d_cap=False, with_suggestions=False,
                    report=rep,
                    strike_range_down=self.strike_range_down,
                    strike_range_up=self.strike_range_up,
                    floor_hard=False,
                    progress_cb=_prog,
                ))
                if report is not None:
                    report.append(rep)

                for cyc in holding:
                    cost_basis = cyc.get("cost_basis") or 0
                    _prog(
                        target_i=ti, target_n=n_targets, symbol=sym, side="CALL",
                        expiry=None, contract_i=0, contract_n=0,
                        message=f"触线 · {sym} CALL · 标的 {ti}/{n_targets}",
                    )
                    rep = {"symbol": sym, "side": "CALL", "dte": f"{dte_lo}-{dte_hi}"}
                    signals.extend(self.monitor.scan_symbol(
                        sym, 0, is_intraday=is_intraday,
                        option_type="CALL",
                        dte_min=dte_lo, dte_max=dte_hi,
                        strike_min=cost_basis if cost_basis > 0 else None,
                        level_map={"EMA50": "WHEEL_CALL", "EMA200": "WHEEL_CALL"},
                        iv_threshold=self.iv_threshold,
                        respect_30d_cap=False, with_suggestions=False,
                        report=rep,
                        strike_range_down=self.strike_range_down,
                        strike_range_up=self.strike_range_up,
                        progress_cb=_prog,
                    ))
                    if report is not None:
                        report.append(rep)
            except Exception as e:
                logger.error("wheel timing scan(%s) failed: %s", sym, e)
                if report is not None:
                    report.append({"symbol": sym, "side": "-", "note": f"扫描异常: {e}"})

        # 写入时机历史(按合约代码合并去重)
        for sig in signals:
            try:
                lrepo.upsert_timing_history(sig)
            except Exception as e:
                logger.warning("时机历史写入失败(%s): %s", sig.contract_code, e)
        return signals


def signal_strength(sig: "LeapsSignal", min_iv_rank: float = 50) -> str:
    """观察 / 可做 / 强 — 与前端确认层对齐"""
    if sig.ema_type == "EMA200" and (sig.iv_rank or 0) >= min_iv_rank:
        return "STRONG"
    if sig.ema_type == "EMA200" or (sig.iv_rank or 0) >= min_iv_rank:
        return "READY"
    return "WATCH"


def format_wheel_signal(sig: "LeapsSignal", min_iv_rank: float = 50) -> str:
    """Telegram 推送文案(合约触线)"""
    kind = "卖Put时机" if sig.signal_level == "WHEEL_PUT" else "卖Call时机(持股)"
    level = signal_strength(sig, min_iv_rank)
    badge = {"STRONG": "🔥强信号", "READY": "✅可做", "WATCH": "👀观察"}.get(level, "")
    lines = [
        f"🛞 [Wheel {kind}] {badge} {sig.symbol}",
        f"合约 {sig.contract_code}  strike {sig.strike}  到期 {sig.expiry}"
        + (f"({sig.dte}天)" if sig.dte else ""),
        f"合约价 {round(sig.trigger_price, 2)} 触及 {sig.ema_type}({round(sig.ema_value, 2)})",
    ]
    detail = []
    if sig.bid:
        detail.append(f"bid {sig.bid:g}")
    if sig.delta is not None:
        detail.append(f"Δ {sig.delta:.2f}")
    if sig.annualized is not None:
        detail.append(f"年化 {sig.annualized:.1f}%")
    if detail:
        lines.append("  ".join(detail))
    lines.append(f"IV分位 {sig.iv_rank}  标的现价 {sig.underlying_price}")
    if getattr(sig, "below_floor", False):
        lines.append(f"⚠ 现价低于接货底线 {sig.floor_price},接货风险自行评估")
    return "\n".join(lines)



def _to_futu_symbol(symbol: str) -> str:
    """股票池符号 → Futu 符号。US: AAPL → US.AAPL;HK: 00700.HK → HK.00700"""
    s = symbol.strip().upper()
    if s.endswith(".HK"):
        return f"HK.{s[:-3]}"
    if s.startswith(("US.", "HK.")):
        return s
    return f"US.{s}"


def _parse_futu_contract(code: str) -> Tuple[str, str, float, str]:
    """从 Futu 合约代码解析 (underlying, expiry, strike, option_type)
    示例: US.AAPL260717C00300000 → ('AAPL', '260717', 300.0, 'C')
    """
    try:
        import re
        parts = code.split(".")
        raw = parts[-1]          # AAPL260717C00300000
        # 从结构上解析:标的(可含C/P字母,如 AAPL) + 6位日期 + C/P + 行权价数字
        m = re.match(r"^(.+?)(\d{6})([CP])(\d+)$", raw)
        if m:
            underlying, expiry, opt_type, strike_raw = m.groups()
            return underlying, expiry, int(strike_raw) / 1000.0, opt_type
    except Exception:
        pass
    return ("", "", 0.0, "")


def _dte(expiry_yymmdd: str) -> int:
    """从 YYMMDD 格式计算剩余天数"""
    try:
        exp = datetime.strptime("20" + expiry_yymmdd, "%Y%m%d").date()
        return max(0, (exp - date.today()).days)
    except Exception:
        return 0


def _annualized_yield(premium: float, strike: float, dte: int) -> float:
    if strike <= 0 or dte <= 0:
        return 0.0
    return round(premium / strike * (365 / dte) * 100, 2)


class LeapsMonitor:
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        self.sig_cfg = config.get("signal", {})
        self.futu_host = config.get("futu", {}).get("host", "127.0.0.1")
        self.futu_port = config.get("futu", {}).get("port", 11111)
        self.iv_threshold = self.sig_cfg.get("iv_percentile_threshold", 70)
        self.ema50_min = self.sig_cfg.get("ema50_min_bars", 60)
        self.ema200_min = self.sig_cfg.get("ema200_min_bars", 210)
        self.cooldown_days = self.sig_cfg.get("contract_cooldown_trading_days", 5)
        self.max_30d = self.sig_cfg.get("per_symbol_max_30d", 3)
        self.dte_min = self.sig_cfg.get("contract_dte_min", 180)
        self.max_contracts = self.sig_cfg.get("contract_max_per_symbol", 5)
        self.strike_range = self.sig_cfg.get("strike_range_pct", 0.20)
        self.intraday_use_last = self.sig_cfg.get("intraday_use_last_price", True)
        self.delta_range = config.get("suggestions", {}).get("delta_range", [0.20, 0.30])

    def scan_all(self, is_intraday: bool = False) -> List[LeapsSignal]:
        watchlist = repo.get_watchlist()
        signals: List[LeapsSignal] = []
        for item in watchlist:
            if not item.get("enabled"):
                continue
            try:
                found = self.scan_symbol(
                    item["symbol"], item["floor_price"], is_intraday=is_intraday
                )
                signals.extend(found)
            except Exception as e:
                logger.error("scan_symbol(%s) failed: %s", item["symbol"], e)
        return signals

    def scan_symbol(
        self, symbol: str, floor_price: float, is_intraday: bool = False,
        option_type: str = "PUT",
        dte_min: Optional[int] = None, dte_max: Optional[int] = None,
        strike_min: Optional[float] = None, strike_max: Optional[float] = None,
        level_map: Optional[Dict[str, str]] = None,
        iv_threshold: Optional[float] = None,
        respect_30d_cap: bool = True,
        with_suggestions: bool = True,
        report: Optional[Dict[str, Any]] = None,
        strike_range_down: Optional[float] = None,
        strike_range_up: Optional[float] = None,
        floor_hard: bool = True,
        progress_cb: Optional[Any] = None,
    ) -> List[LeapsSignal]:
        """通用合约 EMA 触线扫描。默认参数 = 原 LEAPS Put 行为;
        Wheel 时机复用:option_type/dte/strike 可定制,level_map 定制信号级别名,
        iv_threshold=0 表示 IV 仅记录不作硬条件。
        report(可选 dict)会被填入扫描明细,便于前端展示诊断。
        progress_cb: 可选进度回调(symbol/side/expiry/contract_i/n/message)。"""
        import futu

        def _p(**kw):
            if progress_cb:
                try:
                    progress_cb(**kw)
                except Exception:
                    pass

        signals: List[LeapsSignal] = []
        level_map = level_map or {"EMA50": "PRIMARY", "EMA200": "SECONDARY"}
        iv_thr = self.iv_threshold if iv_threshold is None else iv_threshold
        rep = report if report is not None else {}
        rep.update(spot=None, contracts=0, in_cooldown=0, no_history=0,
                   bars_insufficient=0, iv_filtered=0, not_touching=0, signals=0, note=None)
        _p(
            symbol=symbol, side=option_type, expiry=None,
            contract_i=0, contract_n=0,
            message=f"正在扫描：{symbol} {option_type} · 到期日 … · 合约 … · 拉取期权链",
        )

        # S3: 标的现价 > 接货底线(floor_price <= 0 表示跳过该条件,如卖 Call)
        underlying_price = self._fetch_underlying_price(symbol)
        if underlying_price is None:
            logger.warning("%s: 无法获取标的价格，跳过", symbol)
            rep["note"] = "无法获取标的价格"
            return signals
        rep["spot"] = round(float(underlying_price), 2)
        below_floor = bool(floor_price > 0 and underlying_price <= floor_price)
        if below_floor and floor_hard:
            logger.info("%s: 现价 %.2f ≤ 底线 %.2f，S3 不满足，跳过", symbol, underlying_price, floor_price)
            rep["note"] = f"现价 {underlying_price:.2f} ≤ 底线 {floor_price}"
            return signals
        if below_floor:
            rep["note"] = f"⚠ 现价 {underlying_price:.2f} ≤ 底线 {floor_price}(软警告,继续扫描)"

        # 30 天推送上限
        if respect_30d_cap and repo.count_symbol_signals_30d(symbol) >= self.max_30d:
            logger.info("%s: 30 天推送已达上限 %d，跳过", symbol, self.max_30d)
            rep["note"] = "30天推送已达上限"
            return signals

        # 获取符合条件的合约列表(此步含 OpenD 限频,最慢;进度在内部按到期日回传)
        fetch_errors: List[str] = []
        contracts = self._fetch_eligible_contracts(
            symbol, underlying_price, option_type=option_type,
            dte_min=dte_min, dte_max=dte_max,
            strike_min=strike_min, strike_max=strike_max,
            range_down=strike_range_down, range_up=strike_range_up,
            errors=fetch_errors,
            progress_cb=progress_cb,
        )
        rep["contracts"] = len(contracts)
        total_all = len(contracts)
        if not contracts:
            reason = ";".join(fetch_errors) if fetch_errors else "无符合条件的合约(DTE/strike 范围内)"
            logger.info("%s: %s", symbol, reason)
            rep["note"] = reason
            _p(symbol=symbol, side=option_type, expiry=None, contract_i=0, contract_n=0,
               message=f"触线 · {symbol} {option_type} · {reason}")
            return signals

        def _norm_exp(raw: Any) -> str:
            exp_label = str(raw or "")[:10]
            if len(exp_label) == 6 and exp_label.isdigit():
                try:
                    return datetime.strptime("20" + exp_label, "%Y%m%d").date().isoformat()
                except Exception:
                    pass
            return exp_label

        # 按到期日分组,进度与高分扫描一致:该标的该到期日 n/m
        by_exp: Dict[str, List[Dict[str, Any]]] = {}
        for c in contracts:
            by_exp.setdefault(_norm_exp(c.get("expiry")), []).append(c)
        exp_order = sorted(by_exp.keys())

        today = date.today().isoformat()
        first_exp = exp_order[0] if exp_order else None
        _p(
            symbol=symbol, side=option_type, expiry=first_exp,
            contract_i=0, contract_n=len(by_exp.get(first_exp or "", [])),
            message=(
                f"触线 · {symbol} {option_type} · 到期日 {first_exp or '—'} · "
                f"0/{len(by_exp.get(first_exp or '', []))} · 共 {total_all} 张"
            ),
        )

        for exp_label in exp_order:
            exp_contracts = by_exp[exp_label]
            exp_n = len(exp_contracts)
            for ci, contract in enumerate(exp_contracts, start=1):
                code = contract["code"]
                strike = contract["strike"]
                current_iv = contract.get("iv")
                # 每张都更新:触线单张耗时长,便于前端实时看到 n/m 与到期日
                _p(
                    symbol=symbol, side=option_type, expiry=exp_label,
                    contract_i=ci, contract_n=exp_n,
                    message=(
                        f"触线 · {symbol} {option_type} · 到期日 {exp_label} · "
                        f"{ci}/{exp_n}"
                    ),
                )

                # 合约级冷却
                if repo.is_contract_in_cooldown(code):
                    logger.debug("%s: 冷却中，跳过", code)
                    rep["in_cooldown"] += 1
                    continue

                # 更新价格缓存 & IV 历史
                self._update_price_cache(code, contract, today)

                # 读取历史价格序列
                price_history = repo.get_option_price_history(code, limit=250)
                if not price_history:
                    rep["no_history"] += 1
                    continue

                df = pd.DataFrame(price_history)
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df.dropna(subset=["close"])
                closes = df["close"]

                # 确定触发价（EOD 用最高价，盘中用最新价）
                if is_intraday and self.intraday_use_last:
                    trigger_price = contract.get("last_price") or contract.get("close")
                else:
                    today_bar = [b for b in price_history if b["date"] == today]
                    trigger_price = (
                        today_bar[-1].get("high") or today_bar[-1].get("close")
                        if today_bar else closes.iloc[-1] if len(closes) else None
                    )
                if trigger_price is None:
                    continue

                # S2: IV 百分位
                iv_history = repo.get_iv_history_52w(code)
                if current_iv is not None:
                    iv_rank = _iv_percentile(iv_history, current_iv) if iv_history else 0.0
                else:
                    iv_rank = 0.0

                if iv_rank < iv_thr:
                    logger.debug("%s: IV rank %.1f < 阈值 %s，跳过", code, iv_rank, iv_thr)
                    rep["iv_filtered"] += 1
                    continue

                # S1: 价格触及 EMA200（二级强信号）
                signal_level = None
                ema_type = None
                ema_value = None

                if len(closes) >= self.ema200_min:
                    ema200 = _compute_ema(closes, 200).iloc[-1]
                    if trigger_price >= ema200:
                        signal_level = level_map["EMA200"]
                        ema_type = "EMA200"
                        ema_value = float(ema200)

                # S1: 价格触及 EMA50（一级信号，仅在未触及 EMA200 时检查）
                if signal_level is None and len(closes) >= self.ema50_min:
                    ema50 = _compute_ema(closes, 50).iloc[-1]
                    if trigger_price >= ema50:
                        signal_level = level_map["EMA50"]
                        ema_type = "EMA50"
                        ema_value = float(ema50)

                if signal_level is None:
                    if len(closes) < self.ema50_min:
                        rep["bars_insufficient"] += 1
                    else:
                        rep["not_touching"] += 1
                    continue

                # 获取 OTM put 建议
                expiry_raw = contract.get("expiry") or exp_label
                suggestions = (
                    self._fetch_suggestions(symbol, underlying_price, expiry_raw)
                    if with_suggestions else []
                )

                sell_price = contract.get("bid") or contract.get("last_price") or 0
                # _dte 吃 YYMMDD；YYYY-MM-DD 则直接算
                try:
                    if len(str(expiry_raw).replace("-", "")) >= 8 and "-" in str(expiry_raw):
                        dte_val = (date.fromisoformat(str(expiry_raw)[:10]) - date.today()).days
                    else:
                        dte_val = contract.get("dte") or _dte(str(expiry_raw).replace("-", "")[-6:])
                except Exception:
                    dte_val = contract.get("dte") or 0
                sig = LeapsSignal(
                    symbol=symbol,
                    contract_code=code,
                    expiry=exp_label or str(expiry_raw),
                    strike=strike,
                    signal_level=signal_level,
                    trigger_price=float(trigger_price),
                    ema_type=ema_type,
                    ema_value=ema_value,
                    iv_rank=round(iv_rank, 1),
                    underlying_price=round(float(underlying_price), 2),
                    floor_price=floor_price,
                    suggestions=suggestions,
                    is_intraday=is_intraday,
                    delta=contract.get("delta"),
                    bid=contract.get("bid") or None,
                    annualized=_annualized_yield(sell_price, strike, dte_val) if sell_price else None,
                    dte=dte_val,
                    below_floor=below_floor,
                )
                signals.append(sig)

                # 设置冷却
                repo.set_contract_cooldown(code, symbol, self.cooldown_days)

                # 入库
                repo.log_signal(
                    symbol=symbol,
                    contract_code=code,
                    signal_level=signal_level,
                    trigger_price=sig.trigger_price,
                    ema_value=ema_value,
                    ema_type=ema_type,
                    iv_rank=iv_rank,
                    underlying_price=sig.underlying_price,
                    floor_price=floor_price,
                    suggestions=[
                        {
                            "contract_code": s.contract_code,
                            "strike": s.strike,
                            "expiry": s.expiry,
                            "premium": s.premium,
                            "delta": s.delta,
                            "annualized_yield": s.annualized_yield,
                            "cost_basis": s.cost_basis,
                            "dte": s.dte,
                        }
                        for s in suggestions
                    ],
                    is_intraday=is_intraday,
                )

        rep["signals"] = len(signals)
        return signals

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _fetch_underlying_price(self, symbol: str) -> Optional[float]:
        """用市场快照取现价(get_stock_quote 需要先订阅,快照不需要)"""
        import futu
        futu_symbol = _to_futu_symbol(symbol)
        try:
            _throttle()
            from app.core.opend import open_quote_context
            ctx = open_quote_context(host=self.futu_host, port=self.futu_port)
            ret, data = ctx.get_market_snapshot([futu_symbol])
            ctx.close()
            if ret == futu.RET_OK and data is not None and not data.empty:
                price = float(data["last_price"].iloc[0] or 0)
                if price > 0:
                    return price
                # 休市等情况 last_price 可能为 0,退回昨收
                prev = float(data["prev_close_price"].iloc[0] or 0) if "prev_close_price" in data.columns else 0
                return prev if prev > 0 else None
            logger.warning("fetch_underlying_price(%s): snapshot ret=%s %s", symbol, ret, data)
        except Exception as e:
            logger.error("fetch_underlying_price(%s): %s", symbol, e)
        return None

    def _fetch_eligible_contracts(
        self, symbol: str, underlying_price: float,
        option_type: str = "PUT",
        dte_min: Optional[int] = None, dte_max: Optional[int] = None,
        strike_min: Optional[float] = None, strike_max: Optional[float] = None,
        range_down: Optional[float] = None, range_up: Optional[float] = None,
        errors: Optional[List[str]] = None,
        progress_cb: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        import futu
        futu_symbol = _to_futu_symbol(symbol)
        contracts: List[Dict[str, Any]] = []
        errs = errors if errors is not None else []
        eff_dte_min = self.dte_min if dte_min is None else dte_min

        def _p(**kw):
            if progress_cb:
                try:
                    progress_cb(**kw)
                except Exception:
                    pass

        try:
            from app.core.opend import open_quote_context
            ctx = open_quote_context(host=self.futu_host, port=self.futu_port)
            # 获取所有到期日
            _p(
                symbol=symbol, side=option_type, expiry=None,
                contract_i=0, contract_n=0,
                message=f"正在扫描：{symbol} {option_type} · 到期日 … · 合约 … · 拉取到期日列表",
            )
            _throttle()
            ret, dates = ctx.get_option_expiration_date(futu_symbol)
            if ret != futu.RET_OK:
                errs.append(f"获取到期日失败(可能限频): {dates}")
                ctx.close()
                return contracts

            eligible_expiries = []
            for _, row in dates.iterrows():
                exp_str = str(row.get("strike_time") or row.get("option_expiry_date_closes") or "")
                exp_str = exp_str[:10]  # YYYY-MM-DD
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days
                    if dte >= eff_dte_min and (dte_max is None or dte <= dte_max):
                        eligible_expiries.append((exp_str, dte))
                except Exception:
                    continue

            if not eligible_expiries:
                errs.append("无 DTE 范围内的到期日")
                ctx.close()
                return contracts

            strike_lo = underlying_price * (1 - (self.strike_range if range_down is None else range_down))
            strike_hi = underlying_price * (1 + (self.strike_range if range_up is None else range_up))
            if strike_min is not None:
                strike_lo = max(strike_lo, strike_min)
            if strike_max is not None:
                strike_hi = min(strike_hi, strike_max)

            futu_opt_type = futu.OptionType.CALL if option_type == "CALL" else futu.OptionType.PUT
            # (code, expiry) 以便组装时带回到期日
            all_codes: List[str] = []
            code_expiry: Dict[str, str] = {}
            chain_fail = 0
            exp_slice = eligible_expiries[:3]  # 取最近 3 个符合条件的到期日
            n_exp = len(exp_slice)
            for ei, (exp_str, _) in enumerate(exp_slice, start=1):
                _p(
                    symbol=symbol, side=option_type, expiry=exp_str,
                    contract_i=0, contract_n=0,
                    message=(
                        f"正在扫描：{symbol} {option_type} · 到期日 {exp_str} · 合约 … · "
                        f"拉期权链 {ei}/{n_exp}"
                    ),
                )
                _throttle()
                ret2, chain = ctx.get_option_chain(
                    futu_symbol, start=exp_str, end=exp_str,
                    option_type=futu_opt_type
                )
                if ret2 != futu.RET_OK or chain is None or chain.empty:
                    if ret2 != futu.RET_OK:
                        chain_fail += 1
                        logger.warning("get_option_chain(%s %s) 失败: %s", futu_symbol, exp_str, chain)
                    continue
                n_in_exp = 0
                for _, row in chain.iterrows():
                    code = str(row.get("code", ""))
                    strike = float(row.get("strike_price", 0))
                    if not code or strike < strike_lo or strike > strike_hi:
                        continue
                    all_codes.append(code)
                    code_expiry[code] = exp_str
                    n_in_exp += 1
                _p(
                    symbol=symbol, side=option_type, expiry=exp_str,
                    contract_i=0, contract_n=n_in_exp,
                    message=(
                        f"正在扫描：{symbol} {option_type} · 到期日 {exp_str} · "
                        f"合约 0/{n_in_exp} · 链已取,待快照 {ei}/{n_exp}"
                    ),
                )

            if not all_codes:
                if chain_fail:
                    errs.append(f"期权链获取失败 {chain_fail} 次(可能限频)")
                else:
                    errs.append("strike 范围内无合约")
                ctx.close()
                return contracts

            # 快照获取 OI、IV、delta 排序取前 N
            chunk_size = 80
            snapshots: Dict[str, Any] = {}
            snap_fail = 0
            n_chunks = (len(all_codes) + chunk_size - 1) // chunk_size
            for bi, i in enumerate(range(0, len(all_codes), chunk_size), start=1):
                chunk = all_codes[i: i + chunk_size]
                # 用本批第一个合约的到期日做展示
                exp_hint = code_expiry.get(chunk[0]) if chunk else None
                _p(
                    symbol=symbol, side=option_type, expiry=exp_hint,
                    contract_i=min(i + len(chunk), len(all_codes)),
                    contract_n=len(all_codes),
                    message=(
                        f"正在扫描：{symbol} {option_type} · 到期日 {exp_hint or '…'} · "
                        f"合约 {min(i + len(chunk), len(all_codes))}/{len(all_codes)} · "
                        f"快照 {bi}/{n_chunks}"
                    ),
                )
                _throttle()
                ret3, snap = ctx.get_market_snapshot(chunk)
                if ret3 == futu.RET_OK and snap is not None and not snap.empty:
                    for _, srow in snap.iterrows():
                        code = str(srow.get("code", ""))
                        snapshots[code] = srow
                else:
                    snap_fail += 1
                    logger.warning("get_market_snapshot 批次失败: %s", snap)

            ctx.close()
            if not snapshots and snap_fail:
                errs.append(f"合约快照获取失败 {snap_fail} 批(可能限频)")

            # 组装合约信息
            raw: List[Dict] = []
            for code in all_codes:
                if code not in snapshots:
                    continue
                srow = snapshots[code]
                _, expiry, strike, _ = _parse_futu_contract(code)
                dte_val = _dte(expiry)
                oi = float(srow.get("option_open_interest", 0) or 0)
                iv_raw = float(srow.get("option_implied_volatility", 0) or 0)
                iv = iv_raw / 100 if iv_raw > 5 else iv_raw  # 归一化
                last_price = float(srow.get("last_price", 0) or 0)
                high_price = float(srow.get("high_price", 0) or 0)
                bid_price = float(srow.get("bid_price", 0) or 0)
                delta_val = abs(float(srow.get("option_delta", 0) or 0))
                raw.append({
                    "code": code,
                    "expiry": expiry,
                    "strike": strike,
                    "dte": dte_val,
                    "oi": oi,
                    "iv": iv,
                    "last_price": last_price,
                    "close": last_price,
                    "high": high_price,
                    "bid": bid_price,
                    "delta": delta_val or None,
                })

            # 按 OI 降序;max_contracts <= 0 表示不限制
            raw.sort(key=lambda x: x["oi"], reverse=True)
            if self.max_contracts and self.max_contracts > 0:
                contracts = raw[: self.max_contracts]
            else:
                contracts = raw

        except Exception as e:
            logger.error("fetch_eligible_contracts(%s): %s", symbol, e)

        return contracts

    def _update_price_cache(self, code: str, contract: Dict, today: str):
        """增量更新价格缓存；冷启动时拉取历史，后续只追加当日"""
        latest = repo.get_latest_cached_date(code)
        bars_to_save: List[Dict] = []

        if latest is None:
            # 冷启动：从 Futu 拉取历史（最多 250 根）
            bars_to_save = self._fetch_kline_history(code, num=250)
        elif latest < today:
            # 增量：仅追加今日 bar
            bar = {
                "date": today,
                "open": contract.get("open"),
                "high": contract.get("high"),
                "low": contract.get("low"),
                "close": contract.get("last_price") or contract.get("close"),
                "volume": contract.get("volume"),
                "iv": contract.get("iv"),
            }
            bars_to_save = [bar]

        if bars_to_save:
            repo.save_option_prices(code, bars_to_save)

        # 同步 IV 历史
        iv = contract.get("iv")
        if iv and iv > 0:
            repo.save_iv_snapshot(code, today, iv)

    def _fetch_kline_history(self, code: str, num: int = 250) -> List[Dict]:
        """拉合约日K历史。注意:get_cur_kline 需要先订阅 K_DAY;
        订阅随连接关闭自动释放,不占长期配额。"""
        import futu
        bars: List[Dict] = []
        try:
            _throttle(1.0)  # 订阅接口限频较松,1 秒间隔即可
            from app.core.opend import open_quote_context
            ctx = open_quote_context(host=self.futu_host, port=self.futu_port)
            try:
                ret_sub, sub_err = ctx.subscribe([code], [futu.SubType.K_DAY], subscribe_push=False)
                if ret_sub != futu.RET_OK:
                    logger.warning("subscribe K_DAY(%s) 失败: %s", code, sub_err)
                    return bars
                ret, data = ctx.get_cur_kline(code, num, futu.KLType.K_DAY)
            finally:
                ctx.close()
            if ret == futu.RET_OK and data is not None and not data.empty:
                for _, row in data.iterrows():
                    ts = str(row.get("time_key", ""))[:10]
                    bars.append({
                        "date": ts,
                        "open": float(row.get("open", 0) or 0),
                        "high": float(row.get("high", 0) or 0),
                        "low": float(row.get("low", 0) or 0),
                        "close": float(row.get("close", 0) or 0),
                        "volume": float(row.get("volume", 0) or 0),
                        "iv": None,
                    })
            elif ret != futu.RET_OK:
                logger.warning("get_cur_kline(%s) 失败: %s", code, data)
        except Exception as e:
            logger.error("fetch_kline_history(%s): %s", code, e)
        return bars

    def _fetch_suggestions(
        self, symbol: str, underlying_price: float, trigger_expiry: str
    ) -> List[LeapsSuggestion]:
        """获取 delta 在目标区间的虚值 put 建议档位"""
        import futu
        suggestions: List[LeapsSuggestion] = []
        futu_symbol = _to_futu_symbol(symbol)
        delta_lo, delta_hi = self.delta_range[0], self.delta_range[1]

        try:
            # 将 YYMMDD → YYYY-MM-DD
            exp_full = "20" + trigger_expiry
            exp_date = f"{exp_full[:4]}-{exp_full[4:6]}-{exp_full[6:8]}"
            dte_val = _dte(trigger_expiry)

            from app.core.opend import open_quote_context
            ctx = open_quote_context(host=self.futu_host, port=self.futu_port)
            ret, chain = ctx.get_option_chain(
                futu_symbol, start=exp_date, end=exp_date,
                option_type=futu.OptionType.PUT
            )
            if ret != futu.RET_OK or chain is None or chain.empty:
                ctx.close()
                return suggestions

            codes = [str(r["code"]) for _, r in chain.iterrows()]
            ret2, snap = ctx.get_market_snapshot(codes[:80])
            ctx.close()

            if ret2 != futu.RET_OK or snap is None or snap.empty:
                return suggestions

            for _, srow in snap.iterrows():
                delta_raw = float(srow.get("option_delta", 0) or 0)
                delta_abs = abs(delta_raw)
                if not (delta_lo <= delta_abs <= delta_hi):
                    continue
                code = str(srow.get("code", ""))
                _, expiry, strike, _ = _parse_futu_contract(code)
                premium = float(srow.get("last_price", 0) or 0)
                if premium <= 0:
                    continue
                ann_yield = _annualized_yield(premium, strike, dte_val)
                cost_basis = round(strike - premium, 2)
                suggestions.append(
                    LeapsSuggestion(
                        contract_code=code,
                        strike=strike,
                        expiry=expiry,
                        premium=round(premium, 2),
                        delta=round(delta_abs, 3),
                        annualized_yield=ann_yield,
                        cost_basis=cost_basis,
                        dte=dte_val,
                    )
                )

            suggestions.sort(key=lambda s: s.strike)

        except Exception as e:
            logger.error("fetch_suggestions(%s): %s", symbol, e)

        return suggestions[:4]  # 最多返回 4 个档位
