"""Master book list: dedupe every book mentioned across all captures, enrich
each unique title once, persist to data/masters/books.json.

Enrichment: genre + author + about come from the classify model's own knowledge
(instant, one LLM call for a batch — accurate for known books). Ratings come from
the web-enrichment adapter (disabled by default; Goodreads has no public API).
Idempotent — only new titles are enriched; ratings retried only if still blank.

    python -m ig_inbox.enrich_books [rating_budget]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import config, digest as digest_mod
from .adapters import enrich, llm


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _collect_unique(captures: Path) -> dict[str, dict]:
    """All distinct books across captures → {norm_title: {title, author, sources, recommenders}}."""
    out: dict[str, dict] = {}
    if not captures.exists():
        return out
    for line in captures.read_text().split("\n"):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        src = r.get("permalink") or r.get("url") or ""
        poster = (r.get("author") or "").strip()
        for b in (r.get("entities") or {}).get("books") or []:
            title = (b.get("title") or "").strip()
            if not title:
                continue
            k = _norm(title)
            if k not in out:
                out[k] = {"title": title, "author": (b.get("author") or "").strip(),
                          "sources": [], "recommenders": []}
            if not out[k]["author"] and b.get("author"):
                out[k]["author"] = b["author"].strip()
            if src and src not in out[k]["sources"]:
                out[k]["sources"].append(src)
            rec = {"handle": poster, "why": (b.get("why") or "").strip()}
            if (poster or rec["why"]) and rec not in out[k]["recommenders"]:
                out[k]["recommenders"].append(rec)
    return out


def _knowledge_enrich(batch: list[dict]) -> dict[str, dict]:
    """One LLM call: genre + author + about for a batch of books."""
    items = [{"title": b["title"], "author": b.get("author", "")} for b in batch]
    system = "You are a precise literary reference. Reply with ONLY valid JSON."
    user = (
        "For each book below, give its literary genre, author (correct/complete it "
        "if missing or wrong), and a 1-2 sentence 'about'. Reply with ONLY a JSON "
        'array, same order: [{"title":"","author":"","genre":"","about":""}]. Books:\n'
        + json.dumps(items, ensure_ascii=False))
    raw = llm.complete(system, user)
    if not raw:
        return {}
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return {}
    try:
        arr = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return {_norm(x.get("title", "")): x for x in arr if isinstance(x, dict)}


def build(rating_budget: int = 200, captures: Path | None = None,
          master_path: Path | None = None) -> dict[str, Any]:
    """Refresh the master. Returns {total, new_enriched, rated}."""
    captures = captures or config.CAPTURES_FILE
    master_path = master_path or config.BOOKS_MASTER_FILE
    master_path.parent.mkdir(parents=True, exist_ok=True)
    master: dict[str, dict] = {}
    if master_path.exists():
        try:
            master = json.loads(master_path.read_text())
        except json.JSONDecodeError:
            master = {}

    unique = _collect_unique(captures)
    for k, info in unique.items():
        if k in master:
            master[k]["sources"] = sorted(set(master[k].get("sources", [])) | set(info["sources"]))
            recs = master[k].get("recommenders", [])
            for rec in info.get("recommenders", []):
                if rec not in recs:
                    recs.append(rec)
            master[k]["recommenders"] = recs
            if not master[k].get("author") and info["author"]:
                master[k]["author"] = info["author"]
        else:
            master[k] = {**info, "genre": "", "about": "", "rating": ""}

    # Knowledge enrich (genre/author/about) in batches.
    todo = [b for b in master.values() if not b.get("genre") or not b.get("about")]
    new_enriched = 0
    for i in range(0, len(todo), 20):
        got = _knowledge_enrich(todo[i:i + 20])
        for b in todo[i:i + 20]:
            g = got.get(_norm(b["title"]))
            if g:
                b["genre"] = str(g.get("genre", ""))[:80] or b.get("genre", "")
                b["about"] = str(g.get("about", ""))[:400] or b.get("about", "")
                if g.get("author") and not b.get("author"):
                    b["author"] = str(g["author"])[:200]
                new_enriched += 1

    # Ratings (best-effort web enrichment), batched.
    rated = 0
    if enrich.enabled():
        need_rating = [b for b in master.values() if not b.get("rating")][:rating_budget]
        for i in range(0, len(need_rating), 10):
            chunk = need_rating[i:i + 10]
            got = enrich.lookup_book_ratings(
                [{"title": b["title"], "author": b.get("author", "")} for b in chunk])
            for b in chunk:
                r = got.get(b["title"].strip().lower(), "")
                b["rating"] = r
                if r:
                    rated += 1
            master_path.write_text(json.dumps(master, ensure_ascii=True, indent=1))

    master_path.write_text(json.dumps(master, ensure_ascii=True, indent=1))
    return {"total": len(master), "new_enriched": new_enriched, "rated": rated}


if __name__ == "__main__":
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print(build(rating_budget=budget))
