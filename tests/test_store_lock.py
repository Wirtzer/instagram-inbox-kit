"""captures.jsonl write path: idempotent upsert, atomic rewrite, and the
exclusive lock (which must serialize concurrent writers without corruption)."""

import json
import threading

from ig_inbox import route_store


def _read(path):
    return [json.loads(l) for l in path.read_text().split("\n") if l.strip()]


def test_upsert_is_idempotent(tmp_path):
    cap = tmp_path / "captures.jsonl"
    rec = {"id": "a", "category": "other", "summary": "first"}
    route_store.upsert_capture(rec, captures=cap)
    route_store.upsert_capture({**rec, "summary": "second"}, captures=cap)
    rows = _read(cap)
    assert len(rows) == 1
    assert rows[0]["summary"] == "second"  # replaced, not duplicated


def test_rewrite_is_atomic_and_ascii_safe(tmp_path):
    cap = tmp_path / "captures.jsonl"
    # A caption with a U+2028 line separator must stay on ONE line.
    recs = [{"id": "x", "category": "other", "summary": "line1 line2"}]
    route_store.rewrite_captures(recs, captures=cap)
    raw_lines = [l for l in cap.read_text().split("\n") if l.strip()]
    assert len(raw_lines) == 1
    assert _read(cap)[0]["summary"] == "line1 line2"


def test_concurrent_writers_do_not_corrupt(tmp_path):
    cap = tmp_path / "captures.jsonl"

    def writer(i):
        route_store.upsert_capture(
            {"id": f"id{i}", "category": "other", "summary": f"s{i}"}, captures=cap)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = _read(cap)  # every line must be valid JSON (no interleaving)
    assert len(rows) == 25
    assert {r["id"] for r in rows} == {f"id{i}" for i in range(25)}


def test_apply_updates_merges_by_id(tmp_path):
    cap = tmp_path / "captures.jsonl"
    route_store.rewrite_captures(
        [{"id": "a", "category": "restaurant", "summary": "s"}], captures=cap)
    n = route_store.apply_updates({"a": {"enrichment": {"city": "Springfield"}}}, captures=cap)
    assert n == 1
    assert _read(cap)[0]["enrichment"]["city"] == "Springfield"
