"""Storage + routing. All decisions are switch(category) in code — neither the
LLM nor the shared content can name an action; the worst a malicious caption can
do is land in the wrong bucket.

Writes per capture:
  data/captures.jsonl        — canonical structured record (upsert by id)
  data/digests/YYYY-MM.md    — human-readable monthly digest
  data/reading-list.md       — books only, deduped by normalized title
  adapters.kb.on_capture     — optional hook into your own knowledge base

DURABILITY — every writer of captures.jsonl takes an exclusive file lock, and
writes go through a temp file + atomic rename. That combination is what prevents
a concurrent backfill and a live poll from interleaving and corrupting the file
(a real near-miss upstream), and prevents a crash mid-write from leaving a
half-written line.
"""

from __future__ import annotations

import fcntl
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .adapters import kb

CATEGORY_LABEL = {
    "book": "books", "ai_idea": "AI idea", "finance": "finance",
    "research": "research", "other": "saved",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize(text: str, cap: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:cap]


def _captures() -> Path:
    return config.CAPTURES_FILE


def _lock_path(captures: Path) -> Path:
    return captures.with_name(captures.name + ".lock")


@contextmanager
def _captures_lock(captures: Path):
    """Exclusive lock for ALL captures.jsonl writers."""
    lock = _lock_path(captures)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read_records(captures: Path) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if not captures.exists():
        return recs
    # split("\n") not splitlines(): captions can contain U+2028/U+2029, which
    # splitlines() treats as line breaks → corrupted records.
    for line in captures.read_text().split("\n"):
        if line.strip():
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return recs


def upsert_capture(record: dict[str, Any], captures: Path | None = None) -> None:
    """Append-or-replace by id (idempotent across crash-reprocess)."""
    captures = captures or _captures()
    captures.parent.mkdir(parents=True, exist_ok=True)
    with _captures_lock(captures):
        kept = [ln for ln in captures.read_text().split("\n")
                if ln.strip() and _line_id(ln) != record["id"]] if captures.exists() else []
        # ensure_ascii=True: every control/unicode char escaped → one record per line.
        kept.append(json.dumps(record, ensure_ascii=True))
        _atomic_write(captures, "\n".join(kept) + "\n")


def _line_id(line: str) -> str | None:
    try:
        return json.loads(line).get("id")
    except json.JSONDecodeError:
        return None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.rename(path)


def rewrite_captures(records: list[dict[str, Any]], captures: Path | None = None) -> None:
    """Overwrite captures.jsonl with the given records (e.g. after enrichment).
    ensure_ascii so no control char can split a record across lines. Locked."""
    captures = captures or _captures()
    captures.parent.mkdir(parents=True, exist_ok=True)
    with _captures_lock(captures):
        _atomic_write(captures, "\n".join(
            json.dumps(r, ensure_ascii=True) for r in records) + "\n")


def apply_updates(updates: dict[str, dict[str, Any]], captures: Path | None = None) -> int:
    """DURABLE partial update: under the lock, RE-READ the current file, merge
    `updates` (by id), write. Stale-write-proof — the read happens inside the
    lock, so a concurrent writer's changes are never clobbered. Compute changes
    WITHOUT the lock (slow LLM calls), then apply them atomically here."""
    if not updates:
        return 0
    captures = captures or _captures()
    with _captures_lock(captures):
        recs = _read_records(captures)
        n = 0
        for r in recs:
            u = updates.get(r.get("id"))
            if u:
                r.update(u)
                n += 1
        _atomic_write(captures, "\n".join(
            json.dumps(r, ensure_ascii=True) for r in recs) + "\n")
        return n


def append_monthly_digest(record: dict[str, Any]) -> None:
    config.DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    month_file = config.DIGESTS_DIR / f"{month}.md"
    if not month_file.exists():
        month_file.write_text(f"# Instagram captures — {month}\n\n"
                              "Content shared to the bot account, digested by ig-inbox.\n")
    marker = f"<!-- {record['id']} -->"
    if marker in month_file.read_text():
        return
    entities = record.get("entities") or {}
    lines = [
        "",
        f"## {record.get('ts', _now())[:10]} — {record['category']} — @{record.get('author') or 'unknown'} {marker}",
        "",
        f"**Summary:** {record.get('summary', '')}",
        f"**Source:** {record.get('permalink') or record.get('url') or 'n/a'}",
    ]
    if entities.get("books"):
        lines.append("**Books:** " + "; ".join(
            f"{b['title']}" + (f" — {b['author']}" if b.get("author") else "")
            for b in entities["books"]))
    if entities.get("tickers"):
        lines.append("**Tickers:** " + ", ".join(entities["tickers"]))
    if entities.get("tools"):
        lines.append("**Tools:** " + ", ".join(entities["tools"]))
    if entities.get("topics"):
        lines.append("**Topics:** " + ", ".join(entities["topics"]))
    with month_file.open("a") as fh:
        fh.write("\n".join(lines) + "\n")


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def append_reading_list(record: dict[str, Any]) -> list[str]:
    """Add new books; returns titles actually added (for the ack)."""
    books = (record.get("entities") or {}).get("books") or []
    if not books:
        return []
    rl = config.READING_LIST_FILE
    rl.parent.mkdir(parents=True, exist_ok=True)
    if not rl.exists():
        rl.write_text("# Reading list\n\nBooks captured from Instagram shares.\n\n")
    existing = {_norm_title(m) for m in re.findall(r"- \[.\] \*\*(.+?)\*\*", rl.read_text())}
    added: list[str] = []
    entries: list[str] = []
    for b in books:
        title = b.get("title", "").strip()
        if not title or _norm_title(title) in existing:
            continue
        existing.add(_norm_title(title))
        author = b.get("author", "").strip()
        src = record.get("permalink") or record.get("url") or ""
        entries.append(f"- [ ] **{title}**" + (f" — {author}" if author else "")
                       + f" ({record.get('ts', _now())[:10]}, [source]({src}))")
        added.append(title)
    if entries:
        with rl.open("a") as fh:
            fh.write("\n".join(entries) + "\n")
    return added


def compose_ack(record: dict[str, Any], added_books: list[str], max_chars: int) -> str:
    """Deterministic ack for the IG DM thread — never LLM-composed."""
    category = record["category"]
    label = CATEGORY_LABEL.get(category, "saved")
    kind = {"clip": "reel", "media_share": "post", "felix_share": "video",
            "xma_media_share": "reel", "link": "link"}.get(record.get("kind", ""), "post")
    author = record.get("author")
    collection = record.get("collection")
    dest = f"“{collection}”" if collection else label
    head = f"Got it — {kind}" + (f" from @{author}" if author else "") + f" → {dest}"

    body = _sanitize(record.get("summary", ""), 220)
    action = ""
    entities = record.get("entities") or {}
    if category == "book":
        if added_books:
            action = f"Added {len(added_books)} book(s) to your reading list: " + ", ".join(added_books[:6])
        else:
            action = "Books already on your reading list."
    elif category == "finance" and entities.get("tickers"):
        action = "Tickers noted: " + ", ".join(entities["tickers"][:8])
    elif category == "ai_idea" and entities.get("tools"):
        action = "Tools mentioned: " + ", ".join(entities["tools"][:8])
    elif category == "research" and entities.get("topics"):
        action = "Filed for research: " + ", ".join(entities["topics"][:6])

    flags = []
    if record.get("media_unavailable"):
        flags.append("(couldn't download the media — digest is caption-only)")
    if record.get("ocr_empty"):
        flags.append("(no on-screen text detected)")
    if record.get("degraded"):
        flags.append("(classifier was down — stored raw, will look closer later)")

    parts = [head, body, action, "Saved to knowledge base."] + flags
    return "\n".join(p for p in parts if p)[:max_chars]


def store_all(record: dict[str, Any]) -> list[str]:
    """Run every storage sink; returns added book titles for the ack."""
    upsert_capture(record)
    append_monthly_digest(record)
    added = append_reading_list(record)
    kb.on_capture(record)  # optional external knowledge-base hook (no-op default)
    return added
