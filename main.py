#!/usr/bin/env python3
"""Dune: Part Three IMAX tickets watcher for ODEON Luxe Bristol.

    python main.py                 # check now and print what it sees (changes nothing)
    python main.py --status-now    # ...and show the daily note it would send
    python main.py --notify        # a real check: notify through GitHub, update state.json
    python main.py --notify --test-alert   # send a test alert, to check your phone buzzes

Task Scheduler runs `pythonw main.py --notify --log` every 5 minutes (set up
by tools/schedule.ps1). --notify uses your `gh` login for GitHub.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from dune_tickets import messages
from dune_tickets.config import ROOT, load_config
from dune_tickets.github import GitHub, GitHubError, Notifier
from dune_tickets.odeon import Odeon
from dune_tickets.runner import run_once
from dune_tickets.state import State

CACHE = ROOT / ".cache"
# ODEON's API token lasts about 12 hours; keeping it means most checks never
# load the website, only the API.
TOKEN_CACHE = CACHE / "odeon-token.json"
LOG = CACHE / "watch.log"
LOG_LIMIT = 512 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dune-tickets",
        description="Watch ODEON Luxe Bristol for Dune: Part Three IMAX tickets.",
    )
    p.add_argument("--notify", action="store_true", help="Notify through GitHub and save state.json")
    p.add_argument("--status-now", action="store_true", help="Send the daily note now, whatever the time")
    p.add_argument("--test-alert", action="store_true", help="Send a test alert and do nothing else")
    p.add_argument("--log", action="store_true", help=f"Append output to {LOG.relative_to(ROOT)}")
    p.add_argument("-v", "--verbose", action="store_true", help="Show every request made")
    return p.parse_args(argv)


def use_log_file() -> None:
    """Send all output to the log. pythonw has no console, so without this
    a crash would vanish without a trace."""
    CACHE.mkdir(exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > LOG_LIMIT:
        # Keep the most recent half rather than growing forever.
        tail = LOG.read_bytes()[-LOG_LIMIT // 2 :]
        LOG.write_bytes(tail[tail.find(b"\n") + 1 :])
    stream = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    print(f"\n--- {datetime.now().astimezone():%Y-%m-%d %H:%M:%S}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.log:
        use_log_file()
    # Messages carry emoji and arrows. A Windows console piped to a file falls
    # back to a legacy code page and would crash on them mid-run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = load_config()
    gh = None
    if args.notify:
        try:
            gh = GitHub.connect(cfg.repo)
        except GitHubError as exc:
            print(f"Can't notify: {exc}")
            return 2
    # On your PC, alerts go through the Notify workflow so they come from
    # github-actions[bot]; GitHub won't notify you about your own issues.
    via_workflow = os.environ.get("GITHUB_ACTIONS") != "true"
    notifier = Notifier(gh, cfg.github_user, via_workflow=via_workflow)
    now = datetime.now(timezone.utc)

    if args.test_alert:
        title, body = messages.test_alert(cfg, now)
        try:
            notifier.raise_issue("test", title, body, supersede=True)
        except GitHubError as exc:
            print(f"GitHub refused the test alert: {exc}")
            return 1
        return 0

    state = State.load()
    odeon = Odeon(page=cfg.cinema_page, cache=TOKEN_CACHE, verbose=args.verbose)

    code = 0
    try:
        run_once(cfg, state, odeon, notifier, now, status_now=args.status_now)
    except GitHubError as exc:
        # The one failure that must be loud: it means alerts can't go out.
        print(f"GitHub refused a request, so a notification may not have gone out: {exc}")
        code = 1

    if args.notify:
        # Saved even after a GitHub error: state only records what actually
        # happened, so the next run picks up exactly where this one stopped.
        state.save()
    print(f"{odeon.requests} request(s) to ODEON.")
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Under pythonw an uncaught exception goes nowhere; put it in the log.
        import traceback

        traceback.print_exc()
        raise
