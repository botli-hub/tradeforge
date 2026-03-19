# TradeForge 技术文档 v0.6

---

## 一、系统架构

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        TradeForge 系统                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────┐     ┌──────────────────────┐        │
│  │      前端 (Tauri)     │     │    后端 (Python)     │        │
│  │                      │     │                      │        │
│  │  React + TypeScript │◀───▶│  FastAPI + SQLite   │        │
│  │  Zustand            │     │  Backtest Engine    │        │
│  │  lightweight-charts │     │  Mock Data Generator│        │
│  │  react-flow         │     │                      │        │
│  └──────────────────────┘     └──────────────────────┘        │
│                  │                        │                     │
│                  │    IPC (Tauri)         │                     │
│                  └────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 桌面框架 | Tauri | 2.0 |
| 前端框架 | React | 18.x |
| 语言 | TypeScript | 5.x |
| 状态管理 | Zustand | 4.x |
| 图表 | lightweight-charts | 4.x |
| 可视化编辑器 | react-flow | 11.x |
| 后端框架 | FastAPI | 0.109.x |
| 数据存储 | SQLite | 3.x |
| 回测引擎 | Python | 3.10+ |
| 计算 | NumPy/Numba | - |

---

## 二、前端架构

### 2.1 项目结构

```
frontend/
├── src/
│   ├── components/          # UI 组件
│   │   ├── Chart/          # K线图表组件
│   │   ├── Strategy/        # 策略编辑器组件
│   │   ├── Backtest/       # 回测组件
│   │   └── Common/         # 通用组件
│   │
│   ├── stores/             # Zustand 状态管理
│   │   ├── appStore.ts     # 全局状态
│   │   ├── strategyStore.ts # 策略状态
│   │   ├── backtestStore.ts # 回测状态
│   │   └── marketStore.ts  # 行情状态
│   │
│   ├── pages/              # 页面组件
│   │   ├── MarketPage.tsx
│   │   ├── StrategyPage.tsx
│   │   ├── BacktestPage.tsx
│   │   └── SettingsPage.tsx
│   │
│   ├── services/            # API 服务
│   │   ├── api.ts          # Tauri IPC 调用
│   │   └── types.ts        # TypeScript 类型
│   │
│   ├── utils/              # 工具函数
│   │   ├── indicators.ts   # 指标计算
│   │   └── formatters.ts  # 数据格式化
│   │
│   ├── App.tsx             # 根组件
│   └── main.tsx           # 入口
│
├── package.json
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.js     # 可选
```

### 2.2 状态管理 (Zustand)

```typescript
// stores/appStore.ts
import { create } from 'zustand';

interface AppState {
  theme: 'dark' | 'light';
  language: 'zh' | 'en';
  isLoading: boolean;
  toast: { message: string; type: 'success' | 'error' } | null;
  
  setTheme: (theme: 'dark' | 'light') => void;
  setLoading: (loading: boolean) => void;
  showToast: (message: string, type: 'success' | 'error') => void;
}

export const useAppStore = create<AppState>((set) => ({
  theme: 'dark',
  language: 'zh',
  isLoading: false,
  toast: null,
  
  setTheme: (theme) => set({ theme }),
  setLoading: (isLoading) => set({ isLoading }),
  showToast: (message, type) => set({ toast: { message, type } }),
}));
```

### 2.3 Tauri IPC 通信

```typescript
// services/api.ts
import { invoke } from '@tauri-apps/api/core';

// 策略相关
export const createStrategy = (config: StrategyConfig) => 
  invoke('create_strategy', { config });

export const updateStrategy = (id: string, config: StrategyConfig) => 
  invoke('update_strategy', { id, config });

export const deleteStrategy = (id: string) => 
  invoke('delete_strategy', { id });

export const getStrategies = () => 
  invoke('get_strategies');

// 回测相关
export const runBacktest = (params: BacktestParams) => 
  invoke('run_backtest', { params });

export const getBacktestResult = (id: string) => 
  invoke('get_backtest_result', { id });

// 行情相关
export const getKlines = (symbol: string, timeframe: string, limit: number) => 
  invoke('get_klines', { symbol, timeframe, limit });

export const searchSymbols = (keyword: string) => 
  invoke('search_symbols', { keyword });
```

---

## 三、后端架构

### 3.1 项目结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置
│   │
│   ├── api/                 # API 路由
│   │   ├── __init__.py
│   │   ├── strategies.py    # 策略 API
│   │   ├── backtest.py     # 回测 API
│   │   └── market.py       # 行情 API
│   │
│   ├── core/                # 核心引擎
│   │   ├── __init__.py
│   │   ├── engine.py       # 回测引擎
│   │   ├── ir_parser.py    # IR 解析器
│   │   ├── indicators.py   # 指标计算
│   │   └── risk.py         # 风控模块
│   │
│   ├── models/              # 数据模型
│   │   ├── __init__.py
│   │   ├── strategy.py     # 策略模型
│   │   ├── backtest.py     # 回测模型
│   │   └── trade.py        # 成交模型
│   │
│   ├── services/            # 业务服务
│   │   ├── strategy_service.py
│   │   ├── backtest_service.py
│   │   └── market_service.py
│   │
│   ├── data/               # 数据层
│   │   ├── __init__.py
│   │   ├── database.py     # SQLite 连接
│   │   ├── mock.py         # Mock 数据生成
│   │   └── repository.py   # 数据仓库
│   │
│   └── utils/               # 工具
│       ├── __init__.py
│       └── logger.py       # 日志
│
├── requirements.txt
└── run.py                  # 启动脚本
```

### 3.2 FastAPI 入口

```python
# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import strategies, backtest, market

app = FastAPI(title="TradeForge API", version="0.6.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(market.router, prefix="/api/market", tags=["market"])

@app.get("/health")
async def health():
    return {"status": "ok"}
```

---

## 四、核心引擎

### 4.1 回测引擎

```python
# app/core/engine.py
from typing import List, Dict, Optional
from datetime import datetime
import numpy as np

class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, ir_config: dict, data: pd.DataFrame):
        self.config = ir_config
        self.data = data
        self.positions = []  # 持仓
        self.trades = []    # 成交记录
        self.equity_curve = []  # 资金曲线
        
    def run(self) -> BacktestResult:
        """执行回测"""
        # 预热期
        warmup = self.config.get('data_requirements', {}).get('warmup_period', 50)
        
        for i in range(warmup, len(self.data)):
            bar = self.data.iloc[i]
            
            # 更新指标
            self._update_indicators(bar, i)
            
            # 检查入场
            if not self.positions and self._check_entry(bar):
                self._entry(bar)
            
            # 检查出场
            elif self.positions and self._check_exit(bar):
                self._exit(bar)
            
            # 检查风控
            self._check_risk(bar)
            
            # 记录资金
            self._record_equity(bar)
        
        return self._generate_result()
    
    def _check_entry(self, bar) -> bool:
        """检查入场条件"""
        # 实现 IR 中的 entry 条件
        pass
    
    def _check_exit(self, bar) -> bool:
        """检查出场条件"""
        # 实现 IR 中的 exit 条件
        pass
```

### 4.2 指标计算

```python
# app/core/indicators.py
import pandas as pd
import numpy as np

def ma(series: pd.Series, period: int) -> pd.Series:
    """移动平均"""
    return series.rolling(window=period).mean()

def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI 指标"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2):
    """布林带"""
    ma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = ma + (std * std_dev)
    lower = ma - (std * std_dev)
    return upper, ma, lower

def cross_above(series1: pd.Series, series2: pd.Series) -> bool:
    """上穿"""
    return series1.iloc[-1] > series2.iloc[-1] and \
           series1.iloc[-2] <= series2.iloc[-2]

def cross_below(series1: pd.Series, series2: pd.Series) -> bool:
    """下穿"""
    return series1.iloc[-1] < series2.iloc[-1] and \
           series1.iloc[-2] >= series2.iloc[-2]
```

---

## 五、数据模型

### 5.1 SQLite 表结构

```sql
-- 策略表
CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'visual',
    config TEXT NOT NULL,  -- JSON (IR)
    status TEXT DEFAULT 'draft',
    version INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 策略版本表
CREATE TABLE IF NOT EXISTS strategy_versions (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

-- 回测记录表
CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    fee_rate REAL NOT NULL,
    slippage REAL NOT NULL,
    status TEXT DEFAULT 'running',
    metrics TEXT,  -- JSON
    created_at TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

-- 成交记录表
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    backtest_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,  -- long/short
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_time TEXT,
    exit_price REAL,
    quantity REAL NOT NULL,
    pnl REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (backtest_id) REFERENCES backtest_runs(id)
);

-- K线缓存表
CREATE TABLE IF NOT EXISTS bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    UNIQUE(symbol, timeframe, timestamp)
);

CREATE INDEX idx_bars_symbol_time ON bars(symbol, timeframe, timestamp);
```

### 5.2 Pydantic 模型

```python
# app/models/strategy.py
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class IndicatorConfig(BaseModel):
    name: str
    type: str  # MA, EMA, RSI, etc.
    period: int

class ConditionRule(BaseModel):
    id: str
    indicator: Optional[str] = None
    type: Optional[str] = None  # cross_above, cross_below, etc.
    op: Optional[str] = None  # >, <, etc.
    ref: Optional[str] = None
    value: Optional[float] = None

class Conditions(BaseModel):
    entry: List[ConditionRule]
    exit: List[ConditionRule]

class PositionSizing(BaseModel):
    type: str  # fixed_amount, percent
    value: float

class RiskRules(BaseModel):
    initial_capital: float = 100000
    fee_rate: float = 0.0003
    slippage: float = 0.001
    max_position_pct: float = 0.5
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

class StrategyConfig(BaseModel):
    version: str = "1.0"
    strategy_id: str
    name: str
    symbols: List[str]
    timeframe: str
    indicators: List[IndicatorConfig]
    conditions: Conditions
    position_sizing: PositionSizing
    risk_rules: RiskRules

class Strategy(BaseModel):
    id: str
    name: str
    mode: str = "visual"
    config: StrategyConfig
    status: str = "draft"
    version: int = 1
    created_at: datetime
    updated_at: datetime
```

---

## 六、API 接口

### 6.1 策略 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/strategies | 创建策略 |
| GET | /api/strategies | 获取策略列表 |
| GET | /api/strategies/{id} | 获取策略详情 |
| PUT | /api/strategies/{id} | 更新策略 |
| DELETE | /api/strategies/{id} | 删除策略 |
| POST | /api/strategies/{id}/versions | 创建版本 |

### 6.2 回测 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/backtest/run | 执行回测 |
| GET | /api/backtest/{id} | 获取回测结果 |
| GET | /api/backtest/{id}/trades | 获取成交明细 |

### 6.3 行情 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/market/search | 搜索股票 |
| GET | /api/market/klines | 获取K线数据 |
| GET | /api/market/quote | 获取实时报价 |

---

## 七、IR (中间表示) 格式

```json
{
  "version": "1.0",
  "strategy_id": "strat_001",
  "name": "MA双均线策略",
  "symbols": ["AAPL"],
  "timeframe": "1d",
  "indicators": [
    {"name": "ma_fast", "type": "MA", "period": 20},
    {"name": "ma_slow", "type": "MA", "period": 50}
  ],
  "conditions": {
    "entry": {
      "type": "AND",
      "rules": [
        {
          "id": "c1",
          "indicator": "ma_fast",
          "op": "cross_above",
          "ref": "ma_slow"
        }
      ]
    },
    "exit": {
      "type": "OR",
      "rules": [
        {
          "id": "e1",
          "indicator": "ma_fast",
          "op": "cross_below",
          "ref": "ma_slow"
        },
        {
          "id": "e2",
          "type": "stop_loss",
          "value": 0.05
        },
        {
          "id": "e3",
          "type": "take_profit",
          "value": 0.15
        }
      ]
    }
  },
  "position_sizing": {
    "type": "fixed_amount",
    "value": 10000
  },
  "risk_rules": {
    "initial_capital": 100000,
    "fee_rate": 0.0003,
    "slippage": 0.001,
    "max_position_pct": 0.5
  },
  "data_requirements": {
    "warmup_period": 50,
    "timeframes": ["1d"]
  }
}
```

---

## 八、部署

### 8.1 开发环境

```bash
# 前端
cd frontend
npm install
npm run dev

# 后端
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### 8.2 生产构建

```bash
# 构建 Tauri 应用
cd frontend
npm run tauri build
```

---

## 九、性能优化

### 9.1 回测性能

| 优化项 | 方法 |
|--------|------|
| 指标计算 | NumPy 向量化 |
| 密集计算 | Numba JIT 编译 |
| 数据加载 | SQLite 索引 + 缓存 |
| 多标的 | 并行处理 |

### 9.2 前端性能

| 优化项 | 方法 |
|--------|------|
| K线渲染 | lightweight-charts (Canvas) |
| 状态管理 | Zustand (轻量) |
| 打包 | Vite + Code Splitting |

---

*版本: v0.6 | 2026.03.18*
