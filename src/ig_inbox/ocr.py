"""On-screen text recognition with a portable backend.

OCR is the one platform-specific piece. macOS Vision is by far the most accurate
for the stylized text overlaid on reels, but it is Mac-only. Tesseract is the
cross-platform fallback.

Backend selection (OCR_BACKEND env):
  auto       macOS Vision binary if built, else tesseract, else disabled (default)
  vision     force the compiled macOS Vision CLI (built by setup on a Mac)
  tesseract  force Tesseract (`brew install tesseract` / `apt install tesseract-ocr`)
  none       disable OCR entirely

The compiled Vision binary is produced from ocr.swift by the setup step. On
Linux, install Tesseract and either leave OCR_BACKEND=auto or set it to
tesseract.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config


def _backend() -> str:
    return (os.environ.get("OCR_BACKEND") or "auto").strip().lower()


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _vision_lines(frame: Path) -> list[str]:
    p = _run([str(config.OCR_BIN), str(frame)])
    return [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]


def _tesseract_lines(frame: Path) -> list[str]:
    tess = config.TESSERACT
    if not shutil.which(tess) and not Path(tess).exists():
        return []
    p = _run([tess, str(frame), "stdout"])
    return [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]


def _resolve() -> str:
    """Which backend to actually use, honoring auto-detection."""
    b = _backend()
    if b != "auto":
        return b
    if config.OCR_BIN.exists():
        return "vision"
    if shutil.which(config.TESSERACT) or Path(config.TESSERACT).exists():
        return "tesseract"
    return "none"


def ocr_frames(frames: list[Path]) -> str:
    """On-screen text across frames. Order-preserving, case-insensitive dedupe."""
    if not frames:
        return ""
    backend = _resolve()
    if backend == "none":
        print("WARN: no OCR backend available (build the Vision binary or install "
              "tesseract) — skipping on-screen text", file=sys.stderr)
        return ""
    if backend == "vision" and not config.OCR_BIN.exists():
        print(f"WARN: OCR_BACKEND=vision but {config.OCR_BIN} missing — run setup",
              file=sys.stderr)
        return ""

    extract = _vision_lines if backend == "vision" else _tesseract_lines
    seen: set[str] = set()
    lines: list[str] = []
    for frame in frames:
        try:
            for line in extract(frame):
                key = line.lower()
                if key not in seen:
                    seen.add(key)
                    lines.append(line)
        except Exception as exc:
            print(f"WARN: OCR failed on {frame.name}: {exc}", file=sys.stderr)
    return "\n".join(lines)
