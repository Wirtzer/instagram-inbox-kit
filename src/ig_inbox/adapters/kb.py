"""Optional knowledge-base hook. Fires once per stored capture so you can push
the record into your own memory system, vector DB, notes app, etc.

Default: no-op. Two ways to wire it up without editing code:

  1. Set KB_HOOK_CMD to a shell command. The full capture record is passed as
     JSON on that command's stdin. Example:
         KB_HOOK_CMD=/path/to/ingest.sh
     ingest.sh can then do whatever you want (curl to an API, append to a DB…).

Or edit `on_capture()` directly for an in-process integration (import your own
client and call it here).

`on_capture()` never raises — a KB failure must never lose a capture, which is
already safely persisted to captures.jsonl before this runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


def on_capture(record: dict[str, Any]) -> None:
    """Called after a record is durably stored. Best-effort side effect."""
    cmd = os.environ.get("KB_HOOK_CMD")
    if not cmd:
        return  # no-op default
    try:
        subprocess.run(
            ["/bin/sh", "-c", cmd],
            input=json.dumps(record, ensure_ascii=True),
            capture_output=True, text=True, timeout=60, check=False,
        )
    except Exception as exc:
        print(f"WARN: KB_HOOK_CMD failed: {exc}", file=sys.stderr)
