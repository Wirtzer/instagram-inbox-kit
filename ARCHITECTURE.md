# Architecture

This document breaks down every component of the kit and, more importantly,
**why each one exists** — most of the non-obvious pieces are scar tissue from a
real production deployment. If you're integrating or extending this, read the
"why" notes: they'll stop you from "simplifying" away something load-bearing.

## The shape of the problem

You want to save Instagram reels/posts into a knowledge base. The obstacles:

1. Instagram has **no public API** for reading your own DMs or downloading a
   reel's media. The only programmatic route is the **mobile private API**.
2. A reel's *meaning* is spread across three channels: the **caption**, the
   **spoken audio**, and the **on-screen text** — and lots of reels are
   music-only, so you can't assume speech.
3. You want the **actual content** ("here is the full recipe / the entire
   list / the repo link"), not a vague summary.
4. It has to run unattended for months without getting the account banned or
   quietly dropping items.

Every component below maps to one of those obstacles.

---

## 1. Ingestion — reading the DMs

Files: `ig_client.py`, `ig_login.py`, `challenge.py`

### instagrapi = Instagram's mobile private API (not scraping, not a proxy)

We use [`instagrapi`](https://github.com/subzeroid/instagrapi), which speaks
Instagram's **private mobile API** — the same endpoints the phone app uses. This
is deliberately *not* browser scraping and *not* a third-party proxy service:

- It reliably returns structured DM items (media PKs, permalinks, captions),
  which DOM scraping never did dependably. (The system this kit is derived from
  replaced an earlier DOM-scraping monitor that *never once* extracted a link.)
- It can download the reel's actual media from Instagram's CDN.
- A **browser `sessionid` cookie does NOT authorize the private API** — it's a
  different auth surface. You must log in through instagrapi.

Trade-off: it's unofficial and can break when Instagram changes the API, and it
carries **ban risk**. Both are managed below.

### A dedicated bot account

The kit logs in as a **separate Instagram account you create just for this** —
never your personal one. You then **share reels to that bot account's DMs** (or
save them into shared collections). Reasons:

- The private API carries ban risk. If Instagram flags the account, you lose a
  throwaway, not your real profile.
- "Share to a DM" is a natural, one-tap gesture from the Instagram app — it
  becomes your capture inbox.

### Sender whitelist by numeric PK (the security boundary)

`config.json` holds `allowed_sender_pks` — the numeric user IDs allowed to feed
the pipeline. **Content from anyone else is never processed.** This matters
because:

- The bot account can be added to group threads or messaged by strangers. Only
  *your* PK passes the gate in `fetch_new_items`.
- PKs (not usernames) are the boundary because usernames can be changed; the
  numeric PK is stable. `ig_login` resolves your username → PK once at setup.

### Session persistence + fixed device UUIDs (ban-risk hygiene)

`ig_login.py` logs in **once, interactively**, and dumps the full instagrapi
settings — session tokens *and the device UUIDs* — to a session file
(`chmod 600`, stored outside the repo). The poll loop only ever **resumes** from
that file; it never logs in with a password.

Why this specific design:

- **Password login from an automated loop is the single biggest ban trigger.**
  A watched account that re-authenticates on a schedule looks like a bot. So the
  loop is read-mostly and re-uses one long-lived session.
- **Reusing the stored device UUIDs on every re-login** (rather than minting a
  fresh "device") is what keeps a re-login from looking like a new, suspicious
  device. `ig_login` deliberately preserves them on `--force`.
- The client sets `delay_range=[1,3]` (polite pacing) and does **no validation
  call** on load — the first real request validates implicitly. An extra
  `get_timeline_feed()` per poll would double the API footprint for nothing.

### The email-code challenge auto-resolver (`challenge.py`)

Instagram periodically demands a login challenge. The **standard one** emails you
a 6-digit code. `challenge.py` can clear this with no human step: point it at an
IMAP mailbox (the one that receives the bot's security email) via
`IMAP_HOST/USER/PASSWORD`, and it reads the freshly-arrived code and submits it.

Key detail: it **snapshots the existing Instagram-email IDs *before* triggering
login** (`baseline_ids()`) and only ever accepts a code from a *newly arrived*
email — so a stale old code is never reused.

If IMAP isn't configured, it falls back to an interactive stdin prompt (you read
the email and type the code).

### The app-only challenge = a human step (by physics, not choice)

Newer checkpoints — device approval, "confirm your contact info" — are
restricted by Instagram to the **official mobile app**. *No automation can clear
them.* When one appears, a human must open the Instagram app as the bot account
and tap approve. The kit detects this, **stops trying** (see "challenge hold"
below), and alerts you once. This isn't a limitation we can code around; it's how
Instagram gates those flows.

---

## 2. Media interpretation — audio vs video

Files: `extract.py`, `deepgram.py`, `ocr.py`, `ocr.swift`

A reel's content lives in speech *and/or* on-screen text. We extract both, but
cheaply and only when it makes sense.

### The audio-vs-silent decision (a gate, cheapest checks first)

`extract.transcribe()` runs a ladder before spending a cent on transcription:

1. `ffprobe` — **is there an audio stream at all?** No → skip.
2. `ffmpeg volumedetect` — **is the mean volume above a floor** (`volume_gate_db`,
   default −50 dB)? Many reels are music-only or near-silent; below the floor →
   skip.
3. Extract audio to `.m4a`, send to **Deepgram**.
4. **Discard if under `min_transcript_words`** (default 10) — music with a few
   incidental words isn't speech worth keeping.

Why a gate and not "just transcribe everything": transcription costs money and
music-only reels produce garbage transcripts that pollute the classification.
The gate is pure local `ffmpeg`/`ffprobe` and near-free.

### Deepgram transcript with a sidecar cache (`deepgram.py`)

Speech → text via Deepgram. The raw JSON response is cached in a **sidecar file**
next to the audio. **Durability lesson baked in:** if the sidecar already exists
and parses, we reuse it instead of calling the API again. This bounds cost to
**exactly one API call per audio file, ever**, no matter how many times the rest
of the pipeline retries. (Upstream, an infinite-retry loop once re-billed the
same audio for days before this guard existed.)

Deepgram is optional. Without `DEEPGRAM_API_KEY`, spoken content is simply
skipped and the kit runs on caption + on-screen text.

### Frames → pixel-diff dedupe → OCR (on-screen text)

On-screen text is often where the real content is (recipe steps, list items,
repo URLs). Pipeline:

1. `ffmpeg -vf fps=1` — one frame per second, capped at `max_frames`.
2. **Pixel-diff dedupe** (`dedupe_frames`, `extract.py`) — keep a frame only if
   it visually differs from the last kept one. The metric is *"percentage of
   grayscale 96×96 pixels that moved by more than 25/255."* This is deliberate:
   a naive **mean-absolute-difference fails** here because one new line of text
   appearing on a mostly-black reel barely moves the global average but flips a
   small, dense cluster of pixels hard. The percentage-of-changed-pixels metric
   catches exactly that. Deduping first means we only OCR the handful of frames
   that actually changed — not 40 near-identical ones.
3. **OCR** each kept frame; order-preserving, case-insensitive dedupe of lines.

### OCR backend — macOS Vision, with a Tesseract fallback (`ocr.py`)

OCR is the **one platform-specific piece**. macOS **Vision** (`ocr.swift`,
compiled to a tiny CLI by setup) is dramatically more accurate on the stylized,
overlaid text reels use — but it's Mac-only. `ocr.py` dispatches by
`OCR_BACKEND`:

- `auto` (default): Vision binary if built, else Tesseract, else disabled.
- `vision`: force the compiled macOS CLI.
- `tesseract`: force Tesseract (`brew`/`apt install tesseract`) — the
  **cross-platform path for Linux**.
- `none`: disable.

So on macOS you get the best OCR; on Linux you install Tesseract and set
`OCR_BACKEND=tesseract` (or leave `auto`). Everything else is identical.

---

## 3. Classify + extract — the single LLM call

Files: `digest.py`, `assess.py`, `adapters/llm.py`

### One cheap call does everything

`digest.digest(caption, transcript, ocr_text)` makes **one** LLM call that
returns a strict JSON object: `category`, `title`, `summary`, an **exhaustive**
`key_points` list, category-specific `details`, and `entities`
(tickers/books/tools/topics). One call — not a chain — keeps it cheap enough to
run on every item.

The prompt pushes hard on **exhaustive extraction, not summarization**: if a post
is "50 date ideas," it must list all 50, merging caption and on-screen text. The
whole point of the kit is to keep the *content*, not a lossy gist.

### Injection hardening (the content is untrusted)

Captions/transcripts/OCR are **attacker-controllable text**. So:

- The shared content sits between **randomized sentinel markers** and is declared
  DATA, never instructions, in the system prompt.
- The output is **schema-validated in code** (`_parse_and_validate`): unknown
  category → rejected; fields are type-coerced, length-capped, and clamped;
  injected extra keys are dropped.
- **Routing is `switch(category)` in code** (`route_store.py`), never something
  the model or the content can name. The worst a malicious caption can achieve is
  a wrong *label* — there is no action surface to hijack.

If the model is unreachable or returns garbage twice, `digest` **degrades to
category `other`** with the raw text preserved, and flags `degraded: true`. One
bad item never crashes the run.

### Pluggable transport (`adapters/llm.py`)

The call goes through one adapter with two backends, chosen by `LLM_BACKEND`:

- **`anthropic`** → the Anthropic Messages API (`ANTHROPIC_API_KEY`).
- **`openai`** → any OpenAI-compatible `/chat/completions` endpoint
  (`LLM_BASE_URL` + `LLM_API_KEY`) — OpenAI, OpenRouter, vLLM, llama.cpp, Ollama,
  or an internal proxy.

Standard-library `urllib` only, so there's no vendor SDK to pin. This is the one
file to edit to point the kit at any other provider. *(In the private original,
this same seam pointed at an internal LLM proxy; abstracting it is what makes the
kit portable.)*

### The "fit for you?" take on AI posts (`assess.py`)

When someone shares an AI/automation post they're often implicitly asking "is
this worth adopting?" For `ai_idea` posts **only**, the kit makes one extra small
call with a short, configurable context about *your* setup (`ASSISTANT_CONTEXT`)
and stores a 2–4 sentence verdict (`assessment`). Other categories never pay for
this.

---

## 4. Enrichment — restaurants & books

Files: `enrich_restaurants.py`, `enrich_books.py`, `adapters/enrich.py`

Some categories benefit from facts the post didn't state. The **classify call
can't browse the web**, so enrichment is a separate, optional step behind
`adapters/enrich.py`.

- **Restaurants** get a web lookup for `address / city / cuisine / hours /
  rating / price / website`. Only restaurants (it's the category where an address
  is the whole point). Idempotent: only records missing an `enrichment` block are
  touched, capped per run so a burst doesn't stretch one poll.
- **Books** get a deduped master (`data/masters/books.json`): genre/author/about
  from the classify model's own knowledge (one batched LLM call — accurate for
  known titles), and a Goodreads-style **rating from web enrichment** (Goodreads
  has no public API).

`ENRICH_BACKEND` defaults to `none` — the kit is fully functional without it;
restaurants/books just keep whatever the post itself stated. Set it to
`anthropic_web` to use the Anthropic web-search tool, or replace `_web_json()` in
`adapters/enrich.py` to use your own research agent/search API.

---

## 5. Store + output

Files: `route_store.py`, `build_workbook.py`, `build_lists.py`

### Atomic, locked storage (`route_store.py`)

`captures.jsonl` is the canonical store, one JSON record per line. Every writer:

- **Takes an exclusive `flock`** on a lock file, and
- Writes via a **temp file + atomic rename**.

Why both: a concurrent backfill and a live poll writing at once *interleaved and
nearly corrupted the file* upstream — the lock serializes them. The temp-file +
rename means a crash mid-write can't leave a half-written line. Records are
written with `ensure_ascii=True` so an exotic Unicode line-separator (U+2028/9)
inside a caption can never split one record across two lines, and reads use
`split("\n")` rather than `splitlines()` for the same reason.

`apply_updates()` is the **stale-write-proof** partial update: it re-reads the
file *inside* the lock before merging, so a slow enrichment computed outside the
lock can't clobber a concurrent writer's changes (a real revert bug once lost a
batch of reclassifications this way).

### The dedup Excel workbook (`build_workbook.py`)

`build()` does a **deterministic full rebuild** of `Instagram Inbox.xlsx` from
`captures.jsonl` every run (safe to run anytime; the output is always the exact
current state). Tab rule:

- A saved-to **collection name wins** (if you saved a reel into an Instagram
  collection, that's your own organization — honored as the tab).
- Otherwise the **content category** (Recipes, Movies, Finance, …).
- **Books & Restaurants** additionally get **deduped master tabs** — one row per
  unique book/restaurant, merging every post that mentioned it.
- Games group by canonical game name (`known_games` in config) so variants land
  in one tab instead of scattering.

Each tab has **purpose-built columns** (recipes → ingredients + steps;
restaurants → address/hours/rating; AI ideas → key info + links + the
assessment). The markdown lists (`build_lists.py`) are the same data in a
grep-friendly form.

### Optional knowledge-base hook (`adapters/kb.py`)

After a record is durably stored, `kb.on_capture(record)` fires. Default: no-op.
Set `KB_HOOK_CMD` to a shell command (the record JSON arrives on stdin) to push
each capture into your own memory system / vector DB / notes app — or edit
`on_capture()` for an in-process integration. *(This seam replaced a hard-wired
call into the original system's memory store.)*

### The deterministic ack

The "Got it — …" DM reply is **built by a template in code**
(`route_store.compose_ack`), never by the LLM. It states what was saved, where,
and any caveats (media couldn't download / no on-screen text / classifier was
down). Deterministic acks can't be manipulated by post content and cost nothing.

---

## 6. Orchestration & durability

Files: `pipeline.py`, `run.py`, `scripts/run.sh`, `scheduling/`

### The poll loop (`pipeline.py`)

Per run: build the "known" set from the ledger → fetch new items from
whitelisted senders → per item: mark `processing` (write-then-act, so a crash is
visible and recoverable) → download → extract → digest → store → ack. A per-run
cap bounds compute; anything over the cap stays *unknown* in the ledger so the
next run re-fetches it — **nothing is silently dropped**.

### Durability mechanisms — and why each exists

- **`flock` + atomic writes** on `captures.jsonl` — prevents concurrent-writer
  corruption and torn writes (see §5).
- **The seen-ids gap guard** (`ig_client.fetch_new_items`) — the poll only reads
  the last N messages per thread for speed. But if that fast window contains *no
  message we've ever seen*, there may be unread items below it, so it **deep-reads
  the thread** to close the gap. Crucially, the "ever seen" high-water set
  includes the bot's *own* ack messages (`seen_ids`, not just the processed
  ledger) — otherwise the bot's acks fill the window, the guard thinks it's never
  caught up, and it deep-reads on *every* poll: pointless API burn on a watched
  account. This is the completeness guarantee that stops shares from slipping
  through unnoticed.
- **The challenge hold** (`pipeline._try_auto_relogin`) — a dead session triggers
  at most **one** guarded auto-relogin per 12h. But the moment a *challenge* is
  detected, a `challenge-hold` file is written and **all further login attempts
  stop** until a human clears it (`ig_login` deletes the hold on success). Reason:
  hammering login while Instagram already has an open challenge is flag fuel that
  keeps the account suspicious — it turns a one-tap fix into a multi-day outage.
- **No login-spam** — the poll loop *never* does password login (only session
  resume); password login is confined to the interactive `ig_login`. See §1.
- **Hourly polling with jitter** (`scripts/run.sh`) — hourly is frequent enough
  to feel live, gentle enough to stay under the radar; a random 0–120 s jitter
  keeps runs off a fixed clock. Idle polls cost $0.
- **Single-instance lock** (`run.sh`) — `flock` on Linux, a stale-aware
  `mkdir` lock on macOS (which has no `flock(1)`), so overlapping runs can't
  stomp each other.
- **Failure counter → one alert** (`run.sh` + `adapters/notify.py`) — after 3
  consecutive failed runs (or an immediate app-only-challenge exit), **one**
  alert goes out (stdout or webhook), then silence until recovery, when a single
  "recovered" note is sent. A broken account nags you once, not every hour.
- **Idempotency everywhere** — the item ledger (`state/processed.json`), the
  Deepgram sidecar cache, jsonl upsert-by-id, and an `ack_sent` flag (so an ack
  can be re-sent without re-processing) make the whole pipeline safe to re-run.

### Scheduling (`scheduling/`)

Both a **macOS launchd** example (`launchd.plist`) and **portable options**
(`cron.md`: cron, systemd timer, or a plain `while` loop) are provided. All of
them just call `scripts/run.sh`, which owns locking, jitter, and alerting.

---

## 7. The learning loop + telemetry

Files: `feedback.py`, plus hooks in `digest.py`, `build_workbook.py`, `pipeline.py`

A classifier that never learns makes the same mistake forever. This kit closes
the loop: when the user re-categorizes an item, the system records it, pins it,
and **teaches the classifier** so similar future items land right — and it
surfaces metrics so it's not a black box.

### Capturing a correction (and why the read-order matters)

The workbook is **regenerated on every run**. That creates a hazard: if the user
edits it, a naive rebuild would blow their edits away. So the design is
read-back-*before*-regenerate:

- Every per-post sheet carries two trailing columns: an amber **"✎ Correct
  category?"** cell (the user types a category into it) and **"id (don't edit)"**
  (the capture id, so a row maps back to its record).
- At the **start** of a run — before anything rebuilds the workbook —
  `feedback.ingest_corrections()` opens the *existing* workbook, reads any filled
  correction cells (`read_workbook_overrides`), and also drains a manual
  `data/corrections.inbox.jsonl` (`read_inbox_overrides`) for people who'd rather
  edit a text file.
- For each override whose target category differs from what the system assigned,
  it appends a correction `{id, signature, from_category, to_category, ts}` to
  `data/corrections.jsonl` and **pins** the record (`pinned: true`, category set)
  in `captures.jsonl`.

Durability is identical to the rest of the store: `corrections.jsonl` is written
under an exclusive `flock` via temp-file + atomic rename, and the record update
goes through `route_store.apply_updates`, which re-reads inside the lock so it
can't clobber a concurrent writer. A pinned record is skipped by
`backfill_reclassify` — a user's decision is never re-flipped by the machine.

### Applying what was learned (few-shot injection)

`feedback.few_shot_block()` turns accumulated corrections into a compact block
appended to the classifier's system prompt (`digest._system_prompt`):

```
Past corrections from the user — learn from these; classify similar posts the same way:
- A post about "Standing-desk converter" should be Career, not Other.
- ...
```

It's **token-conscious**: most-recent-first, deduped by (signature, target), and
capped at ~12 examples. This is the mechanism that makes classification adapt —
the next time a similar post arrives, the model has the user's own precedent in
front of it.

### Telemetry (`data/metrics.json` + the Metrics tab)

`feedback.compute_metrics()` (run on every workbook build) writes
`data/metrics.json` and populates a **Metrics** sheet:

- **total corrections** and **total items classified**;
- **corrections by transition** (`Other → Career: N`) — shows *what* is being
  fixed;
- **correction rate over time** (corrections ÷ items, monthly) — this should
  **trend down** as the few-shot hints take effect, which is the proof the loop
  works;
- **most-corrected source categories** — signals where the taxonomy or prompt
  needs attention (e.g. if `Other` is corrected constantly, the prompt's `other`
  guidance is too greedy);
- a plain-language **"what was learned"** summary.

Because it's all recomputed deterministically from `captures.jsonl` +
`corrections.jsonl`, the metrics are always consistent with the current state and
can never drift from reality.

## What was abstracted out of the private original

This kit is a genericized export. These couplings were replaced with the
adapters/config so it runs anywhere:

| Original (private) | Now |
|---|---|
| Internal LLM proxy at a fixed localhost port | `adapters/llm.py` (Anthropic or OpenAI-compatible, via env) |
| A specific in-house memory store (`remember-*.sh`) | `adapters/kb.py` `on_capture` hook (no-op default) |
| An in-house agent with web tools | `adapters/enrich.py` (Anthropic web search, or disabled) |
| iMessage system alerts via a specific CLI | `adapters/notify.py` (stdout / webhook) |
| Email-code reader tied to a specific mail CLI | `challenge.py` generic IMAP (or interactive) |
| Hardcoded user paths, a real bot handle, a real PK | `config.py` + `.env` + `config.json` |
| A shared Deepgram helper in another repo | self-contained `deepgram.py` (cache guard preserved) |
| iCloud mirror delivery to a device | dropped — the kit writes local files (add your own delivery) |
