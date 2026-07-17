"""Local speech-to-text via faster-whisper — the no-account, no-cost option.

Runs entirely on the user's machine (nothing leaves the computer). Slower than
Deepgram and wants a reasonably capable CPU/GPU, but free and private. It's an
OPTIONAL backend: `faster-whisper` is not a base dependency, so we import it
lazily and give a clear install hint if the user selected whisper without it.

Selected via TRANSCRIBE_BACKEND=whisper (see config). Model size via
WHISPER_MODEL (default "base": a good speed/accuracy balance; "small"/"medium"
are more accurate and slower). Results are sidecar-cached like Deepgram, so a
given clip is never transcribed twice.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_model = None  # lazily loaded, reused across calls in one run


def _load():
    global _model
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # explicit, actionable
        raise RuntimeError(
            "TRANSCRIBE_BACKEND=whisper but faster-whisper isn't installed. "
            "Install it with:  pip install faster-whisper   (or set "
            "TRANSCRIBE_BACKEND=deepgram / none instead)."
        ) from exc
    size = os.environ.get("WHISPER_MODEL", "base")
    # int8 on CPU is the portable default; faster-whisper auto-uses GPU if present.
    _model = WhisperModel(size, device="auto", compute_type="int8")
    return _model


def transcribe(audio_path: Path, sidecar_path: Path) -> str:
    """Return the transcript text, cached in `sidecar_path`. '' on failure."""
    if sidecar_path.exists():
        try:
            return json.loads(sidecar_path.read_text()).get("text", "")
        except Exception:
            pass
    try:
        model = _load()
        segments, _info = model.transcribe(str(audio_path))
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except RuntimeError:
        raise  # missing dependency — surface it
    except Exception as exc:
        print(f"WARN: local whisper failed: {exc}", file=sys.stderr)
        return ""
    try:
        sidecar_path.write_text(json.dumps({"text": text}, ensure_ascii=True))
    except Exception:
        pass
    return text
