# CLAUDE.md — integrate this kit (for an AI with zero prior context)

You are an AI assistant helping a user stand up **instagram-inbox-kit**. This
file is your complete operating manual. Read it top to bottom before acting. You
do **not** need any other context — everything is here or linked.

## What this kit does (one paragraph)

The user shares Instagram reels/posts to a **dedicated bot Instagram account's
DMs**. This kit polls those DMs hourly, downloads each reel, decides audio-vs-
silent, transcribes speech (Deepgram) + reads on-screen text (OCR), then makes
**one LLM call** to classify the post and exhaustively extract its content. It
writes a categorized **Excel workbook** + markdown lists + a JSONL store, and
replies "Got it — …" in the DM thread. It's a plain Python package — no
framework. Deeper detail: `ARCHITECTURE.md`.

---

## Onboarding: what to ask the user (only if needed)

Your goal: **a well-configured drop-in asks zero questions; a bare checkout asks
only the 2–3 that truly matter.** Do not interrogate.

**Procedure:**

1. **Inspect first, don't ask.** Check what already exists before asking anything:
   ```bash
   test -f .env && grep -vE '^\s*#|^\s*$' .env          # already-set env values
   test -f config.json && cat config.json                # bot handle, allowed senders
   printenv | grep -E 'ANTHROPIC_API_KEY|LLM_BASE_URL|LLM_API_KEY|DEEPGRAM_API_KEY'
   ```
   Consult the **Input table** below to classify each input as
   *present / inferable / defaulted / missing-required*.

2. **Default everything that has a default.** Do **not** ask about anything the
   table marks with a sensible default (output dir, thresholds, OCR backend,
   enrichment, notifications, paths, model). Just write the default.

3. **Ask only for genuinely-missing REQUIRED inputs — in ONE short batch.** The
   only inputs with **no default** are the ones a stranger can't guess: the bot
   Instagram account, the **trigger handles** (see below), and an LLM key. If any
   of those can't be found in step 1, ask for *all* the missing ones at once
   (2–3 questions max), each framed plainly with what it's for and an example
   value. Deepgram is a *soft* ask: mention that without it, spoken audio is
   skipped (captions + on-screen text still work) — offer it, but don't block on it.

   **Trigger handles = the security boundary.** The kit ONLY processes shares
   from an explicit allow-list of Instagram handles (`allowed_sender_usernames`,
   a **list**). A share from anyone else is ignored — so a random DM can never
   inject into the sheet. When asking, ask for **everyone the user wants to be
   able to trigger it**, not just themselves: "Whose Instagram shares should this
   act on? Usually just your own handle — but list anyone you want to be able to
   feed it (a partner, a teammate)." Accept one or more; write them all to
   `allowed_sender_usernames`. At least one is required.

4. **If nothing required is missing → ask nothing. Proceed.**

5. **After the user answers, write the values for them** into `.env` (keys,
   backends, output dir) and `config.json` (`ig_username`,
   `allowed_sender_usernames`), then continue to Setup. Never print secrets back
   to the user or into logs.

**Example of a good single batch** (only if all three are unknown):

> I need three things to wire this up:
> 1. **The bot Instagram account** it should log in as — the throwaway account
>    you'll share reels to (e.g. `my_saves_bot`). Not your personal account.
> 2. **Whose shares should trigger this?** — the handle(s) allowed to feed it.
>    Usually just your own (e.g. `jane_doe`), but list anyone you want to be able
>    to trigger it (a partner, a teammate). Anyone not on the list is ignored.
> 3. **An LLM API key** — either an Anthropic key (`sk-ant-…`) or an
>    OpenAI-compatible base URL + key. Which do you have?
>
> Optional: a **Deepgram key** for transcribing spoken audio in reels. Without
> it I'll still read captions and on-screen text — want to add it?

**Categories are adaptive — don't ask unless useful.** The tabs are NOT a fixed
list; they grow from what this user actually sends (the classifier coins a new
category when nothing fits, and it persists). So do NOT impose the author's
categories. You *may* optionally ask, in one line, "any topics you already know
you save a lot of? (e.g. woodworking, crypto) — or skip and it'll learn your
categories from your posts." Write any answer to config `categories`; leaving it
empty is completely fine (discovery handles it).

### Input table (decide mechanically what's missing vs defaulted)

| Input | Required? | How to get it | Sensible default |
|---|---|---|---|
| **Bot IG account** (`ig_username` + its login) | **REQUIRED — no default** | User creates a dedicated IG account (see `SETUP.md` §1); login happens interactively in setup | — (must ask) |
| **Trigger handles** (`allowed_sender_usernames` = a **list**: everyone allowed to feed it) | **REQUIRED — no default; ≥1** | The user's own @handle, plus anyone else they want to trigger it; each resolved to a numeric PK at login. Shares from anyone NOT listed are ignored — this is the security boundary. | — (must ask) |
| **LLM key** (`ANTHROPIC_API_KEY`, or `LLM_BASE_URL`+`LLM_API_KEY`) | **REQUIRED — no default** | Anthropic console, or any OpenAI-compatible endpoint | — (must ask) |
| **LLM backend / model** (`LLM_BACKEND`, `LLM_MODEL`) | optional | — | `anthropic` / `claude-haiku-4-5` (or `openai` / `gpt-4o-mini`) |
| **Deepgram key** (`DEEPGRAM_API_KEY`) | optional | deepgram.com | unset → **transcription skipped**, kit degrades to caption + OCR |
| **Output folder** (the Excel + lists location) | optional | — | `./data/lists/` (under the repo). Set `IG_INBOX_DATA_DIR` to relocate |
| **OCR backend** (`OCR_BACKEND`) | optional | macOS: built by setup; Linux: `apt/brew install tesseract` | `auto` (Vision on macOS, else Tesseract, else off) |
| **Web enrichment** (`ENRICH_BACKEND`) | optional | needs a web-capable LLM | `none` (restaurants/books keep post-only facts) |
| **Notifications** (`NOTIFY_BACKEND`, `NOTIFY_WEBHOOK_URL`) | optional | Slack/Discord/ntfy webhook | `stdout` |
| **KB hook** (`KB_HOOK_CMD`) | optional | user's own memory system | unset → no-op |
| **IMAP challenge auto-resolver** (`IMAP_*`) | optional | mailbox that gets IG security email + app password | unset → interactive code prompt at login |
| **Thresholds** (frames, volume gate, word min, attempts) | optional | — | shipped defaults in `config.example.json` |

The same required-vs-optional split is annotated in `.env.example` and
`config.example.json`, so you can cross-check.

---

## Prerequisites (verify, install if missing)

```bash
python3 --version          # need 3.10+
ffmpeg -version | head -1   # need ffmpeg + ffprobe (brew/apt install ffmpeg)
```

- **Python 3.10+**, **ffmpeg/ffprobe** on PATH — required.
- **macOS**: OCR uses the built-in Vision framework (setup compiles a tiny Swift
  CLI; needs Xcode command-line tools: `xcode-select --install`).
- **Linux**: install Tesseract for OCR (`apt install tesseract-ocr`) — the kit
  auto-detects it. Everything else is identical.

---

## Integration steps (exact commands)

```bash
# 1. Isolated environment + install
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

# 2. Config files
cp .env.example .env
cp config.example.json config.json
# → now fill them per the Input table above (you write these for the user).
#   .env:        the LLM key (+ Deepgram if provided)
#   config.json: "ig_username" = bot account, "allowed_sender_usernames" = [user's handle]

# 3. Verify the environment before touching Instagram
python -m ig_inbox.setup_cli --check
#   Prints ok/MISSING for ffmpeg, ffprobe, LLM, deepgram, ocr, session.

# 4. One-time setup: builds the OCR binary (macOS) + interactive IG login
python -m ig_inbox.setup_cli
#   This logs into the BOT account. It may prompt for a password or a browser
#   sessionid, and (if IMAP isn't configured) an emailed challenge code.
#   On success it resolves the whitelisted sender's numeric PK into config.json.

# 5. Dry run — poll only, print what WOULD be processed, no side effects
python -m ig_inbox.pipeline --dry-run

# 6. First real run — processes items, builds the workbook, sends acks
python -m ig_inbox.run

# 7. Schedule it (hourly). See scheduling/ :
#    macOS → scheduling/launchd.plist ; Linux/other → scheduling/cron.md
```

Before the first real run, have the user **share one reel** to the bot account
from their personal account, so step 5/6 has something to process.

---

## The learning loop (drive this — it's a headline feature)

The classifier **learns from the user's corrections**, and the workbook shows the
metrics. Explain this to the user and help them use it:

**How the user corrects a category:**
- Open `data/lists/Instagram Inbox.xlsx`. Every per-post sheet has an amber
  **"✎ Correct category?"** column and an **"id (don't edit)"** column.
- To re-file an item (e.g. something the system put in *Other* that's really
  *Career*), type the right category name (a sheet label like `Career` or
  `AI Ideas`, case-insensitive) into that row's amber cell and **save the file**.
- Alternatively, append a line to `data/corrections.inbox.jsonl`:
  `{"id": "ig_...", "to_category": "career"}`.

**What happens on the next run (`python -m ig_inbox.run`):**
1. Overrides are read back from the workbook **before** it's regenerated (so
   edits are never lost), logged to `data/corrections.jsonl`, and the record is
   **pinned** to the chosen category (never auto-re-flipped).
2. Accumulated corrections become compact few-shot hints injected into the
   classifier prompt, so **similar future posts land in the right category**.
3. `data/metrics.json` and the workbook's **Metrics** tab refresh: total
   corrections, transitions (e.g. `Other → Career`), the correction **rate over
   time** (should trend down as it learns), most-corrected categories, and a
   short "what was learned" summary.

To show the user it's working: open the **Metrics** tab, or `cat data/metrics.json`.
When helping them, tell them plainly: *"correct a category once and it teaches
the classifier; the Metrics tab proves the correction rate is dropping."*
Full mechanics: `ARCHITECTURE.md` §7.

## The plug-in points (adapters)

All integration seams live in `src/ig_inbox/adapters/`. To wire the kit into a
larger system, edit these — nothing else:

| File | Purpose | Default | How to change |
|---|---|---|---|
| `adapters/llm.py` | the ONE classify+extract call | Anthropic API | set `LLM_BACKEND=openai` + `LLM_BASE_URL`/`LLM_API_KEY`, or edit `complete()` |
| `adapters/enrich.py` | optional web enrichment | disabled (`none`) | set `ENRICH_BACKEND=anthropic_web`, or replace `_web_json()` with your search agent |
| `adapters/notify.py` | system alerts | stdout | set `NOTIFY_BACKEND=webhook`+`NOTIFY_WEBHOOK_URL`, or edit `notify()` for iMessage/email/PagerDuty |
| `adapters/kb.py` | per-capture hook into your KB | no-op | set `KB_HOOK_CMD` (record JSON on stdin), or edit `on_capture()` |

Everything path-, handle-, and threshold-related is in `config.py` (env-driven) +
`config.json`. There are **no hardcoded secrets or personal paths** anywhere.

---

## How to verify success

Run these; each should hold:

```bash
# a) Package imports and tests pass (mocked LLM — no keys needed)
pip install pytest && python -m pytest -q
#    → expect "16 passed" (or more)

# b) Environment is fully green
python -m ig_inbox.setup_cli --check
#    → ffmpeg/ffprobe ok, LLM ok, session present

# c) The workbook builds from the shipped synthetic sample
python - <<'PY'
from pathlib import Path
from openpyxl import load_workbook
from ig_inbox import build_workbook
ex = Path("examples")
build_workbook.build(captures_path=ex/"captures.jsonl", out_path=Path("/tmp/wb.xlsx"),
                     books_master=ex/"books.master.json",
                     known_games={"pow world": "Palworld"})
print("sheets:", load_workbook("/tmp/wb.xlsx").sheetnames)
PY
#    → lists Overview, Metrics, Books, Restaurants, Recipes, AI Ideas, Finance, ...

# d) A live dry-run sees the account (after setup/login)
python -m ig_inbox.pipeline --dry-run
#    → JSON of new_items (empty list is fine if nothing new was shared)

# e) After a real run, the outputs exist
ls -la data/lists/            # "Instagram Inbox.xlsx" + *.md
wc -l data/captures.jsonl     # one line per captured item
```

Success = tests pass, `--check` is green, a dry-run reaches Instagram without a
session/challenge error, and a real run produces `data/lists/Instagram
Inbox.xlsx`.

## When something needs a human

- **Exit code 3 / "ChallengeRequired"**: Instagram wants an app-only approval no
  automation can do. Tell the user to open the Instagram app as the **bot
  account** and approve, then re-run `python -m ig_inbox.ig_login`. (Standard
  emailed-code challenges auto-resolve if `IMAP_*` is configured.)
- **Exit code 2 / "session dead"**: re-run `python -m ig_inbox.ig_login`.
- **No transcript on video reels**: `DEEPGRAM_API_KEY` unset (expected if the
  user skipped it) or the reel is music-only (by design).
- **No on-screen text**: no OCR backend — build the Vision binary (macOS) or
  install Tesseract (Linux).

Full runbook and the reasoning behind the durability behaviors: `SETUP.md` and
`ARCHITECTURE.md`.
