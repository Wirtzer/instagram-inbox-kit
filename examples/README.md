# Example output (100% synthetic)

Everything here is **fabricated** — no real accounts, people, handles, or
captured content. It exists so you can see what the kit produces before running
it.

| File | What it is |
|---|---|
| `captures.jsonl` | 10 synthetic capture records spanning recipe, restaurant, book, ai_idea, finance, movie, game, research, random_food, and a typed note. This is the canonical store format. |
| `books.master.json` | The deduped book master (genre/author/about/rating) that feeds the workbook's **Books** tab. |
| `corrections.jsonl` | 3 fabricated user corrections (all `Other → …`) that drive the learning loop and telemetry. |
| `metrics.json` | The telemetry computed from the above (totals, transitions, correction rate over time, "what was learned"). |
| `Instagram Inbox.xlsx` | The generated workbook: Overview, **Metrics**, Books & Restaurants masters, and a per-category/collection tab each with an editable **"✎ Correct category?"** column. |
| `lists/*.md` | The grep-friendly markdown companions (Books, AI Ideas, Finance, Research, and a per-collection list). |

## Regenerate it yourself

```bash
python - <<'PY'
import json
from pathlib import Path
from ig_inbox import feedback, build_workbook, build_lists
ex = Path("examples")
recs = [json.loads(l) for l in (ex/"captures.jsonl").read_text().split("\n") if l.strip()]
m = feedback.compute_metrics(records=recs, corrections_path=ex/"corrections.jsonl",
                             out_path=ex/"metrics.json")
build_workbook.build(captures_path=ex/"captures.jsonl", out_path=ex/"Instagram Inbox.xlsx",
                     books_master=ex/"books.master.json",
                     known_games={"pow world": "Palworld"}, metrics=m)
build_lists.build_all(captures_path=ex/"captures.jsonl", lists_dir=ex/"lists")
print("regenerated example workbook + lists + metrics")
PY
```
