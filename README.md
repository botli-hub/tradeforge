# TradeForge

面向 **Wheel 策略交易员** 的本地工作台：开仓机会 → 持仓决策 → 登记台账 → Telegram 提醒。  
同时保留行情 / 策略 / 回测等研究工具（「研究」模式）。

默认本机运行，数据落本地 SQLite，**不提交 git**。

---

## 产品形态

| 模式 | 导航 | 用途 |
|------|------|------|
| **Wheel（交易）** | 今日 · 设置 | 日常管仓、扫机会、记账、推送 |
| **研究** | 行情 · 期权 · 策略 · 回测 · 股票池 · 数据 · LEAPS · 订单 · 持仓 · 2032 · 设置 | 研究与辅助 |

前端顶栏可切换模式。

---

## 核心能力（Wheel）

### 今日一页
- **必须处理**：深 ITM / 临期接货 / 止盈 / Roll 等（来自体检决策树）
- **优先开仓**：高分扫描 ∩ 触线时机，可执行过滤
- **资金**：组合占用、是否停新 Put、可选交易账户购买力
- **指派后**：HOLDING 待挂 CC、成本基础
- **事件 / 集中度**：财报与封锁日、高相关双 Put 提示
- OpenD 弱网时可用**行情缓存**（标 stale）

### 持仓决策树（量化）
对在场 CSP/CC 给出 `action_code` + 优先级 + 置信度 + 带数字的理由：

| 动作 | 含义 |
|------|------|
| `CLOSE` | 买回止盈 / 纪律否决后不硬扛 |
| `REPLACE` | 软止盈或低效 → 腾仓 |
| `ROLL` / `ROLL_ADJUST` | 展期（后者调 strike） |
| `HOLD_THETA` | 高浮盈 OTM 继续收租 |
| `PREPARE_ASSIGN` | 临期 ITM 准备接货/交货 |
| `NONE` | 观察 / 条件持有 |

默认阈值（可在设置 `wheel_position` 覆盖）包括：硬止盈 50%、软止盈 30%、过高持有 80%、吃 θ 40%/14DTE/年化 12%、硬处理窗 21DTE、临期 7DTE、深 ITM 3% 或 Δ0.5、薄垫 1.5%、资金紧利用率 75%、Put **strike ≤ 愿接价 floor** 等。

轮子列表 **平仓 / 管理** → 先开**决策弹窗**，再决定登记（与机会列表一致）。

### 机会流
- **触线时机**：期权价穿 EMA50/200
- **全池高分扫描**：年化 × 流动性 × 趋势 × 财报 × IV × POP 等
- 统一机会流：双轨对齐、档位（优先/可排/观察）、组合闸门

### 台账与执行
- 状态机：`IDLE → CSP → HOLDING → CC → CLOSED`
- 一键执行草稿（买回 / 到期 / 接货 / Roll 两腿）
- Roll 对比与登记
- 建议 vs 实操跟进率、轻量情景（平 vs 到期）

### 推送（Telegram）
- 持仓：状态指纹变化才推；冷却 / 静默 / 紧急破静默
- 机会：TopN、点差与可执行过滤、会话（默认收盘后）、组合停 Put 时不推 Put
- 设置页 **通知中心**：预览、测试、推送日志

### 组合与风控
- 组合净值、单票/组合占用上限
- 行权压力 → 暂停新 Put
- 相关矩阵与板块集中度

---

## 研究模式能力（摘要）

| 模块 | 能力 |
|------|------|
| 行情 | 搜索、quote、K 线、观察池 |
| 期权 | Futu 链、Greeks、收益图 |
| 策略 / Formula | 可视化 + DSL 校验/转译、信号评估 |
| 回测 | 策略回测；Wheel 规则轻回测 |
| 历史数据 | 本地 K 线、补数、订阅、定时调度 |
| 交易 | Mock / Futu 下单与账户（需连接） |
| LEAPS | 监控、触线、信号推送 |
| 2032 | 长期持仓计划 |

---

## 技术栈

- **前端**：React 18 · TypeScript · Vite · lightweight-charts（端口 **1420**）
- **后端**：FastAPI · SQLite · futu-api · httpx · pandas（端口 **8000**）
- **行情 / 期权**：富途 OpenD；美股 quote 可走 Finnhub；历史 K 线 local-first（Yahoo/Futu 补数）

---

## 项目结构

```text
trade/
├── backend/
│   ├── app/
│   │   ├── api/           # wheel / market / leaps / trading / ...
│   │   ├── core/          # 决策树、机会、打分、组合、推送相关逻辑
│   │   ├── data/          # SQLite、wheel_repository、历史库
│   │   ├── services/      # alert_engine、scanner、Telegram
│   │   ├── tradeforge.db  # 本地库（gitignore，勿提交）
│   │   └── main.py
│   ├── tests/
│   ├── requirements.txt
│   └── run.py
├── frontend/
│   └── src/
│       ├── pages/         # WheelPage、Settings、Market...
│       ├── components/wheel/
│       └── services/api.ts
├── scripts/health_guard.py
├── 启动后端.command / 启动前端.command   # macOS 双击启动
└── README.md
```

---

## 快速启动

### 依赖
- Python **≥ 3.10**
- Node.js（前端）
- 本机 **富途 OpenD**（期权链、部分行情、体检；默认 `127.0.0.1:11111`）

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

- API：http://127.0.0.1:8000  
- 文档：http://127.0.0.1:8000/docs  
- 健康：http://127.0.0.1:8000/health  

macOS 也可双击仓库根目录 **`启动后端.command`**。

### 前端

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

- 页面：http://127.0.0.1:1420  

或双击 **`启动前端.command`**。

### 建议首次配置顺序

1. 设置 → **通用**：OpenD Host/Port、Telegram（可选代理）  
2. 设置 → **Wheel**：组合净值、标的愿接价在 Wheel→标的页维护  
3. 通知中心：管仓轮询分钟数、机会推送策略；点「测试连通」  
4. 今日页：扫机会 / 处理待办  

配置默认写在 **本地数据库**（设置页保存即生效），不是靠提交仓库里的密钥。

---

## 数据存储

| 内容 | 位置 | 是否进 git |
|------|------|------------|
| 业务主库 | `backend/app/tradeforge.db` | **否**（`*.db` ignore） |
| 密钥/配置覆盖 | 库内 `app_kv` + 可选 `backend/.env` | `.env` **否** |
| 前端偏好 / 待登记队列 | 浏览器 localStorage | 否 |
| 实时行情 | OpenD / 外部 API | 不落主业务逻辑密钥 |

换机请自行备份 **`tradeforge.db`**。

库内主要包括：Wheel 标的/轮子/交易、配置、推送日志、建议快照、LEAPS、K 线历史等。

---

## 配置原则

1. **密钥不进仓库**：Telegram Token、Finnhub Key 等只存在本机库或 `.env`  
2. **业务参数**：设置页 `wheel_position` / `wheel_scan` / `wheel_alerts` / `wheel_portfolio`  
3. **标的级**：愿接价 floor、min_annualized、max_capital、delta/DTE 区间  

---

## 常用 API（Wheel）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/wheel/today` | 今日聚合看板 |
| GET | `/api/wheel/open-positions/check` | 在场体检 + 决策 |
| GET | `/api/wheel/opportunities` | 统一机会流 |
| GET | `/api/wheel/scan` | 全池高分扫描 |
| POST | `/api/wheel/execute` | 一键记账草稿 |
| POST | `/api/wheel/roll/register` | Roll 两腿登记 |
| GET | `/api/wheel/alerts/log` | 推送日志 |
| POST | `/api/wheel/alerts/push` | 手动推持仓 |
| GET | `/api/config/backend` | 读生效配置 |
| PUT | `/api/config/backend` | 写配置 |

完整列表见 http://127.0.0.1:8000/docs 。

---

## 测试

```bash
cd backend
source .venv/bin/activate
# 无 pytest 时可直接跑：
python -c "import tests.test_wheel_decision as t; [getattr(t,n)() for n in dir(t) if n.startswith('test_')]"
```

主要单测：`tests/test_wheel_decision.py` · `test_alert_engine.py` · `test_wheel_trader_flow.py` · `test_wheel_score.py`。

---

## 健康守护（可选）

```bash
python3 scripts/health_guard.py status
python3 scripts/health_guard.py ensure   # 异常时重启前后端
```

会探活 `/health` 等，而非只看端口。

---

## 注意

- 期权与在场体检强依赖 **OpenD 已登录运行**  
- 中国大陆访问 Telegram 需在设置中填本地代理  
- 本项目是**决策与台账工具**，默认不自动向券商下单；实盘下单需自行连接交易并确认  
- 旧版 PRD/TECH 文档可能滞后，**以本 README 与代码为准**

---

## 许可

见 [LICENSE](./LICENSE)。
