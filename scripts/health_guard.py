#!/usr/bin/env python3
"""TradeForge 本地健康守护 / 自愈重启脚本。

目标：
- 不只看端口是否被占用，而是真正探活关键接口
- 后端卡死 / 超时 / 假在线时自动重启
- 前端 dev 服务不可访问时自动重启
- 提供 status / ensure / restart 三个动作

用法：
  python3 scripts/health_guard.py status
  python3 scripts/health_guard.py ensure
  python3 scripts/health_guard.py restart
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
RUNTIME_DIR = ROOT / ".runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

BACKEND_LOG = RUNTIME_DIR / "backend-dev.log"
FRONTEND_LOG = RUNTIME_DIR / "frontend-dev.log"

BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:1420"

BACKEND_PROBES = [
    "/health",
    "/api/strategies",
    "/api/history/subscriptions",
]


def _http_probe(url: str, timeout: float = 3.0) -> Tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tradeforge-health-guard"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(256).decode("utf-8", errors="ignore")
            return True, f"{resp.status} {body[:120]}".strip()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def backend_health() -> Tuple[bool, List[str]]:
    issues: List[str] = []
    for path in BACKEND_PROBES:
        ok, detail = _http_probe(f"{BACKEND_URL}{path}", timeout=5.0)
        if not ok:
            issues.append(f"{path} -> {detail}")
    return len(issues) == 0, issues


def frontend_health() -> Tuple[bool, str]:
    try:
        with socket.create_connection(("127.0.0.1", 1420), timeout=2.0):
            return True, "tcp-open"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _ps_lines() -> List[str]:
    result = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]


def _matching_pids(kind: str) -> List[int]:
    matches: List[int] = []
    for line in _ps_lines():
        stripped = line.strip()
        try:
            pid_text, command = stripped.split(None, 1)
            pid = int(pid_text)
        except ValueError:
            continue

        cmd = command.strip()
        if kind == "backend":
            hit = (
                "uvicorn app.main:app" in cmd
                or cmd.endswith(" run.py")
                or cmd == "python3 run.py"
                or cmd == "python run.py"
                or ("tradeforge" in cmd and "backend" in cmd and "run.py" in cmd)
            )
        else:
            hit = (
                "vite --host 127.0.0.1" in cmd
                or "npm run dev -- --host 127.0.0.1" in cmd
                or ("tradeforge" in cmd and "frontend" in cmd and "vite" in cmd)
            )

        if hit and pid != os.getpid():
            matches.append(pid)
    return sorted(set(matches))


def _kill_pids(pids: Iterable[int], label: str) -> None:
    pids = list(sorted(set(int(pid) for pid in pids)))
    if not pids:
        return

    print(f"[{label}] stopping pids: {' '.join(map(str, pids))}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.time() + 3
    while time.time() < deadline:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except ProcessLookupError:
                pass
        if not alive:
            return
        time.sleep(0.2)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _spawn(command: List[str], cwd: Path, log_path: Path) -> int:
    log_file = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(  # noqa: S603
        command,
        cwd=str(cwd),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return int(proc.pid)


def restart_backend() -> None:
    _kill_pids(_matching_pids("backend"), "backend")
    pid = _spawn(["python3", "run.py"], BACKEND_DIR, BACKEND_LOG)
    print(f"[backend] started pid={pid}")


def restart_frontend() -> None:
    _kill_pids(_matching_pids("frontend"), "frontend")
    pid = _spawn(["npm", "run", "dev", "--", "--host", "127.0.0.1"], FRONTEND_DIR, FRONTEND_LOG)
    print(f"[frontend] started pid={pid}")


def wait_backend(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, _issues = backend_health()
        if ok:
            return True
        time.sleep(0.5)
    return False


def wait_frontend(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, _detail = frontend_health()
        if ok:
            return True
        time.sleep(0.5)
    return False


def print_status() -> int:
    backend_ok, backend_issues = backend_health()
    frontend_ok, frontend_detail = frontend_health()

    print("TradeForge Health Status")
    print("=" * 24)
    print(f"backend pids : {_matching_pids('backend') or 'none'}")
    print(f"frontend pids: {_matching_pids('frontend') or 'none'}")
    print(f"backend      : {'OK' if backend_ok else 'FAIL'}")
    if backend_issues:
        for issue in backend_issues:
            print(f"  - {issue}")
    print(f"frontend     : {'OK' if frontend_ok else 'FAIL'} ({frontend_detail})")
    print(f"backend log  : {BACKEND_LOG}")
    print(f"frontend log : {FRONTEND_LOG}")
    return 0 if backend_ok and frontend_ok else 1


def ensure_services() -> int:
    backend_ok, backend_issues = backend_health()
    frontend_ok, frontend_detail = frontend_health()

    if backend_ok:
        print("[backend] healthy")
    else:
        print("[backend] unhealthy -> restarting")
        for issue in backend_issues:
            print(f"  - {issue}")
        restart_backend()
        if not wait_backend():
            print("[backend] restart failed")
            return 1
        print("[backend] healthy after restart")

    if frontend_ok:
        print("[frontend] healthy")
    else:
        print(f"[frontend] unhealthy -> restarting ({frontend_detail})")
        restart_frontend()
        if not wait_frontend():
            print("[frontend] restart failed")
            return 1
        print("[frontend] healthy after restart")

    return 0


def restart_all() -> int:
    restart_backend()
    restart_frontend()

    ok = True
    if wait_backend():
        print("[backend] healthy after restart")
    else:
        print("[backend] restart failed")
        ok = False

    if wait_frontend():
        print("[frontend] healthy after restart")
    else:
        print("[frontend] restart failed")
        ok = False

    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="TradeForge health guard")
    parser.add_argument("action", nargs="?", default="ensure", choices=["status", "ensure", "restart"])
    args = parser.parse_args()

    if args.action == "status":
        return print_status()
    if args.action == "restart":
        return restart_all()
    return ensure_services()


if __name__ == "__main__":
    raise SystemExit(main())
