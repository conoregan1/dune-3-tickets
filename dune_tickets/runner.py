"""One check, start to finish: look, decide, notify, remember.

Kept apart from main.py and free of globals so the self-test can drive it
with a fake ODEON, a fake GitHub and a chosen clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import messages
from .config import Config
from .github import Notifier
from .odeon import OdeonError
from .state import State
from .watch import LONDON, ON_SALE, UNKNOWN, Snapshot, assess, fill_seats, take_snapshot


def run_once(
    cfg: Config,
    state: State,
    odeon,
    notifier: Notifier,
    now: datetime | None = None,
    *,
    status_now: bool = False,
    log=print,
) -> None:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(LONDON)
    today = local.date()

    if state.finished:
        log("Already finished: nothing left to watch for.")
        notifier.disable()
        return

    # Closing the on-sale alert is how you say "booked". Checked every run so
    # the watcher goes quiet as soon as you do.
    if state.alert_issue and not notifier.is_open(state.alert_issue):
        log(f"Alert #{state.alert_issue} has been closed, so you're sorted. Stopping.")
        _finish(state, notifier, "Tickets sorted. The watcher has switched itself off.")
        return

    if today > cfg.release_date:
        log("The release day has passed. Stopping.")
        _finish(state, notifier, "The release day has passed; the watcher has switched itself off.")
        return

    first = state.started is None
    if first:
        state.started = now.isoformat(timespec="seconds")

    snap: Snapshot | None = None
    alerted = False
    try:
        snap = take_snapshot(odeon, cfg, now)
    except OdeonError as exc:
        _failing(cfg, state, notifier, now, exc, log)
    else:
        _recovered(state, notifier, now, log)
        log(messages.console_summary(snap, cfg))
        found = assess(snap, set(state.seen), quiet_dates=first)
        if found.level:
            fill_seats(odeon, snap)
            title, body = messages.alert(found.level, snap, cfg, found.new_dates)
            number = notifier.raise_issue("tickets", title, body)
            alerted = True
            if found.level in (ON_SALE, UNKNOWN) and number:
                state.alert_issue = number
        elif found.keys:
            log(f"Noted quietly: {', '.join(sorted(found.keys))}")
        # Only after the issue went up: if GitHub refused, the next run tries again.
        state.remember(found.keys)

    queue_key = f"queue:{today.isoformat()}"
    if getattr(odeon, "queue_active", False) and queue_key not in state.seen:
        title, body = messages.queue(cfg, snap, now)
        notifier.raise_issue("tickets", title, body)
        state.remember({queue_key})
        alerted = True

    due = status_now or first or (
        local.hour >= cfg.status_hour and state.last_status_day != today.isoformat()
    )
    if due and alerted and not (status_now or first):
        # An alert has just buzzed; a "still watching" note straight after it
        # is noise. The alert is proof enough that it's running today.
        state.last_status_day = today.isoformat()
    elif due:
        if snap:
            fill_seats(odeon, snap)
        failing = datetime.fromisoformat(state.failing_since) if state.failing_since else None
        title, body = messages.status(
            cfg,
            snap,
            now,
            notifier.runs_since(now - timedelta(days=1)),
            first=first,
            failing_since=failing,
            error=state.last_error,
            alert_issue=state.alert_issue,
        )
        notifier.close_open("status", "Superseded by today's note.")
        notifier.raise_issue("status", title, body)
        state.last_status_day = today.isoformat()


def _failing(cfg: Config, state: State, notifier: Notifier, now: datetime, exc: Exception, log) -> None:
    log(f"Check failed: {exc}")
    if not state.failing_since:
        state.failing_since = now.isoformat(timespec="seconds")
    state.last_error = str(exc)

    since = datetime.fromisoformat(state.failing_since)
    if state.broken_issue is None and now - since >= timedelta(minutes=cfg.broken_after_minutes):
        title, body = messages.broken(cfg, since, str(exc))
        state.broken_issue = notifier.raise_issue("broken", title, body)


def _recovered(state: State, notifier: Notifier, now: datetime, log) -> None:
    if not state.failing_since:
        return
    log("ODEON is answering again.")
    notifier.close(state.broken_issue, messages.recovered(now))
    state.failing_since = state.last_error = state.broken_issue = None


def _finish(state: State, notifier: Notifier, note: str) -> None:
    state.finished = True
    notifier.close_open("status", note)
    notifier.disable()
