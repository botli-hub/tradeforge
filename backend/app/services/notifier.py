"""Telegram 通知服务"""
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHECKLIST = (
    "□ 近 2 周无财报/重大事件\n"
    "□ 保证金占用 < 组合上限\n"
    "□ 本标的未平仓卖 put ≤ 2 笔\n"
    "□ 接货后仓位不超配置上限"
)


def format_leaps_signal(signal: Any) -> str:
    """将 LeapsSignal 格式化为 Telegram 消息"""
    from app.core.leaps_monitor import LeapsSignal, LeapsSuggestion

    level_label = "二级信号" if signal.signal_level == "SECONDARY" else "一级信号"
    level_icon = "🔔🔔" if signal.signal_level == "SECONDARY" else "🔔"
    intraday_tag = "（盘中）" if signal.is_intraday else ""

    # 合约展示名：expiry + strike + P
    expiry_display = "20" + signal.expiry if len(signal.expiry) == 6 else signal.expiry
    contract_label = f"{signal.symbol} {expiry_display} {int(signal.strike)}P"

    lines = [
        f"{level_icon} [{level_label}]{intraday_tag} {contract_label}",
        f"触发：价格 {signal.trigger_price:.1f} 上穿 {signal.ema_type}（{signal.ema_value:.1f}）",
        f"IV Rank：{signal.iv_rank:.0f} / 100（52周）",
        f"标的：{signal.symbol} ${signal.underlying_price}"
        + (f"（接货底线 ${signal.floor_price} ✓）" if signal.underlying_price > signal.floor_price else f"（接货底线 ${signal.floor_price} ✗）"),
    ]

    if signal.suggestions:
        lines.append("")
        lines.append("📋 建议交易（卖出虚值 put，delta 0.20~0.30）：")
        for s in signal.suggestions:
            lines.append(
                f"  {int(s.strike)}P  权利金 ${s.premium:.1f}"
                f"  年化 ~{s.annualized_yield:.0f}%"
                f"  接货成本 ${s.cost_basis:.1f}"
            )

    lines.append("")
    lines.append("✅ 复核清单：")
    lines.append(_CHECKLIST)

    return "\n".join(lines)


def format_leaps_signal_from_dict(signal: Dict[str, Any]) -> str:
    """从数据库 dict 格式化（供 API 调用）"""
    level_label = "二级信号" if signal.get("signal_level") == "SECONDARY" else "一级信号"
    level_icon = "🔔🔔" if signal.get("signal_level") == "SECONDARY" else "🔔"

    code = signal.get("contract_code", "")
    expiry = signal.get("expiry", "")
    strike = signal.get("strike", 0)
    symbol = signal.get("symbol", "")
    expiry_display = "20" + expiry if len(expiry) == 6 else expiry
    contract_label = f"{symbol} {expiry_display} {int(strike)}P" if expiry and strike else code

    lines = [
        f"{level_icon} [{level_label}] {contract_label}",
        f"触发：价格 {signal.get('trigger_price', 0):.1f} 上穿 {signal.get('ema_type', '')}（{signal.get('ema_value', 0):.1f}）",
        f"IV Rank：{signal.get('iv_rank', 0):.0f} / 100（52周）",
        f"标的：{symbol} ${signal.get('underlying_price', 0)}（接货底线 ${signal.get('floor_price', 0)}）",
    ]

    suggestions = signal.get("suggestions") or []
    if suggestions:
        lines.append("")
        lines.append("📋 建议交易（卖出虚值 put，delta 0.20~0.30）：")
        for s in suggestions:
            lines.append(
                f"  {int(s.get('strike', 0))}P  权利金 ${s.get('premium', 0):.1f}"
                f"  年化 ~{s.get('annualized_yield', 0):.0f}%"
                f"  接货成本 ${s.get('cost_basis', 0):.1f}"
            )

    lines.append("")
    lines.append("✅ 复核清单：")
    lines.append(_CHECKLIST)

    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "TelegramNotifier":
        tg = config.get("telegram", {})
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", "")
        chat = os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id", "")
        return cls(token, chat)

    def send(self, text: str) -> bool:
        if not self._enabled:
            logger.info("Telegram 未配置，跳过推送：\n%s", text)
            return False
        try:
            import httpx
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = httpx.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": ""},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Telegram 推送成功")
                return True
            else:
                logger.error("Telegram 推送失败: %s %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("Telegram 推送异常: %s", e)
        return False

    def send_signal(self, signal: Any) -> bool:
        text = format_leaps_signal(signal)
        return self.send(text)
