"""LLM transport — the single pluggable place the kit talks to a model.

Two backends, chosen by the LLM_BACKEND env var:

  anthropic  → https://api.anthropic.com/v1/messages   (ANTHROPIC_API_KEY)
  openai     → <LLM_BASE_URL>/chat/completions          (LLM_API_KEY)
               works with OpenAI, OpenRouter, vLLM, llama.cpp, Ollama's
               OpenAI-compatible endpoint, or any internal proxy.

Only the standard library is used (urllib) so there is no vendor SDK to pin.
`complete()` returns the assistant's text, or None on any failure — callers
degrade gracefully rather than crash a whole run over one flaky request.

To point the kit at a totally different provider, this is the only file you
need to edit.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def backend() -> str:
    return (os.environ.get("LLM_BACKEND") or "anthropic").strip().lower()


def model() -> str:
    if os.environ.get("LLM_MODEL"):
        return os.environ["LLM_MODEL"]
    return "claude-haiku-4-5" if backend() == "anthropic" else "gpt-4o-mini"


def _anthropic(system: str, user: str, max_tokens: int, timeout: int) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("WARN: ANTHROPIC_API_KEY unset — LLM call skipped", file=sys.stderr)
        return None
    body = json.dumps({
        "model": model(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(ANTHROPIC_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": os.environ.get("LLM_API_VERSION", "2023-06-01"),
    })
    data = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    parts = data.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return text or None


def _openai(system: str, user: str, max_tokens: int, timeout: int) -> str | None:
    base = (os.environ.get("LLM_BASE_URL") or "").rstrip("/")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not base:
        print("WARN: LLM_BASE_URL unset for openai backend — LLM call skipped",
              file=sys.stderr)
        return None
    body = json.dumps({
        "model": model(),
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + "/chat/completions", data=body, headers=headers)
    data = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    choice = (data.get("choices") or [{}])[0]
    return (choice.get("message") or {}).get("content") or None


_RETRYABLE = (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
              socket.timeout, OSError, json.JSONDecodeError, KeyError)


def complete(system: str, user: str, max_tokens: int = 3000) -> str | None:
    """One completion. Two attempts with backoff. Never raises."""
    fn = _anthropic if backend() == "anthropic" else _openai
    for attempt, timeout in enumerate((60, 120), start=1):
        try:
            out = fn(system, user, max_tokens, timeout)
            if out and out.strip():
                return out
        except _RETRYABLE as exc:
            print(f"WARN: LLM attempt {attempt}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            time.sleep(2 ** attempt)
    return None
