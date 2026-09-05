"""Google Sheets 镜像同步:本地 SQLite → 三个 worksheet 幂等 upsert。

持仓/轮次 · 触线流水 · Sim 纸面;每行靠 Sync Key 列查询后 update/append。
映射口径与 notion_sync 一致(复用其 row mappers)。
不改 TG 推送、不下单、不碰决策树。失败只打日志。
默认关闭;需 enabled + credentials + spreadsheet_id。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

DEFAULT_GOOGLE_SHEETS: Dict[str, Any] = {
    "enabled": False,
    "spreadsheet_id": "",  # smile 稍后提供亦可
    "credentials_json": "",  # SA JSON 字符串;勿提交 git
    "service_account_file": "",  # 或本机 SA 文件路径
    "sync_minutes": 15,
    "touch_limit": 50,
    "sheet_positions": "positions",
    "sheet_touches": "touches",
    "sheet_sim": "sim",
}

# 表头固定顺序;Sync Key 在 A 列便于索引
HEADERS: List[str] = [
    "Sync Key",
    "Name",
    "Symbol",
    "Status",
    "Side",
    "Strike",
    "Expiry",
    "Contract",
    "Timeframe",
    "Shares",
    "Premium",
    "PnL",
    "Qty",
    "Open Price",
    "Started",
    "Updated",
    "Last Seen",
    "Trigger",
    "IV Rank",
    "DTE",
    "Strategy",
    "Level",
    "Note",
]

# notion_sync map_* 的 logical field → 列名
_FIELD_COLS: Dict[str, str] = {
    "symbol": "Symbol",
    "status": "Status",
    "side": "Side",
    "strike": "Strike",
    "expiry": "Expiry",
    "contract": "Contract",
    "timeframe": "Timeframe",
    "shares": "Shares",
    "premium": "Premium",
    "pnl": "PnL",
    "qty": "Qty",
    "open_price": "Open Price",
    "started": "Started",
    "updated": "Updated",
    "last_seen": "Last Seen",
    "trigger": "Trigger",
    "iv_rank": "IV Rank",
    "dte": "DTE",
    "strategy": "Strategy",
    "level": "Level",
    "note": "Note",
}


def get_google_sheets_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_GOOGLE_SHEETS)
    if cfg:
        overlay = cfg.get("google_sheets") or {}
        if isinstance(overlay, dict):
            merged.update({k: v for k, v in overlay.items() if v is not None})
    return merged


def resolve_credentials(
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """返回 (service_account_info_dict, service_account_file_path) 二选一。

    优先级:
      1. backend_config.google_sheets.credentials_json
      2. env GOOGLE_SHEETS_CREDENTIALS_JSON
      3. backend_config.google_sheets.service_account_file
      4. env GOOGLE_APPLICATION_CREDENTIALS
    """
    g = get_google_sheets_cfg(cfg)
    raw = (g.get("credentials_json") or "").strip()
    if not raw:
        try:
            from app.core.config import get_env

            raw = (get_env("GOOGLE_SHEETS_CREDENTIALS_JSON") or "").strip()
        except Exception:
            raw = (os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON") or "").strip()
    if raw:
        try:
            info = json.loads(raw) if raw.startswith("{") else None
            if info is None:
                # 也可能是文件路径误填到 credentials_json
                path = raw
                if os.path.isfile(path):
                    return None, path
                raise ValueError("credentials_json is not valid JSON")
            return info, None
        except json.JSONDecodeError as e:
            logger.warning("google_sheets credentials_json parse failed: %s", e)
            return None, None

    path = (g.get("service_account_file") or "").strip()
    if not path:
        try:
            from app.core.config import get_env

            path = (get_env("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        except Exception:
            path = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if path:
        return None, path
    return None, None


def has_credentials(cfg: Optional[Dict[str, Any]] = None) -> bool:
    info, path = resolve_credentials(cfg)
    return bool(info) or bool(path)


def is_sync_armed(cfg: Optional[Dict[str, Any]] = None) -> bool:
    g = get_google_sheets_cfg(cfg)
    sid = (g.get("spreadsheet_id") or "").strip()
    return bool(g.get("enabled")) and bool(sid) and has_credentials(cfg)


def build_row(sync_key: str, title: str, fields: Dict[str, Any]) -> List[Any]:
    """按 HEADERS 顺序生成一行单元格值。"""
    cell: Dict[str, Any] = {"Sync Key": sync_key, "Name": title or ""}
    for logical, col in _FIELD_COLS.items():
        val = fields.get(logical)
        if val is None or val == "":
            continue
        cell[col] = val
    out: List[Any] = []
    for h in HEADERS:
        v = cell.get(h, "")
        if v is None:
            v = ""
        out.append(v)
    return out


class SheetsClient:
    """薄封装 Google Sheets API v4;可注入 values_* 便于单测 mock。"""

    def __init__(
        self,
        spreadsheet_id: str,
        *,
        credentials_info: Optional[Dict[str, Any]] = None,
        credentials_file: Optional[str] = None,
        values_get: Optional[Callable[..., Any]] = None,
        values_update: Optional[Callable[..., Any]] = None,
        values_append: Optional[Callable[..., Any]] = None,
        spreadsheet_get: Optional[Callable[..., Any]] = None,
        batch_update: Optional[Callable[..., Any]] = None,
    ):
        self.spreadsheet_id = spreadsheet_id
        self._credentials_info = credentials_info
        self._credentials_file = credentials_file
        self._values_get = values_get
        self._values_update = values_update
        self._values_append = values_append
        self._spreadsheet_get = spreadsheet_get
        self._batch_update = batch_update
        self._service = None
        self._sheet_titles: Optional[set] = None
        self._key_index: Dict[str, Dict[str, int]] = {}  # sheet -> {sync_key: row_1based}

    def _ensure_service(self):
        if self._service is not None:
            return self._service
        if self._values_get is not None:
            return None  # fully mocked; no real service
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if self._credentials_info:
            creds = service_account.Credentials.from_service_account_info(
                self._credentials_info, scopes=list(SCOPES)
            )
        elif self._credentials_file:
            creds = service_account.Credentials.from_service_account_file(
                self._credentials_file, scopes=list(SCOPES)
            )
        else:
            raise RuntimeError("no Google Sheets credentials")
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def _a1(self, sheet: str, range_part: str) -> str:
        # sheet 名含空格/特殊字符时加单引号
        safe = sheet.replace("'", "''")
        return f"'{safe}'!{range_part}"

    def list_sheet_titles(self) -> set:
        if self._sheet_titles is not None:
            return self._sheet_titles
        if self._spreadsheet_get:
            meta = self._spreadsheet_get(self.spreadsheet_id)
        else:
            svc = self._ensure_service()
            meta = (
                svc.spreadsheets()
                .get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title")
                .execute()
            )
        titles = set()
        for sh in meta.get("sheets") or []:
            props = sh.get("properties") or {}
            t = props.get("title")
            if t:
                titles.add(t)
        self._sheet_titles = titles
        return titles

    def ensure_worksheet(self, title: str) -> None:
        titles = self.list_sheet_titles()
        if title in titles:
            return
        body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
        if self._batch_update:
            self._batch_update(self.spreadsheet_id, body)
        else:
            svc = self._ensure_service()
            svc.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id, body=body
            ).execute()
        titles.add(title)
        self._sheet_titles = titles
        self._key_index.pop(title, None)

    def ensure_header(self, sheet: str) -> None:
        self.ensure_worksheet(sheet)
        rng = self._a1(sheet, "1:1")
        if self._values_get:
            data = self._values_get(self.spreadsheet_id, rng)
        else:
            svc = self._ensure_service()
            data = (
                svc.spreadsheets()
                .values()
                .get(spreadsheetId=self.spreadsheet_id, range=rng)
                .execute()
            )
        rows = data.get("values") or []
        if rows and rows[0] and str(rows[0][0]).strip() == "Sync Key":
            return
        body = {"values": [HEADERS]}
        if self._values_update:
            self._values_update(self.spreadsheet_id, rng, body)
        else:
            svc = self._ensure_service()
            svc.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=rng,
                valueInputOption="USER_ENTERED",
                body=body,
            ).execute()
        self._key_index.pop(sheet, None)

    def load_sync_key_index(self, sheet: str) -> Dict[str, int]:
        """Sync Key → 1-based 行号(含表头行=1)。"""
        if sheet in self._key_index:
            return self._key_index[sheet]
        self.ensure_header(sheet)
        rng = self._a1(sheet, "A:A")
        if self._values_get:
            data = self._values_get(self.spreadsheet_id, rng)
        else:
            svc = self._ensure_service()
            data = (
                svc.spreadsheets()
                .values()
                .get(spreadsheetId=self.spreadsheet_id, range=rng)
                .execute()
            )
        index: Dict[str, int] = {}
        for i, row in enumerate(data.get("values") or [], start=1):
            if i == 1:
                continue  # header
            if not row:
                continue
            key = str(row[0]).strip()
            if key:
                index[key] = i
        self._key_index[sheet] = index
        return index

    def upsert_by_sync_key(
        self,
        sheet: str,
        sync_key: str,
        title: str,
        fields: Dict[str, Any],
    ) -> str:
        """按 Sync Key 幂等 upsert。返回 'updated' / 'appended'。"""
        row = build_row(sync_key, title, fields)
        index = self.load_sync_key_index(sheet)
        if sync_key in index:
            row_num = index[sync_key]
            rng = self._a1(sheet, f"{row_num}:{row_num}")
            body = {"values": [row]}
            if self._values_update:
                self._values_update(self.spreadsheet_id, rng, body)
            else:
                svc = self._ensure_service()
                svc.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=rng,
                    valueInputOption="USER_ENTERED",
                    body=body,
                ).execute()
            return "updated"
        # append
        rng = self._a1(sheet, "A:A")
        body = {"values": [row]}
        if self._values_append:
            self._values_append(self.spreadsheet_id, rng, body)
        else:
            svc = self._ensure_service()
            svc.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=rng,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()
        # 估算新行号(表头 + 已有数据行 + 1)
        new_row = 1 + len(index) + 1
        index[sync_key] = new_row
        self._key_index[sheet] = index
        return "appended"


def collect_local_rows(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    get_cycles_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    get_timing_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    list_sim_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, List[Tuple[str, str, Dict[str, Any]]]]:
    """读本地库 → 三组 (sync_key, title, fields)。复用 notion_sync 映射。"""
    from app.services.notion_sync import (
        map_position_row,
        map_sim_row,
        map_touch_row,
    )

    g = get_google_sheets_cfg(cfg)
    touch_limit = int(g.get("touch_limit") or 50)

    if get_cycles_fn is None:
        from app.data import wheel_repository as wrepo

        get_cycles_fn = wrepo.get_cycles
    if get_timing_fn is None:
        from app.data import leaps_repository as lrepo

        get_timing_fn = lrepo.get_timing_history
    if list_sim_fn is None:
        from app.data import sim_repository as srepo

        list_sim_fn = srepo.list_cycles

    positions = [map_position_row(c) for c in get_cycles_fn(include_closed=False)]
    hist = get_timing_fn(page=1, page_size=min(max(touch_limit, 1), 100))
    touches = [map_touch_row(r) for r in (hist.get("items") or [])]
    sims = [map_sim_row(c) for c in list_sim_fn(include_closed=False, limit=200)]
    return {"positions": positions, "touches": touches, "sim": sims}


def run_google_sheets_sync(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    client: Optional[SheetsClient] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """执行一次全量 upsert。enabled=false / 缺凭证 / 缺 spreadsheet_id 时零请求。"""
    if cfg is None:
        try:
            from app.api.leaps import _load_config

            cfg = _load_config()
        except Exception:
            cfg = {}

    gcfg = get_google_sheets_cfg(cfg)
    out: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "reason": "",
        "created_or_updated": 0,
        "errors": 0,
        "by_sheet": {},
    }

    if not gcfg.get("enabled"):
        out["skipped"] = True
        out["reason"] = "disabled"
        out["ok"] = True
        return out

    spreadsheet_id = (gcfg.get("spreadsheet_id") or "").strip()
    if not spreadsheet_id:
        out["skipped"] = True
        out["reason"] = "no_spreadsheet_id"
        out["ok"] = True
        return out

    info, path = resolve_credentials(cfg)
    if not info and not path and client is None:
        out["skipped"] = True
        out["reason"] = "no_credentials"
        out["ok"] = True
        return out

    if dry_run:
        rows = collect_local_rows(cfg)
        out["ok"] = True
        out["dry_run"] = True
        out["by_sheet"] = {k: len(v) for k, v in rows.items()}
        out["created_or_updated"] = sum(len(v) for v in rows.values())
        return out

    if client is None:
        client = SheetsClient(
            spreadsheet_id,
            credentials_info=info,
            credentials_file=path,
        )

    sheet_map = {
        "positions": (gcfg.get("sheet_positions") or "positions").strip() or "positions",
        "touches": (gcfg.get("sheet_touches") or "touches").strip() or "touches",
        "sim": (gcfg.get("sheet_sim") or "sim").strip() or "sim",
    }
    rows = collect_local_rows(cfg)
    for kind, sheet_name in sheet_map.items():
        stats = {"upserted": 0, "errors": 0}
        for sync_key, title, fields in rows.get(kind) or []:
            try:
                client.upsert_by_sync_key(sheet_name, sync_key, title, fields)
                stats["upserted"] += 1
                out["created_or_updated"] += 1
            except Exception as e:
                stats["errors"] += 1
                out["errors"] += 1
                logger.warning(
                    "Google Sheets upsert %s %s failed: %s", kind, sync_key, e
                )
        out["by_sheet"][kind] = stats

    out["ok"] = out["errors"] == 0
    return out


def google_sheets_sync_loop() -> None:
    """后台循环:仅 enabled+credentials+spreadsheet_id 时跑;间隔 sync_minutes;失败只记日志。"""
    time.sleep(180)  # 错开 notion(150s)与其它启动任务
    while True:
        minutes = 15
        try:
            from app.api.leaps import _load_config

            cfg = _load_config()
            gcfg = get_google_sheets_cfg(cfg)
            try:
                minutes = int(gcfg.get("sync_minutes") or 15)
            except (TypeError, ValueError):
                minutes = 15
            minutes = max(1, minutes)
            if not is_sync_armed(cfg):
                time.sleep(300)
                continue
            out = run_google_sheets_sync(cfg)
            if out.get("created_or_updated"):
                logger.info(
                    "google_sheets sync upserted=%s errors=%s by_sheet=%s",
                    out.get("created_or_updated"),
                    out.get("errors"),
                    out.get("by_sheet"),
                )
        except Exception as e:
            logger.warning("google_sheets sync loop failed: %s", e)
        time.sleep(minutes * 60)
