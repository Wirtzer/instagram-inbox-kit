"""System-alert transport. NOT for content — this is for "Instagram needs a
human" and "the pipeline has failed 3 times in a row" messages.

Backends (NOTIFY_BACKEND):
  stdout   — print to console / log (default)
  webhook  — HTTP POST {"text": "..."} to NOTIFY_WEBHOOK_URL
             (Slack incoming webhooks, Discord, ntfy, etc. all accept this shape)

Swap this file to route alerts to iMessage, email, PagerDuty, or anything else.
`notify()` never raises — a broken alert channel must not crash the pipeline.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def notify(message: str) -> bool:
    """Send one alert. Returns True if it went out, False otherwise."""
    channel = (os.environ.get("NOTIFY_BACKEND") or "stdout").strip().lower()
    if channel == "webhook":
        url = os.environ.get("NOTIFY_WEBHOOK_URL")
        if not url:
            print("WARN: NOTIFY_BACKEND=webhook but NOTIFY_WEBHOOK_URL unset; "
                  "printing instead", file=sys.stderr)
        else:
            try:
                req = urllib.request.Request(
                    url, data=json.dumps({"text": message}).encode(),
                    headers={"content-type": "application/json"})
                urllib.request.urlopen(req, timeout=15).read()
                return True
            except Exception as exc:
                print(f"WARN: webhook notify failed: {exc}", file=sys.stderr)
    # stdout / fallback
    print(f"[ALERT] {message}", file=sys.stderr)
    return True
