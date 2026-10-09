"""Every message the watcher sends, as (title, body) Markdown for an issue.

Written to be read on a phone in the two seconds after it buzzes: what
happened in the title, the booking links first in the body, everything else
after.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime

from .config import Config
from .github import FINISHED as FINISHED_MARK
from .watch import (
    LONDON,
    MEMBERS,
    NEW_DATES,
    NOT_YET,
    ON_SALE,
    OTHER_FORMATS,
    UNKNOWN,
    Showing,
    Snapshot,
)

FILM = "Dune: Part Three"


# ------------------------------------------------------------ formatting


def day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"  # Fri 18 Dec


def long_day(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B}"  # Friday 18 December


def clock(dt: datetime) -> str:
    return dt.astimezone(LONDON).strftime("%H:%M")


def when(dt: datetime | None) -> str:
    if dt is None:
        return "soon"
    return f"{day(dt.astimezone(LONDON).date())} at {clock(dt)}"


def seats(s: Showing) -> str:
    if s.sold_out:
        return "sold out"
    if s.sale == NOT_YET:
        return f"on sale {when(s.opens_at)}"
    if s.seats_left is None:
        return "?"
    if s.seats_left == 0:
        return "sold out" + (", wheelchair spaces only" if s.accessible_left else "")
    return f"{s.seats_left} left"


# On nearly every showing, so they say nothing about the format.
ACCESS_TAGS = {"Audio Described", "Wheelchair Accessible", "Flash Warning"}


def format_tags(s: Showing) -> str:
    return ", ".join(a for a in s.attributes if a not in ACCESS_TAGS) or "standard"


def booking_table(showings: list[Showing]) -> str:
    rows = ["| Starts | Screen | Seats | |", "|---|---|---|---|"]
    for s in showings:
        link = "" if s.sold_out or s.sale == NOT_YET else f"[**Choose seats →**]({s.booking_url})"
        rows.append(f"| **{clock(s.starts)}** | {s.screen} | {seats(s)} | {link} |")
    return "\n".join(rows)


def links(cfg: Config) -> str:
    return f"[Dune on ODEON]({cfg.film_page}) · [ODEON Bristol]({cfg.cinema_page})"


def dune_overview(snap: Snapshot, limit: int = 8) -> str:
    """One line per Dune date at the cinema, with times where they're known."""
    dates = sorted(set(snap.dune_dates) | {snap.release_date})
    lines = []
    for d in dates[:limit]:
        tags = []
        if d < snap.release_date:
            tags.append("preview")
        elif d == snap.release_date:
            tags.append("release day")
        if snap.dune_dates.get(d):
            tags.append("IMAX")
        label = f"**{day(d)}**" + (f" · {' · '.join(tags)}" if tags else "")

        showings = snap.dune_on(d)
        # Only worth marking each time when the day mixes IMAX with other screens.
        mixed = len({s.imax for s in showings}) > 1
        if d in snap.days and not showings:
            lines.append(f"- {label}: not listed yet")
        elif showings:
            times = " · ".join(
                f"[{clock(s.starts)}]({s.booking_url}){' IMAX' if mixed and s.imax else ''} "
                f"({seats(s)})"
                for s in showings
            )
            lines.append(f"- {label}: {times}")
        else:
            lines.append(f"- {label}")
    if len(dates) > limit:
        lines.append(f"- …and {len(dates) - limit} more days")
    return "\n".join(lines)


def release_day_context(snap: Snapshot) -> str:
    """What else ODEON has put on that day, and whether the IMAX screen is taken."""
    rel = snap.release_date
    others = snap.others_on(rel)
    if not others:
        return f"**{day(rel)} at this cinema so far:** nothing on sale for any film yet."
    counts = Counter(s.title for s in others)
    films = ", ".join(f"{title} ×{n}" for title, n in counts.most_common(4))
    imax = [s for s in others if s.imax]
    if imax:
        taken = ", ".join(sorted({s.title for s in imax}))
        screen = f"The IMAX screen has {len(imax)} showing(s) of {taken} that day."
    else:
        screen = "Nothing is booked into the IMAX screen yet, which looks like it's being kept for Dune."
    return f"**{day(rel)} at this cinema so far:** {films}. {screen}"


def footer(now: datetime, extra: str = "") -> str:
    text = f"Spotted {when(now)}."
    return f"<sub>{text}{' ' + extra if extra else ''}</sub>"


# --------------------------------------------------------------- alerts


def alert(level: str, snap: Snapshot, cfg: Config, new_dates: list[date]) -> tuple[str, str]:
    rel = snap.release_date
    who = f"@{cfg.github_user}"
    imax = snap.release_imax
    heading = f"**{cfg.cinema_name} · {long_day(rel)} · IMAX**"

    if level in (ON_SALE, UNKNOWN):
        bookable = [s for s in imax if s.sale in (ON_SALE, UNKNOWN)]
        title = f"🎟️ Dune IMAX tickets ON SALE at ODEON Bristol: {day(rel)}"
        lines = [
            f"{who} **Release-day IMAX tickets for {FILM} are on sale now.** They go fast.",
            "",
            heading,
            "",
            booking_table(bookable),
        ]
        if level == UNKNOWN or any(s.sale == UNKNOWN for s in bookable):
            notes = sorted({s.note for s in bookable if s.note})
            lines += [
                "",
                f"ODEON lists some of these with a restriction this doesn't recognise "
                f"({'; '.join(notes)}). They're probably bookable, so open the link and see.",
            ]
        later = [s for s in imax if s.sale in (MEMBERS, NOT_YET)]
        if later:
            lines += ["", "Not open yet:", "", booking_table(later)]
        close = "Close this issue once you've booked: that's what tells the watcher to stop."
    elif level == MEMBERS:
        open_ = [s for s in imax if s.sale == MEMBERS]
        opens = min((s.opens_at for s in open_ if s.opens_at), default=None)
        title = f"🔑 Dune IMAX pre-sale open at ODEON Bristol (members only): {day(rel)}"
        lines = [
            f"{who} **A members-only pre-sale for release-day IMAX has started.** "
            "Usually that's ODEON Limitless or myODEON. If you're signed up, book now.",
            f"General sale opens **{when(opens)}**. You'll get another alert when it does.",
            "",
            heading,
            "",
            booking_table(open_),
        ]
        close = "You'll get another alert when anyone can book."
    elif level == NOT_YET:
        listed = [s for s in imax if s.sale == NOT_YET]
        opens = min((s.opens_at for s in listed if s.opens_at), default=None)
        title = f"⏰ Dune IMAX for {day(rel)} is listed at ODEON Bristol: on sale {when(opens)}"
        lines = [
            f"{who} **The release-day IMAX showings are loaded but not on sale yet.** "
            f"Tickets open **{when(opens)}**, so set an alarm.",
            "",
            heading,
            "",
            booking_table(listed),
        ]
        close = "You'll get another alert the moment they open."
    elif level == OTHER_FORMATS:
        listed = snap.dune_on(rel)
        title = f"🎬 Dune is on sale at ODEON Bristol for {day(rel)}, but not in IMAX yet"
        lines = [
            f"{who} **Release-day tickets for {FILM} are on sale at ODEON Bristol, "
            "but none of the showings are IMAX.** IMAX may follow separately; you'll get "
            "another alert if it does. Book one of these if a non-IMAX seat will do.",
            "",
            f"**{cfg.cinema_name} · {long_day(rel)}**",
            "",
            "| Starts | Format | Seats | |",
            "|---|---|---|---|",
            *(
                f"| **{clock(s.starts)}** | {s.screen}: {format_tags(s)} "
                f"| {seats(s)} | [Choose seats →]({s.booking_url}) |"
                for s in listed
            ),
        ]
        close = "Still watching for IMAX."
    elif level == NEW_DATES:
        names = ", ".join(
            f"{day(d)}{' (IMAX)' if snap.dune_dates.get(d) else ''}" for d in new_dates[:6]
        )
        title = f"📅 ODEON Bristol has added Dune showings: {names}"
        lines = [
            f"{who} **ODEON Bristol just added {FILM} on "
            f"{'a new date' if len(new_dates) == 1 else 'new dates'}.** "
            f"The release day ({day(rel)}) isn't on sale in IMAX yet, but this means the "
            "schedule is moving. Previews like these sell out quickly.",
            "",
            dune_overview(snap),
        ]
        close = "Still watching the release day."
    else:
        raise ValueError(f"no message for alert level {level!r}")

    if new_dates and level != NEW_DATES:
        lines += ["", "Also just added: " + ", ".join(day(d) for d in new_dates[:10])]
    lines += ["", links(cfg), "", footer(snap.taken, close)]
    return title, "\n".join(lines)


def queue(cfg: Config, snap: Snapshot | None, now: datetime) -> tuple[str, str]:
    title = "🚦 ODEON's website queue is on: check Dune tickets now"
    if snap and any(s.sale in (ON_SALE, UNKNOWN) for s in snap.release_imax):
        state = "Release-day IMAX showings **are** on sale (see the other alert)."
    elif snap:
        state = "Release-day IMAX showings weren't on sale yet when this checked."
    else:
        state = "The watcher couldn't see past the queue to check the showings itself."
    body = "\n".join(
        [
            f"@{cfg.github_user} **ODEON has switched its Queue-it waiting room on.** "
            "It only does that for big on-sales. It might be Dune, or something else.",
            "",
            state,
            "",
            links(cfg),
            "",
            footer(now),
        ]
    )
    return title, body


# ------------------------------------------------------------ status note


def status(
    cfg: Config,
    snap: Snapshot | None,
    now: datetime,
    checks: int | None,
    *,
    first: bool,
    failing_since: datetime | None,
    error: str | None,
) -> tuple[str, str]:
    today = now.astimezone(LONDON).date()
    rel = cfg.release_date
    who = f"@{cfg.github_user}"
    count = f" ({checks} checks since the last note)" if checks is not None else ""
    tail = (
        f"<sub>Your PC checks every 5 minutes while it's awake{count}, and GitHub tells "
        "you if it stops. A separate alert goes out the moment release-day IMAX "
        "showings appear. This note comes once a day so that silence never means "
        "it's broken.</sub>"
    )

    if snap is None:
        title = f"⚠️ {day(today)}: watcher running, but ODEON isn't answering"
        body = [
            f"{who} **Checks have been failing since {when(failing_since)}.** "
            f"Until this clears you won't get a ticket alert, so keep an eye on "
            f"[ODEON]({cfg.film_page}) yourself.",
            "",
            f"Last error: `{error}`",
            "",
            tail,
        ]
        return title, "\n".join(body)

    imax = snap.release_imax
    on_sale = [s for s in imax if s.sale in (ON_SALE, UNKNOWN)]
    listed = [s for s in imax if s.sale in (NOT_YET, MEMBERS)]

    if on_sale:
        title = f"🎟️ Reminder: Dune IMAX for {day(rel)} is on sale at ODEON Bristol"
        lead = (
            f"{who} **Release-day IMAX tickets are on sale.** If you've booked, close the "
            "🎟️ alert issue and the watcher will stop."
        )
        middle = [f"**{cfg.cinema_name} · {long_day(rel)} · IMAX**", "", booking_table(imax)]
    elif listed:
        opens = min((s.opens_at for s in listed if s.opens_at), default=None)
        title = f"⏰ {day(today)}: Dune IMAX for {day(rel)} goes on sale {when(opens)}"
        lead = (
            f"{who} The release-day IMAX showings are listed and go on sale "
            f"**{when(opens)}**. You'll get an alert the moment they open."
        )
        middle = ["**Dune at ODEON Bristol right now**", dune_overview(snap)]
    else:
        if first:
            title = "👋 Watching ODEON Bristol for Dune IMAX tickets"
            lead = (
                f"{who} **Set up and watching.** The moment release-day IMAX tickets for "
                f"{FILM} appear at {cfg.cinema_name} you'll get an alert with booking "
                f"links. Until then, one of these each morning around "
                f"{cfg.status_hour:02d}:00 to show it's still running."
            )
        else:
            title = f"✅ {day(today)}: still watching, no Dune IMAX tickets for {day(rel)} yet"
            lead = (
                f"{who} Checked at {clock(now)}. Release-day IMAX tickets for {FILM} "
                "aren't on sale yet."
            )
        middle = [
            "**Dune at ODEON Bristol right now**",
            dune_overview(snap),
            "",
            release_day_context(snap),
        ]

    body = [lead, "", *middle, "", links(cfg), "", tail]
    if failing_since:
        body[1:1] = ["", f"⚠️ Some checks failed recently (`{error}`), but this one worked."]
    return title, "\n".join(body)


def broken(cfg: Config, since: datetime, error: str) -> tuple[str, str]:
    title = "⚠️ The Dune ticket watcher can't reach ODEON"
    body = "\n".join(
        [
            f"@{cfg.github_user} **Every check since {when(since)} has failed**, so if "
            "tickets went on sale now you wouldn't hear about it. Keep an eye on "
            f"[ODEON]({cfg.film_page}) yourself until this closes.",
            "",
            f"Error: `{error}`",
            "",
            "It closes itself as soon as ODEON answers again. If your internet is "
            "fine and it's still open in a day, ODEON has probably changed its "
            "website; `.cache/watch.log` on your PC has the details.",
        ]
    )
    return title, body


# ------------------------------------------------- heartbeat and watchdog


def heartbeat(
    cfg: Config, now: datetime, snap: Snapshot | None, error: str | None, *, finished: bool = False
) -> str:
    """The body of the heartbeat issue, rewritten after every check."""
    if finished:
        line = "Finished: nothing left to watch for. The watchdog stands down."
    elif snap is None:
        line = f"ODEON didn't answer (`{error}`)."
    elif any(s.sale in (ON_SALE, UNKNOWN) for s in snap.release_imax):
        line = f"release-day IMAX is **on sale** ({len(snap.release_imax)} showings)."
    elif snap.release_imax:
        line = f"release-day IMAX is listed ({len(snap.release_imax)} showings), not on sale yet."
    else:
        line = f"no Dune IMAX showings for {day(cfg.release_date)} yet."
    return "\n".join(
        [
            f"<!-- last-check: {now.isoformat(timespec='seconds')} -->",
            FINISHED_MARK if finished else "",
            f"Last check from your PC: **{when(now)}**: {line}",
            "",
            "The watcher on your PC rewrites this after every check, and GitHub's "
            f"hourly watchdog reads it. If it goes {cfg.stale_after_hours:g} hours without "
            "an update you'll get an alert. Editing it never notifies you. Leave it open.",
        ]
    )


def offline(cfg: Config, last: datetime | None, now: datetime) -> tuple[str, str]:
    since = f"since {when(last)}" if last else "at all yet"
    title = "💤 Your PC has stopped checking for Dune tickets"
    body = "\n".join(
        [
            f"@{cfg.github_user} **Your PC hasn't checked ODEON {since}.** ODEON blocks "
            "GitHub's servers, so your PC is the only thing watching, and while it's "
            "asleep, off or offline, nothing is.",
            "",
            "Wake it, plug it in, or open the lid. This closes itself on the next check.",
            "",
            "If the PC is on and this stays open, the scheduled task may have stopped. "
            "In PowerShell, in the project folder: "
            r"`powershell -ExecutionPolicy Bypass -File tools\schedule.ps1`",
            "",
            f"[Dune on ODEON]({cfg.film_page})",
            "",
            f"<sub>GitHub watchdog, {when(now)}.</sub>",
        ]
    )
    return title, body


def back_online(now: datetime) -> str:
    return f"Back: your PC checked again {when(now)}."


def recovered(now: datetime) -> str:
    return f"Back to normal: ODEON answered again {when(now)}."


def test_alert(cfg: Config, now: datetime) -> tuple[str, str]:
    title = "🧪 Test alert: if your phone buzzed, notifications work"
    body = "\n".join(
        [
            f"@{cfg.github_user} This is a test from the Dune ticket watcher. A real alert "
            "looks like this, with working booking links:",
            "",
            f"> 🎟️ **Dune IMAX tickets ON SALE at ODEON Bristol: {day(cfg.release_date)}**",
            ">",
            "> | Starts | Screen | Seats | |",
            "> |---|---|---|---|",
            "> | **09:30** | Screen 6 | 144 left | **Choose seats →** |",
            "> | **13:15** | Screen 6 | 144 left | **Choose seats →** |",
            "",
            "Close this whenever you like.",
            "",
            f"<sub>Sent {when(now)}.</sub>",
        ]
    )
    return title, body


# ----------------------------------------------------------- run log text


def console_summary(snap: Snapshot, cfg: Config) -> str:
    dates = ", ".join(
        f"{day(d)}{' (IMAX)' if imax else ''}" for d, imax in sorted(snap.dune_dates.items())
    )
    lines = [
        f"{cfg.cinema_name}: '{FILM}' is ODEON film {', '.join(sorted(snap.film_ids))}",
        f"Dune dates listed here: {dates or 'none'}",
    ]
    for d, showings in sorted(snap.days.items()):
        dune = [s for s in showings if s.dune]
        tag = " (release day)" if d == snap.release_date else ""
        if dune:
            detail = "; ".join(
                f"{clock(s.starts)} {'IMAX ' if s.imax else ''}{s.sale.replace('_', ' ')}"
                + (f" (opens {when(s.opens_at)})" if s.sale == NOT_YET else "")
                for s in dune
            )
        else:
            detail = f"no Dune showings ({len(showings)} other showings that day)"
        lines.append(f"  {day(d)}{tag}: {detail}")
    return "\n".join(lines)
