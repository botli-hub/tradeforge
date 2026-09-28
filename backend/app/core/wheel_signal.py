"""Shared live/replay EMA definition, versioned independently of execution."""
import math
SIGNAL_VERSION = 'ema-touch-v1'


def ema_value(closes, period):
    values=[float(v) for v in closes]
    if not values or period not in (50,200) or any(not math.isfinite(v) or v<0 for v in values):
        raise ValueError('EMA 需要有效价格序列与 50/200 周期')
    value=values[0]
    for close in values[1:]: value+=2/(period+1)*(close-value)
    return value


def daily_touch(closes, high, bid=None, period=50, confirm_bid=True):
    if len(closes)<period:return False
    ema=ema_value(closes,period)
    return bool(high is not None and math.isfinite(float(high)) and float(high)>=ema
                and (not confirm_bid or (bid is not None and math.isfinite(float(bid)) and float(bid)>=ema)))
