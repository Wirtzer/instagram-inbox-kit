"""Deterministic link extraction from caption + OCR text (no LLM, no tokens)."""

from ig_inbox import pipeline


def test_extracts_github_from_ocr_without_scheme():
    rec = {"caption": "great tool", "ocr_text": "github.com/example/repo"}
    assert "https://github.com/example/repo" in pipeline.extract_links(rec)


def test_ignores_instagram_and_cdn_links():
    rec = {"caption": "see instagram.com/p/abc and cdninstagram.com/x", "ocr_text": ""}
    assert pipeline.extract_links(rec) == []


def test_dedupes_and_caps_at_ten():
    caption = " ".join(f"site{i}.com/x" for i in range(15))
    links = pipeline.extract_links({"caption": caption, "ocr_text": ""})
    assert len(links) <= 10
    assert len(links) == len(set(links))


def test_merges_caption_and_ocr_sources():
    rec = {"caption": "https://pypi.org/project/thing", "ocr_text": "huggingface.co/models"}
    links = pipeline.extract_links(rec)
    assert "https://pypi.org/project/thing" in links
    assert "https://huggingface.co/models" in links
