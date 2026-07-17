# SETUP.md — from zero to a running inbox

Step-by-step, start to finish. Budget ~20 minutes. If you're an AI doing this for
a user, also read `CLAUDE.md` (it tells you what to ask vs. default).

---

## 1. Create a dedicated bot Instagram account

**Do not use your personal account.** This kit logs in through Instagram's
unofficial private API, which carries ban risk; use a throwaway.

1. In the Instagram app, create a **new account** (a new email + username, e.g.
   `my_saves_bot`). A separate email you control makes challenge codes easy.
2. Log into it once on your phone and complete any "confirm your email" steps, so
   the account is fully activated.
3. From **your personal** account, send that bot account a DM (say hi) so a DM
   thread exists. You'll share reels into this thread.
4. (Optional) Create Instagram **collections** on the bot account (e.g.
   "Restaurants", "Games"). When you save a shared reel into a collection, the kit
   uses the collection name as its workbook tab.

**How you'll use it day-to-day:** on any reel/post, tap **Share → your bot
account**. That's the capture gesture.

---

## 2. Get your keys

### LLM key (required — pick one)

- **Anthropic**: create a key at the Anthropic console. You'll set
  `ANTHROPIC_API_KEY` and `LLM_BACKEND=anthropic`.
- **OpenAI-compatible** (OpenAI, OpenRouter, a local vLLM/llama.cpp/Ollama
  server, or an internal proxy): set `LLM_BACKEND=openai`, `LLM_BASE_URL` (must
  expose `/chat/completions`), and `LLM_API_KEY`.

A small, cheap model is plenty — the default is `claude-haiku-4-5` /
`gpt-4o-mini`. One item costs on the order of ~1–2K tokens.

### How do you want spoken audio handled? (pick one)

Some reels put the useful info only in the voiceover. Three ways to turn that
into text — set `TRANSCRIBE_BACKEND` in `.env`:

| Option | `TRANSCRIBE_BACKEND` | What you need | Trade-off |
|---|---|---|---|
| **Deepgram** (recommended) | `deepgram` (or `auto` + a key) | A free account at [deepgram.com](https://deepgram.com) → set `DEEPGRAM_API_KEY` | Fast; works on any computer. Free credits are generous and last a long time. Audio is sent to Deepgram. Caches every transcript so it never pays twice. |
| **On-device Whisper** | `whisper` | `pip install "ig-inbox-kit[whisper]"` | No account, fully private (audio never leaves your machine) — **but needs a reasonably powerful computer**. Slower. |
| **Skip audio** | `none` | nothing | Captions + on-screen text still capture most posts. |

**Not sure if your machine can run Whisper?** After installing (Step 4), run
`python -m ig_inbox.doctor` — it reports your CPU/RAM and whether on-device
Whisper looks viable, and it steers you to Deepgram if the machine's marginal.
Default (`auto`) uses Deepgram if a key is set, otherwise skips audio.

> **Model note:** the classifier + research calls default to `claude-haiku-4-5`
> — a small, **cost-efficient** model. Bump `LLM_MODEL` to Sonnet or gpt-4o for
> higher quality at higher cost. This runs on its own API key; it does not depend
> on any Claude app you may or may not have on your phone.

---

## 3. Install system dependencies

**All platforms:** Python 3.10+ and ffmpeg.

```bash
# macOS
brew install python@3.12 ffmpeg
xcode-select --install          # for the Vision OCR compiler (swiftc)

# Debian/Ubuntu
sudo apt update && sudo apt install -y python3 python3-venv ffmpeg tesseract-ocr
```

### OCR: macOS vs Linux (the one platform-specific bit)

On-screen text recognition is the only piece that differs by OS:

- **macOS** — the kit compiles a tiny **Vision** CLI from `ocr.swift` during
  setup (needs Xcode command-line tools). Vision is the most accurate option for
  the stylized text reels use. Leave `OCR_BACKEND=auto`.
- **Linux** — install **Tesseract** (`apt install tesseract-ocr`). The kit
  auto-detects it (`OCR_BACKEND=auto`), or force it with `OCR_BACKEND=tesseract`.
  Tesseract is less accurate on decorative overlays than Vision, but works
  everywhere.
- To turn OCR off entirely: `OCR_BACKEND=none`.

---

## 4. Install the kit

```bash
git clone <this-repo> instagram-inbox-kit && cd instagram-inbox-kit
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

---

## 5. Configure

```bash
cp .env.example .env
cp config.example.json config.json
```

Edit **`.env`** — at minimum the LLM key. Required vs optional is annotated in
the file. Example (Anthropic + Deepgram):

```ini
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5
DEEPGRAM_API_KEY=...
```

Edit **`config.json`**:

```json
{
  "ig_username": "my_saves_bot",
  "allowed_sender_usernames": ["your_personal_handle"]
}
```

- `ig_username` — the **bot** account the kit logs in as.
- `allowed_sender_usernames` — **your** personal handle(s). Only shares from
  these are ever processed. (Setup resolves these to numeric PKs automatically.)

### Optional: auto-resolve emailed login codes (IMAP)

Instagram sometimes emails a 6-digit login code. To clear that with no human
step, point the kit at the mailbox that receives the bot's security email:

```ini
IMAP_HOST=imap.gmail.com
IMAP_USER=my_saves_bot_email@gmail.com
IMAP_PASSWORD=your-app-specific-password   # NOT your main password
```

(For Gmail, create an "app password".) If you skip this, login just prompts you
to type the code yourself.

---

## 6. Run setup (builds OCR + logs in)

```bash
python -m ig_inbox.setup_cli --check     # verify env first (no login)
python -m ig_inbox.setup_cli             # build OCR (macOS) + interactive login
```

During login the kit tries, in order: an existing session, a browser
`sessionid`, a credential file, then an interactive password prompt. It saves the
session (chmod 600, in `~/.ig_inbox/credentials/` by default) and resolves your
sender PK.

**If Instagram throws a challenge:**
- *Standard emailed code* — auto-resolved if you set up IMAP; otherwise type the
  code from the email.
- *App-only approval* ("confirm it's you" / device approval) — **no automation
  can clear this.** Open the Instagram **app** as the bot account, approve, then
  re-run `python -m ig_inbox.ig_login`.
- *Password rejected* — often an IP/device flag, not a wrong password. Log into
  `instagram.com` in a browser, grab the `sessionid` cookie (DevTools →
  Application → Cookies → instagram.com → `sessionid`), and run
  `python -m ig_inbox.ig_login --sessionid <value>`.

---

## 7. First run

Share one reel to the bot account from your phone, then:

```bash
python -m ig_inbox.pipeline --dry-run    # shows what it would process
python -m ig_inbox.run                   # real run → builds the workbook
```

Check the output:

```bash
ls -la data/lists/          # "Instagram Inbox.xlsx" + markdown lists
cat data/captures.jsonl     # one JSON record per item
```

Open `data/lists/Instagram Inbox.xlsx` — you'll see per-category tabs, the Books
& Restaurants masters, and a **Metrics** tab.

---

## 8. Schedule it

Run it hourly so it stays current. See `scheduling/`:

- **macOS** → `scheduling/launchd.plist` (edit paths, then
  `launchctl bootstrap`).
- **Linux / anywhere** → `scheduling/cron.md` (cron, systemd timer, or a plain
  loop).

All of them call `scripts/run.sh`, which handles the single-instance lock, poll
jitter, and the "3 strikes → one alert" failure notification for you.

---

## 9. Teach it (optional but recommended)

Open the workbook, find something mis-categorized, and type the correct category
into the amber **"✎ Correct category?"** column, then save. On the next run the
kit records the correction, pins that item, learns from it for future posts, and
updates the **Metrics** tab. See `ARCHITECTURE.md` §7.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `allowed_sender_pks is empty` | Login didn't resolve your PK — re-run `python -m ig_inbox.ig_login` |
| Exit code 2 (session dead) | Re-run `python -m ig_inbox.ig_login` |
| Exit code 3 (challenge) | App-only approval — approve in the Instagram app, then re-login |
| No transcript on video reels | `DEEPGRAM_API_KEY` unset, or the reel is music-only (by design) |
| No on-screen text | No OCR backend — build Vision (macOS) or `apt install tesseract-ocr` |
| `ffmpeg not found` | Install ffmpeg; or set `FFMPEG_BIN`/`FFPROBE_BIN` in `.env` |
| Repeated challenges | Stop re-running login — that's flag fuel. Wait, approve in-app once, then a single `ig_login`. |
