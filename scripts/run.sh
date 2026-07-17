#!/bin/bash
# instagram-inbox-kit entry point — the reliability shell around the poll.
#
# Responsibilities (all durability lessons baked in):
#   - single-instance lock (flock on Linux, mkdir-lock fallback on macOS)
#   - poll jitter (random 0..JITTER_MAX s) so runs don't hit IG on a fixed clock
#   - preflight: verify Python + ffmpeg/ffprobe by resolvable path before running
#     (a PATH-dependent cron that couldn't find its tools was a real failure mode)
#   - a failure counter: after 3 consecutive failed runs, ONE alert goes out via
#     the notify adapter (and a "recovered" note when it comes back), so a broken
#     account nags you exactly once, not every hour
#
# Content acks are Instagram DM replies sent inside the pipeline. This script
# only sends SYSTEM alerts (when IG itself is broken, IG replies can't work).
#
# Env (all optional; defaults shown):
#   IG_INBOX_PYTHON  python to use            (default: python3, or ./.venv/bin/python if present)
#   IG_INBOX_HOME    base dir for data/state/logs (default: repo dir)
#   JITTER_MAX       max jitter seconds        (default: 120; 0 disables)
#
# Usage: run.sh [--now]     (--now skips jitter for manual runs; extra args pass through)

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IG_INBOX_HOME="${IG_INBOX_HOME:-$HERE}"

# Load .env if present so the pipeline sees LLM/Deepgram/etc keys.
if [ -f "$HERE/.env" ]; then set -a; . "$HERE/.env"; set +a; fi

# Pick a python: explicit override, else repo venv, else python3.
if [ -n "${IG_INBOX_PYTHON:-}" ]; then PY="$IG_INBOX_PYTHON"
elif [ -x "$HERE/.venv/bin/python" ]; then PY="$HERE/.venv/bin/python"
else PY="$(command -v python3)"; fi

STATE_DIR="${IG_INBOX_STATE_DIR:-$IG_INBOX_HOME/state}"
LOG_DIR="${IG_INBOX_LOG_DIR:-$IG_INBOX_HOME/logs}"
FAILURES="$STATE_DIR/failures.json"
RELAY="$STATE_DIR/alert-relayed.json"
LOCK="$STATE_DIR/run.lock"
JITTER_MAX="${JITTER_MAX:-120}"

mkdir -p "$STATE_DIR" "$LOG_DIR"
LOG="$LOG_DIR/$(date +%F).log"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

notify() {  # notify "<message>" — routes through the notify adapter
  "$PY" -c "from ig_inbox.adapters import notify; notify.notify('''$1''')" 2>>"$LOG" || true
}

# --- single instance ---------------------------------------------------------
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || { log "another run holds the lock; exiting"; exit 0; }
else
  # macOS has no flock(1); fall back to mkdir lock (stale >2h is cleared).
  LOCKDIR="$STATE_DIR/run.lock.d"
  if ! mkdir "$LOCKDIR" 2>/dev/null; then
    if [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
      rm -rf "$LOCKDIR"; mkdir "$LOCKDIR" 2>/dev/null || exit 0
    else
      exit 0
    fi
  fi
  trap 'rm -rf "$LOCKDIR"' EXIT
fi

# --- preflight ---------------------------------------------------------------
if ! "$PY" -c "import ig_inbox" 2>/dev/null; then
  log "FATAL: ig_inbox not importable by $PY (did you pip install -e .?)"
  exit 1
fi
for bin in ffmpeg ffprobe; do
  command -v "$bin" >/dev/null 2>&1 || log "WARN: $bin not on PATH — video reels won't be transcribed/OCR'd"
done

# --- jitter ------------------------------------------------------------------
if [ "${1:-}" != "--now" ] && [ "$JITTER_MAX" -gt 0 ]; then
  sleep $((RANDOM % JITTER_MAX))
fi
[ "${1:-}" = "--now" ] && shift

# --- run ---------------------------------------------------------------------
log "run start"
OUT="$("$PY" -m ig_inbox.run "$@" 2>>"$LOG")"
RC=$?
[ -n "$OUT" ] && log "pipeline: $OUT"
log "run end rc=$RC"

# --- failure accounting + single alert --------------------------------------
count=0
[ -f "$FAILURES" ] && count="$("$PY" -c "import json;print(json.load(open('$FAILURES')).get('count',0))" 2>/dev/null || echo 0)"
relayed=0
[ -f "$RELAY" ] && relayed="$("$PY" -c "import json;print(json.load(open('$RELAY')).get('relayed',0))" 2>/dev/null || echo 0)"

if [ "$RC" -eq 0 ]; then
  echo '{"count":0}' > "$FAILURES"
  if [ "$relayed" = "1" ]; then
    notify "instagram-inbox recovered — Instagram is back and the backlog is caught up."
    echo '{"relayed":0}' > "$RELAY"
  fi
  exit 0
fi

count=$((count + 1))
echo "{\"count\":$count}" > "$FAILURES"
# rc 3 = Instagram challenge that needs a human; rc>=... after 3 strikes, alert once.
if { [ "$RC" -eq 3 ] || [ "$count" -ge 3 ]; } && [ "$relayed" != "1" ]; then
  if [ "$RC" -eq 3 ]; then
    notify "Instagram flagged the bot account and wants a human to approve it in the Instagram app. Log in as the bot account on your phone, approve the prompt, then re-run: python -m ig_inbox.ig_login"
  else
    notify "instagram-inbox has failed $count runs in a row (exit $RC). Check the latest log in $LOG_DIR."
  fi
  echo '{"relayed":1}' > "$RELAY"
fi
exit "$RC"
