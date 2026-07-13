"""OpenD 连接防护:OpenQuoteContext 在 OpenD 未启动时会长时间阻塞(甚至卡死
async 事件循环),导致整个 API 无响应、前端页面空白。

所有创建 OpenQuoteContext 的路径应先走 ensure_opend_reachable / open_quote_context。
"""
from __future__ import annotations

import socket
from typing import Any

# TCP 探测超时:本机 OpenD 正常应毫秒级;失败时快速返回,避免拖死请求线程/事件循环
OPEND_CONNECT_TIMEOUT = 1.5


class OpenDUnavailable(RuntimeError):
    """OpenD 不可达或未启动"""


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
