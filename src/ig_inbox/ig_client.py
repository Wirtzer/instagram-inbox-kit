"""Instagram client wrapper — session hygiene isolated in one place.

Login is ONLY resumed from the persisted session file (written by setup /
ig_login). Password login never happens from the polling loop: a dead session
raises SessionDead, which the pipeline turns into a one-time alert asking a human
to re-login. Re-logging in on every failure from an automated loop is exactly
what gets a watched account flagged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    ClientError,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from . import config

# Item types we ingest from DMs. Everything else (reactions, likes, placeholders,
# plain chatter) is ignored. NOTE: Instagram renames these over time
# (xma_media_share → xma_clip/xma_link) — any xma_* type is handled by the xma
# branch in _normalize.
SHARE_ITEM_TYPES = {"clip", "media_share", "xma_media_share", "felix_share", "link"}
URL_RE = re.compile(r"https?://\S+")
IG_DOMAINS = ("instagram.com", "cdninstagram.com", "fbcdn.net")


class IgError(Exception):
    """Base for typed pipeline errors."""

    exit_code = 1


class SessionDead(IgError):
    """Session invalid/expired — needs interactive re-login."""

    exit_code = 2


class ChallengeNeeded(IgError):
    """Instagram is suspicious — needs interactive challenge resolution."""

    exit_code = 3


class TransientIgError(IgError):
    """Rate limit / flaky API — safe to retry next run."""

    exit_code = 4


def _wrap(exc: Exception) -> IgError:
    if isinstance(exc, ChallengeRequired):
        return ChallengeNeeded(f"ChallengeRequired: {exc}")
    if isinstance(exc, LoginRequired):
        return SessionDead(f"LoginRequired: {exc}")
    if isinstance(exc, PleaseWaitFewMinutes):
        return TransientIgError(f"PleaseWaitFewMinutes: {exc}")
    return IgError(f"{type(exc).__name__}: {exc}")


class IgClient:
    def __init__(self, session_file: Path | None = None):
        session_file = session_file or config.SESSION_FILE
        if not session_file.exists():
            raise SessionDead(f"session file missing: {session_file}")
        self.cl = Client()
        self.cl.delay_range = [1, 3]  # polite pacing between private-API calls
        try:
            self.cl.load_settings(str(session_file))
            # NO validation call here: the first real request (direct_threads)
            # validates implicitly. A get_timeline_feed() per poll doubles the
            # API footprint for nothing — flag fuel on a watched account.
        except (LoginRequired, ChallengeRequired, ClientError) as exc:
            raise _wrap(exc) from exc

    # ------------------------------------------------------- collection labels

    def fetch_collection_map(self, limit: int = 20) -> dict[str, str]:
        """{message_id: collection_name} for items saved to a shared collection.
        instagrapi's model drops the action_log text, so we read the raw inbox and
        pair each "added to a collection: X" log with the media item sent
        alongside it (near-identical timestamp — microseconds apart)."""
        try:
            res = self.cl.private_request(
                "direct_v2/inbox/",
                params={"thread_message_limit": "20", "limit": str(limit)})
        except Exception as exc:  # non-fatal enrichment
            print(f"WARN: collection map fetch failed: {exc}", file=sys.stderr)
            return {}

        name_re = re.compile(r"added to a collection:\s*(.+?)\s*$")
        mapping: dict[str, str] = {}
        for thread in res.get("inbox", {}).get("threads", []):
            items = thread.get("items", []) or []
            media = [(int(it.get("timestamp", 0)), str(it.get("item_id") or it.get("message_id")))
                     for it in items
                     if str(it.get("item_type", "")).startswith("xma")]
            for it in items:
                if it.get("item_type") != "action_log":
                    continue
                desc = (it.get("action_log") or {}).get("description", "")
                m = name_re.search(desc or "")
                if not m:
                    continue
                name = m.group(1).replace("(shared)", "").strip()
                log_ts = int(it.get("timestamp", 0))
                near = min(media, key=lambda x: abs(x[0] - log_ts), default=None)
                if near and abs(near[0] - log_ts) < 3_000_000:  # within 3s
                    mapping[near[1]] = name
        return mapping

    # ------------------------------------------------------------------ poll

    def fetch_new_items(
        self,
        allowed_sender_pks: set[int],
        known_ids: set[str],
        thread_amount: int = 5,
        thread_message_limit: int = 20,
        deep_amount: int = 300,
        seen_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """New items from whitelisted senders, with a COMPLETENESS GUARANTEE: if
        the fast window (last N messages) contains no message we've EVER seen (any
        sender — `seen_ids` high-water set maintained by the pipeline), there may
        be a gap below it, so we deep-read the thread. Using seen_ids (not just the
        processed ledger) matters: the bot's own ack messages fill the window and
        are never in the ledger, which would otherwise make the deep-read fire on
        every poll — pointless API burn on a bot-watched account."""
        try:
            threads = self.cl.direct_threads(
                amount=thread_amount, thread_message_limit=thread_message_limit)
        except (LoginRequired, ChallengeRequired, PleaseWaitFewMinutes, ClientError) as exc:
            raise _wrap(exc) from exc

        anchor = seen_ids if seen_ids else known_ids
        items: list[dict[str, Any]] = []
        unresolved: list[tuple[str, str, Any]] = []
        for thread in threads:
            msgs = list(thread.messages or [])
            reached_known = any(str(m.id) in anchor for m in msgs)
            if msgs and not reached_known and anchor:
                try:
                    deep = self.cl.direct_messages(thread.id, amount=deep_amount)
                    by_id = {str(m.id): m for m in msgs}
                    for m in deep:
                        by_id.setdefault(str(m.id), m)
                    msgs = list(by_id.values())
                    print(f"INFO: deep-read thread {thread.id} ({len(msgs)} msgs) "
                          "to close a >window burst", file=sys.stderr)
                except (PleaseWaitFewMinutes, ClientError) as exc:
                    print(f"WARN: deep read failed for {thread.id}: {exc}", file=sys.stderr)
            if seen_ids is not None:
                for m in msgs:
                    seen_ids.add(str(m.id))
            for msg in msgs:
                if str(msg.id) in known_ids:
                    continue
                # SECURITY BOUNDARY: numeric-PK sender whitelist. Content from
                # anyone else — including group members — is never processed.
                if int(msg.user_id or 0) not in allowed_sender_pks:
                    continue
                norm = self._normalize(msg, str(thread.id))
                if norm:
                    items.append(norm)
                else:
                    it = str(getattr(msg, "item_type", "") or "")
                    if it.startswith("xma"):
                        unresolved.append((str(msg.id), str(thread.id), msg))
                    elif it not in ("text", "reaction_log", "action_log", "placeholder"):
                        print(f"WARN: unhandled item_type={it!r} from whitelisted "
                              f"sender (msg {msg.id}) — parser may need updating",
                              file=sys.stderr)

        # Raw-inbox fallback for xma items the model couldn't normalize (rare):
        # the raw inbox has the url/title/subtitle the model dropped.
        if unresolved:
            raw = self.raw_item_map()
            for mid, tid, msg in unresolved:
                info = raw.get(mid)
                ts = msg.timestamp.isoformat() if getattr(msg, "timestamp", None) else ""
                if info and info.get("url"):
                    items.append({"item_id": mid, "thread_id": tid, "kind": "clip",
                                  "media_pk": None, "url": info["url"],
                                  "text": info.get("subtitle", ""), "ts": ts})
                elif info and (info.get("title") or info.get("subtitle")):
                    items.append({"item_id": mid, "thread_id": tid, "kind": "note",
                                  "media_pk": None, "url": None,
                                  "text": f"{info.get('title','')} {info.get('subtitle','')}".strip(),
                                  "ts": ts})
                else:
                    print(f"WARN: xma msg {mid} unresolvable even from raw inbox",
                          file=sys.stderr)

        items.sort(key=lambda i: i["ts"])
        return items

    def fetch_thread_history(
        self,
        allowed_sender_pks: set[int],
        known_ids: set[str],
        amount: int = 300,
    ) -> list[dict[str, Any]]:
        """Backlog sweep: page deep into each thread's history (one-off use)."""
        try:
            threads = self.cl.direct_threads(amount=20, thread_message_limit=1)
        except (LoginRequired, ChallengeRequired, PleaseWaitFewMinutes, ClientError) as exc:
            raise _wrap(exc) from exc
        items: list[dict[str, Any]] = []
        for thread in threads:
            try:
                msgs = self.cl.direct_messages(thread.id, amount=amount)
            except (PleaseWaitFewMinutes, ClientError) as exc:
                print(f"WARN: history fetch failed for thread {thread.id}: {exc}",
                      file=sys.stderr)
                continue
            for msg in msgs:
                if str(msg.id) in known_ids:
                    continue
                if int(msg.user_id or 0) not in allowed_sender_pks:
                    continue
                norm = self._normalize(msg, str(thread.id))
                if norm:
                    items.append(norm)
        items.sort(key=lambda i: i["ts"])
        return items

    def raw_item_map(self, limit: int = 20) -> dict[str, dict[str, Any]]:
        """{message_id: {url, title, subtitle}} from the RAW inbox — the model
        drops these fields for some xma subtypes, so this is the fallback that
        keeps rare shares from being silently missed. One API call, reused."""
        try:
            res = self.cl.private_request(
                "direct_v2/inbox/",
                params={"thread_message_limit": "20", "limit": str(limit)})
        except Exception:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for thread in res.get("inbox", {}).get("threads", []):
            for it in thread.get("items", []) or []:
                typ = str(it.get("item_type", "") or "")
                if not typ.startswith("xma"):
                    continue
                xma = it.get(typ) or it.get("xma_media_share")
                if isinstance(xma, list):
                    xma = xma[0] if xma else {}
                xma = xma or {}
                mid = str(it.get("item_id") or it.get("message_id"))
                url = xma.get("video_url") or xma.get("target_url") or ""
                if not url and URL_RE.search(str(xma.get("subtitle_text", ""))):
                    url = URL_RE.search(xma["subtitle_text"]).group(0)
                out[mid] = {
                    "url": str(url),
                    "title": str(xma.get("title_text") or xma.get("header_title_text") or ""),
                    "subtitle": str(xma.get("subtitle_text") or ""),
                    "thread_id": str(thread.get("thread_id") or ""),
                }
        return out

    def _normalize(self, msg: Any, thread_id: str) -> dict[str, Any] | None:
        item_type = str(getattr(msg, "item_type", "") or "")
        text = getattr(msg, "text", None) or ""
        base = {
            "item_id": str(msg.id),
            "thread_id": thread_id,
            "kind": item_type,
            "media_pk": None,
            "url": None,
            "text": text,
            "ts": msg.timestamp.isoformat() if getattr(msg, "timestamp", None) else "",
        }

        if item_type in ("clip", "media_share", "felix_share"):
            media = (getattr(msg, "clip", None) or getattr(msg, "media_share", None)
                     or getattr(msg, "felix_share", None))
            pk = getattr(media, "pk", None) or getattr(media, "id", None)
            if pk:
                base["media_pk"] = str(pk)
                return base
            return None

        if item_type.startswith("xma"):
            xma = getattr(msg, "xma_share", None)
            target = getattr(xma, "target_url", None) or getattr(xma, "video_url", None)
            if not target and URL_RE.search(text):
                target = URL_RE.search(text).group(0)
            if target:
                base["url"] = str(target)
                author = getattr(xma, "header_title_text", None)
                if author:
                    base["xma_author"] = str(author)
                return base
            return None

        if item_type == "link":
            link = getattr(msg, "link", None)
            url = None
            ctx = getattr(link, "link_context", None)
            if ctx is not None:
                url = getattr(ctx, "link_url", None)
            url = url or (URL_RE.search(text).group(0) if URL_RE.search(text) else None)
            if url:
                base["url"] = str(url)
                return base
            return None

        if item_type == "text":
            if URL_RE.search(text):
                base["kind"] = "link"
                base["url"] = URL_RE.search(text).group(0)
                return base
            # Plain typed note — capture it too, but skip trivial one-word
            # replies/acks (< 12 chars, no space).
            stripped = text.strip()
            if len(stripped) >= 12 and " " in stripped:
                base["kind"] = "note"
                return base
            return None

        return None

    # --------------------------------------------------------------- resolve

    def media_pk_from_url(self, url: str) -> str | None:
        if not any(d in url for d in IG_DOMAINS):
            return None
        try:
            return str(self.cl.media_pk_from_url(url))
        except Exception:
            return None

    def media_details(self, media_pk: str) -> dict[str, Any]:
        """permalink / caption / author / media kind / download URLs."""
        try:
            info = self.cl.media_info(media_pk)
        except (LoginRequired, ChallengeRequired, PleaseWaitFewMinutes, ClientError) as exc:
            raise _wrap(exc) from exc
        code = getattr(info, "code", None)
        video_url = getattr(info, "video_url", None)
        thumb = getattr(info, "thumbnail_url", None)
        resources = getattr(info, "resources", None) or []  # albums
        if not video_url:
            for r in resources:
                if getattr(r, "video_url", None):
                    video_url = r.video_url
                    break
        if not thumb and resources:
            thumb = getattr(resources[0], "thumbnail_url", None)
        return {
            "permalink": f"https://www.instagram.com/p/{code}/" if code else None,
            "caption": getattr(info, "caption_text", "") or "",
            "author": getattr(getattr(info, "user", None), "username", "") or "",
            "media_type": int(getattr(info, "media_type", 0) or 0),  # 1 photo, 2 video, 8 album
            "video_url": str(video_url) if video_url else None,
            "thumbnail_url": str(thumb) if thumb else None,
        }

    # ------------------------------------------------------------------- ack

    def send_reply(self, thread_id: str, text: str) -> bool:
        try:
            self.cl.direct_answer(int(thread_id), text)
            return True
        except Exception:
            return False
