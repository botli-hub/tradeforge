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


def _cfg_num(cfg: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def merge_pos_quant(pos_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """合并量化默认与用户配置。"""
    out = dict(POSITION_QUANT)
    if pos_cfg:
        for k, v in pos_cfg.items():
            if k in out or k in (
                "profit_target_pct", "soft_profit_pct", "hard_roll_dte",
                "gamma_warn_dte", "hold_theta_min_profit_pct", "hold_theta_max_dte",
                "hold_theta_min_remaining_ann", "thin_otm_buffer_pct",
                "max_hold_profit_pct", "min_close_notional", "shallow_itm_pct",
                "deep_itm_moneyness_pct", "capital_tight_util_pct",
                "dividend_warn_days", "threat_otm_buffer_pct",
                "deep_itm_delta", "shallow_itm_delta_max",
                "early_assign_delta_deep", "early_assign_delta_div",
                "early_assign_delta_shallow_div", "early_assign_delta_otm_div",
                "open_caution_buffer_pct",
            ):
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


def remaining_annualized(close_px: float, strike: float, dte: Optional[int]) -> Optional[float]:
    if not strike or not dte or dte <= 0 or close_px <= 0:
        return None
    return round(close_px / strike * 365 / dte * 100, 2)


def residual_floor(min_annualized: float, pos_cfg: Optional[Dict[str, Any]] = None) -> float:
    """剩余年化「仍值得拿」的下限。"""
    q = merge_pos_quant(pos_cfg)
    floor = float(q["hold_theta_min_remaining_ann"])
    if min_annualized and min_annualized > 0:
        floor = min(floor, max(8.0, min_annualized * 0.5))
    return floor
