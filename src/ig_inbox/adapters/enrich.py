"""Optional web enrichment for restaurants (address/hours/rating) and books
(Goodreads-style rating). This needs a model that can browse the web, which the
plain classify call does not.

Backends (ENRICH_BACKEND):
  auto           — DEFAULT. Uses anthropic_web when an ANTHROPIC_API_KEY is
                   present (so restaurants/places get looked up out of the box,
                   like the reference system); otherwise off. This makes ONE
                   extra web-search LLM call per enrichable item — a bit of extra
                   cost for real address/hours/rating. Set `none` to disable.
  none           — disabled. Restaurants/places keep only the facts the post
                   itself stated. The kit is fully functional this way.
  anthropic_web  — force the Anthropic web_search path (needs ANTHROPIC_API_KEY).

To use a different research provider (an agent with a browser, a search API,
your own tool), replace `_web_json()` — everything else keys off it.

All functions are best-effort and never raise: enrichment is a bonus, not a
correctness requirement.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _resolved_backend() -> str:
    b = (os.environ.get("ENRICH_BACKEND") or "auto").strip().lower()
    if b == "auto":
        return "anthropic_web" if os.environ.get("ANTHROPIC_API_KEY") else "none"
    return b


def enabled() -> bool:
    return _resolved_backend() != "none"


def _web_json(prompt: str, max_tokens: int = 1200, timeout: int = 220):
    """Ask a web-capable model and parse the first JSON value in its reply.
    Returns the parsed object/array, or None."""
    if _resolved_backend() != "anthropic_web":
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("WARN: ENRICH_BACKEND=anthropic_web but ANTHROPIC_API_KEY unset",
              file=sys.stderr)
        return None
    body = json.dumps({
        "model": os.environ.get("ENRICH_MODEL") or os.environ.get("LLM_MODEL")
        or "claude-haiku-4-5",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search",
                   "max_uses": int(os.environ.get("ENRICH_MAX_SEARCHES", "4"))}],
    }).encode()
    req = urllib.request.Request(ANTHROPIC_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": os.environ.get("LLM_API_VERSION", "2023-06-01"),
    })
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except Exception as exc:
        print(f"WARN: web enrichment call failed: {exc}", file=sys.stderr)
        return None
    text = "".join(b.get("text", "") for b in (data.get("content") or [])
                   if b.get("type") == "text")
    return _first_json(text)


def _first_json(text: str):
    for opener, closer in (("[", "]"), ("{", "}")):
        s, e = text.find(opener), text.rfind(closer)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                continue
    return None


# --- restaurant -------------------------------------------------------------

RESTAURANT_FIELDS = ("full_name", "address", "city", "cuisine", "hours",
                     "rating", "price", "website")


def lookup_restaurant(name: str, location_hint: str = "", notes: str = "") -> dict:
    """Return a dict of RESTAURANT_FIELDS (blank strings for anything unknown),
    or {} if enrichment is off / failed."""
    if not enabled() or not name:
        return {}
    hint = name
    if location_hint:
        hint += f" ({location_hint})"
    if notes:
        hint += f". Post says: {notes[:200]}"
    prompt = (
        "Use web search to find this specific restaurant, then reply with ONLY a "
        "JSON object (no prose, no markdown fence): "
        '{"full_name":"","address":"","city":"","cuisine":"","hours":"","rating":"","price":"","website":""}. '
        'Use "" for anything you cannot confirm; never guess an address. '
        f"Restaurant: {hint}"
    )
    got = _web_json(prompt)
    if not isinstance(got, dict):
        return {}
    return {k: str(got.get(k, ""))[:300] for k in RESTAURANT_FIELDS}


# --- books ------------------------------------------------------------------

def lookup_book_ratings(books: list[dict]) -> dict[str, str]:
    """Batch Goodreads-style ratings. Input: [{"title","author"}]. Returns
    {lower_title: "4.21"}. {} if enrichment off / failed."""
    if not enabled() or not books:
        return {}
    listing = "\n".join(
        f'- "{b.get("title","")}"' + (f' by {b["author"]}' if b.get("author") else "")
        for b in books)
    prompt = (
        "Use web search to find each book's Goodreads average rating. Reply with "
        "ONLY a JSON array (same order, no prose): "
        '[{"title":"","rating":""}] — rating like "4.21", "" if not found. '
        "Never guess a rating.\nBooks:\n" + listing)
    arr = _web_json(prompt)
    if not isinstance(arr, list):
        return {}
    out: dict[str, str] = {}
    for x in arr:
        if isinstance(x, dict) and x.get("title"):
            out[str(x["title"]).strip().lower()] = str(x.get("rating", ""))[:8]
    return out
