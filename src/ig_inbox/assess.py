"""Optional "fit for me/us" take on ai_idea posts.

When someone shares an AI/automation post they're often implicitly asking "is
this worth adopting for my setup?" This adds ONE extra LLM call per ai_idea item
— never for other categories — with a short, configurable context describing
YOUR system, so the assessment is grounded rather than generic.

Set ASSISTANT_CONTEXT in .env to describe your stack. Returns '' on any failure.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .adapters import llm

DEFAULT_CONTEXT = (
    "You are assessing whether a shared AI/automation post is worth adopting for "
    "the reader's personal setup. Assume a technical individual who runs some "
    "automation and coding tools and wants a concrete, honest verdict."
)

INSTRUCTION = (
    "\n\nAssess the post below in 2-4 sentences, concretely: (1) does this overlap "
    "with something the reader likely already has, (2) what specific piece could "
    "improve their setup and where it would plug in, (3) verdict — adopt / "
    "experiment / skip, and why. Be direct; don't flatter the post. Plain text, "
    "no preamble."
)


def _context() -> str:
    return (os.environ.get("ASSISTANT_CONTEXT") or DEFAULT_CONTEXT) + INSTRUCTION


def assess(record: dict[str, Any]) -> str:
    """One-call assessment for an ai_idea record. Returns '' on any failure."""
    kp = "\n".join(f"- {p}" for p in (record.get("key_points") or [])[:15])
    links = ", ".join(record.get("links") or [])
    prompt = (f"POST: {record.get('title') or ''}\n"
              f"SUMMARY: {record.get('summary') or ''}\n"
              f"KEY POINTS:\n{kp}\n"
              + (f"LINKS: {links}\n" if links else ""))
    try:
        out = llm.complete(_context(), prompt[:3000], max_tokens=400)
        return (out or "").strip()[:900]
    except Exception as exc:
        print(f"WARN: assessment failed: {exc}", file=sys.stderr)
        return ""
