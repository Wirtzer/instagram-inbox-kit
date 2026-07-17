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

# --- category taxonomy (ADAPTIVE — grows from THIS user's content) ----------
# The taxonomy is NOT a fixed list. It starts from a small generic SEED (plus
# anything the user pre-seeds in config `categories`), and the classifier COINS
# new categories when a post doesn't fit — those get registered and persist in
# data/taxonomy.json. So the tabs reflect what *this* user actually saves, not
# whatever the kit's author happened to save. See `discover_categories` config.
SEED_CATEGORIES = [
    "recipe", "restaurant", "travel", "product", "finance",
    "book", "movie", "fitness", "home", "tech", "other",
]
# Pretty display names for known keys; unknown keys are title-cased on the fly.
_PRETTY = {
    "recipe": "Recipes", "restaurant": "Restaurants", "travel": "Travel",
    "product": "Products", "finance": "Finance", "book": "Books",
    "movie": "Movies & TV", "fitness": "Fitness", "home": "Home & DIY",
    "tech": "Tech", "ai_idea": "AI Ideas", "other": "Other",
}
CORRECTIONS_FILE = DATA_DIR / "corrections.jsonl"
CORRECTIONS_INBOX = DATA_DIR / "corrections.inbox.jsonl"
METRICS_FILE = DATA_DIR / "metrics.json"
TAXONOMY_FILE = DATA_DIR / "taxonomy.json"


def normalize_category(text: str) -> str:
    """A label → a canonical key: lowercase snake_case, alnum only. '' → ''."""
    import re
    t = (text or "").strip().lower()
    t = t.replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:40]


def _seed_taxonomy() -> list[str]:
    """SEED + any user-configured `categories`, deduped, 'other' last."""
    try:
        user = load_config().get("categories") or []
    except Exception:
        user = []
    keys, seen = [], set()
    for c in list(SEED_CATEGORIES) + [normalize_category(x) for x in user]:
        k = normalize_category(c) if c != "other" else "other"
        if k and k not in seen:
            seen.add(k); keys.append(k)
    if "other" in keys:  # keep 'other' last
        keys = [k for k in keys if k != "other"] + ["other"]
    return keys


def load_categories() -> list[str]:
    """The user's LIVE taxonomy (seeded on first use, grows via register)."""
    import json
    if TAXONOMY_FILE.exists():
        try:
            cats = json.loads(TAXONOMY_FILE.read_text())
            if isinstance(cats, list) and cats:
                return cats
        except Exception:
            pass
    cats = _seed_taxonomy()
    save_categories(cats)
    return cats


def save_categories(cats: list[str]) -> None:
    import json
    TAXONOMY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TAXONOMY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cats, ensure_ascii=True))
    tmp.rename(TAXONOMY_FILE)


def register_category(label: str) -> str:
    """Canonicalize `label`; if it's a genuinely new category, add it to the
    live taxonomy (unless discovery is disabled). Returns the canonical key."""
    key = normalize_category(label)
    if not key:
        return "other"
    cats = load_categories()
    if key in cats:
        return key
    # discovery gate: if off, unknown → 'other'
    try:
        if not load_config().get("discover_categories", True):
            return "other"
    except Exception:
        pass
    cats = [c for c in cats if c != "other"] + [key, "other"]
    save_categories(cats)
    return key


def pretty_label(key: str) -> str:
    if key in _PRETTY:
        return _PRETTY[key]
    return " ".join(w.capitalize() for w in key.split("_")) or "Other"


def resolve_category(user_text: str) -> str | None:
    """Map user input (a key or a human label) → a category key. A label that
    doesn't exist yet is REGISTERED (users invent categories by correcting)."""
    if not user_text or not user_text.strip():
        return None
    key = normalize_category(user_text)
    cats = load_categories()
    if key in cats:
        return key
    # match against pretty labels of existing categories
    for k in cats:
        if normalize_category(pretty_label(k)) == key:
            return k
    # a new category the user is coining via a correction
    return register_category(user_text)

DEFAULT_CONFIG: dict[str, Any] = {
    "ig_username": "",
    "allowed_sender_usernames": [],
    "allowed_sender_pks": [],

    # Category taxonomy is ADAPTIVE. Leave `categories` empty to let the kit
    # discover your categories from what you send; or pre-seed your interests
    # (e.g. ["woodworking","crypto","climbing"]). `discover_categories` lets the
    # classifier coin new categories when nothing fits (recommended).
    "categories": [],
    "discover_categories": True,
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
