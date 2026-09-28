# TradeForge Backend

TradeForge 后端基于 **FastAPI + SQLite**，负责：

- 行情路由（Futu / Finnhub / Yahoo / Mock）
- 策略与实时信号评估
- Mock / Futu 交易接口
- Futu 期权链与收益分析
- 本地历史 K 线存储与调度器

---

## 环境要求

- Python 3.10+
- SQLite3
- macOS / Linux

已验证依赖见：

```bash
requirements.txt
```

---

## 安装依赖

```bash
pip install -r requirements.txt
```

---

## 本地配置

复制模板：

```bash
cp .env.example .env
```

示例：

```bash
FINNHUB_API_KEY=your_finnhub_api_key_here
FUTU_OPEND_HOST=127.0.0.1
FUTU_OPEND_PORT=11111
```

> 注意：`.env` 只用于本地，不提交仓库。

---

## 启动服务

```bash
python run.py
```

默认监听：
- `127.0.0.1:8000`

---

## 文档与接口

启动后访问：

- Swagger：<http://127.0.0.1:8000/docs>
- Health：<http://127.0.0.1:8000/health>

---

## 数据源策略

### Quote
- 美股 → Finnhub
- A股 / 港股 → Futu

### 历史 K 线
- 统一 local-first
- 美股补数 → Yahoo
- A股 / 港股补数 → Futu

### 期权
- 固定走 Futu

### 交易
- Mock / Futu

---

## 历史数据调度

每天 **08:00** 自动更新订阅标的的：

- `1d`
- `1h`
- `30m`
- `5m`
- `1m`

相关接口：
- `/api/history/subscriptions`
- `/api/history/scheduler/status`
- `/api/history/scheduler/run`

---

## 核心模块

```text
app/
├── api/
├── core/
├── data/
└── main.py
```

重点：
- `app/data/adapter.py` → 多数据源适配
- `app/data/history_repository.py` → 本地历史库
- `app/data/history_backfill.py` → 历史补数
- `app/data/history_scheduler.py` → 每日 08:00 定时更新
- `app/core/signal_engine.py` → 实时策略信号引擎

---

## 测试建议

常用检查：

```bash
python run.py
```

然后访问：
- `/docs`
- `/api/market/quote`
- `/api/market/klines`
- `/api/options/chain`
- `/api/history/scheduler/status`

---

更多整体说明请看根目录：
- `../README.md`

---

## Google Sheets / Notion 同步

可选后台镜像：`app/services/google_sheets_sync.py`、`notion_sync.py`。  
默认 `enabled=false`；凭证见 `.env.example`（勿提交真实密钥）。
# 触线历史回测

`POST /api/wheel/backtest/timing-compare` 接收 `bars`、`quotes`、可选
`params` 和 `ema_period`（50 或 200），用同一份历史数据比较普通 Wheel
和日线 EMA 触线后开仓。页面中的 HV 情景模拟使用的是另一条接口。

- `bars`：按日期严格递增的标的日线，字段 `date`（YYYY-MM-DD）、`close`。
- `quotes`：每合约每日一个快照，包含 `date`、`contract_code`、`side`
  （PUT/CALL）、`strike`、`expiry`、`delta`、`bid`、`ask`。
  同一代码的方向、执行价、到期日必须保持一致。
- 信号定义：使用与实盘相同的 adjust=False EMA，价格达到 EMA 且 bid 确认。
  缺少真实 high/close 时明确使用中间价代理；信号最早在下一标的交易日按 bid 卖出，
  平仓按 ask，另计手续费。预热期最后一天的信号可以在回测首日执行。
- 先筛选资金、年化、DTE、delta 和执行价约束，再选最接近目标 delta 的合约。
  持仓若在窗口内到期，须有到期日标的价格；窗口后到期可在期末买回。缺失快照不会自动补成理论报价。
- `baseline` 和 `timing` 返回权益曲线与逐笔现金流；`opened_trade_count`
  单独统计开仓，`trade_count` 统计全部流水。`signal_count` 是可用于后续
  执行的样本日期内原始触线次数，尚未经过开仓条件筛选。
  成交流水包含合约身份，触线开仓另附 `signal_date`。
- 无开仓会返回 `warnings`；失败原因见顶层 `error`。日线报价代理与实盘
  1h 合约 K 线触线的定义不同，结果不能视为已验证 1h 策略优势。


## 策略验证与风险预算工作台

入口：Wheel → 风控 →「策略验证与风险预算」。详细数据约定和限制见 [RESEARCH.md](RESEARCH.md)。

- 扫描时保存原始期权链、淘汰原因、参数、触线输入和最终账户风险结论；可以下载逐次证据。手动或定时留档覆盖启用标的当前可查询到的全部到期日。
- 固定周期、仅高分、高分＋触线、高分＋触线＋趋势四组使用同一输入、资金和成本；支持固定参数时间留出。触线计算与实盘共用 EMA 定义。
- 股价、IV、退出点差联合压力测试；组合/单笔/杠杆标的预算及账户采样回撤闸门。默认仅观察，页面可启用硬闸门；真实成交始终允许如实登记，计划新增风险才会拦截。
- 持仓管理显示“今天是否仍会开仓”及原因，并明确 Roll 的旧腿损益与新腿风险需要独立评估。
- 评分标记为经验规则；归档拆分行情事实、模型估计和评分依据，默认卖出权利金使用 bid，保留用户已有定价配置。
- 成交观测表单记录委托、部分成交、撤销、成交耗时及成交后价格；修订采用版本校验并保留历史。收益归因核对期权/股票已实现与未实现盈亏和费用。

默认定时归档关闭（可设 ≥15 分钟），不会自动开启交易、推送或更改券商订单。
