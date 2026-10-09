#!/usr/bin/env python3
"""Dune: Part Three IMAX tickets watcher for ODEON Luxe Bristol.

    python main.py                 # check now and print what it sees (changes nothing)
    python main.py --status-now    # ...and show the daily note it would send
    python main.py --notify        # the scheduled run: raise GitHub issues, update state.json
    python main.py --notify --test-alert   # send a test alert, to check your phone buzzes

--notify only works inside GitHub Actions, which supplies GITHUB_TOKEN and
GITHUB_REPOSITORY. Without it, nothing is sent and state.json is left alone.
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

# Where the ODEON API token is kept between runs. The workflow carries this
# folder from one run to the next with actions/cache; it is never committed.
TOKEN_CACHE = ROOT / ".cache" / "odeon-token.json"

# Messages carry emoji and arrows. A Windows console piped to a file falls back
# to a legacy code page and would crash on them mid-run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dune-tickets",
        description="Watch ODEON Luxe Bristol for Dune: Part Three IMAX tickets.",
    )
    p.add_argument("--notify", action="store_true", help="Raise GitHub issues and save state (Actions only)")
    p.add_argument("--status-now", action="store_true", help="Send the daily note now, whatever the time")
    p.add_argument("--test-alert", action="store_true", help="Send a test alert and do nothing else")
    p.add_argument("-v", "--verbose", action="store_true", help="Show every request made")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config()

    gh = GitHub.from_env() if args.notify else None
    if args.notify and gh is None:
        print(
            "--notify needs GITHUB_TOKEN and GITHUB_REPOSITORY, which GitHub Actions "
            "provides. Run without it for a dry run."
        )
        return 2

    log_lines: list[str] = []

    def log(message: str) -> None:
        print(message)
        log_lines.append(message)

    notifier = Notifier(gh, cfg.github_user, log=log)
    now = datetime.now(timezone.utc)

    if args.test_alert:
        title, body = messages.test_alert(cfg, now)
        try:
            notifier.close_open("test", "Superseded by a newer test.")
            notifier.raise_issue("test", title, body)
        except GitHubError as exc:
            print(f"GitHub refused the test alert: {exc}")
            return 1
        return 0

    state = State.load()
    odeon = Odeon(page=cfg.cinema_page, cache=TOKEN_CACHE, verbose=args.verbose)

    code = 0
    try:
        run_once(cfg, state, odeon, notifier, now, status_now=args.status_now, log=log)
    except GitHubError as exc:
        # The one failure that must be loud: it means alerts can't go out.
        # Failing the run makes GitHub tell you by its own route.
        log(f"GitHub refused a request, so a notification may not have gone out: {exc}")
        code = 1

    if args.notify:
        # Saved even after a GitHub error: state only records what actually
        # happened, so the next run picks up exactly where this one stopped.
        state.save()
    log(f"{odeon.requests} request(s) to ODEON.")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("```\n" + "\n".join(log_lines) + "\n```\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
