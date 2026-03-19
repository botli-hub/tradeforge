# TradeForge

TradeForge 是一个面向多市场的量化交易工作台，当前已经打通：

- **行情**：美股 / A股 / 港股
- **策略**：可视化策略 + Formula DSL
- **信号**：实时策略信号评估 + 确认弹窗
- **交易**：Mock / Futu
- **期权**：Futu 期权链 + 收益曲线
- **历史数据**：本地 SQLite 落库 + 每天 08:00 定时更新

---

## 当前能力

### 1. 行情页
- 搜索股票
- 查看实时 quote
- 查看 K 线
- 自动显示当前 **报价源 / K线源**
- 观察池管理（关注 / 取消关注 / 快速切换）

### 2. 策略与信号
- 策略列表 / 保存 / 更新
- Formula 策略验证 / parse / transpile
- 实时信号评估
- 信号确认弹窗 → 下单

### 3. 订单 / 持仓
- 订单页
- 持仓页
- 账户资金概览

### 4. 期权页
- 固定走 **Futu**
- 到期日
- 期权链
- Greeks（Delta / Gamma / Theta / Vega / IV）
- 组合策略收益图
  - Long Call
  - Long Put
  - Bull Call Spread
  - Bear Put Spread

### 5. 历史数据系统
- 本地数据库落地历史 K 线
- local-first 读取
- 历史数据页管理
- 手动 backfill
- 订阅列表
- 每天 **08:00** 自动更新：
  - `1d`
  - `1h`
  - `30m`
  - `5m`
  - `1m`

---

## 技术栈

### 前端
- React 18
- TypeScript
- Vite
- lightweight-charts

### 后端
- FastAPI
- SQLite
- Pandas / NumPy
- futu-api

---

## 项目结构

```text
tradeforge/
├── backend/
│   ├── app/
│   │   ├── api/                 # 接口层
│   │   ├── core/                # 策略/信号/Formula/配置
│   │   ├── data/                # 行情适配器、本地历史库、调度器
│   │   └── main.py
│   ├── tests/
│   ├── .env.example
│   ├── requirements.txt
│   └── run.py
├── frontend/
│   ├── src/pages/               # 页面：行情/策略/回测/期权/历史数据
│   ├── src/services/api.ts
│   └── package.json
└── README.md
```

---

## 快速启动

### 一、后端

```bash
cd backend
cp .env.example .env
```

编辑 `backend/.env`，填写你本地需要的配置：

```bash
FINNHUB_API_KEY=your_finnhub_api_key_here
FUTU_OPEND_HOST=127.0.0.1
FUTU_OPEND_PORT=11111
```

安装依赖并启动：

```bash
pip install -r requirements.txt
python run.py
```

启动后：
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

---

### 二、前端

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

前端地址：
- <http://127.0.0.1:1420>

打包：

```bash
npm run build
```

---

## 健康守护 / 自愈重启

当你怀疑出现“端口还在，但服务已经卡死”的情况，可以直接运行：

```bash
cd /Users/alibot/.openclaw/workspace/forge/projects/tradeforge
python3 scripts/health_guard.py ensure
```

常用命令：

```bash
# 查看当前健康状态
python3 scripts/health_guard.py status

# 发现异常时自动重启前后端
python3 scripts/health_guard.py ensure

# 强制重启前后端
python3 scripts/health_guard.py restart
```

脚本会真正探活这些接口，而不是只看端口：
- `/health`
- `/api/strategies`
- `/api/history/subscriptions`

日志位置：
- `./.runtime/backend-dev.log`
- `./.runtime/frontend-dev.log`

---

## 配置原则

### 敏感配置只从本地读取
项目中**不要硬编码密钥**。

当前统一通过：
- 系统环境变量
- `backend/.env`

读取配置。

### 可提交模板
可提交文件：
- `backend/.env.example`

不可提交文件：
- `backend/.env`

---

## 数据源路由

当前数据链路如下：

### 行情 quote
- **美股** → Finnhub
- **A股 / 港股** → Futu
- **兜底** → Mock

### 历史 K 线
- **统一 local-first**
- 本地没有时自动补数：
  - **美股** → Yahoo
  - **A股 / 港股** → Futu

### 期权
- **固定走 Futu**
- 包括：
  - 到期日
  - 期权链
  - 快照
  - Greeks

### 交易
- **Mock / Futu**

---

## 历史数据模块

### local-first 逻辑
`/api/market/klines` 当前不是直接打外部源，而是：

```text
先查本地 SQLite
→ 不足则自动补数
→ 写回本地
→ 从本地返回
```

### 核心表
- `instruments`
- `kline_bars`
- `kline_sync_state`
- `kline_backfill_jobs`
- `data_subscriptions`
- `history_scheduler_runs`

### 定时任务
每天 **08:00** 自动更新所有订阅标的的：
- `1d`
- `1h`
- `30m`
- `5m`
- `1m`

可以在“历史数据”页：
- 查看订阅列表
- 手动执行调度
- 查看补数任务
- 查看 coverage

---

## 主要页面

- `📊 行情`
- `📈 策略`
- `🎯 回测`
- `🧩 期权`
- `🧾 订单`
- `💼 持仓`
- `🗄️ 历史数据`
- `⚙️ 设置`

---

## 常用后端接口

### 行情
- `GET /api/market/status`
- `GET /api/market/search`
- `GET /api/market/quote`
- `GET /api/market/klines`

### 策略 / 信号
- `GET /api/strategies`
- `POST /api/strategies`
- `GET /api/strategies/{id}/signal`
- `POST /api/formula/validate`
- `POST /api/formula/parse`
- `POST /api/formula/transpile`

### 交易
- `POST /api/trading/connect`
- `GET /api/trading/status`
- `POST /api/trading/order`
- `GET /api/trading/orders`
- `GET /api/trading/positions`
- `GET /api/trading/account`

### 期权
- `GET /api/options/expirations`
- `GET /api/options/chain`
- `POST /api/options/payoff`

### 历史数据
- `GET /api/history/coverage`
- `GET /api/history/jobs`
- `POST /api/history/backfill`
- `GET /api/history/subscriptions`
- `POST /api/history/subscriptions`
- `POST /api/history/subscriptions/{symbol}/enable`
- `GET /api/history/scheduler/status`
- `POST /api/history/scheduler/run`

---

## 当前开发状态

当前已经完成：
- 多市场行情路由
- Futu 真实期权链
- 本地历史 K 线库
- 每日定时更新
- Git 仓库初始化与首次推送

后续建议优先级：
1. 回测 / 实时信号进一步统一到本地历史库
2. 观察池 / 策略 / 历史订阅更深度联动
3. 期权页标的现价来源精修

---

## 注意

- `backend/.env` 是本地文件，不提交。
- Futu 需要本机 OpenD 运行。
- Finnhub 当前用于美股 quote；若 K 线权限不足，会由历史模块自动走 Yahoo 补数。
- 期权页固定依赖 Futu。
