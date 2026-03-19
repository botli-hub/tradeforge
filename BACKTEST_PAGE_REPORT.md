# BACKTEST_PAGE_REPORT

## 改动摘要
- 重构 `frontend/src/pages/BacktestPage.tsx`，补齐可编辑回测表单、状态机、结果展示、资金曲线、交易明细、错误/空状态。
- 重写 `backend/app/api/backtest.py` 回测链路，支持前端单标的输入自动转换为 `symbols`，并接入 TradeForge 现有 local-first 历史数据体系。
- 新增 `backend/tests/test_backtest_api.py`，覆盖单标的回测请求兼容与 mock 兜底逻辑。
- 修复 `frontend/src/pages/MarketPage.tsx` 的 TypeScript 构建错误，确保前端 build 可通过。

## 修复的问题
1. `BacktestPage` 的 `startDate/endDate` 原来是常量，现已改为 state，可编辑。
2. 时间输入框原来无 `onChange`，现已支持单独编辑 `start_date` / `end_date`。
3. 前端原来发送 `symbol`，后端要求 `symbols`；现前端统一发送 `symbols: [symbol]`，后端也兼容旧的 `symbol` 入参。
4. 回测结果链路原来只拿 `metrics`，前端看不到完整结果；现后端统一返回：
   - `metrics`
   - `trades`
   - `equity_curve`
   - `data_sources`
   - `completed_at`
5. 后端原来偏 mock；现改为：
   - 优先 local-first 历史 K 线
   - 本地不足时自动按现有路由补数
   - 美股默认 Yahoo / local-first
   - 港股 / A股默认 Futu / local-first
   - 若真实数据失败则回退 mock，并明确标注 `data_source=mock`
6. 页面新增 `running / error / empty / success` 四种状态，失败不再 silent fail。
7. 修复前端 TS 构建问题，`npm run build` 已通过。

## 测试案例
### 1. 前端构建
- 命令：`npm run build`
- 结果：通过

### 2. 后端单元测试
- 命令：`source backend/.venv/bin/activate && PYTHONPATH=backend python3 -m unittest backend/tests/test_backtest_api.py`
- 结果：2/2 通过

### 3. AAPL 回测联调
- 策略：`Formula Signal Test`
- 标的：`AAPL`
- 周期：`1d`
- 区间：`2024-01-01 ~ 2025-01-01`
- 数据来源：`yahoo`（实际落本地后读取，`load_mode=local`）
- 结果：
  - 总收益率：`-0.18%`
  - 最大回撤：`1.43%`
  - 交易次数：`101`
  - 资金曲线：有
  - 交易明细：有

### 4. 港股回测联调
- 标的：`00700.HK`
- 数据来源：`futu`（`load_mode=local`）
- 结果：
  - 总收益率：`0.77%`
  - 最大回撤：`0.31%`
  - 交易次数：`11`
  - 资金曲线：有
  - 交易明细：有

### 5. A股回测联调
- 标的：`600519.SH`
- 数据来源：`futu`（`load_mode=local`）
- 结果：
  - 总收益率：`-0.50%`
  - 最大回撤：`0.54%`
  - 交易次数：`15`
  - 资金曲线：有
  - 交易明细：有

### 6. 页面可用性
- 页面地址打开正常：`http://127.0.0.1:5173/`
- 回测页可进入，空状态正常显示
- 运行后成功展示：指标卡片 / 资金曲线 / 数据来源 / 交易明细
- 不再使用只读拼接时间输入框

## 测试结果
- 前后端联调通过
- AAPL / 00700.HK / 600519.SH 三个样例均可成功回测
- 回测结果展示链路完整可见
- 回测接口可以稳定返回可渲染结果

## 已知限制
- 当前回测引擎策略逻辑仍偏 MVP，`BacktestEngine` 内部信号判断较简化，后续可继续增强真实条件计算。
- 前端资金曲线为轻量 SVG 线图，已可用，但不是专业交互式图表。
- 如果外部数据源不可用，后端会回退 mock；当前页面会明确显示数据来源，不会假装是真实行情。

## 访问地址
- 前端：`http://127.0.0.1:5173/`
- 后端：`http://127.0.0.1:8000/`
- 回测接口：`POST /api/backtest/run`
