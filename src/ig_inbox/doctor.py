"""Capability preflight — "can this machine actually do each step?"

The kit makes NO assumptions about the host. This checks every dependency and
capability, and for anything missing says exactly how to fix or integrate it.
Run it FIRST (the integrating AI should resolve every ❌ before the first real
run — auto-fixing what it can, asking the user only for accounts/keys).

    python -m ig_inbox.doctor          # human report, exit 1 if a hard need is unmet
    python -m ig_inbox.doctor --json   # machine-readable for an agent to act on

Each check is one of: ok (✅), warn (⚠️ degraded but usable), fail (❌ must fix).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

OK, WARN, FAIL = "ok", "warn", "fail"


def _check(name, status, detail, fix=""):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _py() -> dict:
    v = sys.version_info
    if v >= (3, 10):
        return _check("Python 3.10+", OK, f"{v.major}.{v.minor}.{v.micro}")
    return _check("Python 3.10+", FAIL, f"found {v.major}.{v.minor}",
                  "Install Python 3.10 or newer (brew install python@3.12 / apt install python3).")


def _bin(name, path, install) -> dict:
    if path and (shutil.which(path) or Path(path).exists()):
        return _check(name, OK, path)
    return _check(name, FAIL, "not found", install)


def _ocr() -> dict:
    # macOS Vision (built binary) → Tesseract → off (degrade, not fatal).
    if config.OCR_BIN.exists():
        return _check("On-screen text (OCR)", OK, f"macOS Vision ({config.OCR_BIN.name})")
    if shutil.which(config.TESSERACT):
        return _check("On-screen text (OCR)", OK, "Tesseract")
    if sys.platform == "darwin":
        return _check("On-screen text (OCR)", WARN, "Vision binary not built yet",
                      "Run setup (it compiles it), or `xcode-select --install` then re-run setup.")
    return _check("On-screen text (OCR)", WARN, "no OCR available — on-screen text will be skipped",
                  "Linux: `apt install tesseract-ocr`. Or set OCR_BACKEND=none to silence this.")


def _llm() -> dict:
    backend = os.environ.get("LLM_BACKEND", "anthropic").lower()
    if backend == "anthropic":
        if os.environ.get("ANTHROPIC_API_KEY"):
            model = os.environ.get("LLM_MODEL", "claude-haiku-4-5")
            return _check("LLM (classify + research)", OK, f"anthropic · {model}")
        return _check("LLM (classify + research)", FAIL, "ANTHROPIC_API_KEY not set",
                      "Add an Anthropic key to .env, or switch to LLM_BACKEND=openai with LLM_BASE_URL+LLM_API_KEY.")
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"):
        return _check("LLM (classify + research)", OK,
                      f"openai-compat · {os.environ.get('LLM_MODEL', 'gpt-4o-mini')}")
    return _check("LLM (classify + research)", FAIL, "LLM_BASE_URL / LLM_API_KEY not set",
                  "Set LLM_BASE_URL (a /chat/completions endpoint) and LLM_API_KEY in .env.")


def _machine_hint() -> str:
    cores = os.cpu_count() or 0
    ram_gb = 0
    try:  # best-effort, portable-ish
        ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9)
    except (ValueError, OSError, AttributeError):
        pass
    strong = cores >= 6 and (ram_gb == 0 or ram_gb >= 12)
    tag = "looks capable" if strong else "may be slow"
    return f"{cores or '?'} cores{f', ~{ram_gb}GB RAM' if ram_gb else ''} — {tag} for on-device Whisper"


def _transcription() -> dict:
    backend = config.resolve_transcribe_backend()
    if backend == "none":
        return _check("Audio → text (transcription)", WARN,
                      "OFF — spoken audio in reels is skipped (captions + on-screen text still work)",
                      "To transcribe speech, pick a backend: Deepgram (free-tier key, any machine) → "
                      "set DEEPGRAM_API_KEY; or on-device Whisper → set TRANSCRIBE_BACKEND=whisper "
                      "and `pip install faster-whisper`. See SETUP.md 'How do you want audio?'.")
    if backend == "deepgram":
        if os.environ.get("DEEPGRAM_API_KEY"):
            return _check("Audio → text (transcription)", OK, "Deepgram (cloud)")
        return _check("Audio → text (transcription)", FAIL,
                      "TRANSCRIBE_BACKEND=deepgram but DEEPGRAM_API_KEY not set",
                      "Create a free account at deepgram.com (free credits last a long time) and set DEEPGRAM_API_KEY.")
    # whisper
    try:
        import faster_whisper  # noqa: F401
        return _check("Audio → text (transcription)", OK, f"on-device Whisper — {_machine_hint()}")
    except ImportError:
        return _check("Audio → text (transcription)", FAIL,
                      f"TRANSCRIBE_BACKEND=whisper but faster-whisper isn't installed ({_machine_hint()})",
                      "`pip install faster-whisper` — or if this machine is underpowered, use Deepgram instead "
                      "(set TRANSCRIBE_BACKEND=deepgram + a free Deepgram key).")


def _config_and_session() -> list[dict]:
    out = []
    cfg = config.load_config()
    if cfg.get("ig_username"):
        out.append(_check("Bot Instagram account", OK, cfg["ig_username"]))
    else:
        out.append(_check("Bot Instagram account", FAIL, "config `ig_username` empty",
                          "Set the throwaway IG account it logs in as (SETUP.md §1)."))
    senders = cfg.get("allowed_sender_usernames") or []
    if senders:
        out.append(_check("Trigger allow-list", OK, ", ".join(senders)))
    else:
        out.append(_check("Trigger allow-list", FAIL, "no allowed senders",
                          "List whose shares may trigger it (your own handle, plus anyone you allow)."))
    if config.SESSION_FILE.exists():
        out.append(_check("Instagram login", OK, "session saved"))
    else:
        out.append(_check("Instagram login", WARN, "not logged in yet",
                          "Run `python -m ig_inbox.setup_cli` for the one-time interactive login."))
    return out


def _writable() -> dict:
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        t = config.DATA_DIR / ".doctor_write_test"
        t.write_text("ok"); t.unlink()
        return _check("Output folder writable", OK, str(config.DATA_DIR))
    except Exception as exc:
        return _check("Output folder writable", FAIL, f"{config.DATA_DIR}: {exc}",
                      "Point IG_INBOX_DATA_DIR at a writable folder.")


def run() -> list[dict]:
    checks = [_py(),
              _bin("ffmpeg", config.FFMPEG, "Install ffmpeg (brew install ffmpeg / apt install ffmpeg)."),
              _bin("ffprobe", config.FFPROBE, "Comes with ffmpeg — install ffmpeg."),
              _ocr(), _llm(), _transcription(), _writable(),
              *_config_and_session()]
    return checks


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    checks = run()
    if "--json" in argv:
        print(json.dumps({"checks": checks,
                          "ok": all(c["status"] != FAIL for c in checks)}, indent=2))
    else:
        icon = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}
        print("Instagram-inbox — capability check\n")
        for c in checks:
            print(f"{icon[c['status']]} {c['name']}: {c['detail']}")
            if c["status"] != OK and c["fix"]:
                print(f"     → {c['fix']}")
        fails = [c for c in checks if c["status"] == FAIL]
        print()
        print(f"{'❌ ' + str(len(fails)) + ' thing(s) must be fixed before running.' if fails else '✅ Ready to run.'}")
    return 1 if any(c["status"] == FAIL for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
