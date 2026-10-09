"""Notifications, as GitHub issues.

An issue assigned to you that @-mentions you is pushed to the GitHub app on
your phone. But GitHub never notifies you about your own actions, and the
watcher runs on your PC with your own login, so an issue it opened directly
would arrive in silence.

So the PC doesn't open alert issues itself. It asks the Notify workflow to,
and the workflow opens them as github-actions[bot], which does buzz. Quiet
housekeeping (closing old issues, updating the heartbeat) needs no
notification and is done directly.

Inside GitHub Actions (the watchdog) the token is the bot's own, so issues are
opened directly. With no token at all, ``Notifier(None, ...)`` prints what it
would have done: the local dry run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

NOTIFY_WORKFLOW = "notify.yml"
WATCHDOG_WORKFLOW = "watchdog.yml"
TASK_NAME = "Dune 3 Tickets"

LABELS = {
    "tickets": ("d93f0b", "Dune tickets: act on this"),
    "on-sale": ("b60205", "Release-day IMAX on sale. Close once booked."),
    "status": ("0e8a16", "Daily still-watching note"),
    "broken": ("b60205", "The watcher can't reach ODEON"),
    "pc-offline": ("fbca04", "Your PC has stopped checking"),
    "heartbeat": ("ededed", "Updated by your PC after every check"),
    "test": ("c5def5", "Test notification"),
}

# What the heartbeat issue body carries for the watchdog to read.
LAST_CHECK = re.compile(r"<!-- last-check: (\S+) -->")
FINISHED = "<!-- finished -->"

# pythonw has no console, and without this every gh/schtasks call would
# flash one up on screen.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitHubError(RuntimeError):
    pass


class GitHub:
    """The few REST calls this needs, on the standard library."""

    def __init__(self, token: str, repo: str) -> None:
        self.token = token
        self.repo = repo

    @classmethod
    def connect(cls, repo: str) -> "GitHub":
        """Actions provides a token; on your PC it comes from the gh CLI."""
        token = os.environ.get("GITHUB_TOKEN") or _gh_cli_token()
        if not token:
            raise GitHubError(
                "no GitHub token: not in Actions, and `gh auth token` gave nothing "
                "(run `gh auth login`)"
            )
        return cls(token, os.environ.get("GITHUB_REPOSITORY") or repo)

    def call(self, method: str, path: str, body: dict | None = None, **query: str):
        url = f"https://api.github.com/repos/{self.repo}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "dune-3-tickets",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GitHubError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise GitHubError(f"{method} {path} -> {getattr(exc, 'reason', exc)}") from exc
        return json.loads(data) if data else None

    def issues(self, label: str, state: str = "open") -> list[dict]:
        found = self.call("GET", "/issues", state=state, labels=label, per_page="50")
        # The issues endpoint includes pull requests; there are none here, but
        # it costs nothing to be sure.
        return [i for i in found if "pull_request" not in i]


def _gh_cli_token() -> str | None:
    gh = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    try:
        result = subprocess.run(
            [gh, "auth", "token"], capture_output=True, text=True, timeout=20,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


class Notifier:
    def __init__(self, gh: GitHub | None, user: str, *, via_workflow: bool = False, log=print) -> None:
        self.gh = gh
        self.user = user
        self.via_workflow = via_workflow
        self.log = log
        self._labels_ready: set[str] = set()
        # Everything raised, for the dry-run printout and the tests.
        self.raised: list[tuple[str, str, str]] = []

    # ------------------------------------------------------- notifications

    def raise_issue(self, labels: str, title: str, body: str, *, supersede: bool = False) -> None:
        """Open an issue that buzzes your phone.

        ``labels`` is comma-separated; the first is its kind. ``supersede``
        closes any open issue of the same kind first (yesterday's note).
        """
        self.raised.append((labels, title, body))
        kind = labels.split(",")[0]
        if not self.gh:
            self.log(f"\n[dry run] would raise a '{kind}' issue:\n  {title}\n")
            self.log("    " + body.replace("\n", "\n    ") + "\n")
            return
        if self.via_workflow:
            self.gh.call(
                "POST",
                f"/actions/workflows/{NOTIFY_WORKFLOW}/dispatches",
                {
                    "ref": "main",
                    "inputs": {
                        "labels": labels,
                        "title": title,
                        "body": body,
                        "supersede": "true" if supersede else "false",
                    },
                },
            )
            self.log(f"Asked GitHub to raise: {title}")
            return
        if supersede:
            self.close_open(kind, "Superseded by a newer one.")
        names = [n.strip() for n in labels.split(",") if n.strip()]
        for name in names:
            self._ensure_label(name)
        issue = self.gh.call(
            "POST", "/issues", {"title": title, "body": body, "labels": names, "assignees": [self.user]}
        )
        self.log(f"Raised issue #{issue['number']}: {title}")

    # ----------------------------------------------------- quiet housekeeping

    def close_open(self, label: str, comment: str) -> int:
        """Close every open issue with this label. Returns how many."""
        if not self.gh:
            return 0
        closed = 0
        for issue in self.gh.issues(label):
            number = issue["number"]
            self.gh.call("POST", f"/issues/{number}/comments", {"body": comment})
            self.gh.call("PATCH", f"/issues/{number}", {"state": "closed"})
            self.log(f"Closed issue #{number}.")
            closed += 1
        return closed

    def alert_closed(self) -> bool:
        """True once an on-sale alert exists and you've closed every one."""
        if not self.gh:
            return False
        alerts = self.gh.issues("on-sale", state="all")
        return bool(alerts) and all(i["state"] == "closed" for i in alerts)

    def heartbeat(self, number: int | None, body: str) -> int | None:
        """Rewrite the heartbeat issue, creating it if needed. Returns its number.

        Edited by you, so it never notifies: it's there for the watchdog.
        """
        if not self.gh:
            return number
        if number:
            try:
                self.gh.call("PATCH", f"/issues/{number}", {"body": body})
                return number
            except GitHubError as exc:
                if "HTTP 404" not in str(exc) and "HTTP 410" not in str(exc):
                    raise
        existing = self.gh.issues("heartbeat", state="all")
        if existing:
            number = existing[0]["number"]
            self.gh.call("PATCH", f"/issues/{number}", {"body": body, "state": "open"})
            return number
        self._ensure_label("heartbeat")
        issue = self.gh.call(
            "POST",
            "/issues",
            {"title": "🫀 Watcher heartbeat (updated by your PC, don't close)", "body": body, "labels": ["heartbeat"]},
        )
        return int(issue["number"])

    def disable(self) -> None:
        """Nothing left to watch: switch off the watchdog and the PC's task."""
        if not self.gh:
            self.log("[dry run] would switch the watchdog and the scheduled task off.")
            return
        try:
            self.gh.call("PUT", f"/actions/workflows/{WATCHDOG_WORKFLOW}/disable")
            self.log("Switched the GitHub watchdog off.")
        except GitHubError as exc:
            self.log(f"Couldn't switch the watchdog off: {exc}")
        if sys.platform == "win32" and not os.environ.get("GITHUB_ACTIONS"):
            subprocess.run(
                ["schtasks", "/Change", "/TN", TASK_NAME, "/DISABLE"],
                capture_output=True, creationflags=NO_WINDOW,
            )
            self.log(f"Switched off the '{TASK_NAME}' scheduled task.")

    def _ensure_label(self, label: str) -> None:
        if label in self._labels_ready or not self.gh:
            return
        color, description = LABELS.get(label, ("ededed", ""))
        try:
            self.gh.call("POST", "/labels", {"name": label, "color": color, "description": description})
        except GitHubError as exc:
            if "HTTP 422" not in str(exc):  # 422: it already exists, which is fine
                raise
        self._labels_ready.add(label)


def last_check_in(body: str) -> datetime | None:
    found = LAST_CHECK.search(body or "")
    if not found:
        return None
    try:
        return datetime.fromisoformat(found.group(1))
    except ValueError:
        return None
