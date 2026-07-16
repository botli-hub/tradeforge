"""富途持仓与 Wheel 台账对账

拉取 OpenD 期权/正股持仓,与 CSP_OPEN/CC_OPEN/HOLDING 比对,生成差异与登记草稿。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _to_plain_symbol(code: str) -> str:
    """US.AAPL -> AAPL; HK.00700 -> 00700.HK 简化。"""
    c = (code or "").strip()
    if c.startswith("US."):
        return c[3:]
    if c.startswith("HK."):
        return c[3:].zfill(5) + ".HK"
    return c


def fetch_futu_positions(host: str = "127.0.0.1", port: int = 11111,
                        trd_env: str = "SIMULATE") -> Dict[str, Any]:
    """返回 {options: [...], stocks: [...], errors: []}。"""
    import futu
    from app.core.leaps_monitor import _throttle

    errors: List[str] = []
    options: List[Dict[str, Any]] = []
    stocks: List[Dict[str, Any]] = []

    env = futu.TrdEnv.SIMULATE if str(trd_env).upper() in ("SIM", "SIMULATE") else futu.TrdEnv.REAL
    # 尝试 US / HK 市场
    for trd_mkt in (futu.TrdMarket.US, futu.TrdMarket.HK):
        try:
            ctx = futu.OpenSecTradeContext(filter_trdmarket=trd_mkt, host=host, port=port)
            try:
                _throttle()
                ret, data = ctx.position_list_query(trd_env=env)
                if ret != futu.RET_OK or data is None:
                    errors.append(f"{trd_mkt} position_list: {data}")
                    continue
                if getattr(data, "empty", True):
                    continue
                for _, row in data.iterrows():
                    code = str(row.get("code", ""))
                    qty = float(row.get("qty", 0) or 0)
                    if abs(qty) < 1e-9:
                        continue
                    cost = float(row.get("cost_price", 0) or 0)
                    item = {
                        "code": code,
                        "symbol": _to_plain_symbol(code),
                        "qty": qty,
                        "cost_price": cost,
                        "market_val": float(row.get("market_val", 0) or 0),
                        "pl_val": float(row.get("pl_val", 0) or 0),
                        "stock_name": str(row.get("stock_name", "") or ""),
                    }
                    # 期权代码通常含更长结构
                    if len(code) > 12 or "C" in code[6:] or "P" in code[6:] or code.count(".") >= 1 and any(
                        x in code for x in ("C", "P")
                    ):
                        # 粗判:美股期权 US.AAPL250117C150000
                        if any(ch.isdigit() for ch in code) and ("C" in code or "P" in code):
                            side = "CALL" if "C" in code.split(".")[-1] else "PUT"
                            item["option_type"] = side
                            item["is_option"] = True
                            options.append(item)
                            continue
                    item["is_option"] = False
                    stocks.append(item)
            finally:
                ctx.close()
        except Exception as e:
            errors.append(f"{trd_mkt}: {e}")

    return {"options": options, "stocks": stocks, "errors": errors}


def reconcile(host: str = "127.0.0.1", port: int = 11111,
              trd_env: str = "SIMULATE") -> Dict[str, Any]:
    from app.data import wheel_repository as repo

    try:
        futu_pos = fetch_futu_positions(host, port, trd_env)
    except Exception as e:
        return {"ok": False, "error": str(e), "diffs": [], "drafts": []}

    cycles = [c for c in repo.get_cycles(include_closed=False)]
    open_opt = {
        c["open_contract_code"]: c
        for c in cycles
        if c["status"] in ("CSP_OPEN", "CC_OPEN") and c.get("open_contract_code")
    }
    holding_by_sym = {
        c["symbol"]: c for c in cycles if c["status"] == "HOLDING" and (c.get("shares") or 0) > 0
    }

    diffs: List[Dict[str, Any]] = []
    drafts: List[Dict[str, Any]] = []

    # 期权:台账有、富途无 → 可能已平未记
    futu_opt_codes = {o["code"] for o in futu_pos["options"]}
    futu_opt_codes |= {o["code"].replace("US.", "") for o in futu_pos["options"]}
    for code, c in open_opt.items():
        variants = {code, code.replace("US.", ""), f"US.{code}" if not code.startswith("US.") else code}
        if not variants & futu_opt_codes:
            diffs.append({
                "type": "ledger_only_option",
                "severity": "warning",
                "cycle_id": c["id"],
                "symbol": c["symbol"],
                "contract_code": code,
                "message": "台账有在场期权,富途未见持仓(可能已平仓未登记)",
            })
            drafts.append({
                "action": "suggest_close",
                "cycle_id": c["id"],
                "symbol": c["symbol"],
                "trade_type": "BUY_PUT_CLOSE" if c.get("open_option_type") == "PUT" else "BUY_CALL_CLOSE",
                "contract_code": code,
                "strike": c.get("open_strike"),
                "expiry": c.get("open_expiry"),
                "qty": c.get("open_qty") or 1,
                "price": 0,
                "note": "对账草稿:请填实际买回价",
            })

    # 期权:富途有、台账无
    ledger_codes = set()
    for code in open_opt:
        ledger_codes.add(code)
        ledger_codes.add(code.replace("US.", ""))
        if not code.startswith("US."):
            ledger_codes.add(f"US.{code}")

    for o in futu_pos["options"]:
        if o["qty"] >= 0:
            # Wheel 卖方应为负持仓(空头);若接口给绝对值需结合字段
            pass
        code = o["code"]
        plain = code.replace("US.", "")
        if code not in ledger_codes and plain not in ledger_codes:
            # 空头期权才相关
            if o["qty"] < 0 or True:  # 兼容:部分环境 qty 为正表示张数
                diffs.append({
                    "type": "futu_only_option",
                    "severity": "info",
                    "contract_code": code,
                    "symbol": o.get("symbol"),
                    "qty": o["qty"],
                    "cost_price": o["cost_price"],
                    "message": "富途有期权持仓,台账无在场记录",
                })
                side = o.get("option_type") or "PUT"
                drafts.append({
                    "action": "suggest_open",
                    "symbol": _underlying_from_option(code) or o.get("symbol"),
                    "trade_type": "SELL_PUT" if side == "PUT" else "SELL_CALL",
                    "contract_code": code,
                    "qty": abs(o["qty"]) or 1,
                    "price": o.get("cost_price") or 0,
                    "note": "对账草稿:请核对 strike/expiry 后登记",
                })

    # 正股:持股状态
    for s in futu_pos["stocks"]:
        sym = s["symbol"]
        if s["qty"] > 0 and sym not in holding_by_sym:
            # 可能 ASSIGNED 未记或 BUY_SHARES 未记
            has_cc = any(
                c["symbol"] == sym and c["status"] == "CC_OPEN" for c in cycles
            )
            if not has_cc:
                diffs.append({
                    "type": "futu_only_stock",
                    "severity": "info",
                    "symbol": sym,
                    "qty": s["qty"],
                    "cost_price": s["cost_price"],
                    "message": "富途有正股,台账无 HOLDING/CC 周期",
                })
                drafts.append({
                    "action": "suggest_shares",
                    "symbol": sym,
                    "trade_type": "BUY_SHARES",
                    "qty": s["qty"],
                    "price": s["cost_price"],
                    "note": "对账草稿:已持正股入轮",
                })

    for sym, c in holding_by_sym.items():
        futu_qty = sum(s["qty"] for s in futu_pos["stocks"] if s["symbol"] == sym)
        if futu_qty <= 0:
            diffs.append({
                "type": "ledger_only_stock",
                "severity": "warning",
                "cycle_id": c["id"],
                "symbol": sym,
                "shares": c.get("shares"),
                "message": "台账 HOLDING 但富途无对应正股",
            })

    return {
        "ok": True,
        "futu": {
            "option_count": len(futu_pos["options"]),
            "stock_count": len(futu_pos["stocks"]),
            "options": futu_pos["options"][:50],
            "stocks": futu_pos["stocks"][:50],
            "errors": futu_pos["errors"],
        },
        "diffs": diffs,
        "drafts": drafts,
        "summary": {
            "diff_count": len(diffs),
            "draft_count": len(drafts),
            "warnings": sum(1 for d in diffs if d.get("severity") == "warning"),
        },
    }


def _underlying_from_option(code: str) -> Optional[str]:
    c = code.replace("US.", "")
    # AAPL250117C00150000
    i = 0
    while i < len(c) and c[i].isalpha():
        i += 1
    if i > 0:
        return c[:i]
    return None


def apply_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """将草稿登记为 trade(需调用方补全 price 等)。"""
    from app.data import wheel_repository as repo

    required = ["symbol", "trade_type"]
    for k in required:
        if not draft.get(k):
            raise ValueError(f"draft 缺少 {k}")
    body = {
        "symbol": draft["symbol"],
        "trade_type": draft["trade_type"],
        "contract_code": draft.get("contract_code"),
        "strike": draft.get("strike"),
        "expiry": draft.get("expiry"),
        "qty": draft.get("qty") or 1,
        "price": draft.get("price") or 0,
        "fee": draft.get("fee") or 0,
        "contract_size": draft.get("contract_size") or 100,
        "note": draft.get("note") or "对账登记",
        "cycle_id": draft.get("cycle_id"),
    }
    return repo.record_trade(**{k: v for k, v in body.items() if v is not None or k in ("price", "fee", "qty")})
