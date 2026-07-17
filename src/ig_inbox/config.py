"""Central configuration: paths, tunables, and the config.json contract.

Everything that was a hardcoded internal path or handle in the original private
skill lives here now, driven by environment variables (loaded from .env) and a
per-install config.json. Nothing in this file is secret.

Path model (all overridable via env):
  IG_INBOX_HOME       base dir for runtime data (default: current working dir)
  IG_INBOX_DATA_DIR   captures, digests, lists, media   (default: HOME/data)
  IG_INBOX_STATE_DIR  ledgers, locks, failure counters  (default: HOME/state)
  IG_INBOX_LOG_DIR    per-day logs                       (default: HOME/logs)
  IG_INBOX_CRED_DIR   IG session + credential files      (default: ~/.ig_inbox/credentials)

Credentials default OUTSIDE the repo so a session file can never be committed.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    # override=False: a real process env var always beats the .env file.
    load_dotenv(override=False)
except Exception:  # python-dotenv is a normal dep, but never hard-fail on it.
    pass


def _path_env(name: str, default: Path) -> Path:
    val = os.environ.get(name)
    return Path(val).expanduser() if val else default


HOME_DIR = _path_env("IG_INBOX_HOME", Path.cwd())
DATA_DIR = _path_env("IG_INBOX_DATA_DIR", HOME_DIR / "data")
STATE_DIR = _path_env("IG_INBOX_STATE_DIR", HOME_DIR / "state")
LOG_DIR = _path_env("IG_INBOX_LOG_DIR", HOME_DIR / "logs")
CRED_DIR = _path_env("IG_INBOX_CRED_DIR", Path.home() / ".ig_inbox" / "credentials")

# Config file: ./config.json unless IG_INBOX_CONFIG points elsewhere.
CONFIG_FILE = _path_env("IG_INBOX_CONFIG", HOME_DIR / "config.json")

# --- derived data paths ------------------------------------------------------
CAPTURES_FILE = DATA_DIR / "captures.jsonl"
DIGESTS_DIR = DATA_DIR / "digests"
LISTS_DIR = DATA_DIR / "lists"
MASTERS_DIR = DATA_DIR / "masters"
MEDIA_DIR = DATA_DIR / "media"
READING_LIST_FILE = DATA_DIR / "reading-list.md"
BOOKS_MASTER_FILE = MASTERS_DIR / "books.json"
WORKBOOK_FILE = LISTS_DIR / "Instagram Inbox.xlsx"

# --- credential / session paths ---------------------------------------------
SESSION_FILE = CRED_DIR / "instagram-session.json"
CRED_FILE = CRED_DIR / "instagram-credentials.json"

# --- OCR binary (built by setup on macOS) -----------------------------------
PKG_DIR = Path(__file__).resolve().parent
OCR_BIN = PKG_DIR / "bin" / "ocr"
OCR_SWIFT = PKG_DIR / "ocr.swift"


def _tool(env_name: str, *candidates: str) -> str:
    """Resolve an external tool: explicit env override, else first on PATH,
    else the first candidate (so error messages name a real path)."""
    override = os.environ.get(env_name)
    if override:
        return override
    for c in candidates:
        found = shutil.which(c)
        if found:
            return found
    return candidates[0] if candidates else env_name


FFMPEG = _tool("FFMPEG_BIN", "ffmpeg")
FFPROBE = _tool("FFPROBE_BIN", "ffprobe")
TESSERACT = _tool("TESSERACT_BIN", "tesseract")

ALLOWED_MEDIA_HOSTS = ("instagram.com", "cdninstagram.com", "fbcdn.net")

# --- category taxonomy (shared by digest, feedback, workbook) ---------------
CATEGORIES = frozenset({
    "recipe", "restaurant", "movie", "game", "book", "random_food", "place",
    "product", "finance", "ai_idea", "research", "self_improvement",
    "relationships", "career", "news", "other",
})

# Human-facing sheet/label ↔ category key (so a user can type "AI Ideas" or
# "Career" into the workbook's correction column and we resolve it).
CATEGORY_SHEET = {
    "recipe": "Recipes", "restaurant": "Restaurants", "movie": "Movies & TV",
    "game": "Games", "book": "Books", "random_food": "Random Food",
    "place": "Places", "product": "Products", "finance": "Finance",
    "ai_idea": "AI Ideas", "research": "Research",
    "self_improvement": "Self-Improvement", "relationships": "Relationships",
    "career": "Career", "news": "News", "other": "Other",
}

# corrections/metrics artifacts
CORRECTIONS_FILE = DATA_DIR / "corrections.jsonl"
CORRECTIONS_INBOX = DATA_DIR / "corrections.inbox.jsonl"
METRICS_FILE = DATA_DIR / "metrics.json"


def resolve_category(user_text: str) -> str | None:
    """Map user input (a category key OR a human label like 'AI Ideas') → key."""
    if not user_text:
        return None
    t = user_text.strip().lower()
    if t in CATEGORIES:
        return t
    for key, label in CATEGORY_SHEET.items():
        if t == label.lower():
            return key
    # also tolerate light variants: 'ai idea', 'movies', 'self improvement'
    squished = t.replace("&", "").replace("-", " ").replace("_", " ")
    squished = " ".join(squished.split())
    for key, label in CATEGORY_SHEET.items():
        norm_label = label.lower().replace("&", "").replace("-", " ")
        norm_label = " ".join(norm_label.split())
        if squished in (norm_label, key.replace("_", " ")):
            return key
    return None

DEFAULT_CONFIG: dict[str, Any] = {
    "ig_username": "",
    "allowed_sender_usernames": [],
    "allowed_sender_pks": [],
    "thread_amount": 10,
    "thread_message_limit": 20,
    "max_frames": 40,
    "frame_changed_pct": 1.0,
    "min_transcript_words": 10,
    "volume_gate_db": -50.0,
    "ack_max_chars": 400,
    "max_item_attempts": 3,
    "known_games": {},
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read config.json merged over the defaults. Missing file → defaults."""
    path = path or CONFIG_FILE
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    return cfg


def ensure_dirs() -> None:
    for d in (DATA_DIR, STATE_DIR, LOG_DIR, DIGESTS_DIR, LISTS_DIR,
              MASTERS_DIR, MEDIA_DIR):
        d.mkdir(parents=True, exist_ok=True)
