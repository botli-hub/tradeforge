"""CBOE delayed US option quotes — 不依赖 OpenD、无需 API Key。

GET https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json
一次返回全部到期日 + bid/ask/IV/希腊值（约 15 分钟延时）。

合约代码用 OCC，并加上 US. 前缀，与 wheel 合成规则一致：
US.AAPL260821P00200000
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

CBOE_OPTIONS_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
DEFAULT_TTL_SEC = 90.0
_UA = "Mozilla/5.0 (compatible; TradeForge/1.0)"

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


class CboeOptionsError(RuntimeError):
    """CBOE 链拉取或解析失败。"""


def parse_occ(option_symbol: str) -> Tuple[str, str, str, float]:
    """AAPL260904P00320000 -> (AAPL, 2026-09-04, PUT, 320.0)."""
    raw = (option_symbol or "").strip().upper()
    if raw.startswith("US."):
        raw = raw[3:]
    if len(raw) < 16:
        raise ValueError(f"OCC too short: {option_symbol}")
    tail = raw[-15:]
    root = raw[:-15]
    yy, mm, dd = int(tail[0:2]), int(tail[2:4]), int(tail[4:6])
    cp = tail[6]
    strike = int(tail[7:15]) / 1000.0
    if cp not in ("C", "P") or not root:
        raise ValueError(f"bad OCC: {option_symbol}")
    expiry = f"{2000 + yy:04d}-{mm:02d}-{dd:02d}"
    date.fromisoformat(expiry)  # validate
    return root, expiry, "CALL" if cp == "C" else "PUT", strike


def occ_to_code(option_symbol: str) -> str:
    raw = (option_symbol or "").strip().upper()
    if raw.startswith("US."):
        return raw
    return f"US.{raw}"


def cboe_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s.startswith("US."):
        s = s[3:]
    return s.replace(".", "/")


def _url(symbol: str) -> str:
    return CBOE_OPTIONS_URL.format(symbol=cboe_symbol(symbol))


def fetch_raw(symbol: str, timeout: float = 20.0) -> Dict[str, Any]:
    url = _url(symbol)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise CboeOptionsError(f"CBOE HTTP {e.code} for {url}") from e
    except Exception as e:
        raise CboeOptionsError(f"CBOE 拉取失败 {url}: {e}") from e
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("options"):
        raise CboeOptionsError(f"CBOE 返回空链: {symbol}")
    return payload


def get_payload(symbol: str, *, force: bool = False, ttl: float = DEFAULT_TTL_SEC) -> Dict[str, Any]:
    key = cboe_symbol(symbol)
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and not force and now - hit[0] < ttl:
            return hit[1]
    payload = fetch_raw(symbol)
    with _LOCK:
        _CACHE[key] = (time.monotonic(), payload)
    return payload


def clear_cache(symbol: Optional[str] = None) -> None:
    with _LOCK:
        if symbol is None:
            _CACHE.clear()
        else:
            _CACHE.pop(cboe_symbol(symbol), None)


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_iv(raw: Any) -> float:
    try:
        iv = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if iv > 3:
        return round(iv / 100.0, 6)
    return round(max(iv, 0.0), 6)


def future_expiries(exps: List[str], today: Optional[date] = None) -> List[str]:
    today = today or date.today()
    out: List[str] = []
    for e in sorted(set(exps)):
        try:
            d = date.fromisoformat(e[:10])
        except Exception:
            continue
        if d >= today:
            out.append(e[:10])
    return out


def expirations_from_payload(payload: Dict[str, Any], today: Optional[date] = None) -> List[str]:
    data = payload.get("data") or {}
    exps: List[str] = []
    for row in data.get("options") or []:
        try:
            _, exp, _, _ = parse_occ(str(row.get("option") or ""))
            exps.append(exp)
        except Exception:
            continue
    return future_expiries(exps, today)


def load_expirations(symbol: str) -> List[str]:
    return expirations_from_payload(get_payload(symbol))


def row_to_contract(
    row: Dict[str, Any],
    *,
    underlying: str,
    spot: float,
    today: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    occ = str(row.get("option") or "")
    try:
        _root, expiry, option_type, strike = parse_occ(occ)
    except Exception:
        return None
    today = today or date.today()
    bid = _as_float(row.get("bid"))
    ask = _as_float(row.get("ask"))
    last = _as_float(row.get("last_trade_price"))
    theo = _as_float(row.get("theo"))
    if last <= 0:
        if bid > 0 and ask > 0:
            last = round((bid + ask) / 2.0, 4)
        elif theo > 0:
            last = round(theo, 4)
        elif bid > 0:
            last = bid
        elif ask > 0:
            last = ask
    iv = _normalize_iv(row.get("iv"))
    delta = _as_float(row.get("delta"))
    dte = max((date.fromisoformat(expiry) - today).days, 0)
    delta_source = "cboe"
    if abs(delta) < 1e-9 and iv > 0 and spot > 0 and strike > 0 and dte > 0:
        try:
            from app.core.greeks import bs_delta
            bs = bs_delta(option_type, spot, strike, dte, iv)
            if bs is not None:
                delta = bs
                delta_source = "bs"
        except Exception:
            pass
    intrinsic = max(spot - strike, 0.0) if option_type == "CALL" else max(strike - spot, 0.0)
    time_value = max(last - intrinsic, 0.0)
    return {
        "option_symbol": occ_to_code(occ),
        "underlying_symbol": underlying,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "bid": bid,
        "ask": ask,
        "last": last,
        "iv": iv,
        "delta": round(delta, 6),
        "delta_source": delta_source,
        "gamma": round(_as_float(row.get("gamma")), 6),
        "theta": round(_as_float(row.get("theta")), 6),
        "vega": round(_as_float(row.get("vega")), 6),
        "volume": _as_int(row.get("volume")),
        "open_interest": _as_int(row.get("open_interest")),
        "intrinsic_value": round(intrinsic, 2),
        "time_value": round(time_value, 2),
        "contract_size": 100,
    }


def chain_from_payload(
    payload: Dict[str, Any],
    expiry: str,
    display_symbol: str,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    data = payload.get("data") or {}
    spot = _as_float(data.get("current_price") or data.get("close"))
    name = str(data.get("symbol") or display_symbol)
    exp = expiry[:10]
    contracts: List[Dict[str, Any]] = []
    for row in data.get("options") or []:
        try:
            _, row_exp, _, _ = parse_occ(str(row.get("option") or ""))
        except Exception:
            continue
        if row_exp != exp:
            continue
        item = row_to_contract(row, underlying=display_symbol, spot=spot, today=today)
        if item:
            contracts.append(item)
    if not contracts:
        raise CboeOptionsError(f"CBOE 无 {display_symbol} {exp} 合约")
    today = today or date.today()
    dte = max((date.fromisoformat(exp) - today).days, 0)
    ts = payload.get("timestamp")
    return {
        "symbol": display_symbol,
        "name": name,
        "expiry": exp,
        "spot_price": round(spot, 2),
        "days_to_expiry": dte,
        "contracts": contracts,
        "adapter": "cboe",
        "pricing_source": "cboe",
        "delayed": True,
        "detail": f"CBOE delayed {ts}" if ts else "CBOE delayed",
    }


def load_chain(symbol: str, expiry: str) -> Dict[str, Any]:
    display = (symbol or "").strip().upper()
    if display.startswith("US."):
        display = display[3:]
    return chain_from_payload(get_payload(symbol), expiry, display)
