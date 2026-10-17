"""Per-day aggregation of the usage recorded on attempts; read only, never estimates."""

import json
import sqlite3
from typing import Any

METRICS = ("input", "cached_input", "output", "cost_usd")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def daily(connection: sqlite3.Connection, *, since: str) -> list[dict[str, Any]]:
    """Bucket attempt usage by UTC day and provider for days on or after ``since``.

    A metric's ``value`` is ``None`` when no attempt in the bucket reported it; attempts that did
    not are counted in ``unknown`` instead of being treated as zero.
    """
    rows = connection.execute(
        "SELECT substr(coalesce(a.t_launch, a.t_queued), 1, 10) AS day, e.provider, a.usage "
        "FROM attempts a JOIN executions e ON e.id = a.execution_id "
        "WHERE coalesce(a.t_launch, a.t_queued) IS NOT NULL "
        "AND substr(coalesce(a.t_launch, a.t_queued), 1, 10) >= ?", (since, ))
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for day, provider, text in rows:
        try:
            usage = json.loads(text) if text else None
        except ValueError:
            usage = None
        bucket = buckets.setdefault(
            (day, provider), {
                "day": day,
                "provider": provider,
                "attempts": 0,
                **{
                    key: {
                        "value": None,
                        "unknown": 0
                    }
                    for key in METRICS
                }
            })
        bucket["attempts"] += 1
        for key in METRICS:
            value = usage.get(key) if isinstance(usage, dict) else None
            metric = bucket[key]
            if _number(value):
                metric["value"] = (metric["value"] or 0) + value
            else:
                metric["unknown"] += 1
    return [buckets[key] for key in sorted(buckets)]
