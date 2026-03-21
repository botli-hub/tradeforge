# TradeForge PRD v1.0（当前实际状态）

---

## 一、产品定位

> **TradeForge = 面向多市场的量化交易工作台**

| 维度 | 定义 |
|------|------|
| 产品类型 | 量化策略研究、回测、信号评估与交易执行 |
| 目标用户 | 进阶用户 + 专业用户 |
| 数据源 | Finnhub（美股行情）/ Yahoo（美股历史）/ Futu（港股/A股/期权/实盘） / Mock（兜底） |
| 交付形式 | Web 应用（React SPA + FastAPI，本地运行） |

---

## 二、已实现功能（当前版本）

| 模块 | 状态 | 说明 |
|------|------|------|
| 多市场行情 | ✅ | 美股 / A股 / 港股，自动路由数据源 |
| K线展示 | ✅ | lightweight-charts，支持 1m/5m/15m/1h/1d |
| 自选池 | ✅ | 关注 / 取消关注 / 快速切换 |
| Visual 策略 | ✅ | MA均线 + 成交量比条件，IR 转换 |
| Formula 策略 | ✅ | Pine Script DSL，Monaco 编辑器，参数面板 |
| 策略信号评估 | ✅ | 实时评估当前 K 线信号 |
| 信号确认弹窗 | ✅ | 评估结果 → 确认弹窗 → 下单 |
| 回测引擎 | ✅ | 单标的 + 多标的，含买入持有基准对比 |
| 回测报告 | ✅ | 9项指标 + Buy & Hold 基准 + 资金曲线 + 明细 CSV 导出 |
| 交易（Mock） | ✅ | Mock 模式，完整委托/成交/持仓流程 |
| 交易（Futu） | ✅ | 富途实盘/模拟，需本机 OpenD |
| 订单页 | ✅ | 委托列表，状态追踪 |
| 持仓页 | ✅ | 持仓汇总，浮盈亏 |
| 期权页 | ✅ | 富途期权链，Greeks，组合收益图 |
| 历史数据 | ✅ | local-first SQLite，每日 08:00 自动更新 |
| 历史数据管理 | ✅ | 订阅列表，手动 backfill，coverage 查询 |
| 设置页 | ✅ | 数据源模式，Futu 连接配置，主题 |

---

## 三、数据源路由

### 行情报价
- **美股** → Finnhub
- **港股 / A股** → Futu
- **兜底** → Mock

### 历史 K 线（local-first）
```
先查本地 SQLite → 不足则自动补数 → 写回本地 → 返回本地数据
```
- **美股** → Yahoo Finance
- **港股 / A股** → Futu

### 期权
- 固定走 **Futu**（到期日 / 期权链 / 快照 / Greeks）

### 交易
- **Mock** 或 **Futu**（设置页切换）

---

## 四、主流程

### 4.1 策略研究→回测循环

```
行情页（选标的）→ 策略页（创建/编辑）→ 回测页（执行）→ 查看报告 → 优化策略
```

### 4.2 实时信号→下单流程

```
行情页（选标的 + 选策略）→ 评估信号 → 确认弹窗（含价格/数量/模式）→ 发送订单 → 订单页追踪
```

---

## 五、页面详细设计

### 5.1 导航结构

```
[行情] [策略] [回测] [期权] [订单] [持仓] [历史数据] [设置]
```

### 5.2 行情页 /market

| 功能 | 说明 |
|------|------|
| 股票搜索 | 支持美股/港股/A股代码 |
| K线 + 报价 | 实时行情，含报价源/K线源标注 |
| 周期切换 | 1m/5m/15m/1h/1d |
| 策略信号评估 | 当前 K 线对选定策略打分 |
| 信号确认下单 | 买入/卖出弹窗，支持 LIMIT/MARKET |
| 自选池 | 关注 / 取消关注 |
| 账户面板 | 现金 / 购买力 / 持仓市值 / 总资产 |

**数据来源标注**（已实现）：
```
当前报价：Finnhub    当前K线：本地历史库
```

### 5.3 策略页 /strategy

**策略列表**：名称 / Mode / 标的 / Timeframe / 状态 / 版本

**策略编辑器**（页内全屏）：
- Visual 模式：MA 均线参数 + 成交量阈值
- Formula 模式：Monaco 代码编辑器 + 实时 validate/parse/transpile

**策略状态**：
| 状态 | 触发条件 |
|------|----------|
| draft | 新建未保存 / 验证失败 |
| ready | 保存成功且通过 IR 验证 |

### 5.4 回测页 /backtest

**参数**：策略 / 标的 / Timeframe / 日期区间 / 初始资金 / 手续费 / 滑点

**状态机**：
```
empty → running（点击开始回测）→ success（完成）
                               → error（失败，可重试）
```

**回测报告（9项指标 + 基准）**：

| # | 指标 | 说明 |
|---|------|------|
| 1 | 总收益率 | 策略期间总回报 |
| 2 | 年化收益率 | 基于实际天数年化 |
| 3 | 最大回撤 | 资金曲线最大跌幅 |
| 4 | 夏普比率 | 基于每日权益收益率 |
| 5 | 胜率 | 盈利交易 / 总交易 |
| 6 | 盈亏比 | 平均盈利 / 平均亏损 |
| 7 | 交易次数 | 总成交笔数 |
| 8 | 平均持仓周期 | 基于实际入场/出场时间 |
| 9 | 买入持有基准 | 同期 Buy & Hold 收益 + 超额收益 |

**额外功能**：
- 资金曲线 SVG 图（含 Buy & Hold 基准虚线）
- 数据来源显示（实际来源 / 路由方式 / bar数量）
- 交易明细表格 + **CSV 导出**

### 5.5 期权页 /options

- 标的搜索 + 到期日选择
- 期权链（Call/Put，行权价，成交量，持仓量）
- Greeks（Delta / Gamma / Theta / Vega / IV）
- 组合策略收益图：Long Call / Long Put / Bull Call Spread / Bear Put Spread

### 5.6 历史数据页 /history

- 订阅管理（添加 / 启停 / 删除）
- 手动 Backfill（指定标的/时间范围）
- Coverage 查询（各 timeframe 数据覆盖情况）
- 调度器状态 + 手动触发

---

## 六、IR（中间表示）格式

```json
{
  "version": "1.0",
  "strategy_id": "strat_xxx",
  "mode": "visual | formula",
  "name": "策略名称",
  "symbols": ["AAPL"],
  "timeframe": "1d",
  "source_code": "（仅 formula 模式）",
  "parameters": [],
  "indicators": [
    {"name": "ma_fast", "type": "MA", "period": 5, "source": "close"},
    {"name": "ma_slow", "type": "MA", "period": 20, "source": "close"},
    {"name": "vol_ma", "type": "MA", "period": 20, "source": "volume"}
  ],
  "variables": {
    "vol_ratio": {"op": "/", "left": "volume", "right": "vol_ma"}
  },
  "conditions": {
    "entry": {
      "type": "AND",
      "rules": [
        {"id": "entry_1", "type": "crossover", "op": "cross_above", "left": "ma_fast", "right": "ma_slow"},
        {"id": "entry_2", "type": "binary", "op": ">", "left": "vol_ratio", "right": 1.5}
      ]
    },
    "exit": {
      "type": "OR",
      "rules": [
        {"id": "exit_1", "type": "crossover", "op": "cross_below", "left": "ma_fast", "right": "ma_slow"}
      ]
    }
  },
  "position_sizing": {"type": "fixed_amount", "value": 10000},
  "risk_rules": {
    "initial_capital": 100000,
    "fee_rate": 0.0003,
    "slippage": 0.001,
    "max_position_pct": 0.5,
    "stop_loss": 0.05,
    "take_profit": 0.15
  }
}
```

---

## 七、回测规则

### 7.1 撮合时序

| 规则 | 说明 |
|------|------|
| 信号检测 | 当前 bar 收盘时检测条件 |
| 成交时序 | 同根 K 线的开盘价执行（滑点调整后） |
| 买入成交价 | open × (1 + slippage) |
| 卖出成交价 | open × (1 - slippage) |
| 风控触发 | 基于当前 bar close 检测，同 bar 平仓 |

### 7.2 v1.0 限制

- ❌ 不支持部分成交
- ❌ 不处理停牌/缺失 K 线
- ❌ 不处理复权（回测数据为原始价）
- ❌ 不支持做空（多空信号仅执行多头方向）

### 7.3 参数职责边界

| 参数 | 归属 | 说明 |
|------|------|------|
| 策略逻辑（指标/条件） | 策略配置 | 保存在策略 IR 中 |
| 初始资金/手续费/滑点 | 回测执行参数 | 仅在回测页设置，覆盖全局默认 |
| 全局默认参数 | 设置页 | 作为回测页的初始值 |

---

## 八、数据存储

### SQLite 核心表

| 表名 | 用途 |
|------|------|
| strategies | 策略定义（含 IR JSON） |
| strategy_versions | 策略历史版本 |
| backtest_runs | 回测记录 |
| trades | 回测成交明细 |
| instruments | 标的基础信息 |
| kline_bars | 历史 K 线（主数据库） |
| kline_sync_state | 各标的/周期同步状态 |
| kline_backfill_jobs | 补数任务队列 |
| data_subscriptions | 定时更新订阅列表 |
| history_scheduler_runs | 调度运行记录 |
| risk_events | 风控事件日志 |

---

## 九、性能目标

> 在 MacBook Air (M2/M3)，单标的、1年日线数据：
> - 回测完成时间 < 3 秒
> - 页面交互响应 < 100ms
> - 历史数据查询（本地 SQLite）< 200ms

---

## 十、后续优化方向

| 优先级 | 方向 |
|--------|------|
| P1 | Visual 策略支持更多指标（RSI/MACD/BBands）和真正的拖拽条件块 |
| P1 | 回测结果 PDF 导出 |
| P2 | 多标的回测并发信号排队规则（优先级/轮转） |
| P2 | 策略参数扫描（Parameter Sweep / Grid Search） |
| P3 | 国际化（英文界面） |
| P3 | 组合回测（Portfolio Backtest） |

---

*版本: v1.0 | 更新: 2026.03.22*
