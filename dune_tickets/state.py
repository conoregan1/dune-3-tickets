"""What the watcher remembers between runs, kept in state.json.

The workflow commits this file whenever it changes. That happens rarely (an
alert, the daily note, ODEON going down or coming back), never on a quiet
check, so the history stays readable. The daily commit also counts as
repository activity, which stops GitHub switching the schedule off: it does
that to public repositories after 60 days without any.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import ROOT

STATE = ROOT / "state.json"


@dataclass
class State:
    # When the first check ran. None means this run is the first.
    started: str | None = None
    # Everything already announced: "on_sale:315-1234", "date:2026-12-15", ...
    seen: list[str] = field(default_factory=list)
    # London date of the last daily note, so it goes out once a day.
    last_status_day: str | None = None
    # The release-day "on sale" alert. Closing it is how you say "booked".
    alert_issue: int | None = None
    # Set when ODEON stops answering, cleared when it comes back.
    failing_since: str | None = None
    last_error: str | None = None
    broken_issue: int | None = None
    # Tickets found and booked, or the day has passed: nothing left to do.
    finished: bool = False

    @classmethod
    def load(cls, path: Path = STATE) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path = STATE) -> None:
        data = asdict(self)
        data["seen"] = sorted(set(self.seen))
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def remember(self, keys: set[str]) -> None:
        self.seen = sorted(set(self.seen) | keys)
