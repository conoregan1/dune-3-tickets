"""What the watcher remembers between checks, kept in state.json.

It lives on the PC that runs the checks and is not committed: it changes every
five minutes, and nothing else needs it. Delete it to start over (you'll get a
fresh "watching" note, and anything still on sale is announced again).
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
    # When the previous check ran, whatever its result. A long gap means the
    # PC was asleep or off, which isn't ODEON's fault.
    last_check: str | None = None
    checks_since_note: int = 0
    # An on-sale alert has gone out. Closing it is how you say "booked".
    alert_sent: bool = False
    # Set when ODEON stops answering, cleared when it comes back.
    failing_since: str | None = None
    last_error: str | None = None
    broken_reported: bool = False
    # The issue the watchdog reads to know this PC is still checking.
    heartbeat_issue: int | None = None
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
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        # Atomic: a laptop going to sleep mid-write mustn't leave half a file.
        tmp.replace(path)

    def remember(self, keys: set[str]) -> None:
        self.seen = sorted(set(self.seen) | keys)
