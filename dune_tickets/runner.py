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

# A longer gap than this since the previous check means the PC was asleep or
# off. Failures from before it don't belong to the same outage: a laptop
# waking up often fails its first check before the Wi-Fi reconnects.
SLEEP_GAP = timedelta(minutes=30)


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
    if state.alert_sent and notifier.alert_closed():
        log("The on-sale alert has been closed, so you're sorted. Stopping.")
        _finish(cfg, state, notifier, now, "Tickets sorted. The watcher has switched itself off.")
        return

    if today > cfg.release_date:
        log("The release day has passed. Stopping.")
        _finish(cfg, state, notifier, now, "The release day has passed; the watcher has switched itself off.")
        return

    first = state.started is None
    if first:
        state.started = now.isoformat(timespec="seconds")
    gap = now - datetime.fromisoformat(state.last_check) if state.last_check else None
    state.checks_since_note += 1

    snap: Snapshot | None = None
    alerted = False
    try:
        snap = take_snapshot(odeon, cfg, now)
    except OdeonError as exc:
        _failing(cfg, state, notifier, now, exc, gap, log)
    else:
        _recovered(state, notifier, now, log)
        log(messages.console_summary(snap, cfg))
        found = assess(snap, set(state.seen), quiet_dates=first)
        if found.level:
            fill_seats(odeon, snap)
            title, body = messages.alert(found.level, snap, cfg, found.new_dates)
            on_sale = found.level in (ON_SALE, UNKNOWN)
            notifier.raise_issue("tickets,on-sale" if on_sale else "tickets", title, body)
            alerted = True
            state.alert_sent = state.alert_sent or on_sale
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
        state.checks_since_note = 0
    elif due:
        if snap:
            fill_seats(odeon, snap)
        failing = datetime.fromisoformat(state.failing_since) if state.failing_since else None
        title, body = messages.status(
            cfg,
            snap,
            now,
            None if first else state.checks_since_note,
            first=first,
            failing_since=failing,
            error=state.last_error,
        )
        notifier.raise_issue("status", title, body, supersede=True)
        state.last_status_day = today.isoformat()
        state.checks_since_note = 0

    # Back after a while away: if the watchdog said this PC had gone quiet,
    # say it's back. Closed by you, so it doesn't buzz.
    if first or (gap is not None and gap > timedelta(hours=1)):
        notifier.close_open("pc-offline", messages.back_online(now))

    state.heartbeat_issue = notifier.heartbeat(
        state.heartbeat_issue, messages.heartbeat(cfg, now, snap, state.last_error)
    )
    state.last_check = now.isoformat(timespec="seconds")


def _failing(
    cfg: Config, state: State, notifier: Notifier, now: datetime, exc: Exception, gap, log
) -> None:
    log(f"Check failed: {exc}")
    if not state.failing_since or (gap is not None and gap > SLEEP_GAP):
        state.failing_since = now.isoformat(timespec="seconds")
    state.last_error = str(exc)

    since = datetime.fromisoformat(state.failing_since)
    if not state.broken_reported and now - since >= timedelta(minutes=cfg.broken_after_minutes):
        title, body = messages.broken(cfg, since, str(exc))
        notifier.raise_issue("broken", title, body)
        state.broken_reported = True


def _recovered(state: State, notifier: Notifier, now: datetime, log) -> None:
    if not state.failing_since:
        return
    log("ODEON is answering again.")
    if state.broken_reported:
        notifier.close_open("broken", messages.recovered(now))
    state.failing_since = state.last_error = None
    state.broken_reported = False


def _finish(cfg: Config, state: State, notifier: Notifier, now: datetime, note: str) -> None:
    state.finished = True
    notifier.close_open("status", note)
    notifier.close_open("pc-offline", note)
    # Tells the watchdog to stand down rather than report a silent PC.
    state.heartbeat_issue = notifier.heartbeat(
        state.heartbeat_issue, messages.heartbeat(cfg, now, None, None, finished=True)
    )
    notifier.disable()
