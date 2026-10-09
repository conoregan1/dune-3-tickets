"""Notifications, as GitHub issues.

An issue assigned to you that @-mentions you is pushed to the GitHub app on
your phone (and emailed, if you leave that on). It needs no credentials beyond
the GITHUB_TOKEN each Actions run is given, so there is nothing to set up or
leak.

Outside Actions there is no token, and ``Notifier(None, ...)`` prints what it
would have raised instead. That is the local dry run.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

WORKFLOW = "check.yml"

LABELS = {
    "tickets": ("d93f0b", "Dune tickets: act on this"),
    "status": ("0e8a16", "Daily still-watching note"),
    "broken": ("b60205", "The watcher can't reach ODEON"),
    "test": ("c5def5", "Test notification"),
}


class GitHubError(RuntimeError):
    pass


class GitHub:
    """The few REST calls this needs, on the standard library."""

    def __init__(self, token: str, repo: str) -> None:
        self.token = token
        self.repo = repo

    @classmethod
    def from_env(cls) -> "GitHub | None":
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        return cls(token, repo) if token and repo else None

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
        except urllib.error.URLError as exc:
            raise GitHubError(f"{method} {path} -> {exc.reason}") from exc
        return json.loads(data) if data else None


class Notifier:
    def __init__(self, gh: GitHub | None, user: str, log=print) -> None:
        self.gh = gh
        self.user = user
        self.log = log
        self._labels_ready: set[str] = set()
        # What a dry run would have raised, for printing and for the tests.
        self.raised: list[tuple[str, str, str]] = []

    @property
    def live(self) -> bool:
        return self.gh is not None

    def raise_issue(self, label: str, title: str, body: str) -> int | None:
        self.raised.append((label, title, body))
        if not self.gh:
            self.log(f"\n[dry run] would raise a '{label}' issue:\n  {title}\n")
            self.log("    " + body.replace("\n", "\n    ") + "\n")
            return None
        self._ensure_label(label)
        issue = self.gh.call(
            "POST",
            "/issues",
            {"title": title, "body": body, "labels": [label], "assignees": [self.user]},
        )
        self.log(f"Raised issue #{issue['number']}: {title}")
        return int(issue["number"])

    def close_open(self, label: str, comment: str) -> None:
        """Close every open issue with this label, e.g. yesterday's note."""
        if not self.gh:
            return
        for issue in self.gh.call("GET", "/issues", state="open", labels=label, per_page="50"):
            self.close(int(issue["number"]), comment)

    def close(self, number: int | None, comment: str) -> None:
        if not self.gh or number is None:
            return
        self.gh.call("POST", f"/issues/{number}/comments", {"body": comment})
        self.gh.call("PATCH", f"/issues/{number}", {"state": "closed"})
        self.log(f"Closed issue #{number}.")

    def is_open(self, number: int | None) -> bool:
        if not self.gh or number is None:
            return True
        return self.gh.call("GET", f"/issues/{number}")["state"] == "open"

    def runs_since(self, since: datetime) -> int | None:
        """How many times the check has run since then: proof it's running."""
        if not self.gh:
            return None
        stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            data = self.gh.call(
                "GET", f"/actions/workflows/{WORKFLOW}/runs", created=f">={stamp}", per_page="1"
            )
        except GitHubError as exc:
            self.log(f"Couldn't count recent runs: {exc}")
            return None
        return int(data.get("total_count", 0))

    def disable(self) -> None:
        """Switch the schedule off for good. Re-enable from the Actions tab."""
        if not self.gh:
            self.log("[dry run] would switch the scheduled check off.")
            return
        try:
            self.gh.call("PUT", f"/actions/workflows/{WORKFLOW}/disable")
            self.log("Switched the scheduled check off.")
        except GitHubError as exc:
            self.log(f"Couldn't switch the schedule off: {exc}")

    def _ensure_label(self, label: str) -> None:
        if label in self._labels_ready or not self.gh:
            return
        color, description = LABELS.get(label, ("ededed", ""))
        try:
            self.gh.call(
                "POST", "/labels", {"name": label, "color": color, "description": description}
            )
        except GitHubError as exc:
            if "HTTP 422" not in str(exc):  # 422: it already exists, which is fine
                raise
        self._labels_ready.add(label)
