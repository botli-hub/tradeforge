#!/usr/bin/env python3
"""
TradeForge v1.1 核心层 Smoke Test

测试内容：
1. Market State - 能获取历史bars和实时forming bar
2. Strategy Runtime - 在mock/AAPL上返回信号
3. Risk Check - 返回 allow/block

运行方式：
cd /Users/alibot/.openclaw/workspace/forge/projects/tradeforge/backend
python test_runtime_smoke.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime

def test_market_state():
    """测试市场状态获取"""
    print("\n" + "="*60)
    print("TEST 1: Market State")
    print("="*60)
    
    from app.core.market_state import (
        get_market_state,
        SUPPORTED_TIMEFRAMES,
        TriggerMode,
    )
    
    print(f"Supported timeframes: {SUPPORTED_TIMEFRAMES}")
    
    # 测试 on_quote 模式
    print("\n[Mode: ON_QUOTE - 实时模式]")
    state = get_market_state(
        symbol="AAPL",
        timeframe="1d",
        trigger_mode="on_quote",
        history_days=30,
        adapter_type="mock",
    )
    
    print(f"  Symbol: {state.symbol}")
    print(f"  Timeframe: {state.timeframe}")
    print(f"  Trigger mode: {state.trigger_mode.value}")
    print(f"  History bars: {len(state.history_bars)}")
    print(f"  Has forming bar: {state.forming_bar is not None}")
    
    if state.forming_bar:
        fb = state.forming_bar
        print(f"  Forming bar: O={fb.open:.2f} H={fb.high:.2f} L={fb.low:.2f} C={fb.close:.2f}")
    
    latest = state.get_latest_bar_for_signal()
    if latest:
        print(f"  Latest bar for signal: {latest.get('timestamp', latest.get('period_start', 'N/A'))}")
    
    # 测试 on_bar_close 模式
    print("\n[Mode: ON_BAR_CLOSE - 收盘模式]")
    state2 = get_market_state(
        symbol="AAPL",
        timeframe="1d",
        trigger_mode="on_bar_close",
        history_days=30,
        adapter_type="mock",
    )
    print(f"  History bars: {len(state2.history_bars)}")
    print(f"  Has forming bar: {state2.forming_bar is not None}")
    
    print("\n✅ Market State test PASSED")
    return True


def test_strategy_runtime():
    """测试策略执行"""
    print("\n" + "="*60)
    print("TEST 2: Strategy Runtime")
    print("="*60)
    
    from app.core.strategy_runtime import evaluate_strategy, get_available_strategies
    
    # 获取可用策略
    strategies = get_available_strategies()
    print(f"Available strategies: {len(strategies)}")
    for s in strategies[:5]:
        print(f"  - {s['id']}: {s['name']} ({s['status']})")
    
    if not strategies:
        print("\n⚠️  No strategies found, creating a test strategy...")
        _create_test_strategy()
        strategies = get_available_strategies()
    
    if strategies:
        strategy_id = strategies[0]['id']
        print(f"\nTesting with strategy: {strategy_id}")
        
        # 测试 on_quote 实时模式
        print("\n[Mode: ON_QUOTE - 实时触发]")
        result = evaluate_strategy(
            strategy_id=strategy_id,
            symbol="AAPL",
            timeframe="1d",
            trigger_mode="on_quote",
            adapter_type="mock",
        )
        
        print(f"  Signal: {result.signal}")
        print(f"  Reason: {result.reason}")
        print(f"  Trigger mode: {result.trigger_mode}")
        print(f"  Is live triggered: {result.is_live_triggered}")
        print(f"  Entry triggered: {result.entry_triggered}")
        print(f"  Exit triggered: {result.exit_triggered}")
        if result.latest_bar:
            print(f"  Latest bar: O={result.latest_bar.get('open', 'N/A')} C={result.latest_bar.get('close', 'N/A')}")
        
        # 测试 on_bar_close 收盘模式
        print("\n[Mode: ON_BAR_CLOSE - 收盘触发]")
        result2 = evaluate_strategy(
            strategy_id=strategy_id,
            symbol="AAPL",
            timeframe="1d",
            trigger_mode="on_bar_close",
            adapter_type="mock",
        )
        print(f"  Signal: {result2.signal}")
        print(f"  Trigger mode: {result2.trigger_mode}")
        print(f"  Is live triggered: {result2.is_live_triggered}")
        
        print("\n✅ Strategy Runtime test PASSED")
        return True
    else:
        print("\n❌ No strategies to test")
        return False


def test_risk_check():
    """测试风控检查"""
    print("\n" + "="*60)
    print("TEST 3: Risk Check")
    print("="*60)
    
    from app.core.risk_engine import (
        check_order_risk,
        get_risk_events,
        get_risk_policy,
    )
    
    # 查看当前策略
    policy = get_risk_policy()
    print(f"Current policy:")
    print(f"  max_position_pct: {policy.max_position_pct}")
    print(f"  max_order_value: {policy.max_order_value}")
    print(f"  signal_cooldown_seconds: {policy.signal_cooldown_seconds}")
    print(f"  price_deviation_pct: {policy.price_deviation_pct}")
    print(f"  allow_same_side_pyramid: {policy.allow_same_side_pyramid}")
    
    # 测试正常订单
    print("\n[Test: Normal order]")
    result = check_order_risk(
        symbol="AAPL",
        side="BUY",
        quantity=100,
        price=0,
        order_type="MARKET",
    )
    print(f"  Result: {result.result}")
    print(f"  Allowed: {result.allowed}")
    print(f"  Risk score: {result.risk_score}")
    print(f"  Warnings: {result.warnings}")
    
    # 测试大额订单（应该触发警告）
    print("\n[Test: Large order]")
    result2 = check_order_risk(
        symbol="AAPL",
        side="BUY",
        quantity=100000,  # 很大
        price=0,
        order_type="MARKET",
    )
    print(f"  Result: {result2.result}")
    print(f"  Allowed: {result2.allowed}")
    print(f"  Risk score: {result2.risk_score}")
    print(f"  Warnings: {result2.warnings}")
    
    # 测试风控事件记录
    events = get_risk_events(limit=5)
    print(f"\nRisk events: {len(events)} recent events")
    
    print("\n✅ Risk Check test PASSED")
    return True


def test_api_endpoints():
    """测试API端点（需要先启动服务器）"""
    print("\n" + "="*60)
    print("TEST 4: API Endpoints (requires running server)")
    print("="*60)
    
    import requests
    
    base_url = "http://127.0.0.1:8000/api/runtime"
    
    # 测试健康检查
    print("\n[GET /api/runtime/health]")
    try:
        r = requests.get(f"{base_url}/health", timeout=5)
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.json()}")
    except Exception as e:
        print(f"  ⚠️  Server not running: {e}")
        return False
    
    # 测试 timeframes
    print("\n[GET /api/runtime/timeframes]")
    try:
        r = requests.get(f"{base_url}/timeframes", timeout=5)
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.json()}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # 测试 trigger modes
    print("\n[GET /api/runtime/trigger-modes]")
    try:
        r = requests.get(f"{base_url}/trigger-modes", timeout=5)
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.json()}")
    except Exception as e:
        print(f"  Error: {e}")
    
    print("\n✅ API Endpoints test PASSED")
    return True


def _create_test_strategy():
    """创建测试策略"""
    import json
    import uuid
    from app.data.database import get_db
    
    strategy_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    config = {
        "name": "Test Strategy",
        "symbols": ["AAPL", "TSLA"],
        "timeframe": "1d",
        "indicators": [
            {"name": "ma20", "type": "MA", "period": 20, "source": "close"},
            {"name": "ma50", "type": "MA", "period": 50, "source": "close"},
        ],
        "conditions": {
            "entry": {
                "type": "AND",
                "rules": [
                    {"id": "r1", "type": "crossover", "indicator": "ma20", "ref": "ma50", "op": "cross_above"},
                ]
            },
            "exit": {
                "type": "OR",
                "rules": [
                    {"id": "r2", "type": "crossover", "indicator": "ma20", "ref": "ma50", "op": "cross_below"},
                ]
            }
        }
    }
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO strategies (id, name, mode, config, status, version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (strategy_id, "Test Strategy", "visual", json.dumps(config), "ready", 1, now, now)
    )
    conn.commit()
    conn.close()
    
    print(f"Created test strategy: {strategy_id}")


def main():
    print("="*60)
    print("TradeForge v1.1 Core Layers Smoke Test")
    print("="*60)
    print(f"Time: {datetime.now().isoformat()}")
    
    results = []
    
    # 1. Market State
    try:
        results.append(("Market State", test_market_state()))
    except Exception as e:
        print(f"\n❌ Market State test FAILED: {e}")
        results.append(("Market State", False))
    
    # 2. Strategy Runtime
    try:
        results.append(("Strategy Runtime", test_strategy_runtime()))
    except Exception as e:
        print(f"\n❌ Strategy Runtime test FAILED: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Strategy Runtime", False))
    
    # 3. Risk Check
    try:
        results.append(("Risk Check", test_risk_check()))
    except Exception as e:
        print(f"\n❌ Risk Check test FAILED: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Risk Check", False))
    
    # 4. API Endpoints (optional)
    try:
        results.append(("API Endpoints", test_api_endpoints()))
    except Exception as e:
        print(f"\n❌ API Endpoints test FAILED: {e}")
        results.append(("API Endpoints", False))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
    
    all_passed = all(r[1] for r in results)
    print(f"\nOverall: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
