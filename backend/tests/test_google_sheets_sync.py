"""Google Sheets sync: mock API; Sync Key 幂等; enabled=false 零请求。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.google_sheets_sync import (  # noqa: E402
    HEADERS,
    SheetsClient,
    build_row,
    collect_local_rows,
    get_google_sheets_cfg,
    has_credentials,
    is_sync_armed,
    resolve_credentials,
    run_google_sheets_sync,
)


def test_get_google_sheets_cfg_defaults():
    g = get_google_sheets_cfg({})
    assert g["enabled"] is False
    assert g["sync_minutes"] == 15
    assert g["spreadsheet_id"] == ""
    assert g["sheet_positions"] == "positions"
    assert g["sheet_touches"] == "touches"
    assert g["sheet_sim"] == "sim"
    assert g["touch_limit"] == 50


def test_build_row_layout():
    row = build_row(
        "cycle:c1",
        "AAPL CSP_OPEN",
        {"symbol": "AAPL", "status": "CSP_OPEN", "strike": 180, "note": "dte=30"},
    )
    assert len(row) == len(HEADERS)
    assert row[0] == "cycle:c1"
    assert row[1] == "AAPL CSP_OPEN"
    assert row[HEADERS.index("Symbol")] == "AAPL"
    assert row[HEADERS.index("Status")] == "CSP_OPEN"
    assert row[HEADERS.index("Strike")] == 180
    assert row[HEADERS.index("Note")] == "dte=30"


def test_enabled_false_makes_zero_requests():
    calls: List[str] = []

    def boom(*a, **k):
        calls.append("hit")
        raise AssertionError("should not call Sheets API when disabled")

    client = SheetsClient(
        "ss-id",
        values_get=boom,
        values_update=boom,
        values_append=boom,
        spreadsheet_get=boom,
        batch_update=boom,
    )
    out = run_google_sheets_sync(
        {
            "google_sheets": {
                "enabled": False,
                "spreadsheet_id": "ss-id",
                "credentials_json": json.dumps({"type": "service_account"}),
            }
        },
        client=client,
    )
    assert out["ok"] is True
    assert out["skipped"] is True
    assert out["reason"] == "disabled"
    assert calls == []


def test_enabled_true_no_spreadsheet_id_zero_requests(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    calls: List[str] = []

    def boom(*a, **k):
        calls.append("hit")
        raise AssertionError("should not HTTP")

    out = run_google_sheets_sync(
        {
            "google_sheets": {
                "enabled": True,
                "spreadsheet_id": "  ",
                "credentials_json": json.dumps({"type": "service_account"}),
            }
        },
        client=SheetsClient(
            "x",
            values_get=boom,
            values_update=boom,
            values_append=boom,
            spreadsheet_get=boom,
            batch_update=boom,
        ),
    )
    assert out["skipped"] is True
    assert out["reason"] == "no_spreadsheet_id"
    assert calls == []


def test_enabled_true_no_credentials_zero_requests(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    calls: List[str] = []

    def boom(*a, **k):
        calls.append("hit")
        raise AssertionError("should not HTTP")

    out = run_google_sheets_sync(
        {
            "google_sheets": {
                "enabled": True,
                "spreadsheet_id": "ss-real",
                "credentials_json": "",
                "service_account_file": "",
            }
        },
        # no client → must check credentials and skip
    )
    assert out["skipped"] is True
    assert out["reason"] == "no_credentials"
    assert calls == []
    assert is_sync_armed(
        {"google_sheets": {"enabled": True, "spreadsheet_id": "ss", "credentials_json": ""}}
    ) is False


def test_upsert_idempotent_update_then_append():
    """同一 Sync Key:索引命中 → update;未命中 → append。"""
    sheet = "positions"
    # in-memory store: sheet -> list of rows (row0 = header)
    store: Dict[str, List[List[Any]]] = {
        sheet: [list(HEADERS)],
    }
    meta_sheets = {sheet}
    ops: List[str] = []

    def spreadsheet_get(sid):
        ops.append("spreadsheet_get")
        return {
            "sheets": [{"properties": {"title": t}} for t in sorted(meta_sheets)]
        }

    def batch_update(sid, body):
        ops.append("batch_update")
        for req in body.get("requests") or []:
            add = req.get("addSheet") or {}
            title = (add.get("properties") or {}).get("title")
            if title:
                meta_sheets.add(title)
                store.setdefault(title, [list(HEADERS)])

    def values_get(sid, rng):
        ops.append(("get", rng))
        # parse "'sheet'!A:A" or "'sheet'!1:1"
        name = rng.split("!")[0].strip("'")
        part = rng.split("!")[1]
        rows = store.get(name) or []
        if part == "1:1":
            return {"values": [rows[0]] if rows else []}
        if part == "A:A":
            return {"values": [[r[0]] if r else [""] for r in rows]}
        return {"values": rows}

    def values_update(sid, rng, body):
        ops.append(("update", rng))
        name = rng.split("!")[0].strip("'")
        part = rng.split("!")[1]
        vals = (body.get("values") or [[]])[0]
        if part == "1:1":
            if name not in store:
                store[name] = [vals]
            else:
                store[name][0] = vals
            return {}
        # row update like "2:2"
        row_num = int(part.split(":")[0])
        while len(store[name]) < row_num:
            store[name].append([""] * len(HEADERS))
        store[name][row_num - 1] = vals
        return {}

    def values_append(sid, rng, body):
        ops.append(("append", rng))
        name = rng.split("!")[0].strip("'")
        vals = (body.get("values") or [[]])[0]
        store.setdefault(name, [list(HEADERS)])
        store[name].append(vals)
        return {}

    client = SheetsClient(
        "ss-test",
        values_get=values_get,
        values_update=values_update,
        values_append=values_append,
        spreadsheet_get=spreadsheet_get,
        batch_update=batch_update,
    )

    r1 = client.upsert_by_sync_key(
        sheet, "cycle:c1", "AAPL CSP_OPEN", {"symbol": "AAPL", "status": "CSP_OPEN", "strike": 180}
    )
    assert r1 == "appended"
    assert any(o[0] == "append" for o in ops if isinstance(o, tuple))
    assert store[sheet][1][0] == "cycle:c1"

    before_len = len(store[sheet])
    # clear cached index to force re-read (simulates second sync cycle)
    client._key_index.clear()
    r2 = client.upsert_by_sync_key(
        sheet, "cycle:c1", "AAPL CSP_OPEN", {"symbol": "AAPL", "status": "CSP_OPEN", "strike": 175}
    )
    assert r2 == "updated"
    assert len(store[sheet]) == before_len
    assert store[sheet][1][HEADERS.index("Strike")] == 175
    assert any(o[0] == "update" for o in ops if isinstance(o, tuple))

    client._key_index.clear()
    r3 = client.upsert_by_sync_key(
        sheet, "cycle:c2", "TSLA HOLDING", {"symbol": "TSLA", "status": "HOLDING"}
    )
    assert r3 == "appended"
    assert len(store[sheet]) == 3  # header + 2


def test_run_sync_with_mock_client_and_local_inject(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    upserts: List[tuple] = []

    class StubClient(SheetsClient):
        def __init__(self):
            super().__init__("ss-ok")

        def upsert_by_sync_key(self, sheet, sync_key, title, fields):
            upserts.append((sheet, sync_key, title))
            return "appended"

    cycles = [
        {"id": "c1", "symbol": "AAPL", "status": "CSP_OPEN", "shares": 0},
        {"id": "c2", "symbol": "TSLA", "status": "CLOSED", "shares": 0},
    ]

    def get_cycles(include_closed=True, **kw):
        return cycles if include_closed else [c for c in cycles if c["status"] != "CLOSED"]

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

    import app.services.google_sheets_sync as gs

    orig = gs.collect_local_rows

    def fake_collect(cfg=None, **kw):
        return orig(
            cfg,
            get_cycles_fn=get_cycles,
            get_timing_fn=get_timing,
            list_sim_fn=list_sim,
        )

    monkeypatch.setattr(gs, "collect_local_rows", fake_collect)

    cfg = {
        "google_sheets": {
            "enabled": True,
            "spreadsheet_id": "ss-ok",
            "credentials_json": json.dumps({"type": "service_account", "client_email": "x@y.iam"}),
            "sheet_positions": "positions",
            "sheet_touches": "touches",
            "sheet_sim": "sim",
        }
    }
    out = run_google_sheets_sync(cfg, client=StubClient())
    assert out["ok"] is True
    assert out["skipped"] is False
    keys = {u[1] for u in upserts}
    assert "cycle:c1" in keys
    assert "cycle:c2" not in keys
    assert "touch:AAPL250117P180:1d" in keys
    assert "sim:s1" in keys
    assert out["created_or_updated"] == 3


def test_resolve_credentials_from_json_and_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    info, path = resolve_credentials(
        {"google_sheets": {"credentials_json": json.dumps({"type": "service_account", "x": 1})}}
    )
    assert info and info["type"] == "service_account"
    assert path is None

    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")
    info, path = resolve_credentials(
        {"google_sheets": {"credentials_json": "", "service_account_file": str(sa)}}
    )
    assert info is None
    assert path == str(sa)

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa))
    info, path = resolve_credentials({"google_sheets": {}})
    assert path == str(sa)
    assert has_credentials({"google_sheets": {"service_account_file": str(sa)}})


def test_is_sync_armed_requires_all_three(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    sa_json = json.dumps({"type": "service_account"})
    assert is_sync_armed({"google_sheets": {"enabled": False, "spreadsheet_id": "s", "credentials_json": sa_json}}) is False
    assert is_sync_armed({"google_sheets": {"enabled": True, "spreadsheet_id": "", "credentials_json": sa_json}}) is False
    assert is_sync_armed({"google_sheets": {"enabled": True, "spreadsheet_id": "s", "credentials_json": ""}}) is False
    assert is_sync_armed({"google_sheets": {"enabled": True, "spreadsheet_id": "s", "credentials_json": sa_json}}) is True


def test_collect_local_rows_injection():
    rows = collect_local_rows(
        {"google_sheets": {"touch_limit": 10}},
        get_cycles_fn=lambda include_closed=True: [
            {"id": "1", "symbol": "X", "status": "HOLDING"}
        ],
        get_timing_fn=lambda page=1, page_size=20, symbol=None: {"items": []},
        list_sim_fn=lambda include_closed=True, limit=200: [],
    )
    assert len(rows["positions"]) == 1
    assert rows["positions"][0][0] == "cycle:1"
