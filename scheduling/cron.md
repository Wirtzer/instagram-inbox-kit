# Portable scheduling (Linux / macOS / anywhere)

Pick ONE of these. All of them call `scripts/run.sh`, which handles locking,
jitter, and the failure alert on its own — you do not need to duplicate that.

Replace `/ABSOLUTE/PATH/TO/instagram-inbox-kit` with your real repo path.

---

## Option A — cron (hourly)

```cron
# m h dom mon dow  command
17 * * * * /ABSOLUTE/PATH/TO/instagram-inbox-kit/scripts/run.sh >> /ABSOLUTE/PATH/TO/instagram-inbox-kit/logs/cron.log 2>&1
```

Install with `crontab -e`. Minute `17` (not `0`) keeps you off the top of the
hour; `run.sh` adds its own 0–120 s jitter on top. Hourly is the recommended
cadence — frequent enough to feel live, gentle on a watched account.

cron runs with a minimal environment. `run.sh` loads the repo `.env` itself, so
your keys are picked up as long as `.env` exists in the repo root.

---

## Option B — systemd timer (Linux)

`~/.config/systemd/user/instagram-inbox.service`:

```ini
[Unit]
Description=instagram-inbox-kit poll

[Service]
Type=oneshot
WorkingDirectory=/ABSOLUTE/PATH/TO/instagram-inbox-kit
ExecStart=/ABSOLUTE/PATH/TO/instagram-inbox-kit/scripts/run.sh --now
```

`~/.config/systemd/user/instagram-inbox.timer`:

```ini
[Unit]
Description=Run instagram-inbox-kit hourly

[Timer]
OnCalendar=hourly
RandomizedDelaySec=120
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now instagram-inbox.timer
systemctl --user list-timers | grep instagram-inbox
```

(Use `--now` in the service so `run.sh` skips its own sleep; the timer's
`RandomizedDelaySec` provides the jitter instead.)

---

## Option C — plain foreground loop (no scheduler, e.g. inside a container)

```bash
cd /ABSOLUTE/PATH/TO/instagram-inbox-kit
while true; do
  ./scripts/run.sh --now
  sleep 3600
done
```

Good for a Docker container or a `tmux`/`screen` session. `run.sh` still takes
its single-instance lock, so overlapping runs are impossible.

---

## macOS

Prefer `scheduling/launchd.plist` over cron on macOS (cron is deprecated there).
See the comments in that file for install steps.
