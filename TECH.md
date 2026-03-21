# TradeForge 技术文档 v1.0

---

## 一、系统架构

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        TradeForge 系统                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────┐     ┌──────────────────────┐        │
│  │      前端 (React)     │     │    后端 (Python)     │        │
│  │                      │     │                      │        │
│  │  React + TypeScript  │◀───▶│  FastAPI + SQLite   │        │
│  │  Zustand (Store)     │HTTP │  Backtest Engine    │        │
│  │  lightweight-charts  │REST │  Formula Parser     │        │
│  │  Monaco Editor       │     │  History Scheduler  │        │
│  └──────────────────────┘     └──────────────────────┘        │
│          :1420 / :5173                 :8000                    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    外部数据源                             │  │
│  │  Finnhub（美股报价）  Yahoo（美股历史K线）               │  │
│  │  Futu OpenD（港股/A股/实盘/期权）   Mock（兜底）        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

> **注意**：前后端通过 HTTP REST API 通信（非 Tauri IPC）。
> 当前为纯 Web 应用，后续可封装为 Tauri 桌面应用。

### 1.2 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端框架 | React | 18.x |
| 语言 | TypeScript | 5.x |
| 状态管理 | Zustand | 4.x |
| 图表 | lightweight-charts | 4.x |
| 代码编辑器 | Monaco Editor | latest |
| 构建工具 | Vite | 5.x |
| 后端框架 | FastAPI | 0.109.x |
| 数据存储 | SQLite | 3.x |
| 回测计算 | NumPy / Pandas | latest |
| 数据源 | Finnhub / Yahoo / futu-api | - |

---

## 二、前端架构

### 2.1 项目结构

```
frontend/
├── src/
│   ├── components/          # 公共 UI 组件
│   │   └── SignalConfirmModal.tsx
│   │
│   ├── pages/               # 页面组件
│   │   ├── MarketPage.tsx   # 行情页（772行）
│   │   ├── StrategyPage.tsx # 策略页（463行）
│   │   ├── BacktestPage.tsx # 回测页（473行）
│   │   ├── FormulaEditor.tsx# Formula编辑器（195行）
│   │   ├── OptionsPage.tsx  # 期权页（345行）
│   │   ├── OrdersPage.tsx   # 订单页（122行）
│   │   ├── PositionsPage.tsx# 持仓页（145行）
│   │   ├── HistoryPage.tsx  # 历史数据页（364行）
│   │   └── SettingsPage.tsx # 设置页（265行）
│   │
│   ├── services/
│   │   └── api.ts           # HTTP REST API 封装
│   │
│   ├── App.tsx
│   └── main.tsx
│
├── package.json
├── tsconfig.json
└── vite.config.ts
```

### 2.2 API 调用方式（HTTP REST）

前端通过 `fetch` 调用后端 HTTP API，**不使用** Tauri IPC。

```typescript
// services/api.ts — 实际实现（HTTP REST）
const BASE = 'http://127.0.0.1:8000'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// 策略相关
export const getStrategies = () => request('/api/strategies')
export const createStrategy = (payload: any) =>
  request('/api/strategies', { method: 'POST', body: JSON.stringify(payload) })
export const updateStrategy = (id: string, payload: any) =>
  request(`/api/strategies/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
export const deleteStrategy = (id: string) =>
  request(`/api/strategies/${id}`, { method: 'DELETE' })

// 回测相关
export const runBacktest = (params: any) =>
  request('/api/backtest/run', { method: 'POST', body: JSON.stringify(params) })
export const getBacktestResult = (id: string) =>
  request(`/api/backtest/${id}`)

// 行情相关
export const getKlines = (symbol: string, timeframe: string, limit?: number) =>
  request(`/api/market/klines?symbol=${symbol}&timeframe=${timeframe}&limit=${limit ?? 500}`)
export const searchStocks = (q: string) =>
  request(`/api/market/search?q=${encodeURIComponent(q)}`)
```

### 2.3 本地设置持久化

应用设置（数据源模式、Futu 地址、刷新频率等）存储在 `localStorage`，通过 `getAppSettings` / `saveAppSettings` 读写，支持跨组件订阅（`subscribeSettings`）。

---

## 三、后端架构

### 3.1 项目结构

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口，路由注册，CORS 配置
│   ├── config.py            # 环境变量配置
│   │
│   ├── api/                 # 路由层
│   │   ├── strategies.py    # 策略 CRUD
│   │   ├── backtest.py      # 回测执行 + 结果查询
│   │   ├── market.py        # 行情查询（报价/K线/搜索）
│   │   ├── formula.py       # Formula DSL 验证/解析/转译
│   │   ├── trading.py       # 交易委托/订单/持仓/账户
│   │   ├── options.py       # 期权链/Greeks
│   │   ├── history.py       # 历史数据管理
│   │   └── runtime.py       # 策略信号实时评估
│   │
│   ├── core/                # 核心引擎
│   │   ├── engine.py        # 单标的回测引擎
│   │   ├── multi_engine.py  # 多标的回测引擎
│   │   ├── signal_engine.py # 信号评估引擎
│   │   ├── risk_engine.py   # 风控模块
│   │   ├── market_state.py  # 市场状态
│   │   ├── strategy_runtime.py # 策略运行时
│   │   └── formula/         # Formula DSL 解析器
│   │
│   ├── data/                # 数据层
│   │   ├── database.py      # SQLite 初始化 + 表结构
│   │   ├── adapter.py       # 数据源适配器接口
│   │   ├── source_router.py # 数据源路由（US/HK/CN/Mock）
│   │   ├── history_repository.py  # 历史K线读写
│   │   ├── history_backfill.py    # 自动补数逻辑
│   │   ├── history_scheduler.py   # 定时调度（每日08:00）
│   │   ├── mock.py          # Mock 数据生成
│   │   ├── trading.py       # 交易适配器
│   │   └── options.py       # 期权数据适配器
│   │
│   └── utils/
│       └── logger.py
│
├── tests/                   # 测试脚本
├── .env.example
├── requirements.txt
└── run.py
```

### 3.2 FastAPI 入口

```python
# app/main.py
app = FastAPI(title="TradeForge API", version="1.0.0")

# CORS — 仅允许本地前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 四、核心引擎

### 4.1 回测引擎（engine.py）

**设计原则**：
- 指标预计算（向量化，O(n)），避免循环内重复计算
- 正确的 crossover 检测：比较当前 bar 与前一 bar 的指标值
- 支持 `variables`（如 `vol_ratio = volume / vol_ma`）
- AND/OR 逻辑树完整评估（非短路）

**核心流程**：

```python
class BacktestEngine:
    def run(self) -> Dict:
        warmup = self._get_warmup_period()
        ind_series = self._precompute_indicators()   # 向量化预计算

        for i in range(warmup, len(self.data)):
            bar = self.data.iloc[i]
            if not self.position and self._check_conditions(entry_cond, ind_series, i):
                self._entry(bar, i)
            elif self.position and self._check_conditions(exit_cond, ind_series, i):
                self._exit(bar, i)
            elif self.position:
                self._check_risk(bar)        # 止损/止盈检测
            self._record_equity(bar)

        return self._generate_result(warmup)  # 含 buy_and_hold_return
```

**条件评估**：

| op | 说明 | 实现 |
|----|------|------|
| `cross_above` | 当前 left>right 且上一 bar left<=right | 需 prev 指标 |
| `cross_below` | 当前 left<right 且上一 bar left>=right | 需 prev 指标 |
| `>`, `<`, `>=`, `<=` | 与常数或指标比较 | 直接比较 |

### 4.2 指标计算

```python
def _precompute_indicators(self) -> Dict[str, pd.Series]:
    for ind in indicators:
        # 支持 source: 'close' | 'volume' | 'open' | 'high' | 'low'
        src = self.data[ind['source']]
        if ind['type'] == 'MA':
            series[name] = src.rolling(window=period).mean()
        elif ind['type'] == 'EMA':
            series[name] = src.ewm(span=period, adjust=False).mean()

    # 变量计算（支持 / * + - 运算）
    for var_name, expr in variables.items():
        left = series[expr['left']] or data[expr['left']]
        right = series[expr['right']] or data[expr['right']]
        series[var_name] = left / right   # 以除法为例
```

### 4.3 回测结果结构

```python
{
    "total_return": float,        # 总收益率
    "annual_return": float,       # 年化收益率（基于实际天数）
    "sharpe_ratio": float,        # 夏普比率（基于每日权益收益率）
    "max_drawdown": float,        # 最大回撤
    "win_rate": float,            # 胜率
    "profit_factor": float,       # 盈亏比
    "total_trades": int,          # 总交易次数
    "avg_holding_days": float,    # 平均持仓天数（实际计算）
    "buy_and_hold_return": float, # 买入持有基准收益率
    "equity_curve": [...],        # 资金曲线
    "trades": [...],              # 成交明细
}
```

---

## 五、数据模型

### 5.1 SQLite 表结构（实际）

见 `backend/app/data/database.py`，核心表：

```sql
-- 策略表
CREATE TABLE strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'visual',   -- visual | formula
    config TEXT NOT NULL,                  -- IR JSON
    status TEXT DEFAULT 'draft',           -- draft | ready
    version INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 回测记录
CREATE TABLE backtest_runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,                  -- 多标的用逗号分隔
    timeframe TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    fee_rate REAL NOT NULL,
    slippage REAL NOT NULL,
    status TEXT DEFAULT 'running',         -- running | completed | failed
    metrics TEXT,                          -- 完整结果 JSON
    created_at TEXT NOT NULL
);

-- 历史K线（主数据库）
CREATE TABLE kline_bars (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts TEXT NOT NULL,                      -- ISO8601 时间戳
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    source TEXT NOT NULL,                  -- yahoo | futu | mock
    PRIMARY KEY (symbol, timeframe, ts)
);
```

### 5.2 Pydantic 策略模型

```python
class ConditionRule(BaseModel):
    id: str
    type: str                   # crossover | binary
    op: str                     # cross_above | cross_below | > | < | >= | <=
    left: str                   # 指标名 或 变量名
    right: Union[str, float]    # 指标名 或 常数

class ConditionGroup(BaseModel):
    type: str                   # AND | OR
    rules: List[ConditionRule]

class Conditions(BaseModel):
    entry: ConditionGroup       # 注意：不是 List，是含 type+rules 的对象
    exit: ConditionGroup

class StrategyConfig(BaseModel):
    version: str = "1.0"
    strategy_id: str
    mode: str                   # visual | formula
    name: str
    symbols: List[str]
    timeframe: str
    source_code: Optional[str] = None   # 仅 formula 模式
    indicators: List[IndicatorConfig]
    variables: Dict[str, Any] = {}
    conditions: Conditions
    position_sizing: PositionSizing
    risk_rules: RiskRules
```

---

## 六、API 接口

### 6.1 策略

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/strategies | 列表 |
| POST | /api/strategies | 创建 |
| GET | /api/strategies/{id} | 详情 |
| PUT | /api/strategies/{id} | 更新 |
| DELETE | /api/strategies/{id} | 删除 |
| GET | /api/strategies/{id}/signal | 实时信号评估 |

### 6.2 回测

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/backtest/run | 执行回测 |
| GET | /api/backtest/{id} | 查询结果 |
| GET | /api/backtest/{id}/trades | 成交明细 |

### 6.3 行情

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/market/search | 搜索标的 |
| GET | /api/market/klines | K线数据（local-first） |
| GET | /api/market/quote | 实时报价 |
| GET | /api/market/status | 数据源状态 |

### 6.4 Formula

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/formula/validate | 语法验证 |
| POST | /api/formula/parse | 解析 AST |
| POST | /api/formula/transpile | 转译为 IR |

### 6.5 交易

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/trading/connect | 连接交易源 |
| GET | /api/trading/status | 连接状态 |
| POST | /api/trading/order | 下单 |
| GET | /api/trading/orders | 订单列表 |
| GET | /api/trading/positions | 持仓列表 |
| GET | /api/trading/account | 账户资金 |

### 6.6 期权

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/options/expirations | 到期日列表 |
| GET | /api/options/chain | 期权链 |
| POST | /api/options/payoff | 组合收益计算 |

### 6.7 历史数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/history/coverage | 数据覆盖情况 |
| POST | /api/history/backfill | 手动补数 |
| GET | /api/history/subscriptions | 订阅列表 |
| POST | /api/history/subscriptions | 添加订阅 |
| POST | /api/history/scheduler/run | 手动触发调度 |

---

## 七、部署

### 7.1 开发环境

```bash
# 后端
cd backend
cp .env.example .env
# 编辑 .env 填写 FINNHUB_API_KEY, FUTU_OPEND_HOST 等
pip install -r requirements.txt
python run.py
# 服务地址: http://127.0.0.1:8000
# API 文档: http://127.0.0.1:8000/docs

# 前端
cd frontend
npm install
npm run dev -- --host 127.0.0.1
# 前端地址: http://127.0.0.1:5173
```

### 7.2 配置项（.env）

```bash
FINNHUB_API_KEY=your_key        # 美股报价
FUTU_OPEND_HOST=127.0.0.1       # Futu OpenD 地址
FUTU_OPEND_PORT=11111           # Futu OpenD 端口
```

---

## 八、性能优化

### 8.1 回测引擎

| 优化项 | 方法 |
|--------|------|
| 指标计算 | 预计算完整序列（向量化 Pandas），O(n) |
| 多标的 | ThreadPoolExecutor 并行回测 |
| 数据加载 | local-first SQLite + 索引 |

### 8.2 前端

| 优化项 | 方法 |
|--------|------|
| K线渲染 | lightweight-charts（Canvas） |
| 状态管理 | Zustand（轻量，无 Redux 样板） |
| 打包 | Vite + Code Splitting |

---

*版本: v1.0 | 更新: 2026.03.22*
