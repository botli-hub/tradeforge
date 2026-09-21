"""Fail-closed quote quality shared by scanning, boards and execution previews."""
from datetime import datetime, timezone
import math


def quote_is_fresh(asof, *, now=None, max_age_seconds=300):
    try:
        stamp = datetime.fromisoformat(str(asof).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.astimezone(timezone.utc)  # old local timestamps
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.astimezone(timezone.utc)
        age = (current - stamp.astimezone(timezone.utc)).total_seconds()
        return 0 <= age <= max_age_seconds
    except (ValueError, TypeError):
        return False


def executable_quote(item, *, max_spread_pct=8, now=None):
    try:
        bid, ask = float(item.get("bid") or 0), float(item.get("ask") or 0)
        if not all(math.isfinite(v) for v in (bid, ask)) or bid <= 0 or ask < bid:
            return False
        if (ask - bid) / ((ask + bid) / 2) * 100 > max_spread_pct:
            return False
        if item.get("stale") or item.get("quote_delayed"):
            return False
        return quote_is_fresh(item.get("quote_asof"), now=now)
    except (TypeError, ValueError):
        return False
