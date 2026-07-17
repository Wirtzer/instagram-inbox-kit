"""Pluggable integration points. Swap these to wire the kit into your stack.

  llm.py     — the ONE classify+extract call (Anthropic API or OpenAI-compatible)
  enrich.py  — optional web enrichment for restaurants/books (or disabled)
  notify.py  — where system alerts go (stdout / webhook)
  kb.py      — optional per-capture hook into your own knowledge base (no-op default)
"""
