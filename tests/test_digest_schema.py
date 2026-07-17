"""digest.digest() must return a schema-valid record, and degrade cleanly when
the LLM is unavailable. The LLM call is mocked — no network, no keys."""

import json

from ig_inbox import digest, config
from ig_inbox.adapters import llm


GOOD = json.dumps({
    "category": "recipe",
    "title": "Test Dish",
    "summary": "A short summary.",
    "key_points": ["step one", "step two"],
    "details": {"cuisine": "Test", "ingredients": ["a", "b"], "steps": ["mix", "cook"]},
    "entities": {"tickers": [], "books": [], "tools": [], "topics": []},
    "confidence": 0.9,
})

REQUIRED_KEYS = {"category", "title", "summary", "key_points", "details",
                 "entities", "confidence"}


def test_valid_response_parses(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: GOOD)
    out = digest.digest("caption", "transcript", "ocr")
    assert REQUIRED_KEYS <= set(out)
    assert out["category"] == "recipe"
    assert isinstance(out["category"], str) and out["category"] == out["category"].lower() and out["category"]
    assert isinstance(out["key_points"], list) and out["key_points"]
    assert set(out["entities"]) == digest.ENTITY_KEYS
    assert 0.0 <= out["confidence"] <= 1.0


def test_response_wrapped_in_prose_still_parses(monkeypatch):
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: "Sure! Here is the JSON:\n" + GOOD + "\nHope that helps")
    out = digest.digest("c", "t", "o")
    assert out["category"] == "recipe"


def test_novel_category_is_coined_not_rejected(monkeypatch, tmp_path):
    # Adaptive taxonomy: a category the author never used is COINED, not dumped
    # into 'other'. (An empty/missing category still degrades — see below.)
    monkeypatch.setattr(config, "TAXONOMY_FILE", tmp_path / "taxonomy.json")
    novel = json.dumps({"category": "Woodworking", "summary": "x"})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: novel)
    out = digest.digest("c", "t", "o")
    assert out["category"] == "woodworking"          # coined + normalized
    assert not out.get("degraded")
    assert "woodworking" in config.load_categories()  # persisted for next time


def test_empty_category_degrades(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps({"category": "", "summary": "x"}))
    out = digest.digest("c", "t", "o")
    assert out["category"] == "other"
    assert out.get("degraded") is True


def test_llm_unavailable_degrades_to_other(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: None)
    out = digest.digest("some caption", "", "")
    assert out["category"] == "other"
    assert out.get("degraded") is True
    assert REQUIRED_KEYS <= set(out)


def test_entities_are_coerced_and_capped(monkeypatch):
    resp = json.dumps({
        "category": "book", "title": "T", "summary": "s", "key_points": [],
        "details": {}, "confidence": 2.0,  # out of range → clamped
        "entities": {"books": [{"title": "Dune", "author": "Herbert", "why": "classic"},
                               {"notitle": True}],
                     "tickers": ["AAPL"], "tools": [], "topics": []},
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: resp)
    out = digest.digest("c", "t", "o")
    assert out["confidence"] == 1.0
    assert out["entities"]["books"] == [{"title": "Dune", "author": "Herbert", "why": "classic"}]
    assert out["entities"]["tickers"] == ["AAPL"]
