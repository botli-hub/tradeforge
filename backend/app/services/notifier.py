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
        + (
            f"（愿接价 ${signal.floor_price} · 现价在上方）"
            if signal.underlying_price > signal.floor_price
            else f"（愿接价 ${signal.floor_price} · 已入愿接区·指派风险升）"
        ),
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
        f"标的：{symbol} ${signal.get('underlying_price', 0)}（愿接最高价 ${signal.get('floor_price', 0)}）",
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



# Telegram 频道拆分:
# - chan / timing_put / timing_call: 必须各自配置 bot_token(+chat_id);缺键=静默,绝不回落到顶层全局 bot
# - legacy / position / None: 顶层 telegram.bot_token + chat_id(持仓/管仓/周报等过渡用途)
TELEGRAM_SPLIT_KINDS = frozenset({"chan", "timing_put", "timing_call"})
TELEGRAM_LEGACY_KINDS = frozenset({"legacy", "position", "scan", "digest", "uncovered", "leaps", ""})


def timing_channel_kind(signal_level: Optional[str]) -> Optional[str]:
    """WHEEL_PUT → timing_put; WHEEL_CALL → timing_call; 其它 → None(不推时机频道)."""
    level = (signal_level or "").upper()
    if level == "WHEEL_PUT":
        return "timing_put"
    if level == "WHEEL_CALL":
        return "timing_call"
    return None


def resolve_telegram_channel(kind: Optional[str], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按 kind 解析 bot_token / chat_id / proxy。

    返回 dict: kind, bot_token, chat_id, proxy, enabled, silent, source。
    - 拆分频道缺配置 → enabled=False, silent=True(不回落旧全局 bot)
    - legacy/position 等 → 读顶层 telegram{bot_token,chat_id}(+环境变量)
    """
    cfg = config or {}
    tg = cfg.get("telegram") or {}
    if not isinstance(tg, dict):
        tg = {}
    proxy = (
        os.environ.get("TELEGRAM_PROXY")
        or tg.get("proxy")
        or cfg.get("proxy")
        or ""
    )
    proxy = str(proxy).strip() or None
    raw_kind = (kind or "legacy").strip().lower()

    if raw_kind in TELEGRAM_SPLIT_KINDS:
        channel = tg.get(raw_kind)
        if not isinstance(channel, dict):
            return {
                "kind": raw_kind,
                "bot_token": "",
                "chat_id": "",
                "proxy": proxy,
                "enabled": False,
                "silent": True,
                "source": f"telegram.{raw_kind}:missing",
            }
        # 频道级 proxy 可覆盖共享 proxy
        ch_proxy = str(channel.get("proxy") or "").strip() or proxy
        token = str(channel.get("bot_token") or "").strip()
        chat = str(channel.get("chat_id") or "").strip()
        # chat_id 可与其它频道共享;但 bot_token 必须本频道独立配置
        # 若本频道未填 chat_id,不偷偷用顶层 chat_id(避免串台);允许显式同值复制
        enabled = bool(token and chat)
        return {
            "kind": raw_kind,
            "bot_token": token,
            "chat_id": chat,
            "proxy": ch_proxy,
            "enabled": enabled,
            "silent": not enabled,
            "source": f"telegram.{raw_kind}",
        }

    # legacy / position / scan / ...
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", "") or ""
    chat = os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id", "") or ""
    token = str(token).strip()
    chat = str(chat).strip()
    enabled = bool(token and chat)
    return {
        "kind": raw_kind or "legacy",
        "bot_token": token,
        "chat_id": chat,
        "proxy": proxy,
        "enabled": enabled,
        "silent": False,  # legacy 未配置只是跳过,不算「拆分静默」
        "source": "telegram(legacy)",
    }


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, proxy: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.proxy = (proxy or "").strip() or None
        self._enabled = bool(bot_token and chat_id)
        self.last_error: Optional[str] = None
        self.channel_kind: str = "legacy"
        self.channel_silent: bool = False
        self.channel_source: Optional[str] = None

    @classmethod
    def from_channel(cls, kind: Optional[str], config: Dict[str, Any]) -> "TelegramNotifier":
        """按频道 kind 构造。拆分频道缺配置 → 静默(enabled=False),不回落全局 bot。"""
        resolved = resolve_telegram_channel(kind, config)
        n = cls(resolved["bot_token"], resolved["chat_id"], resolved.get("proxy"))
        n.channel_kind = resolved["kind"]
        n.channel_silent = bool(resolved.get("silent"))
        n.channel_source = resolved.get("source")
        return n

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "TelegramNotifier":
        """兼容旧调用:等价于 legacy 顶层 bot(持仓/周报/LEAPS)。"""
        return cls.from_channel("legacy", config)

    def send_detailed(self, text: str) -> Dict[str, Any]:
        """发送并返回明确原因,避免把网络失败误报为\"未配置\"。返回 {ok, reason}。"""
        if not self._enabled:
            if self.channel_silent:
                reason = f"Telegram 频道静默:未配置 {self.channel_kind}(telegram.{self.channel_kind})"
            else:
                reason = "Telegram 未配置:Bot Token 或 Chat ID 为空(请到设置页填写并保存)"
            self.last_error = reason
            logger.info("%s，跳过推送", reason)
            return {"ok": False, "reason": reason}
        try:
            import httpx
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": text}
            if self.proxy:
                with httpx.Client(proxy=self.proxy, timeout=10) as client:
                    resp = client.post(url, json=payload)
            else:
                resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                self.last_error = None
                logger.info("Telegram 推送成功")
                return {"ok": True, "reason": "ok"}
            reason = f"Telegram 拒绝(HTTP {resp.status_code}): {resp.text[:300]}"
            self.last_error = reason
            logger.error("Telegram 推送失败: %s %s", resp.status_code, resp.text)
            return {"ok": False, "reason": reason}
        except Exception as e:
            hint = ""
            if not self.proxy:
                hint = "。若在中国大陆,api.telegram.org 被墙,需在设置页 Telegram 代理填入本地代理(如 http://127.0.0.1:7890)"
            reason = f"无法连接 Telegram({type(e).__name__}: {e}){hint}"
            self.last_error = reason
            logger.error("Telegram 推送异常: %s", e)
            return {"ok": False, "reason": reason}

    def send(self, text: str) -> bool:
        return self.send_detailed(text)["ok"]

    def send_signal(self, signal: Any) -> bool:
        text = format_leaps_signal(signal)
        return self.send(text)
