"""Interactive Instagram login / session bootstrap. NEVER run from the poll loop.

Order of attempts:
  1. Existing session file → validate, keep if alive
  2. --sessionid <value>  → login_by_sessionid (grab it from a logged-in browser:
     DevTools → Application → Cookies → instagram.com → sessionid)
  3. Credential file (JSON {"username","password"}) → password login
  4. Interactive prompt for the password (also writes the credential file)

On success: dumps instagrapi settings (session + FIXED device UUIDs) to the
session file (chmod 600) and resolves the allowed senders' numeric PKs into
config.json. Reusing the stored device UUIDs on every relogin — rather than
minting a fresh "device" — is what keeps a watched account from tripping flags.

Run:  python -m ig_inbox.ig_login [--sessionid VALUE] [--force]
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from instagrapi import Client
from instagrapi.exceptions import BadPassword, ChallengeRequired, LoginRequired

from . import challenge, config


def write_private(path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        mode = path.stat().st_mode & 0o077
        if mode:
            print(f"WARN: could not chmod {path} and it is group/other-accessible "
                  f"(mode {oct(path.stat().st_mode & 0o777)}) — tighten manually",
                  file=sys.stderr)


def alive(cl: Client) -> bool:
    try:
        cl.get_timeline_feed()
        return True
    except Exception:
        return False


def resolve_pks(cl: Client, cfg: dict) -> None:
    """Resolve allowed sender usernames → numeric PKs into config.json."""
    pks: list[int] = []
    for username in cfg.get("allowed_sender_usernames", []):
        try:
            pk = int(cl.user_id_from_username(username))
            pks.append(pk)
            print(f"Resolved @{username} → PK {pk}")
        except Exception as exc:
            print(f"WARN: could not resolve @{username}: {exc}", file=sys.stderr)
    if pks:
        cfg["allowed_sender_pks"] = pks
        config.CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"{config.CONFIG_FILE} updated with allowed_sender_pks.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessionid", help="sessionid cookie from a logged-in browser "
                                        "(or set IG_SESSIONID env to keep it out of argv)")
    ap.add_argument("--force", action="store_true", help="ignore existing session file")
    args = ap.parse_args()
    if not args.sessionid and os.environ.get("IG_SESSIONID"):
        args.sessionid = os.environ["IG_SESSIONID"]

    cfg = config.load_config()
    cl = Client()
    cl.delay_range = [1, 3]
    # Auto-resolve the standard emailed-code challenge. Snapshot the baseline
    # BEFORE any login attempt so only a freshly-arrived code is ever used.
    cl.challenge_code_handler = challenge.make_handler(challenge.baseline_ids())

    session_file = config.SESSION_FILE
    cred_file = config.CRED_FILE

    # 1. Existing session
    if session_file.exists() and not args.force and not args.sessionid:
        cl.load_settings(str(session_file))
        if alive(cl):
            print("Existing session is alive — nothing to do.")
            resolve_pks(cl, cfg)
            return 0
        print("Existing session dead — re-authenticating…")

    # Device continuity: even on --force / relogin, reuse the stored device UUIDs.
    if session_file.exists():
        cl = Client()
        cl.load_settings(str(session_file))
        cl.set_settings({**cl.get_settings(), "authorization_data": {}, "cookies": {}})
        cl.challenge_code_handler = challenge.make_handler(challenge.baseline_ids())

    try:
        # 2. sessionid from browser
        if args.sessionid:
            cl.login_by_sessionid(args.sessionid)
        else:
            # 3./4. password login
            if cred_file.exists():
                cred = json.loads(cred_file.read_text())
                username, password = cred["username"], cred["password"]
            else:
                username = cfg.get("ig_username") or input("IG username: ").strip()
                password = getpass.getpass(f"IG password for {username}: ")
                write_private(cred_file, json.dumps({"username": username,
                                                     "password": password}))
                print(f"Credential file written: {cred_file} (600)")
            cl.login(username, password)
    except ChallengeRequired:
        print("\nInstagram raised a CHALLENGE it won't let automation clear "
              "(likely an app-only device approval).\n"
              "  1. Open the Instagram app, log in as the bot account, approve.\n"
              "  2. Or grab a fresh sessionid from a logged-in browser and re-run:\n"
              "     python -m ig_inbox.ig_login --sessionid <value>\n", file=sys.stderr)
        return 3
    except BadPassword as exc:
        print(f"\nInstagram rejected the password login: {exc}\n"
              "NOTE: Instagram often returns this for IP/device flags, not an\n"
              "actually-wrong password. Preferred fix: log into instagram.com in a\n"
              "real browser, then re-run with the browser session:\n"
              "  python -m ig_inbox.ig_login --sessionid <sessionid cookie>\n",
              file=sys.stderr)
        return 2
    except LoginRequired as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 2

    if not alive(cl):
        print("Login appeared to succeed but the session doesn't work.", file=sys.stderr)
        return 1

    write_private(session_file, json.dumps(cl.get_settings(), default=str))
    print(f"Session saved: {session_file} (600)")
    # Successful login clears the challenge hold (pipeline auto-relogin resumes).
    hold = config.STATE_DIR / "challenge-hold.json"
    if hold.exists():
        hold.unlink()
        print("challenge hold cleared — auto-relogin re-enabled")
    resolve_pks(cl, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
