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
