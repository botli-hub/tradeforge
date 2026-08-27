"""Wheel 持仓动态决策树(量化阈值版)

所有形容词决策映射为数字阈值(见 POSITION_QUANT)。
输出 action_code + priority + confidence + reasons(含数字) + quant_thresholds。

优先级序(命中即停):
  1 深ITM/临期ITM → 2 提前行权 → 3 吃θ → 4 硬止盈/过高持有
  → 5 软止盈+低效 → 6 ≤21DTE处理 → 7 纯低效 → 8 超愿接/纪律否决
  → 9 浮亏OTM → 10 浅ITM观察 → 11 健康持有
"""
from typing import Any, Dict, List, Optional, Tuple

# action_code 枚举(前端建卡/按钮用,少做字符串匹配)
ACTION_CLOSE = "CLOSE"
ACTION_ROLL = "ROLL"
ACTION_ROLL_ADJUST = "ROLL_ADJUST"
ACTION_HOLD_THETA = "HOLD_THETA"
ACTION_REPLACE = "REPLACE"
ACTION_PREPARE_ASSIGN = "PREPARE_ASSIGN"
ACTION_NONE = "NONE"

STANCE_INCOME = "income"
STANCE_ACQUIRE = "acquire"


def normalize_stance(raw: Any) -> str:
    """标的立场: income=只收租 / acquire=允许接货. 缺省 acquire(兼容旧标的)."""
    s = str(raw or "").strip().lower()
    if s in ("income", "只收租", "rent", "premium"):
        return STANCE_INCOME
    return STANCE_ACQUIRE

# ── 量化默认(设置页 wheel_position 可覆盖) ──────────────────────────────────
# 单位: 百分比用「点」(50=50%), DTE/美元/小数 delta 见注释
POSITION_QUANT: Dict[str, float] = {
    "profit_target_pct": 50.0,          # 硬止盈
    "soft_profit_pct": 30.0,            # 软止盈
    "max_hold_profit_pct": 80.0,        # 过高持有 → 落袋
    "hold_theta_min_profit_pct": 40.0,  # 吃θ最低浮盈
    "hold_theta_max_dte": 14.0,         # 吃θ最长 DTE(配合剩余年化)
    "hold_theta_min_remaining_ann": 12.0,
    "hard_roll_dte": 21.0,              # 硬处理窗
    "gamma_warn_dte": 7.0,              # 临期/gamma
    "shallow_itm_pct": 1.5,             # 浅 ITM 价内%
    "deep_itm_moneyness_pct": 3.0,      # 深 ITM 价内%
    "deep_itm_delta": 0.50,             # 深 ITM Δ
    "shallow_itm_delta_max": 0.55,
    "thin_otm_buffer_pct": 1.5,         # 薄 OTM 垫%
    "threat_otm_buffer_pct": 5.0,       # 浮亏威胁垫% (≤hard_roll 内)
    "min_close_notional": 20.0,         # 买回名义过低 → 手续费陷阱 $
    "capital_tight_util_pct": 75.0,     # 资金紧利用率%
    "dividend_warn_days": 14.0,
    # CC 提前行权 Δ 门槛
    "early_assign_delta_deep": 0.80,
    "early_assign_delta_div": 0.55,
    "early_assign_delta_shallow_div": 0.45,
    "early_assign_delta_otm_div": 0.70,
    # would_open 薄垫 caution
    "open_caution_buffer_pct": 2.0,
}
