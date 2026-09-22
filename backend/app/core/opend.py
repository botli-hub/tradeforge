"""OpenD 连接防护:OpenQuoteContext 在 OpenD 未启动时会长时间阻塞(甚至卡死
async 事件循环),导致整个 API 无响应、前端页面空白。

所有创建 OpenQuoteContext 的路径应先走 ensure_opend_reachable / open_quote_context。

QuoteSession: 一轮扫描内复用同一连接,避免每合约 subscribe/get_cur_kline 反复开闭。

线程安全设计选择:
  - futu OpenQuoteContext **非线程安全** → 一会话只绑一线程,禁止跨线程共享同一 ctx。
  - 标的级并行时:每个 worker 各自持有一个短生命周期 QuoteSession(连接池大小≈max_workers)。
  - 全局限频(leaps_monitor._throttle ≈ 3.2s)仍全局共享,不拆分/不按 worker 复制预算。
"""
from __future__ import annotations

import socket
from typing import Any, Optional

# TCP 探测超时:本机 OpenD 正常应毫秒级;失败时快速返回,避免拖死请求线程/事件循环
OPEND_CONNECT_TIMEOUT = 1.5

# 标的级并行硬顶,防止配置写成无界线程
SCAN_MAX_WORKERS_CAP = 8
SCAN_MAX_WORKERS_DEFAULT = 4


class OpenDUnavailable(RuntimeError):
    """OpenD 不可达或未启动"""


# 状态摘要 / Roll 预检等轻量探测默认更短,失败快速返回
OPEND_ALIVE_TIMEOUT = 0.4


def is_opend_alive(
    host: str = "127.0.0.1",
    port: int = 11111,
    timeout: float = OPEND_ALIVE_TIMEOUT,
) -> bool:
    """TCP 探测 OpenD 是否可达;不通返回 False,不抛异常、不创建 QuoteContext。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_opend_reachable(
    host: str = "127.0.0.1",
    port: int = 11111,
    timeout: float = OPEND_CONNECT_TIMEOUT,
) -> None:
    """先做 TCP 探测;不通则立即抛 OpenDUnavailable,绝不进入会阻塞的 OpenQuoteContext。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return
    except OSError as e:
        raise OpenDUnavailable(
            f"富途 OpenD 未连接({host}:{port}): {e}. 请先启动 OpenD 再试"
        ) from e


def open_quote_context(
    host: str = "127.0.0.1",
    port: int = 11111,
    timeout: float = OPEND_CONNECT_TIMEOUT,
) -> Any:
    """探测可达后再创建 OpenQuoteContext;调用方负责 ctx.close()。"""
    ensure_opend_reachable(host, port, timeout=timeout)
    from futu import OpenQuoteContext
    return OpenQuoteContext(host=host, port=port)


def resolve_scan_max_workers(
    raw: Any,
    default: int = SCAN_MAX_WORKERS_DEFAULT,
    hard_cap: int = SCAN_MAX_WORKERS_CAP,
) -> int:
    """解析标的级并行度;默认 4,硬顶 8,最小 1(串行)。"""
    try:
        n = int(default if raw is None else raw)
    except (TypeError, ValueError):
        n = int(default)
    if n < 1:
        n = 1
    if n > hard_cap:
        n = hard_cap
    return n


class QuoteSession:
    """一轮扫描复用的 OpenQuoteContext 上下文管理器。

    用法::
        with QuoteSession(host, port) as qs:
            qs.ctx.get_market_snapshot([...])
            qs.ctx.subscribe([...], [...])
            qs.ctx.get_cur_kline(...)

    退出时 close。同一实例勿跨线程使用(见模块文档)。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        timeout: float = OPEND_CONNECT_TIMEOUT,
        ctx: Any = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._ctx = ctx
        self._owns = ctx is None

    @property
    def ctx(self) -> Any:
        if self._ctx is None:
            raise RuntimeError("QuoteSession 尚未 enter / 已关闭")
        return self._ctx

    def __enter__(self) -> "QuoteSession":
        if self._ctx is None:
            self._ctx = open_quote_context(
                host=self.host, port=self.port, timeout=self.timeout
            )
            self._owns = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owns and self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
        if self._owns:
            self._ctx = None
