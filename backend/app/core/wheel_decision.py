"""Wheel 持仓动态决策树(量化阈值版).

核心树在 wheel_decision_tree(默认允许接货);
本模块叠加只收租偏离守卫与浮盈两本账.
"""
from app.core.wheel_decision_lib import *  # noqa: F401,F403
from app.core.wheel_decision_tree import format_alert_line  # noqa: F401
from app.core.wheel_decision_tree import decide_position as _decide_position_core
from app.core.wheel_stance import decorate


def decide_position(item, min_annualized, profit_target, pos_cfg=None):
    result = _decide_position_core(item, min_annualized, profit_target, pos_cfg)
    return decorate(result, item, pos_cfg)
