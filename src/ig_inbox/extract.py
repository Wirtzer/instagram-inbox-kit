"""Content extraction: speech transcript (Deepgram) + on-screen text (OCR).

Everything here is deterministic and local except the Deepgram call, which is
sidecar-cached (never re-bills for the same audio).

The audio-vs-silent decision is a cheap ffprobe/ffmpeg gate BEFORE any paid API
call: many reels are music-only, and running speech-to-text on them wastes money
and produces garbage. Order, cheapest first: audio stream exists → mean volume
above a floor → Deepgram → discard if under a word count (music/negligible speech).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from . import config, deepgram, ocr

MEAN_VOL_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def has_audio_stream(video: Path) -> bool:
    p = _run([config.FFPROBE, "-v", "error", "-select_streams", "a",
              "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)])
    return "audio" in (p.stdout or "")


def mean_volume_db(video: Path) -> float | None:
    p = _run([config.FFMPEG, "-hide_banner", "-i", str(video), "-map", "a:0",
              "-af", "volumedetect", "-f", "null", "-"])
    m = MEAN_VOL_RE.search(p.stderr or "")
    return float(m.group(1)) if m else None


def transcribe(video: Path, workdir: Path, min_words: int, volume_gate_db: float) -> str:
    """Speech transcript, or '' when there is no meaningful speech. Routes to the
    configured backend (Deepgram cloud, on-device Whisper, or none)."""
    backend = config.resolve_transcribe_backend()
    if backend == "none":
        return ""
    if not has_audio_stream(video):
        return ""
    vol = mean_volume_db(video)
    if vol is not None and vol < volume_gate_db:
        return ""  # music-only / near-silent

    audio = workdir / "audio.m4a"
    if not audio.exists():
        p = _run([config.FFMPEG, "-hide_banner", "-y", "-i", str(video),
                  "-vn", "-acodec", "aac", "-b:a", "64k", str(audio)])
        if p.returncode != 0 or not audio.exists():
            print(f"WARN: audio extraction failed: {p.stderr[-300:]}", file=sys.stderr)
            return ""

    if backend == "whisper":
        from . import whisper
        transcript = whisper.transcribe(audio, workdir / "whisper.json").strip()
    else:  # deepgram
        transcript = deepgram.transcribe(audio, workdir / "deepgram.json").strip()
    if len(transcript.split()) < min_words:
        return ""  # music-only / negligible speech
    return transcript


def extract_frames(video: Path, workdir: Path, max_frames: int) -> list[Path]:
    frames_dir = workdir / "frames"
    frames_dir.mkdir(exist_ok=True)
    existing = sorted(frames_dir.glob("f_*.jpg"))
    if existing:
        return existing
    p = _run([config.FFMPEG, "-hide_banner", "-y", "-i", str(video),
              "-vf", "fps=1,scale=720:-2", "-frames:v", str(max_frames),
              str(frames_dir / "f_%03d.jpg")])
    if p.returncode != 0:
        print(f"WARN: frame extraction failed: {p.stderr[-300:]}", file=sys.stderr)
    return sorted(frames_dir.glob("f_*.jpg"))


def dedupe_frames(frames: list[Path], changed_pct: float) -> list[Path]:
    """Keep only frames that visually differ from the last kept frame.

    Metric: percentage of grayscale 96x96 pixels whose value moved by >25/255.
    (Mean-abs-diff fails here: one new text line on a black reel barely moves the
    global mean but flips a small cluster of pixels hard.) This keeps OCR — and
    the frames we pay to process — down to the moments that actually changed.
    """
    if len(frames) <= 1:
        return frames
    import numpy as np
    from PIL import Image

    def gray(p: Path):
        return np.asarray(Image.open(p).convert("L").resize((96, 96)), dtype=np.float32)

    kept = [frames[0]]
    last = gray(frames[0])
    for f in frames[1:]:
        cur = gray(f)
        ratio = float((np.abs(cur - last) > 25).mean()) * 100.0
        if ratio >= changed_pct:
            kept.append(f)
            last = cur
    return kept


def ocr_frames(frames: list[Path]) -> str:
    """On-screen text via the configured OCR backend (Vision or Tesseract)."""
    return ocr.ocr_frames(frames)
