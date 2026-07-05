"""Black-Scholes 期权希腊值(仅用于兜底)

当行情源(富途 OpenD)未返回 option_delta —— 限频、冷门合约、或港股快照缺字段 ——
用 BS 模型按 现价/行权价/DTE/隐含波动率 反算 delta,让 wheel 的 delta 区间筛选
在数据缺失时仍可工作,而不是直接把该合约丢弃。

约定:
- 返回带符号 delta,与富途一致(put 为负、call 为正);调用方通常取 abs()。
- sigma(iv)传入小数,如 0.32;dte 为自然日,内部按 /365 折算年化。
- 无股息(q=0)、无风险利率默认 4%,可通过参数覆盖。
"""
import math
from typing import Optional

DEFAULT_RISK_FREE = 0.04


def _norm_cdf(x: float) -> float:
    """标准正态分布累积函数,用 erf 实现(无需 scipy)"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(
    option_type: str,
    spot: float,
    strike: float,
    dte: int,
    iv: float,
    r: float = DEFAULT_RISK_FREE,
    q: float = 0.0,
) -> Optional[float]:
    """Black-Scholes delta。参数非法(缺价、iv<=0、已到期)时返回 None。

    put delta ∈ [-1, 0],call delta ∈ [0, 1]。
    """
    if not option_type:
        return None
    ot = option_type.upper()
    if ot not in ("PUT", "CALL"):
        return None
    if spot is None or strike is None or spot <= 0 or strike <= 0:
        return None
    if iv is None or iv <= 0:
        return None
    if dte is None or dte <= 0:
        return None

    t = dte / 365.0
    try:
        d1 = (math.log(spot / strike) + (r - q + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    except (ValueError, ZeroDivisionError):
        return None

    disc_q = math.exp(-q * t)
    if ot == "CALL":
        delta = disc_q * _norm_cdf(d1)
    else:
        delta = disc_q * (_norm_cdf(d1) - 1.0)
    return round(delta, 6)
