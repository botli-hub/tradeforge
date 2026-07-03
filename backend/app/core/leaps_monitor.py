"""LEAPS Put 权利金卖出信号核心引擎

信号三条件（S1 & S2 & S3 同时满足）：
  S1: 合约当日最高价（盘中用最新价）≥ EMA50（一级）或 ≥ EMA200（二级强信号）
  S2: 合约当前 IV ≥ 自身 52 周 IV 70 分位
  S3: 标的现价 > 接货底线价
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.data import leaps_repository as repo

logger = logging.getLogger(__name__)


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


def _compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _iv_percentile(iv_history: List[float], current_iv: float) -> float:
    """返回 current_iv 在 iv_history 中的百分位（0-100）"""
    if not iv_history:
        return 0.0
    arr = np.array(iv_history)
    return float(np.sum(arr <= current_iv) / len(arr) * 100)


def _parse_futu_contract(code: str) -> Tuple[str, str, float, str]:
    """从 Futu 合约代码解析 (underlying, expiry, strike, option_type)
    示例: US.AAPL260717C00300000 → ('AAPL', '260717', 300.0, 'C')
    """
    try:
        parts = code.split(".")
        raw = parts[-1]          # AAPL260717C00300000
        # 找到 C/P 分隔
        for i, ch in enumerate(raw):
            if ch in ("C", "P"):
                underlying = raw[:i - 6]
                expiry = raw[i - 6: i]
                opt_type = ch
                strike = int(raw[i + 1:]) / 1000.0
                return underlying, expiry, strike, opt_type
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
        self, symbol: str, floor_price: float, is_intraday: bool = False
    ) -> List[LeapsSignal]:
        import futu

        signals: List[LeapsSignal] = []

        # S3: 标的现价 > 接货底线
        underlying_price = self._fetch_underlying_price(symbol)
        if underlying_price is None:
            logger.warning("%s: 无法获取标的价格，跳过", symbol)
            return signals
        if underlying_price <= floor_price:
            logger.info("%s: 现价 %.2f ≤ 底线 %.2f，S3 不满足，跳过", symbol, underlying_price, floor_price)
            return signals

        # 30 天推送上限
        if repo.count_symbol_signals_30d(symbol) >= self.max_30d:
            logger.info("%s: 30 天推送已达上限 %d，跳过", symbol, self.max_30d)
            return signals

        # 获取符合条件的合约列表
        contracts = self._fetch_eligible_contracts(symbol, underlying_price)
        if not contracts:
            logger.info("%s: 无符合条件的合约", symbol)
            return signals

        today = date.today().isoformat()

        for contract in contracts:
            code = contract["code"]
            expiry = contract["expiry"]
            strike = contract["strike"]
            current_iv = contract.get("iv")

            # 合约级冷却
            if repo.is_contract_in_cooldown(code):
                logger.debug("%s: 冷却中，跳过", code)
                continue

            # 更新价格缓存 & IV 历史
            self._update_price_cache(code, contract, today)

            # 读取历史价格序列
            price_history = repo.get_option_price_history(code, limit=250)
            if not price_history:
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

            if iv_rank < self.iv_threshold:
                logger.debug("%s: IV rank %.1f < 阈值 %d，跳过", code, iv_rank, self.iv_threshold)
                continue

            # S1: 价格触及 EMA200（二级强信号）
            signal_level = None
            ema_type = None
            ema_value = None

            if len(closes) >= self.ema200_min:
                ema200 = _compute_ema(closes, 200).iloc[-1]
                if trigger_price >= ema200:
                    signal_level = "SECONDARY"
                    ema_type = "EMA200"
                    ema_value = float(ema200)

            # S1: 价格触及 EMA50（一级信号，仅在未触及 EMA200 时检查）
            if signal_level is None and len(closes) >= self.ema50_min:
                ema50 = _compute_ema(closes, 50).iloc[-1]
                if trigger_price >= ema50:
                    signal_level = "PRIMARY"
                    ema_type = "EMA50"
                    ema_value = float(ema50)

            if signal_level is None:
                continue

            # 获取 OTM put 建议
            suggestions = self._fetch_suggestions(symbol, underlying_price, expiry)

            sig = LeapsSignal(
                symbol=symbol,
                contract_code=code,
                expiry=expiry,
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

        return signals

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _fetch_underlying_price(self, symbol: str) -> Optional[float]:
        import futu
        futu_symbol = f"US.{symbol}"
        try:
            ctx = futu.OpenQuoteContext(host=self.futu_host, port=self.futu_port)
            ret, data = ctx.get_stock_quote([futu_symbol])
            ctx.close()
            if ret == futu.RET_OK and not data.empty:
                return float(data["last_price"].iloc[0])
        except Exception as e:
            logger.error("fetch_underlying_price(%s): %s", symbol, e)
        return None

    def _fetch_eligible_contracts(
        self, symbol: str, underlying_price: float
    ) -> List[Dict[str, Any]]:
        import futu
        futu_symbol = f"US.{symbol}"
        contracts: List[Dict[str, Any]] = []
        try:
            ctx = futu.OpenQuoteContext(host=self.futu_host, port=self.futu_port)
            # 获取所有到期日
            ret, dates = ctx.get_option_expiration_date(futu_symbol)
            if ret != futu.RET_OK:
                ctx.close()
                return contracts

            eligible_expiries = []
            for _, row in dates.iterrows():
                exp_str = str(row.get("strike_time") or row.get("option_expiry_date_closes") or "")
                exp_str = exp_str[:10]  # YYYY-MM-DD
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days
                    if dte >= self.dte_min:
                        eligible_expiries.append((exp_str, dte))
                except Exception:
                    continue

            if not eligible_expiries:
                ctx.close()
                return contracts

            strike_lo = underlying_price * (1 - self.strike_range)
            strike_hi = underlying_price * (1 + self.strike_range)

            all_codes: List[str] = []
            for exp_str, _ in eligible_expiries[:3]:  # 取最近 3 个符合条件的到期日
                ret2, chain = ctx.get_option_chain(
                    futu_symbol, start=exp_str, end=exp_str,
                    option_type=futu.OptionType.PUT
                )
                if ret2 != futu.RET_OK or chain is None or chain.empty:
                    continue
                for _, row in chain.iterrows():
                    code = str(row.get("code", ""))
                    strike = float(row.get("strike_price", 0))
                    if not code or strike < strike_lo or strike > strike_hi:
                        continue
                    all_codes.append(code)

            if not all_codes:
                ctx.close()
                return contracts

            # 快照获取 OI、IV、delta 排序取前 N
            chunk_size = 80
            snapshots: Dict[str, Any] = {}
            for i in range(0, len(all_codes), chunk_size):
                chunk = all_codes[i: i + chunk_size]
                ret3, snap = ctx.get_market_snapshot(chunk)
                if ret3 == futu.RET_OK and snap is not None and not snap.empty:
                    for _, srow in snap.iterrows():
                        code = str(srow.get("code", ""))
                        snapshots[code] = srow

            ctx.close()

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
                })

            # 按 OI 降序，取前 max_contracts
            raw.sort(key=lambda x: x["oi"], reverse=True)
            contracts = raw[: self.max_contracts]

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
        import futu
        bars: List[Dict] = []
        try:
            ctx = futu.OpenQuoteContext(host=self.futu_host, port=self.futu_port)
            ret, data = ctx.get_cur_kline(code, futu.KLType.K_DAY, num=num)
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
        except Exception as e:
            logger.error("fetch_kline_history(%s): %s", code, e)
        return bars

    def _fetch_suggestions(
        self, symbol: str, underlying_price: float, trigger_expiry: str
    ) -> List[LeapsSuggestion]:
        """获取 delta 在目标区间的虚值 put 建议档位"""
        import futu
        suggestions: List[LeapsSuggestion] = []
        futu_symbol = f"US.{symbol}"
        delta_lo, delta_hi = self.delta_range[0], self.delta_range[1]

        try:
            # 将 YYMMDD → YYYY-MM-DD
            exp_full = "20" + trigger_expiry
            exp_date = f"{exp_full[:4]}-{exp_full[4:6]}-{exp_full[6:8]}"
            dte_val = _dte(trigger_expiry)

            ctx = futu.OpenQuoteContext(host=self.futu_host, port=self.futu_port)
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
