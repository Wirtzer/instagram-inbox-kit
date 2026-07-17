"""One-time backfill: re-classify every capture with the current category schema
using the ALREADY-STORED caption + transcript + OCR — no media re-download.

Run this after you change the schema in digest.py; the live pipeline uses the new
digest for new items automatically. Restaurant enrichment + workbook build happen
on the next run (or run them by hand).

    python -m ig_inbox.backfill_reclassify
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from . import config, digest as digest_mod, route_store


def main() -> None:
    captures = config.CAPTURES_FILE
    if not captures.exists():
        print("no captures to reclassify")
        return
    recs = [json.loads(l) for l in captures.read_text().split("\n") if l.strip()]
    print(f"re-classifying {len(recs)} captures…")

    changed = 0
    for i, r in enumerate(recs, 1):
        if r.get("pinned"):
            continue  # user-corrected category — never re-flip it
        res = digest_mod.digest(r.get("caption", ""), r.get("transcript", ""),
                                r.get("ocr_text", ""))
        r.update(res)
        r.pop("enrichment", None)  # category may have changed; re-enrich later
        changed += 1
        if i % 25 == 0:
            print(f"  …{i}/{len(recs)}")

    route_store.rewrite_captures(recs)
    print(f"done. re-classified {changed}. categories now:",
          dict(Counter(r.get("category") for r in recs)))


if __name__ == "__main__":
    main()
