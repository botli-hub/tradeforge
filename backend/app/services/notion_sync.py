"""Notion 私有看板同步:本地 SQLite → 三个 DB 幂等 upsert。

持仓/轮次 · 触线流水 · Sim 纸面;每行靠 Sync Key 查询后 update/create。
不改 TG 推送、不下单、不碰决策树。失败只打日志。

假定 Notion 属性(不存在则跳过该字段;运行时会拉 schema):
  Name(title), Sync Key(rich_text), Symbol, Status, Side, Strike, Expiry,
  Contract, Timeframe, Shares, Premium, PnL, Started, Updated, Last Seen,
  Trigger, IV Rank, DTE, Strategy, Level, Note
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

# smile 已建私有草稿板(示例默认,可被设置页覆盖)
DEFAULT_PAGE_ID = "3d271fa5-7f68-8182-8f5a-d86eff5ca643"
DEFAULT_DB_POSITIONS = "c99ca82a-77c2-4514-b032-0c9869a5cae8"  # 持仓/轮次
DEFAULT_DB_TOUCHES = "a3704582-5dcb-4efc-83b7-11a1bd7b0bc9"  # 触线流水
DEFAULT_DB_SIM = "5539f0fe-bc10-4d91-89c3-7ef07814b8ea"  # Sim纸面

DEFAULT_NOTION: Dict[str, Any] = {
    "enabled": False,
    "token": "",
    "page_id": DEFAULT_PAGE_ID,
    "database_positions": DEFAULT_DB_POSITIONS,
    "database_touches": DEFAULT_DB_TOUCHES,
    "database_sim": DEFAULT_DB_SIM,
    "sync_minutes": 15,
    "touch_limit": 50,
}

# 本地字段 → Notion 属性名候选(按 schema 命中第一个存在的)
_PROP_ALIASES: Dict[str, Tuple[str, ...]] = {
    "sync_key": ("Sync Key", "sync_key", "SyncKey"),
    "symbol": ("Symbol", "标的", "symbol"),
    "status": ("Status", "状态", "status"),
    "side": ("Side", "方向", "side"),
    "strike": ("Strike", "行权价", "strike"),
    "expiry": ("Expiry", "到期", "expiry"),
    "contract": ("Contract", "合约", "contract_code", "Contract Code"),
    "timeframe": ("Timeframe", "周期", "timeframe"),
    "shares": ("Shares", "股数", "shares"),
    "premium": ("Premium", "权利金", "total_premium", "Premium Net"),
    "pnl": ("PnL", "盈亏", "realized_pnl", "Realized PnL"),
    "started": ("Started", "开始", "started_at", "Started At"),
    "updated": ("Updated", "更新", "updated_at", "Updated At"),
    "last_seen": ("Last Seen", "最近触线", "last_seen"),
    "trigger": ("Trigger", "触发价", "trigger_price"),
    "iv_rank": ("IV Rank", "IVR", "iv_rank"),
    "dte": ("DTE", "dte"),
    "strategy": ("Strategy", "策略", "strategy"),
    "level": ("Level", "档位", "level"),
    "note": ("Note", "备注", "note"),
    "qty": ("Qty", "数量", "open_qty", "Quantity"),
    "open_price": ("Open Price", "开仓价", "open_price"),
}


def get_notion_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_NOTION)
    if cfg:
        overlay = cfg.get("notion") or {}
        if isinstance(overlay, dict):
            merged.update({k: v for k, v in overlay.items() if v is not None})
    return merged


def resolve_token(cfg: Optional[Dict[str, Any]] = None) -> str:
    """优先 backend_config.notion.token,其次环境变量 NOTION_TOKEN。永不硬编码。"""
    n = get_notion_cfg(cfg)
    token = (n.get("token") or "").strip()
    if token:
        return token
    try:
        from app.core.config import get_env
        return (get_env("NOTION_TOKEN") or "").strip()
    except Exception:
        return (os.getenv("NOTION_TOKEN") or "").strip()


def is_sync_armed(cfg: Optional[Dict[str, Any]] = None) -> bool:
    n = get_notion_cfg(cfg)
    return bool(n.get("enabled")) and bool(resolve_token(cfg))


def touch_sync_key(row: Dict[str, Any]) -> str:
    """触线 Sync Key:优先稳定指纹 contract+timeframe,否则 hash。"""
    code = str(row.get("contract_code") or "").strip()
    tf = str(row.get("timeframe") or "1d").strip() or "1d"
    if code:
        return f"touch:{code}:{tf}"
    raw = "|".join(
        str(row.get(k) or "")
        for k in ("symbol", "side", "strike", "expiry", "ema_type", "timeframe", "first_seen")
    )
    return "touch:" + hashlib.sha1(raw.encode()).hexdigest()[:20]


def cycle_sync_key(cycle_id: str) -> str:
    return f"cycle:{cycle_id}"


def sim_sync_key(sim_id: str) -> str:
    return f"sim:{sim_id}"


def _title_prop(text: str) -> Dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": (text or "")[:2000]}}]}


def _rich_text_prop(text: str) -> Dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": str(text or "")[:2000]}}]}


def _number_prop(val: Any) -> Optional[Dict[str, Any]]:
    if val is None or val == "":
        return None
    try:
        return {"number": float(val)}
    except (TypeError, ValueError):
        return None


def _select_prop(name: str) -> Dict[str, Any]:
    return {"select": {"name": str(name or "")[:100] or "-"}}


def _date_prop(iso: Any) -> Optional[Dict[str, Any]]:
    if not iso:
        return None
    s = str(iso).strip()
    if not s:
        return None
    # Notion date 要 YYYY-MM-DD 或完整 ISO
    start = s[:10] if len(s) >= 10 and s[4] == "-" else s[:19]
    return {"date": {"start": start}}


class NotionClient:
    """薄封装 Notion REST;可注入 http 以便单测 mock。"""

    def __init__(
        self,
        token: str,
        *,
        http_post: Optional[Callable[..., Any]] = None,
        http_patch: Optional[Callable[..., Any]] = None,
        http_get: Optional[Callable[..., Any]] = None,
        timeout: float = 20.0,
    ):
        self.token = token
        self.timeout = timeout
        self._http_post = http_post
        self._http_patch = http_patch
        self._http_get = http_get
        self._schema_cache: Dict[str, Dict[str, str]] = {}  # db_id -> {prop_name: type}

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        import httpx

        url = f"{NOTION_BASE}{path}"
        headers = self._headers()
        if method == "GET" and self._http_get:
            resp = self._http_get(url, headers=headers, timeout=self.timeout)
        elif method == "POST" and self._http_post:
            resp = self._http_post(url, headers=headers, json=payload or {}, timeout=self.timeout)
        elif method == "PATCH" and self._http_patch:
            resp = self._http_patch(url, headers=headers, json=payload or {}, timeout=self.timeout)
        else:
            if method == "GET":
                resp = httpx.get(url, headers=headers, timeout=self.timeout)
            elif method == "POST":
                resp = httpx.post(url, headers=headers, json=payload or {}, timeout=self.timeout)
            elif method == "PATCH":
                resp = httpx.patch(url, headers=headers, json=payload or {}, timeout=self.timeout)
            else:
                raise ValueError(f"unsupported method {method}")

        status = getattr(resp, "status_code", None)
        try:
            data = resp.json()
        except Exception:
            data = {"raw": getattr(resp, "text", "")}
        if status is not None and status >= 400:
            raise RuntimeError(f"Notion {method} {path} -> {status}: {data}")
        if isinstance(data, dict) and data.get("object") == "error":
            raise RuntimeError(f"Notion error: {data}")
        return data if isinstance(data, dict) else {"data": data}

    def get_database_schema(self, database_id: str) -> Dict[str, str]:
        """返回 {property_name: type};失败返回空(调用方用假定名)。"""
        if database_id in self._schema_cache:
            return self._schema_cache[database_id]
        try:
            data = self._request("GET", f"/databases/{database_id}")
            props = data.get("properties") or {}
            schema = {name: (meta.get("type") or "") for name, meta in props.items()}
            self._schema_cache[database_id] = schema
            return schema
        except Exception as e:
            logger.warning("Notion schema fetch failed for %s: %s", database_id, e)
            return {}

    def find_page_by_sync_key(self, database_id: str, sync_key: str) -> Optional[str]:
        schema = self.get_database_schema(database_id)
        prop_name = self._resolve_prop_name(schema, "sync_key") or "Sync Key"
        prop_type = (schema.get(prop_name) or "rich_text").lower()
        if prop_type == "title":
            filt: Dict[str, Any] = {"property": prop_name, "title": {"equals": sync_key}}
        elif prop_type in ("rich_text", "text"):
            filt = {"property": prop_name, "rich_text": {"equals": sync_key}}
        else:
            # formula / unique_id 等:仍尝试 rich_text equals
            filt = {"property": prop_name, "rich_text": {"equals": sync_key}}
        data = self._request(
            "POST",
            f"/databases/{database_id}/query",
            {"filter": filt, "page_size": 1},
        )
        results = data.get("results") or []
        if not results:
            return None
        return results[0].get("id")

    def upsert_by_sync_key(
        self,
        database_id: str,
        sync_key: str,
        title: str,
        fields: Dict[str, Any],
    ) -> str:
        """按 Sync Key 幂等 upsert。返回 page_id。"""
        props = self._build_properties(database_id, sync_key, title, fields)
        page_id = self.find_page_by_sync_key(database_id, sync_key)
        if page_id:
            self._request("PATCH", f"/pages/{page_id}", {"properties": props})
            return page_id
        created = self._request(
            "POST",
            "/pages",
            {"parent": {"database_id": database_id}, "properties": props},
        )
        return created.get("id") or ""

    def _resolve_prop_name(self, schema: Dict[str, str], logical: str) -> Optional[str]:
        aliases = _PROP_ALIASES.get(logical) or (logical,)
        if not schema:
            return aliases[0]
        for name in aliases:
            if name in schema:
                return name
        # 大小写不敏感兜底
        lower = {k.lower(): k for k in schema}
        for name in aliases:
            if name.lower() in lower:
                return lower[name.lower()]
        return None

    def _encode_value(self, prop_type: str, value: Any) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        t = (prop_type or "rich_text").lower()
        if t == "title":
            return _title_prop(str(value))
        if t == "rich_text":
            return _rich_text_prop(str(value))
        if t == "number":
            return _number_prop(value)
        if t == "select":
            return _select_prop(str(value))
        if t == "date":
            return _date_prop(value)
        if t == "checkbox":
            return {"checkbox": bool(value)}
        if t == "url":
            return {"url": str(value)[:2000] if value else None}
        # formula / rollup / relation 只读 — 跳过
        if t in ("formula", "rollup", "relation", "created_time", "last_edited_time",
                 "created_by", "last_edited_by", "files", "people", "unique_id"):
            return None
        return _rich_text_prop(str(value))

    def _build_properties(
        self,
        database_id: str,
        sync_key: str,
        title: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        schema = self.get_database_schema(database_id)
        props: Dict[str, Any] = {}

        # title: Name / 名称 / 第一个 title 型
        title_name = None
        if schema:
            for n, t in schema.items():
                if t == "title":
                    title_name = n
                    break
        if not title_name:
            for cand in ("Name", "名称", "Title", "标题"):
                if not schema or cand in schema:
                    title_name = cand
                    break
        title_name = title_name or "Name"
        props[title_name] = _title_prop(title)

        sk_name = self._resolve_prop_name(schema, "sync_key") or "Sync Key"
        sk_type = schema.get(sk_name, "rich_text") if schema else "rich_text"
        enc = self._encode_value(sk_type, sync_key)
        if enc:
            props[sk_name] = enc

        for logical, value in fields.items():
            if value is None or value == "":
                continue
            pname = self._resolve_prop_name(schema, logical)
            if not pname:
                continue
            ptype = schema.get(pname, "rich_text") if schema else "rich_text"
            encoded = self._encode_value(ptype, value)
            if encoded:
                props[pname] = encoded
        return props


def map_position_row(cycle: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    cid = str(cycle.get("id") or "")
    symbol = str(cycle.get("symbol") or "").upper()
    status = str(cycle.get("status") or "")
    title = f"{symbol} {status}".strip()
    fields = {
        "symbol": symbol,
        "status": status,
        "shares": cycle.get("shares"),
        "premium": cycle.get("total_premium"),
        "pnl": cycle.get("realized_pnl"),
        "strike": cycle.get("open_strike"),
        "expiry": cycle.get("open_expiry"),
        "contract": cycle.get("open_contract_code"),
        "qty": cycle.get("open_qty"),
        "open_price": cycle.get("open_price"),
        "side": cycle.get("open_option_type"),
        "started": cycle.get("started_at"),
        "updated": cycle.get("updated_at"),
        "note": f"dte={cycle.get('open_dte')}" if cycle.get("open_dte") is not None else None,
    }
    return cycle_sync_key(cid), title, fields


def map_touch_row(row: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    sk = touch_sync_key(row)
    symbol = str(row.get("symbol") or "").upper()
    side = str(row.get("side") or "")
    strike = row.get("strike")
    tf = row.get("timeframe") or "1d"
    title = f"{symbol} {side} {strike} {tf}".strip()
    fields = {
        "symbol": symbol,
        "side": side,
        "strike": strike,
        "expiry": row.get("expiry"),
        "contract": row.get("contract_code"),
        "timeframe": tf,
        "trigger": row.get("trigger_price"),
        "iv_rank": row.get("iv_rank"),
        "dte": row.get("dte"),
        "last_seen": row.get("last_seen"),
        "started": row.get("first_seen"),
        "status": side,
        "note": f"x{row.get('times_triggered') or 1}",
    }
    return sk, title, fields


def map_sim_row(cycle: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    sid = str(cycle.get("id") or "")
    symbol = str(cycle.get("symbol") or "").upper()
    status = str(cycle.get("status") or "")
    title = f"{symbol} {status}".strip()
    fields = {
        "symbol": symbol,
        "status": status,
        "strategy": cycle.get("strategy"),
        "level": cycle.get("level"),
        "shares": cycle.get("shares"),
        "premium": cycle.get("total_premium"),
        "pnl": cycle.get("realized_pnl"),
        "strike": cycle.get("open_strike"),
        "expiry": cycle.get("open_expiry"),
        "contract": cycle.get("open_contract_code"),
        "qty": cycle.get("open_qty"),
        "open_price": cycle.get("open_price"),
        "side": cycle.get("open_option_type"),
        "started": cycle.get("started_at"),
        "updated": cycle.get("updated_at"),
    }
    return sim_sync_key(sid), title, fields


def collect_local_rows(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    get_cycles_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    get_timing_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    list_sim_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, List[Tuple[str, str, Dict[str, Any]]]]:
    """读本地库 → 三组 (sync_key, title, fields)。可注入 repo 便于单测。"""
    n = get_notion_cfg(cfg)
    touch_limit = int(n.get("touch_limit") or 50)

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


def run_notion_sync(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    client: Optional[NotionClient] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """执行一次全量 upsert。enabled=false 或无 token 时零请求。"""
    if cfg is None:
        try:
            from app.api.leaps import _load_config
            cfg = _load_config()
        except Exception:
            cfg = {}

    ncfg = get_notion_cfg(cfg)
    out: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "reason": "",
        "created_or_updated": 0,
        "errors": 0,
        "by_db": {},
    }

    if not ncfg.get("enabled"):
        out["skipped"] = True
        out["reason"] = "disabled"
        out["ok"] = True
        return out

    token = resolve_token(cfg)
    if not token:
        out["skipped"] = True
        out["reason"] = "no_token"
        out["ok"] = True
        return out

    if dry_run:
        rows = collect_local_rows(cfg)
        out["ok"] = True
        out["dry_run"] = True
        out["by_db"] = {k: len(v) for k, v in rows.items()}
        out["created_or_updated"] = sum(len(v) for v in rows.values())
        return out

    if client is None:
        client = NotionClient(token)

    db_map = {
        "positions": (ncfg.get("database_positions") or "").strip(),
        "touches": (ncfg.get("database_touches") or "").strip(),
        "sim": (ncfg.get("database_sim") or "").strip(),
    }
    rows = collect_local_rows(cfg)
    for kind, db_id in db_map.items():
        stats = {"upserted": 0, "errors": 0}
        if not db_id:
            out["by_db"][kind] = {**stats, "skipped": "no_database_id"}
            continue
        for sync_key, title, fields in rows.get(kind) or []:
            try:
                client.upsert_by_sync_key(db_id, sync_key, title, fields)
                stats["upserted"] += 1
                out["created_or_updated"] += 1
            except Exception as e:
                stats["errors"] += 1
                out["errors"] += 1
                logger.warning("Notion upsert %s %s failed: %s", kind, sync_key, e)
        out["by_db"][kind] = stats

    out["ok"] = out["errors"] == 0
    return out


def notion_sync_loop() -> None:
    """后台循环:仅 enabled+token 时跑;间隔 sync_minutes(默认 15);失败只记日志。"""
    time.sleep(150)  # 错开启动
    while True:
        minutes = 15
        try:
            from app.api.leaps import _load_config
            cfg = _load_config()
            ncfg = get_notion_cfg(cfg)
            try:
                minutes = int(ncfg.get("sync_minutes") or 15)
            except (TypeError, ValueError):
                minutes = 15
            minutes = max(1, minutes)
            if not is_sync_armed(cfg):
                time.sleep(300)
                continue
            out = run_notion_sync(cfg)
            if out.get("created_or_updated"):
                logger.info(
                    "notion sync upserted=%s errors=%s by_db=%s",
                    out.get("created_or_updated"),
                    out.get("errors"),
                    out.get("by_db"),
                )
        except Exception as e:
            logger.warning("notion sync loop failed: %s", e)
        time.sleep(minutes * 60)
