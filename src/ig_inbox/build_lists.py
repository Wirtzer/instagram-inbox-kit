"""Rebuild the curated markdown list files in data/lists/ from captures.jsonl.

Deterministic full regeneration (no LLM): safe to run any time; output is always
the complete current state. These are the human-/grep-friendly companion to the
Excel workbook.

    python -m ig_inbox.build_lists
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import config

HEADER = ("> Auto-maintained by ig-inbox from Instagram shares — updated {now}. "
          "Don't edit; changes are overwritten.\n")


def _load(captures: Path) -> list[dict]:
    if not captures.exists():
        return []
    recs = []
    for line in captures.read_text().split("\n"):
        if line.strip():
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    recs.sort(key=lambda r: r.get("ts", ""))
    return recs


def _src(r: dict) -> str:
    return r.get("permalink") or r.get("url") or ""


def _date(r: dict) -> str:
    return (r.get("ts") or "")[:10]


def _write(lists_dir: Path, name: str, body: list[str], now: str) -> None:
    lists_dir.mkdir(parents=True, exist_ok=True)
    (lists_dir / name).write_text("\n".join([HEADER.format(now=now), *body, ""]))


def build_books(recs, now, lists_dir):
    body = ["# 📚 Books", ""]
    seen: set[str] = set()
    count = 0
    for r in recs:
        books = (r.get("entities") or {}).get("books") or []
        fresh = []
        for b in books:
            key = "".join(c for c in b.get("title", "").lower() if c.isalnum())
            if key and key not in seen:
                seen.add(key)
                fresh.append(b)
        if not fresh:
            continue
        summary = (r.get("summary") or "").split(".")[0].strip()
        body.append(f"## {summary or 'Book recommendations'}  ·  {_date(r)}")
        if _src(r):
            body.append(f"[source post]({_src(r)})")
        body.append("")
        for b in fresh:
            author = f" — {b['author']}" if b.get("author") else ""
            body.append(f"- **{b['title']}**{author}")
            count += 1
        body.append("")
    body.insert(2, f"*{count} books, grouped by the post that recommended them.*\n")
    _write(lists_dir, "Books.md", body, now)


def build_ai_ideas(recs, now, lists_dir):
    items = [r for r in recs if r.get("category") == "ai_idea"]
    body = ["# 🤖 AI Ideas & Builds", "", f"*{len(items)} captured ideas, newest first.*", ""]
    for r in reversed(items):
        tools = ", ".join((r.get("entities") or {}).get("tools") or [])
        body.append(f"### {_date(r)} — @{r.get('author') or 'unknown'}")
        body.append(r.get("summary") or "")
        if tools:
            body.append(f"*Tools:* {tools}")
        if _src(r):
            body.append(f"[source]({_src(r)})")
        body.append("")
    _write(lists_dir, "AI Ideas.md", body, now)


def build_finance(recs, now, lists_dir):
    items = [r for r in recs if r.get("category") == "finance"]
    tickers = Counter(t for r in items for t in (r.get("entities") or {}).get("tickers") or [])
    body = ["# 📈 Finance & Stocks", ""]
    if tickers:
        body.append("**Tickers mentioned:** " + ", ".join(
            f"{t} ({n}×)" if n > 1 else t for t, n in tickers.most_common()))
        body.append("")
    body.append(f"*{len(items)} captures, newest first.*\n")
    for r in reversed(items):
        body.append(f"### {_date(r)} — @{r.get('author') or 'unknown'}")
        body.append(r.get("summary") or "")
        if _src(r):
            body.append(f"[source]({_src(r)})")
        body.append("")
    _write(lists_dir, "Finance.md", body, now)


def build_research(recs, now, lists_dir):
    items = [r for r in recs if r.get("category") == "research"]
    body = ["# 🔬 Research Topics", "", f"*{len(items)} things to dig into, newest first.*", ""]
    for r in reversed(items):
        topics = ", ".join((r.get("entities") or {}).get("topics") or [])
        body.append(f"### {_date(r)} — @{r.get('author') or 'unknown'}")
        body.append(r.get("summary") or "")
        if topics:
            body.append(f"*Topics:* {topics}")
        if _src(r):
            body.append(f"[source]({_src(r)})")
        body.append("")
    _write(lists_dir, "Research.md", body, now)


def build_collections(recs, now, lists_dir) -> list[str]:
    """One list file per shared IG collection the user saves into."""
    by_coll: dict[str, list[dict]] = {}
    for r in recs:
        name = (r.get("collection") or "").strip()
        if name:
            by_coll.setdefault(name, []).append(r)
    for name, items in by_coll.items():
        emoji = {"restaurants": "🍽️", "recipes": "🍳", "travel": "✈️",
                 "places": "📍"}.get(name.lower(), "📌")
        body = [f"# {emoji} {name}", "", f"*{len(items)} saved, newest first.*", ""]
        for r in sorted(items, key=lambda r: r.get("ts", ""), reverse=True):
            body.append(f"### {_date(r)} — @{r.get('author') or 'unknown'}")
            body.append(r.get("summary") or "")
            if _src(r):
                body.append(f"[open]({_src(r)})")
            body.append("")
        safe = re.sub(r"[^\w &-]", "", name).strip() or "Collection"
        _write(lists_dir, f"{safe}.md", body, now)
    return list(by_coll.keys())


def build_all(captures_path: Path | None = None, lists_dir: Path | None = None) -> int:
    captures_path = captures_path or config.CAPTURES_FILE
    lists_dir = lists_dir or config.LISTS_DIR
    recs = _load(captures_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    build_books(recs, now, lists_dir)
    build_ai_ideas(recs, now, lists_dir)
    build_finance(recs, now, lists_dir)
    build_research(recs, now, lists_dir)
    build_collections(recs, now, lists_dir)
    return len(recs)


if __name__ == "__main__":
    print(f"lists rebuilt from {build_all()} captures → {config.LISTS_DIR}")
