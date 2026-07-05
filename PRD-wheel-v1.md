# TradeForge Wheel 模块设计 v1.0

> 产品重心转向:以期权 Wheel(车轮)策略为核心工作流,现有模块保留但降级为辅助。
> 下单方式:系统给信号与建议,用户在富途 App 手动下单,系统内登记跟踪。

---

## 一、为什么现有系统撑不起 Wheel

Wheel 是一个循环状态机,不是单次信号:

```
[空仓] --卖现金担保Put--> [CSP持仓] --到期作废--> 收权利金,回[空仓]
                              |
                          被行权接货
                              v
[持股] --卖Covered Call--> [CC持仓] --到期作废--> 收权利金,回[持股]
                              |
                          被行权交货
                              v
                    [一轮结束,结算收益,回空仓]
```

现有系统只有 LEAPS 监控覆盖"卖 Put 信号"一环,缺失:
1. **状态跟踪**——每个标的处在轮子哪个阶段,当前挂着什么合约
2. **权利金台账**——每笔卖出/平仓/被行权的记录
3. **成本基础(Cost Basis)**——接货成本 − 累计净权利金,这是卖 Call 定 strike 的锚
4. **到期/Roll 管理**——临期合约提醒、ITM 风险、滚仓对比

---

## 二、核心概念与数据模型

### 2.1 Wheel 标的 (wheel_targets)

与股票池(美股/港股)打通,复用 LEAPS 白名单的候选机制。

| 字段 | 说明 |
|------|------|
| symbol / name / market | 标的,来自股票池或手动输入 |
| floor_price | 接货底线价(愿意接货的最高成本) |
| max_capital | 该标的最大占用资金(现金担保上限) |
| delta_min / delta_max | 卖方 delta 区间,默认 0.15 ~ 0.30 |
| dte_min / dte_max | 到期天数区间,默认 21 ~ 45 |
| min_annualized | 最低年化收益率要求,默认 15% |
| enabled | 是否启用 |

### 2.2 Wheel 周期 (wheel_cycles)

一轮完整循环 = 一条 cycle 记录。

| 字段 | 说明 |
|------|------|
| id / symbol | |
| status | IDLE / CSP_OPEN / HOLDING / CC_OPEN / CLOSED |
| shares / share_cost | 接货后的股数与每股成本 |
| total_premium | 本轮累计净权利金(卖出收入 − 买回支出) |
| realized_pnl | 本轮平仓总盈亏(权利金 + 股票价差) |
| started_at / closed_at | |

**派生指标**(计算,不存储):
- `cost_basis = share_cost − 累计净权利金/股` —— 真实持股成本
- `breakeven` —— 盈亏平衡价
- 本轮年化 = realized_pnl / 占用资金 / 天数 × 365

### 2.3 交易腿 (wheel_trades)

| 字段 | 说明 |
|------|------|
| cycle_id | 所属周期 |
| type | SELL_PUT / BUY_PUT_CLOSE / SELL_CALL / BUY_CALL_CLOSE / ASSIGNED(被行权接货) / CALLED_AWAY(被行权交货) / ROLL |
| contract_code / strike / expiry | 合约信息 |
| qty / price / fee | 数量、成交价、手续费 |
| traded_at / note | |

状态机流转由登记的 trade 类型驱动,例如登记 ASSIGNED 自动把 cycle 从 CSP_OPEN 转为 HOLDING 并写入 shares/share_cost。

---

## 三、页面设计(WheelPage,导航第一位,默认首页)

### 3.1 总览区(顶部卡片)

| 卡片 | 内容 |
|------|------|
| 活跃轮子 | 进行中的 cycle 数 / 标的数 |
| 本月权利金 | 当月净权利金收入 |
| 累计权利金 | 历史总净收入 |
| 组合年化 | 已结束 cycle 的加权年化 |
| ⚠ 待处理 | DTE ≤ 7 或已 ITM 的合约数,点击跳转 Roll 管理 |

### 3.2 标的看板(核心视图)

每个启用标的一张卡片:
- **轮子状态图**:环形四阶段(空仓→CSP→持股→CC),高亮当前阶段
- 当前合约:代码 / strike / 到期日 / DTE / 开仓价 vs 现价 / 浮盈比例 / ITM 标红
- cost basis、breakeven、本轮已收权利金
- **下一步建议按钮**:空仓→"找 Put"、持股→"找 Call"、临期→"看 Roll"

### 3.3 卖 Put 助手

选标的 → 拉富途期权链(复用现有 /api/options/chain)→ 按规则筛选打分:

- 硬性过滤:delta ∈ 目标区间、DTE ∈ 目标区间、strike ≤ floor_price、OI ≥ 100、有买价
- 打分排序:年化收益率(权利金/担保金×365/DTE)为主,IV rank 加成(复用 LEAPS 的 IV 历史)
- 每行显示:strike / delta / DTE / 权利金 / 年化 / 接货成本 / 距现价%
- 行动:「已在富途下单」→ 弹窗填成交价和手续费 → 登记 SELL_PUT,cycle 进入 CSP_OPEN

### 3.4 卖 Call 助手

持股状态下使用,同上结构,差异:
- 硬性过滤:strike ≥ cost_basis(保证被 call 走不亏),delta ∈ 区间
- 显示"若被行权总收益"= (strike − cost_basis) × 股数 + 本次权利金

### 3.5 到期 / Roll 管理

列出所有 DTE ≤ 7 或 ITM 的在场合约:
- 每条给三个选项的对比数据:**放任到期** / **买回平仓**(当前买回成本) / **Roll**(同 delta 下一到期日合约,net credit 计算)
- Roll 登记为 BUY_*_CLOSE + SELL_* 两条腿,留在同一 cycle

### 3.6 台账

- trades 全列表(按时间倒序,可按标的筛选),CSV 导出
- cycles 汇总表:每轮起止、权利金、盈亏、年化

---

## 四、后端 API 设计

```
GET/POST/PUT/DELETE  /api/wheel/targets          标的 CRUD(候选来自股票池,复用 leaps candidates 模式)
GET                  /api/wheel/cycles            周期列表(含派生指标)
POST                 /api/wheel/trades            登记交易腿(驱动状态机)
GET                  /api/wheel/trades            台账查询
GET                  /api/wheel/suggest/put       卖Put建议(期权链+筛选打分)
GET                  /api/wheel/suggest/call      卖Call建议
GET                  /api/wheel/expiring          临期/ITM 合约 + Roll 对比
GET                  /api/wheel/stats             总览统计
```

复用现有资产:期权链(options.py)、IV rank(leaps_repository)、Telegram 推送(notifier.py)、股票池候选(leaps candidates)。

---

## 五、与现有模块的关系

| 模块 | 处理 |
|------|------|
| Wheel(新) | 导航第一位,默认首页 |
| LEAPS 监控 | 保留,定位为"长周期卖 Put 择时信号",与 Wheel 互补 |
| 期权页 | 保留,作为通用期权链查看工具 |
| 2032Plan / 行情 / 股票池 | 保留不动 |
| 策略 / 回测 / 数据 | 保留,导航后移 |

---

## 六、分期实施

| 阶段 | 内容 | 工作量估计 |
|------|------|-----------|
| **Phase 1** | 数据模型 + 状态机 + 台账登记 + 标的看板 + 卖Put/Call助手 + 导航调整 | 核心,先做 |
| **Phase 2** | 到期/Roll 管理 + Telegram 临期推送 + 总览统计完善 | 其次 |
| **Phase 3** | 富途期权持仓自动对账、Wheel 历史回测 | 后续迭代 |

---

## 七、说明

- delta/DTE/年化等默认参数是 Wheel 社区常见做法,均可在标的级别配置;本工具只做信息整理与记录,不构成投资建议,下单决策始终由用户做出。
- 港股期权同样支持(复用现有 HK 符号转换),但注意港股期权流动性筛选阈值可能需要放宽,OI 阈值做成可配置。
