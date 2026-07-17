"""One-time setup: directories, OCR binary (macOS), Instagram session.

    python -m ig_inbox.setup_cli          # full interactive setup
    python -m ig_inbox.setup_cli --check  # verify environment, no login

Steps:
  1. Create data/state/logs dirs.
  2. Seed config.json from config.example.json if missing.
  3. Build the macOS Vision OCR binary from ocr.swift (Mac + Xcode only). On
     Linux this is skipped — install Tesseract and set OCR_BACKEND accordingly.
  4. Interactive Instagram login (ig_login), unless --check.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import config


def build_ocr_binary() -> bool:
    """Compile ocr.swift → bin/ocr on macOS. Returns True if the binary exists."""
    if platform.system() != "Darwin":
        print("   (not macOS — skipping Vision OCR; use Tesseract via OCR_BACKEND)")
        return False
    if not shutil.which("xcrun"):
        print("   WARN: xcrun not found (install Xcode command-line tools) — "
              "OCR will fall back to Tesseract if installed")
        return False
    config.OCR_BIN.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["xcrun", "swiftc", "-O", str(config.OCR_SWIFT),
                        "-o", str(config.OCR_BIN)], check=True)
        print(f"   ok: built {config.OCR_BIN}")
        return True
    except Exception as exc:
        print(f"   WARN: OCR build failed: {exc}")
        return False


def seed_config() -> None:
    if config.CONFIG_FILE.exists():
        print(f"   config exists: {config.CONFIG_FILE}")
        return
    example = config.HOME_DIR / "config.example.json"
    if example.exists():
        shutil.copy2(example, config.CONFIG_FILE)
        print(f"   seeded {config.CONFIG_FILE} from config.example.json — edit it "
              "(ig_username + allowed_sender_usernames) before login")
    else:
        print(f"   WARN: no config.example.json next to {config.CONFIG_FILE}; "
              "create config.json manually")


def check_env() -> int:
    import os
    print("Environment check:")
    ok = True
    for tool, path in (("ffmpeg", config.FFMPEG), ("ffprobe", config.FFPROBE)):
        found = shutil.which(path) or Path(path).exists()
        print(f"  {tool:9} {'ok' if found else 'MISSING'} ({path})")
        ok = ok and found
    llm_ready = bool(os.environ.get("ANTHROPIC_API_KEY")
                     or os.environ.get("LLM_BASE_URL"))
    print(f"  LLM       {'ok' if llm_ready else 'NOT CONFIGURED'} "
          f"(backend={os.environ.get('LLM_BACKEND', 'anthropic')})")
    print(f"  deepgram  {'ok' if os.environ.get('DEEPGRAM_API_KEY') else 'unset (transcription skipped)'}")
    ocr = "vision" if config.OCR_BIN.exists() else (
        "tesseract" if shutil.which(config.TESSERACT) else "none")
    print(f"  ocr       {ocr}")
    print(f"  session   {'present' if config.SESSION_FILE.exists() else 'MISSING (run login)'}")
    return 0 if ok and llm_ready else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify env only, no login")
    ap.add_argument("--sessionid")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    print("== 1/3 directories ==")
    config.ensure_dirs()
    seed_config()
    print("   ok")

    print("== 2/3 Vision OCR binary (macOS only) ==")
    build_ocr_binary()

    if args.check:
        return check_env()

    print("== 3/3 Instagram session (interactive) ==")
    from . import ig_login
    argv = []
    if args.sessionid:
        argv += ["--sessionid", args.sessionid]
    if args.force:
        argv += ["--force"]
    sys.argv = ["ig_login"] + argv
    rc = ig_login.main()
    if rc == 0:
        print("\nSetup complete. Test with:  python -m ig_inbox.pipeline --dry-run")
    return rc


if __name__ == "__main__":
    sys.exit(main())
