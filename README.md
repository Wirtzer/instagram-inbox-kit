# instagram-inbox-kit

Turn the Instagram reels and posts you share into a **searchable, categorized
knowledge base** — a per-category Excel workbook plus markdown lists — with one
cheap LLM call per item.

You share a reel to a **dedicated bot Instagram account**. Every hour this kit
reads those DMs, downloads each reel, figures out whether it has speech or is
music-only, transcribes the speech, reads the on-screen text, classifies the
post and extracts *everything actionable* in it (the full recipe, the whole
"50 date ideas" list, the repo link, the book titles), optionally enriches it
(restaurant address, book rating), stores it, and replies "Got it — …" in the
same DM thread.

It is a plain Python package. **No external framework required.** You bring an
LLM API key (Anthropic or any OpenAI-compatible endpoint) and, optionally, a
Deepgram key for speech.

> **Integrating this with an AI agent?** Point it at [`CLAUDE.md`](CLAUDE.md) —
> that file is written for an AI with zero prior context and tells it exactly
> what to install, what to ask you (only if it can't infer it), and how to verify
> success.

---

## Flow

```
        you share a reel/post to your BOT ig account's DMs
                              │
                 ┌────────────▼─────────────┐
                 │  poll (hourly, jittered)  │  ig_client.py  (instagrapi = IG's
                 │  whitelisted senders only │                 mobile private API)
                 └────────────┬─────────────┘
                              │  new items
                 ┌────────────▼─────────────┐
                 │  download media (CDN)     │  pipeline.py
                 └────────────┬─────────────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                 ▼
      audio vs silent?   frames @1fps      caption text
      (ffprobe + volume) → pixel-diff
              │           dedupe               │
        Deepgram          → OCR                │
        transcript      (Vision / Tesseract)   │
              └───────────────┼────────────────┘
                              ▼
                 ┌──────────────────────────┐
                 │  ONE LLM call:            │  digest.py  →  adapters/llm.py
                 │  classify + EXHAUSTIVE    │
                 │  extract + entities       │
                 └────────────┬─────────────┘
                              │  (+ optional "fit for you?" take on AI posts)
                 ┌────────────▼─────────────┐
                 │  enrich (optional):       │  adapters/enrich.py
                 │  restaurant / book web    │
                 └────────────┬─────────────┘
                              ▼
      ┌────────────────┬──────────────┬──────────────┬──────────────┐
      ▼                ▼              ▼              ▼              ▼
  captures.jsonl   digests/*.md   reading-list   Excel workbook   your KB
  (atomic+locked)                    .md         (tab/category)   (optional hook)
                              │
                              ▼
              deterministic "Got it — …" reply in the IG DM thread
```

## What you get

- **`data/lists/Instagram Inbox.xlsx`** — one sheet per collection or content
  category (Recipes, Restaurants, Movies & TV, Games, Books, Finance, AI Ideas,
  Research, …), each with purpose-built columns. Books & Restaurants get deduped
  master tabs (restaurants web-enriched with address/hours/rating).
- **`data/lists/*.md`** — the same content as grep-friendly markdown.
- **`data/captures.jsonl`** — the canonical structured record for every item.
- **`data/digests/YYYY-MM.md`** — a human-readable monthly log.

See real, synthetic output in [`examples/`](examples/) (`Instagram Inbox.xlsx`
built from the fabricated `captures.jsonl`).

## It learns from your corrections

Categorization is never perfect on day one — so the system **learns**. When the
system files something wrong, you fix it: type the right category into the amber
**"✎ Correct category?"** column in the workbook (or drop a line into
`data/corrections.inbox.jsonl`) and save. On the next run the kit:

- **records** the correction (`data/corrections.jsonl`) and **pins** that item to
  your category — it's never auto-re-flipped;
- feeds accumulated corrections back into the classifier as compact few-shot
  hints, so **similar future posts land in the right category**;
- refreshes **telemetry** — `data/metrics.json` and a **Metrics** tab in the
  workbook show total corrections, what's being fixed (`Other → Career`), the
  correction **rate over time** (it should trend *down* as it learns), and the
  most-corrected categories.

No black box: the metrics prove it's improving. Details in `ARCHITECTURE.md` §7.

## Quickstart

```bash
git clone <this-repo> instagram-inbox-kit && cd instagram-inbox-kit
python3 -m venv .venv && . .venv/bin/activate      # Python 3.10+
pip install -e .

cp .env.example .env            # fill in an LLM key (+ Deepgram if you want speech)
cp config.example.json config.json   # set your bot handle + your own handle

python -m ig_inbox.setup_cli    # builds OCR (macOS), then interactive IG login
python -m ig_inbox.pipeline --dry-run   # see what it would process
python -m ig_inbox.run          # do one real poll → workbook

# then schedule it — see scheduling/
```

Full step-by-step (creating the bot account, getting keys, the macOS-vs-Linux
OCR note) is in [`SETUP.md`](SETUP.md). The why-behind-every-part is in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Requirements

- **Python 3.10+**
- **ffmpeg / ffprobe** on PATH (audio gate, frame extraction)
- **An LLM key** — Anthropic (`ANTHROPIC_API_KEY`) or any OpenAI-compatible
  endpoint (`LLM_BASE_URL` + `LLM_API_KEY`)
- **A dedicated Instagram account** for the bot to log in as (never your main one)
- Optional: **Deepgram key** for speech transcription (without it, spoken content
  is skipped; captions + on-screen text still work)
- Optional: **OCR** — macOS Vision (best, built by setup) or Tesseract (Linux)

## License

MIT — see [`LICENSE`](LICENSE). This kit talks to Instagram through an
unofficial private-API library (instagrapi); use a dedicated account and read
the ban-risk notes in `ARCHITECTURE.md`.
