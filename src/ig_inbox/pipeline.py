"""Orchestrator: poll → fetch → extract → digest → store → ack.

Invoked by scripts/run.sh (or directly). Flags for manual testing:
  --dry-run     poll and print what WOULD be processed; no state change
  --only ID     process a single item id (must be visible in the DM window)
  --no-send     skip IG DM acks
  --no-store    skip storage sinks (captures/digest/reading-list/kb)
  --max-items N safety cap per run (default 25)
  --backlog N   one-off: page N messages deep into thread history
  --mark-acked  with --no-send: mark done items acked (prevents an ack flood later)

Exit codes: 0 ok · 2 session dead · 3 challenge required · 4 transient · 1 other
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config, digest as digest_mod, extract, feedback, route_store
from .ig_client import IgClient, IgError, SessionDead

STALE_PROCESSING = timedelta(hours=2)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_file() -> Path:
    return config.STATE_DIR / "processed.json"


def load_state() -> dict[str, Any]:
    sf = _state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text())
        except json.JSONDecodeError:
            print("WARN: processed.json corrupt — starting fresh ledger", file=sys.stderr)
    return {"items": {}, "heartbeat": None}


def save_state(state: dict[str, Any]) -> None:
    sf = _state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    tmp = sf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.rename(sf)


def safe_id(item_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", item_id)


LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?"
                     r"(github\.com|gitlab\.com|huggingface\.co|npmjs\.com|pypi\.org|"
                     r"[a-z0-9-]+\.(?:com|io|ai|dev|app|co|org|net))"
                     r"(/[\w./#?=&%-]*)?", re.I)


def extract_links(record: dict) -> list[str]:
    """External links (repos/tools/sites) from caption + OCR — deterministic,
    zero tokens. OCR often drops the scheme, so bare domains count."""
    hay = f"{record.get('caption', '')}\n{record.get('ocr_text', '')}"
    out: list[str] = []
    for m in LINK_RE.finditer(hay):
        dom, path = m.group(1).lower(), (m.group(2) or "").rstrip(".,)")
        if "instagram" in dom or dom.endswith("cdninstagram.com"):
            continue
        url = f"https://{dom}{path}"
        if url not in out:
            out.append(url)
    return out[:10]


def _try_auto_relogin() -> bool:
    """Self-heal a dead session: ONE non-interactive relogin per 12h.

    CHALLENGE HOLD: while Instagram has an unresolved challenge, every login
    attempt is flag fuel that keeps the account suspicious. Once a challenge is
    seen, STOP attempting until a human login succeeds (ig_login clears the hold).
    """
    guard = config.STATE_DIR / "relogin-attempt.json"
    now = datetime.now(timezone.utc)
    if guard.exists():
        try:
            last = datetime.fromisoformat(
                json.loads(guard.read_text())["ts"].replace("Z", "+00:00"))
            if now - last < timedelta(hours=12):
                return False
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    hold = config.STATE_DIR / "challenge-hold.json"
    if hold.exists():
        print("auto-relogin held: unresolved Instagram challenge (needs a human)",
              file=sys.stderr)
        return False
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text(json.dumps({"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ")}))
    print("session dead — attempting guarded auto-relogin", file=sys.stderr)
    p = subprocess.run([sys.executable, "-m", "ig_inbox.ig_login", "--force"],
                       capture_output=True, text=True, timeout=300)
    if p.returncode == 0:
        print("auto-relogin succeeded", file=sys.stderr)
        return True
    if p.returncode == 3:  # ChallengeRequired — engage the hold
        hold.write_text(json.dumps({"since": now.strftime("%Y-%m-%dT%H:%M:%SZ")}))
        print("challenge detected — auto-relogin HELD until manual fix", file=sys.stderr)
    print(f"auto-relogin failed rc={p.returncode}: {(p.stderr or '')[-200:]}", file=sys.stderr)
    return False


def download_media(item: dict, details: dict, workdir: Path) -> Path | None:
    """Video (or photo) → workdir. CDN URL first, yt-dlp fallback. None = unavailable."""
    video_path = workdir / "video.mp4"
    photo_path = workdir / "photo.jpg"
    if video_path.exists():
        return video_path
    if photo_path.exists():
        return photo_path

    url = details.get("video_url") or details.get("thumbnail_url")
    is_video = bool(details.get("video_url"))
    if url and any(h in url for h in config.ALLOWED_MEDIA_HOSTS):
        target = video_path if is_video else photo_path
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, target.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
            if target.stat().st_size > 1024:
                return target
            target.unlink(missing_ok=True)
        except Exception as exc:
            print(f"WARN: CDN download failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    # Optional yt-dlp fallback (only if installed).
    permalink = details.get("permalink")
    ytdlp = shutil.which("yt-dlp")
    if permalink and ytdlp:
        try:
            subprocess.run([ytdlp, permalink, "-o", str(video_path), "--no-playlist"],
                           capture_output=True, text=True, timeout=300)
            if video_path.exists() and video_path.stat().st_size > 1024:
                return video_path
        except Exception as exc:
            print(f"WARN: yt-dlp fallback errored: {exc}", file=sys.stderr)
    return None


def process_item(client: IgClient, cfg: dict, item: dict, no_store: bool) -> dict:
    """Fetch/extract/digest/store one item → capture record. Raises only on IgError."""
    capture_id = f"ig_{safe_id(item['item_id'])}"
    workdir = config.MEDIA_DIR / capture_id
    workdir.mkdir(parents=True, exist_ok=True)

    # Plain typed note: no media to fetch — digest the text directly.
    if item.get("kind") == "note":
        record = {
            "id": capture_id, "ts": item.get("ts") or now_iso(), "kind": "note",
            "permalink": None, "url": None, "author": "note",
            "caption": item.get("text", ""), "collection": item.get("collection"),
            "transcript": "", "ocr_text": "",
        }
        record.update(digest_mod.digest(record["caption"], "", ""))
        record["links"] = extract_links(record)
        added_books = [] if no_store else route_store.store_all(record)
        record["ack_text"] = route_store.compose_ack(record, added_books, cfg["ack_max_chars"])
        return record

    media_pk = item.get("media_pk")
    if not media_pk and item.get("url"):
        media_pk = client.media_pk_from_url(item["url"])

    details: dict[str, Any] = {}
    if media_pk:
        details = client.media_details(media_pk)

    record: dict[str, Any] = {
        "id": capture_id,
        "ts": item.get("ts") or now_iso(),
        "kind": item.get("kind"),
        "permalink": details.get("permalink"),
        "url": item.get("url"),
        "author": details.get("author") or item.get("xma_author"),
        "caption": details.get("caption", "") or item.get("text", ""),
        "collection": item.get("collection"),
        "transcript": "",
        "ocr_text": "",
    }

    media_path = None
    if details:
        media_path = download_media(item, details, workdir)
        if media_path is None:
            record["media_unavailable"] = True

    if media_path and media_path.suffix == ".mp4":
        record["transcript"] = extract.transcribe(
            media_path, workdir,
            min_words=cfg["min_transcript_words"], volume_gate_db=cfg["volume_gate_db"])
        frames = extract.dedupe_frames(
            extract.extract_frames(media_path, workdir, cfg["max_frames"]),
            cfg["frame_changed_pct"])
        record["ocr_text"] = extract.ocr_frames(frames)
        record["frames_kept"] = len(frames)
        if frames and not record["ocr_text"]:
            record["ocr_empty"] = True
    elif media_path:  # photo
        record["ocr_text"] = extract.ocr_frames([media_path])

    result = digest_mod.digest(record["caption"], record["transcript"], record["ocr_text"])
    record.update(result)
    record["links"] = extract_links(record)
    # Optional "fit for you?" take — ONLY for ai_idea posts. Other categories
    # never pay the extra call.
    if record.get("category") == "ai_idea":
        try:
            from . import assess
            record["assessment"] = assess.assess(record)
        except Exception as exc:
            print(f"WARN: assessment failed: {exc}", file=sys.stderr)

    added_books: list[str] = []
    if not no_store:
        added_books = route_store.store_all(record)
    record["ack_text"] = route_store.compose_ack(record, added_books, cfg["ack_max_chars"])
    return record


def _post_process(should_rebuild: bool, no_store: bool) -> None:
    """After a run that processed items OR applied corrections: enrich, rebuild
    lists + workbook (which recomputes metrics)."""
    if not should_rebuild or no_store:
        return
    from . import build_lists, build_workbook, enrich_books, enrich_restaurants
    try:
        recs = [json.loads(l) for l in config.CAPTURES_FILE.read_text().split("\n") if l.strip()]
        enrich_restaurants.enrich_records(recs, max_per_run=4,
                                          persist=route_store.rewrite_captures)
    except Exception as exc:
        print(f"WARN: restaurant enrichment failed: {exc}", file=sys.stderr)
    try:
        enrich_books.build(rating_budget=40)
    except Exception as exc:
        print(f"WARN: book master build failed: {exc}", file=sys.stderr)
    try:
        build_lists.build_all()
    except Exception as exc:
        print(f"WARN: list rebuild failed: {exc}", file=sys.stderr)
    try:
        build_workbook.build()
    except Exception as exc:
        print(f"WARN: workbook build failed: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", "--list-only", action="store_true", dest="dry_run")
    ap.add_argument("--only")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--max-items", type=int, default=25)
    ap.add_argument("--backlog", type=int, default=0)
    ap.add_argument("--mark-acked", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    cfg = config.load_config()
    if not cfg.get("allowed_sender_pks"):
        print("ERROR: config.json allowed_sender_pks is empty — run "
              "`python -m ig_inbox.ig_login` first", file=sys.stderr)
        return 1

    # FEEDBACK LOOP: harvest the user's category overrides from the EXISTING
    # workbook (and the manual inbox file) BEFORE anything regenerates it, log
    # them, and pin the corrected records. Must happen before _post_process.
    n_corr = 0
    if not args.no_store:
        try:
            n_corr = feedback.ingest_corrections()
        except Exception as exc:
            print(f"WARN: correction ingest failed: {exc}", file=sys.stderr)

    state = load_state()
    items_state: dict[str, Any] = state["items"]

    try:
        client = IgClient()
    except IgError as exc:
        if isinstance(exc, SessionDead) and _try_auto_relogin():
            try:
                client = IgClient()
            except IgError as exc2:
                print(f"ERROR: relogin ok but client still failing: {exc2}", file=sys.stderr)
                return exc2.exit_code
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
            return exc.exit_code

    known: set[str] = set()
    cutoff = datetime.now(timezone.utc) - STALE_PROCESSING
    for iid, meta in items_state.items():
        if meta.get("status") == "processing":
            started = meta.get("started_at")
            try:
                if started and datetime.fromisoformat(started.replace("Z", "+00:00")) > cutoff:
                    known.add(iid)
            except ValueError:
                pass
        else:
            known.add(iid)

    seen_file = config.STATE_DIR / "seen-ids.json"
    try:
        seen_ids: set[str] = set(json.loads(seen_file.read_text())) if seen_file.exists() else set()
    except json.JSONDecodeError:
        seen_ids = set()

    try:
        if args.backlog:
            new_items = client.fetch_thread_history(
                allowed_sender_pks=set(cfg["allowed_sender_pks"]),
                known_ids=known, amount=args.backlog)
        else:
            new_items = client.fetch_new_items(
                allowed_sender_pks=set(cfg["allowed_sender_pks"]),
                known_ids=known,
                thread_amount=cfg.get("thread_amount", 5),
                thread_message_limit=cfg["thread_message_limit"],
                seen_ids=seen_ids)
            seen_file.write_text(json.dumps(sorted(seen_ids, reverse=True)[:2000]))
    except IgError as exc:
        print(f"ERROR: poll failed: {exc}", file=sys.stderr)
        return exc.exit_code

    coll_map = client.fetch_collection_map()
    for i in new_items:
        if i["item_id"] in coll_map:
            i["collection"] = coll_map[i["item_id"]]

    if args.only:
        new_items = [i for i in new_items if i["item_id"] == args.only]

    if args.dry_run:
        print(json.dumps({"new_items": new_items}, indent=2, default=str))
        return 0

    print(f"coverage: {len(new_items)} new item(s) since last check "
          f"(ledger has {len(items_state)} tracked)", file=sys.stderr)

    if len(new_items) > args.max_items:
        print(f"INFO: {len(new_items)} new exceeds per-run cap {args.max_items}; "
              f"processing oldest {args.max_items}, rest next run", file=sys.stderr)
    new_items = new_items[:args.max_items]
    hard_failure: IgError | None = None

    for item in new_items:
        iid = item["item_id"]
        meta = items_state.get(iid, {"attempts": 0})
        if meta.get("attempts", 0) >= cfg["max_item_attempts"]:
            meta["status"] = "failed"
            items_state[iid] = meta
            continue
        meta.update({"status": "processing", "started_at": now_iso(),
                     "attempts": meta.get("attempts", 0) + 1,
                     "thread_id": item["thread_id"]})
        items_state[iid] = meta
        save_state(state)  # write-then-act: a crash here is visible + recoverable

        try:
            record = process_item(client, cfg, item, no_store=args.no_store)
            meta.update({"status": "done", "capture_id": record["id"],
                         "category": record.get("category"),
                         "ack_text": record["ack_text"], "ack_sent": False})
            print(f"processed {iid} → {record.get('category')}")
        except IgError as exc:
            print(f"ERROR: item {iid}: {exc}", file=sys.stderr)
            meta["status"] = "processing"
            hard_failure = exc
        except Exception as exc:  # one bad item never blocks the rest
            print(f"ERROR: item {iid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            meta["status"] = "processing"
        items_state[iid] = meta
        save_state(state)
        if isinstance(hard_failure, IgError) and hard_failure.exit_code in (2, 3):
            break

    if args.no_send and args.mark_acked:
        for meta in items_state.values():
            if meta.get("status") == "done" and not meta.get("ack_sent"):
                meta["ack_sent"] = True
                meta["ack_suppressed"] = "backlog"
    if not args.no_send:
        for iid, meta in items_state.items():
            if meta.get("status") == "done" and not meta.get("ack_sent") and meta.get("ack_text"):
                if client.send_reply(meta.get("thread_id", ""), meta["ack_text"]):
                    meta["ack_sent"] = True
                    meta["acked_at"] = now_iso()
                else:
                    print(f"WARN: ack send failed for {iid} — will retry next run",
                          file=sys.stderr)

    _post_process(bool(new_items) or n_corr > 0, args.no_store)

    state["heartbeat"] = now_iso()
    save_state(state)
    return hard_failure.exit_code if hard_failure is not None else 0


if __name__ == "__main__":
    sys.exit(main())
