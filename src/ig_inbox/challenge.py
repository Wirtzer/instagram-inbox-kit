"""Instagram email-challenge auto-resolver.

When Instagram emails a 6-digit login code, instagrapi calls a
`challenge_code_handler(username, choice)` to obtain it. Two strategies:

  1. IMAP (automated, no human): if IMAP_HOST/USER/PASSWORD are set, read the
     code straight from the mailbox that receives the bot account's security
     email. Works with Gmail (app password), Fastmail, any IMAP server.
  2. Interactive prompt (default): if IMAP is not configured, fall back to
     asking on stdin — you read the code from the email yourself and type it in.

IMPORTANT — this only clears the STANDARD "enter the code we emailed you"
challenge. Newer app-only checkpoints (device approval, "confirm your contact
info") are restricted by Instagram to the official mobile app; no automation can
clear them. When one of those appears, a human must open the Instagram app as
the bot account and approve, then re-run login.

Usage (wired by ig_login):
    from . import challenge
    baseline = challenge.baseline_ids()          # snapshot BEFORE login
    cl.challenge_code_handler = challenge.make_handler(baseline)
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import sys
import time
from typing import Callable

CODE_RE = re.compile(r"\b(\d{6})\b")
CODE_ANCHORS = ("confirm your identity", "is your instagram code",
                "verification code", "instagram")


def _imap_configured() -> bool:
    return bool(os.environ.get("IMAP_HOST") and os.environ.get("IMAP_USER")
                and os.environ.get("IMAP_PASSWORD"))


def _connect() -> imaplib.IMAP4_SSL:
    host = os.environ["IMAP_HOST"]
    port = int(os.environ.get("IMAP_PORT", "993"))
    conn = imaplib.IMAP4_SSL(host, port)
    conn.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    return conn


def _recent_instagram_uids(conn: imaplib.IMAP4_SSL, limit: int = 8) -> list[bytes]:
    conn.select("INBOX")
    # Instagram security mail comes from an instagram.com / mail.instagram.com
    # address. FROM search matches the substring.
    typ, data = conn.search(None, 'FROM', 'instagram')
    if typ != "OK" or not data or not data[0]:
        return []
    uids = data[0].split()
    return uids[-limit:][::-1]  # newest first


def _body_text(conn: imaplib.IMAP4_SSL, uid: bytes) -> str:
    typ, data = conn.fetch(uid, "(RFC822)")
    if typ != "OK" or not data or not data[0]:
        return ""
    msg = email.message_from_bytes(data[0][1])
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    parts.append(part.get_payload(decode=True).decode(errors="ignore"))
                except Exception:
                    pass
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(errors="ignore"))
        except Exception:
            pass
    return " ".join(parts)


def _extract_code(body: str) -> str | None:
    low = body.lower()
    for anchor in CODE_ANCHORS:
        idx = low.find(anchor)
        if idx != -1:
            m = CODE_RE.search(body[idx: idx + 300])
            if m:
                return m.group(1)
    m = CODE_RE.search(body)
    return m.group(1) if m else None


def baseline_ids() -> set[str]:
    """Snapshot existing Instagram-email UIDs BEFORE triggering a challenge, so
    the handler only ever accepts a code from a NEWLY arrived email."""
    if not _imap_configured():
        return set()
    try:
        conn = _connect()
        uids = {u.decode() for u in _recent_instagram_uids(conn)}
        conn.logout()
        return uids
    except Exception as exc:
        print(f"WARN: IMAP baseline failed: {exc}", file=sys.stderr)
        return set()


def make_handler(baseline: set[str], tries: int = 15,
                 delay: int = 8) -> Callable[[str, object], str]:
    """Return an instagrapi challenge_code_handler(username, choice) -> code."""

    def handler(username: str, choice: object) -> str:
        if not _imap_configured():
            # Human-in-the-loop fallback.
            code = input(f"Enter the Instagram verification code emailed to "
                         f"{username}: ").strip()
            return code
        for _ in range(tries):
            try:
                conn = _connect()
                for uid in _recent_instagram_uids(conn):
                    if uid.decode() in baseline:
                        continue
                    code = _extract_code(_body_text(conn, uid))
                    if code:
                        conn.logout()
                        return code
                conn.logout()
            except Exception as exc:
                print(f"WARN: IMAP poll failed: {exc}", file=sys.stderr)
            time.sleep(delay)
        raise TimeoutError(
            f"no new Instagram code email arrived within {tries * delay}s")

    return handler
