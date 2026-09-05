"""Notion sync: mock HTTP; Sync Key 幂等; enabled=false 零请求。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.notion_sync import (  # noqa: E402
    NotionClient,
    collect_local_rows,
    cycle_sync_key,
    get_notion_cfg,
    is_sync_armed,
    map_position_row,
    map_sim_row,
    map_touch_row,
    resolve_token,
    run_notion_sync,
    sim_sync_key,
    touch_sync_key,
)


class FakeResp:
    def __init__(self, data: Dict[str, Any], status_code: int = 200):
        self._data = data
        self.status_code = status_code
        self.text = str(data)

    def json(self):
        return self._data


def _schema_db(props: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    default = {
        "Name": "title",
        "Sync Key": "rich_text",
        "Symbol": "rich_text",
        "Status": "select",
        "Strike": "number",
        "Premium": "number",
    }
    props = props or default
    return {
        "object": "database",
        "properties": {k: {"type": v, "name": k} for k, v in props.items()},
    }


def test_sync_key_helpers():
    assert cycle_sync_key("abc") == "cycle:abc"
    assert sim_sync_key("x1") == "sim:x1"
    assert touch_sync_key({"contract_code": "AAPL250117P180", "timeframe": "1h"}) == (
        "touch:AAPL250117P180:1h"
    )
    a = touch_sync_key({"symbol": "AAPL", "side": "PUT", "strike": 180, "first_seen": "t"})
    b = touch_sync_key({"symbol": "AAPL", "side": "PUT", "strike": 180, "first_seen": "t"})
    assert a == b and a.startswith("touch:")


def test_map_rows():
    sk, title, fields = map_position_row(
        {"id": "c1", "symbol": "aapl", "status": "CSP_OPEN", "shares": 0, "open_strike": 180}
    )
    assert sk == "cycle:c1"
    assert title == "AAPL CSP_OPEN"
    assert fields["symbol"] == "AAPL"
    assert fields["status"] == "CSP_OPEN"

    sk, title, fields = map_touch_row(
        {
            "contract_code": "TSLA250221C300",
            "timeframe": "1h",
            "symbol": "TSLA",
            "side": "CALL",
            "strike": 300,
        }
    )
    assert sk == "touch:TSLA250221C300:1h"
    assert "TSLA" in title

    sk, title, fields = map_sim_row(
        {"id": "s9", "symbol": "ARM", "status": "HOLDING", "strategy": "L2"}
    )
    assert sk == "sim:s9"
    assert title == "ARM HOLDING"


def test_enabled_false_makes_zero_requests():
    calls: List[str] = []

    def boom(*a, **k):
        calls.append("hit")
        raise AssertionError("should not call Notion HTTP when disabled")

    out = run_notion_sync(
        {"notion": {"enabled": False, "token": "secret_xxx"}},
        client=NotionClient("secret_xxx", http_post=boom, http_patch=boom, http_get=boom),
    )
    assert out["ok"] is True
    assert out["skipped"] is True
    assert out["reason"] == "disabled"
    assert calls == []


def test_enabled_true_no_token_zero_requests(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    calls: List[str] = []

    def boom(*a, **k):
        calls.append("hit")
        raise AssertionError("should not HTTP")

    out = run_notion_sync(
        {"notion": {"enabled": True, "token": "  "}},
        client=NotionClient("x", http_post=boom, http_patch=boom, http_get=boom),
    )
    assert out["skipped"] is True
    assert out["reason"] == "no_token"
    assert calls == []
    assert is_sync_armed({"notion": {"enabled": True, "token": ""}}) is False


def test_upsert_idempotent_update_then_create():
    """同一 Sync Key:先 query 命中 → PATCH;未命中 → POST create。"""
    db = "db-positions"
    pages: Dict[str, Dict[str, Any]] = {}
    requests: List[tuple] = []

    def http_get(url, headers=None, timeout=None):
        requests.append(("GET", url))
        assert db in url
        return FakeResp(_schema_db())

    def http_post(url, headers=None, json=None, timeout=None):
        requests.append(("POST", url, json))
        if url.endswith("/query"):
            sk = (
                ((json or {}).get("filter") or {})
                .get("rich_text", {})
                .get("equals")
            )
            hits = [p for p in pages.values() if p.get("sync_key") == sk]
            return FakeResp({"results": hits[:1], "object": "list"})
        if url.endswith("/pages"):
            props = (json or {}).get("properties") or {}
            # extract sync key text
            sk_prop = props.get("Sync Key") or {}
            texts = (sk_prop.get("rich_text") or [{}])[0].get("text", {}).get("content")
            pid = f"page-{len(pages)+1}"
            pages[pid] = {"id": pid, "sync_key": texts}
            return FakeResp({"id": pid, "object": "page"})
        raise AssertionError(url)

    def http_patch(url, headers=None, json=None, timeout=None):
        requests.append(("PATCH", url, json))
        pid = url.rstrip("/").split("/")[-1]
        assert pid in pages
        return FakeResp({"id": pid, "object": "page"})

    client = NotionClient(
        "secret_test",
        http_get=http_get,
        http_post=http_post,
        http_patch=http_patch,
    )

    # first: create
    pid1 = client.upsert_by_sync_key(
        db, "cycle:c1", "AAPL CSP_OPEN", {"symbol": "AAPL", "status": "CSP_OPEN", "strike": 180}
    )
    assert pid1 == "page-1"
    assert any(r[0] == "POST" and str(r[1]).endswith("/pages") for r in requests)

    # second same key: update
    before = len(pages)
    pid2 = client.upsert_by_sync_key(
        db, "cycle:c1", "AAPL CSP_OPEN", {"symbol": "AAPL", "status": "CSP_OPEN", "strike": 175}
    )
    assert pid2 == "page-1"
    assert len(pages) == before
    assert any(r[0] == "PATCH" for r in requests)

    # different key: another create
    pid3 = client.upsert_by_sync_key(
        db, "cycle:c2", "TSLA HOLDING", {"symbol": "TSLA", "status": "HOLDING"}
    )
    assert pid3 == "page-2"
    assert len(pages) == 2


def test_run_sync_with_mock_client_and_local_inject(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)

    upserts: List[tuple] = []

    class StubClient(NotionClient):
        def __init__(self):
            super().__init__("secret_ok")

        def upsert_by_sync_key(self, database_id, sync_key, title, fields):
            upserts.append((database_id, sync_key, title))
            return f"id-{sync_key}"

    cycles = [
        {"id": "c1", "symbol": "AAPL", "status": "CSP_OPEN", "shares": 0},
        {"id": "c2", "symbol": "TSLA", "status": "CLOSED", "shares": 0},  # filtered by include_closed=False in collect
    ]

    def get_cycles(include_closed=True, **kw):
        rows = cycles if include_closed else [c for c in cycles if c["status"] != "CLOSED"]
        return rows

    def get_timing(page=1, page_size=20, symbol=None):
        return {
            "items": [
                {
                    "contract_code": "AAPL250117P180",
                    "timeframe": "1d",
                    "symbol": "AAPL",
                    "side": "PUT",
                    "strike": 180,
                }
            ]
        }

    def list_sim(include_closed=True, limit=200, **kw):
        return [{"id": "s1", "symbol": "ARM", "status": "HOLDING", "strategy": "L1"}]

    # patch collect sources via run path — inject by patching collect_local_rows deps
    import app.services.notion_sync as ns

    orig = ns.collect_local_rows

    def fake_collect(cfg=None, **kw):
        return orig(
            cfg,
            get_cycles_fn=get_cycles,
            get_timing_fn=get_timing,
            list_sim_fn=list_sim,
        )

    monkeypatch.setattr(ns, "collect_local_rows", fake_collect)

    cfg = {
        "notion": {
            "enabled": True,
            "token": "secret_ok",
            "database_positions": "db-pos",
            "database_touches": "db-touch",
            "database_sim": "db-sim",
        }
    }
    out = run_notion_sync(cfg, client=StubClient())
    assert out["ok"] is True
    assert out["skipped"] is False
    keys = {u[1] for u in upserts}
    assert "cycle:c1" in keys
    assert "cycle:c2" not in keys  # CLOSED excluded
    assert "touch:AAPL250117P180:1d" in keys
    assert "sim:s1" in keys
    assert out["created_or_updated"] == 3


def test_get_notion_cfg_defaults():
    n = get_notion_cfg({})
    assert n["enabled"] is False
    assert n["sync_minutes"] == 15
    assert n["database_positions"].startswith("c99ca82a")
    assert resolve_token({"notion": {"token": "abc"}}) == "abc"


def test_collect_local_rows_injection():
    rows = collect_local_rows(
        {"notion": {"touch_limit": 10}},
        get_cycles_fn=lambda include_closed=True: [
            {"id": "1", "symbol": "X", "status": "HOLDING"}
        ],
        get_timing_fn=lambda page=1, page_size=20, symbol=None: {"items": []},
        list_sim_fn=lambda include_closed=True, limit=200: [],
    )
    assert len(rows["positions"]) == 1
    assert rows["positions"][0][0] == "cycle:1"
