"""Restaurant enrichment: look up address / hours / cuisine / rating online.

Only restaurants get this. It routes through adapters/enrich.py, which needs a
web-capable model (disabled by default — set ENRICH_BACKEND). Best-effort: any
failure leaves the capture with just what the post said.

Idempotent: only enriches records missing an `enrichment` block. Writes results
back onto the passed records in place; caller persists.
"""

from __future__ import annotations

from typing import Any

from .adapters import enrich


def enrich_records(records: list[dict[str, Any]], max_per_run: int = 12,
                   persist=None) -> int:
    """Enrich restaurant records lacking an `enrichment` block, one lookup each.
    `persist(records)` (if given) is called after each success so a long run's
    progress survives interruption. Returns count enriched."""
    if not enrich.enabled():
        return 0
    pending = [r for r in records
               if r.get("category") == "restaurant" and "enrichment" not in r
               and (r.get("title") or (r.get("details") or {}).get("name"))]
    enriched = 0
    for r in pending[:max_per_run]:
        d = r.get("details") or {}
        got = enrich.lookup_restaurant(
            name=r.get("title") or d.get("name") or "",
            location_hint=d.get("location_hint", ""),
            notes=(d.get("notes") or r.get("summary") or "")[:200],
        )
        r["enrichment"] = got or {}
        if got:
            enriched += 1
        if persist:
            persist(records)
    return enriched
