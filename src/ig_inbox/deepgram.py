"""Self-contained Deepgram transcription with a sidecar cache.

Reel audio → plain transcript text. Simple single-speaker settings (reels aren't
multi-speaker calls). Standard library only.

DURABILITY — idempotency guard: if a sidecar JSON already exists and parses, we
reuse it instead of calling the API again. This bounds cost to exactly one API
call per audio file, ever, no matter how many times the surrounding pipeline
retries. (This exact guard was added upstream after an infinite-retry loop
re-billed the same audio for days.)
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
HTTP_TIMEOUT = 600


def _params() -> dict[str, str]:
    return {
        "model": os.environ.get("DEEPGRAM_MODEL", "nova-3"),
        "punctuate": "true",
        "smart_format": "true",
        "paragraphs": "true",
        "numerals": "true",
        "language": os.environ.get("DEEPGRAM_LANGUAGE", "en"),
    }


def _text_from_data(data: dict) -> str:
    """Flatten Deepgram JSON to plain transcript text."""
    try:
        alt = data["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError):
        return ""
    paragraphs = (alt.get("paragraphs") or {}).get("paragraphs")
    if paragraphs:
        out = []
        for p in paragraphs:
            text = " ".join(s.get("text", "") for s in p.get("sentences", [])).strip()
            if text:
                out.append(text)
        return "\n".join(out).strip()
    return str(alt.get("transcript", "")).strip()


def transcribe(audio_path: Path, sidecar_path: Path) -> str:
    """Transcript text, or '' if unavailable. Caches raw JSON at sidecar_path."""
    audio_path, sidecar_path = Path(audio_path), Path(sidecar_path)

    # Cache reuse (idempotency guard).
    if sidecar_path.exists() and sidecar_path.stat().st_size > 0:
        try:
            cached = json.loads(sidecar_path.read_text())
            cached["results"]["channels"][0]["alternatives"][0]
            return _text_from_data(cached)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            pass  # bad sidecar → fall through and refetch

    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        print("WARN: DEEPGRAM_API_KEY unset — skipping transcript", file=sys.stderr)
        return ""

    url = DEEPGRAM_URL + "?" + urllib.parse.urlencode(_params())
    mime = mimetypes.guess_type(str(audio_path))[0] or "audio/m4a"
    try:
        audio_bytes = audio_path.read_bytes()
        req = urllib.request.Request(url, data=audio_bytes, headers={
            "Authorization": f"Token {key}", "Content-Type": mime})
        data = json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read().decode())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:300]
        except Exception:
            pass
        print(f"WARN: Deepgram HTTP {exc.code}: {body}", file=sys.stderr)
        return ""
    except Exception as exc:
        print(f"WARN: Deepgram request failed: {exc}", file=sys.stderr)
        return ""

    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(data))
    return _text_from_data(data)
