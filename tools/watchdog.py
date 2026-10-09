#!/usr/bin/env python3
"""GitHub's half: is your PC still checking? Runs hourly from watchdog.yml.

ODEON turns away GitHub's servers, so the checks themselves run on your PC.
This is the safety net for that. It reads the heartbeat issue the PC rewrites
after every check, and if the last check is too old it raises an alert, which
buzzes your phone. The PC closes that alert itself the next time it checks.

It never contacts ODEON.

    python tools/watchdog.py     # needs GITHUB_TOKEN, i.e. inside Actions
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dune_tickets import messages  # noqa: E402
from dune_tickets.config import Config, load_config  # noqa: E402
from dune_tickets.github import FINISHED, GitHub, Notifier, last_check_in  # noqa: E402
from dune_tickets.watch import LONDON  # noqa: E402


def quiet(hour: int, cfg: Config) -> bool:
    if cfg.quiet_from > cfg.quiet_until:  # wraps midnight, e.g. 23 -> 8
        return hour >= cfg.quiet_from or hour < cfg.quiet_until
    return cfg.quiet_from <= hour < cfg.quiet_until


def watch(cfg: Config, gh, notifier: Notifier, now: datetime, log=print) -> str:
    """What the watchdog did, as one word, for the log and the tests."""
    local = now.astimezone(LONDON)
    if local.date() > cfg.release_date:
        log("The release day has passed; switching the watchdog off.")
        notifier.disable()
        return "past"

    beats = gh.issues("heartbeat", state="all")
    body = beats[0].get("body") or "" if beats else ""
    if FINISHED in body:
        log("The PC says it's finished; switching the watchdog off.")
        notifier.disable()
        return "finished"

    last = last_check_in(body)
    if last and now - last <= timedelta(hours=cfg.stale_after_hours):
        log(f"Fine: the PC last checked {messages.when(last)}.")
        return "fresh"
    log(f"The PC hasn't checked since {messages.when(last) if last else 'ever'}.")

    if quiet(local.hour, cfg):
        log("Quiet hours; saying nothing until morning.")
        return "quiet"
    if gh.issues("pc-offline"):
        log("Already reported; the alert is still open.")
        return "reported"

    title, body = messages.offline(cfg, last, now)
    notifier.raise_issue("pc-offline", title, body)
    return "alerted"


def main() -> int:
    cfg = load_config()
    gh = GitHub.connect(cfg.repo)
    watch(cfg, gh, Notifier(gh, cfg.github_user), datetime.now(timezone.utc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
