# TradeForge v1.1 实时数据融合层 - 测试报告

**测试时间**: 2026-03-19 18:00
**测试人员**: FORGE
**后端地址**: http://127.0.0.1:8000

---

## 一、测试概述

本次测试验证 TradeForge v1.1 核心架构优化成果，包括：
1. 实时数据融合层
2. 统一策略执行层  
3. 交易风控层
4. 实时报价推送服务

---

## 二、测试结果

### ✅ 通过的测试

| 序号 | 功能 | 接口 | 测试结果 |
|------|------|------|----------|
| 1 | Health 检查 | GET /api/runtime/health | ✅ 通过 |
| 2 | 市场状态 API | GET /api/runtime/market-state | ✅ 通过 |
| 3 | 策略信号 (实时模式) | GET /api/runtime/strategies/{id}/signal | ✅ 通过 |
| 4 | 风控检查 | POST /api/runtime/risk/check | ✅ 通过 |
| 5 | 推送服务启动 | POST /api/runtime/push/start | ✅ 通过 |

### ❌ 未通过的测试

| 序号 | 功能 | 接口 | 测试结果 | 原因 |
|------|------|------|----------|------|
| 1 | 推送订阅 | POST /api/runtime/push/subscribe | ❌ 失败 | 富途 WebSocket 订阅问题 |

---

## 三、详细测试数据

### 3.1 Health 检查

```json
{
  "status": "ok",
  "trigger_modes": ["on_quote", "on_bar_close"],
  "default_mode": "on_quote",
  "components": {
    "market_state": "ok",
    "strategy_runtime": "ok",
    "risk_engine": "ok",
    "quote_push": "ok"
  },
  "push_markets": {
    "supported": ["HK", "SH", "SZ"],
    "unsupported": ["US"]
  }
}
```

### 3.2 市场状态 API

```
GET /api/runtime/market-state?symbol=00700.HK&timeframe=1m&trigger_mode=on_quote&adapter=futu

结果: 751 根历史 K 线数据
数据来源: futu
时间范围: 2026-03-16 ~ 2026-03-19
```

### 3.3 策略信号测试

```
GET /api/runtime/strategies/a76c49db-e386-406b-b580-acfefc2b176c/signal?symbol=00700.HK&timeframe=1m&trigger_mode=on_quote&adapter=futu

结果:
{
  "signal": "NONE",
  "trigger_mode": "on_quote",
  "is_live_triggered": false,
  "ma_fast": 518.7,
  "ma_slow": 521.725,
  "vol_ratio": 1.81,
  "entry_triggered": false,
  "exit_triggered": false
}
```

**分析**:
- MA5 (518.7) < MA20 (521.725) → 未触发金叉
- 成交量放大 81% → 触发成交量条件
- 当前无买入信号

### 3.4 风控检查测试

```
POST /api/runtime/risk/check
{
  "symbol": "00700.HK",
  "side": "BUY",
  "quantity": 100,
  "price": 550,
  "order_type": "LIMIT"
}

结果:
{
  "allowed": true,
  "result": "WARN",
  "reason": "风险警告 (25)，但允许下单",
  "warnings": ["建仓后仓位占比 55.0% 超过限制 30.0%"]
}
```

**风控规则生效**: 仓位占比 55% 超过 30% 限制会发出警告。

---

## 四、两种信号触发模式说明

### 4.1 ON_QUOTE (实时触发) - 默认

- 每次请求获取最新报价
- 适用于：轮询场景、美股/港股/A股
- 当前状态: ✅ 可用

### 4.2 ON_BAR_CLOSE (收盘触发)

- 等 K 线收盘才计算信号
- 适用于：回测、传统定时信号
- 当前状态: ✅ 可用

---

## 五、已知问题

### 5.1 推送订阅失败

**问题描述**: 
- 推送服务已启动 (`running: true`)
- 但订阅港股 00700.HK 失败

**可能原因**:
1. 富途 OpenD WebSocket 连接配置问题
2. 订阅 API 调用方式需要调整

**当前解决方案**:
- 使用轮询模式（ON_QUOTE）获取实时信号
- 该方式不需要推送服务，每次请求会实时获取报价

---

## 六、新增文件清单

| 文件路径 | 说明 |
|----------|------|
| `backend/app/core/quote_push.py` | 实时报价推送服务 |
| `backend/app/core/market_state.py` | 新增 update_forming_bar_with_quote 方法 |
| `backend/app/api/runtime.py` | 新增 push 相关 API + 风控 API |
| `backend/app/data/database.py` | 新增 risk_events 表 |

---

## 七、API 接口清单

### 7.1 Runtime API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/runtime/health` | GET | 服务健康检查 |
| `/api/runtime/market-state` | GET | 获取市场状态 |
| `/api/runtime/strategies/{id}/signal` | GET | 获取策略信号 |
| `/api/runtime/strategies/{id}/signal/on-quote` | GET | 实时触发模式 |
| `/api/runtime/strategies/{id}/signal/on-bar-close` | GET | 收盘触发模式 |

### 7.2 推送服务 API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/runtime/push/start` | POST | 启动推送服务 |
| `/api/runtime/push/stop` | POST | 停止推送服务 |
| `/api/runtime/push/subscribe` | POST | 订阅行情 |
| `/api/runtime/push/unsubscribe` | POST | 取消订阅 |
| `/api/runtime/push/status` | GET | 推送服务状态 |

### 7.3 风控 API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/runtime/risk/check` | POST | 风控检查 |
| `/api/runtime/risk/events` | GET | 风险事件记录 |
| `/api/runtime/risk/policy` | GET | 获取风控策略 |
| `/api/runtime/risk/policy` | POST | 更新风控策略 |

---

## 八、结论

1. **核心架构**: ✅ 已完成并验证通过
2. **策略信号**: ✅ 实时计算正常
3. **风控层**: ✅ 规则生效
4. **推送服务**: ⚠️ 框架已搭建，订阅功能待优化

**建议**: 
- 推送订阅问题可后续优化，当前轮询模式已满足需求
- 美股使用轮询方式获取信号，港股/A股可后续启用推送模式

---

**报告生成时间**: 2026-03-19 18:00
