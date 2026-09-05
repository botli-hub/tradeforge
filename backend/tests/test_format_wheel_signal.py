"""format_wheel_signal TG 文案须含 K 线周期(Put/Call)。"""
from types import SimpleNamespace

from app.core.leaps_monitor import (
    LeapsSignal,
    _signal_timeframe_label,
    format_wheel_signal,
)


def _sig(**kwargs):
    base = dict(
        symbol="AAPL",
        contract_code="US.AAPL260717P00150000",
        expiry="260717",
        strike=150.0,
        signal_level="WHEEL_PUT",
        trigger_price=2.5,
        ema_type="EMA50",
        ema_value=2.48,
        iv_rank=72.0,
        underlying_price=155.0,
        floor_price=140.0,
        bid=2.4,
        delta=0.25,
        annualized=18.5,
        dte=45,
        timeframe="1d",
    )
    base.update(kwargs)
    return LeapsSignal(**base)


def test_put_format_includes_timeframe_1d():
    text = format_wheel_signal(_sig(signal_level="WHEEL_PUT", timeframe="1d"))
    assert "周期 1d" in text
    assert "卖Put时机" in text
    assert "触及 EMA50" in text


def test_call_format_includes_timeframe_1h():
    text = format_wheel_signal(
        _sig(
            signal_level="WHEEL_CALL",
            contract_code="US.AAPL260717C00180000",
            timeframe="1h",
            ema_type="EMA200",
            iv_rank=80.0,
        )
    )
    assert "周期 1h" in text
    assert "卖Call时机" in text


def test_call_format_includes_timeframe_1d():
    text = format_wheel_signal(
        _sig(signal_level="WHEEL_CALL", timeframe="1d", iv_rank=80.0)
    )
    assert "周期 1d" in text


def test_missing_timeframe_shows_question_no_crash():
    # SimpleNamespace: 无 timeframe 字段
    bare = SimpleNamespace(
        symbol="NVDA",
        contract_code="US.NVDA260717P00100000",
        expiry="260717",
        strike=100.0,
        signal_level="WHEEL_PUT",
        trigger_price=1.2,
        ema_type="EMA50",
        ema_value=1.1,
        iv_rank=40.0,
        underlying_price=110.0,
        floor_price=90.0,
        bid=None,
        delta=None,
        annualized=None,
        dte=30,
        below_floor=False,
    )
    text = format_wheel_signal(bare)
    assert "周期 ?" in text
    assert _signal_timeframe_label(bare) == "?"


def test_empty_timeframe_shows_question():
    assert _signal_timeframe_label(SimpleNamespace(timeframe="")) == "?"
    assert _signal_timeframe_label(SimpleNamespace(timeframe=None)) == "?"
    assert _signal_timeframe_label({"timeframe": "K_60M"}) == "1h"
    assert _signal_timeframe_label({"timeframe": "K_DAY"}) == "1d"
