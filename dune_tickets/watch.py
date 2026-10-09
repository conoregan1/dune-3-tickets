"""Turn ODEON's raw data into an answer: is Dune on sale in IMAX on the day?

Two sources, because they fail differently:

- ``film-screening-dates`` is ODEON's summary of which days each film plays
  at a cinema. It is cheap and covers months, so it is how new dates (an
  early-access preview, say) are noticed.
- ``showtimes/by-business-date`` is the truth for one day: every showing,
  its screen and attributes, and the rule saying when it can be booked. The
  release day is always read from here directly, whatever the summary says.

A showing can be loaded before it can be bought. ODEON marks that with a
``FilmAdvanceBookingRule`` restriction whose booking periods say when sales
open, and to whom. That is the difference between "listed, on sale at 9am on
Tuesday" and "on sale now", and both are worth an alert.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .odeon import SITE, OdeonError

LONDON = ZoneInfo("Europe/London")

# How a showing can be bought, most urgent first.
ON_SALE = "on_sale"  # anyone can book it right now
UNKNOWN = "unknown"  # listed with a restriction this doesn't recognise
MEMBERS = "members"  # a members-only pre-sale is open
NOT_YET = "not_yet"  # loaded, but sales open later
SALE_ORDER = [ON_SALE, UNKNOWN, MEMBERS, NOT_YET]

# Alert levels beyond the release-day IMAX ones.
OTHER_FORMATS = "other_formats"  # release-day Dune on sale, but none in IMAX
NEW_DATES = "new_dates"  # ODEON added Dune dates at this cinema
LEVEL_ORDER = SALE_ORDER + [OTHER_FORMATS, NEW_DATES]


@dataclass
class Showing:
    id: str
    film_id: str
    title: str
    business_date: date
    starts: datetime
    screen: str
    imax: bool
    dune: bool
    attributes: list[str]
    sold_out: bool
    sale: str
    opens_at: datetime | None = None
    note: str = ""
    layout_id: str = ""
    # Filled in only when a message needs them; None means not asked.
    seats_left: int | None = None  # ordinary seats
    accessible_left: int = 0  # wheelchair and companion spaces

    @property
    def booking_url(self) -> str:
        return f"{SITE}/ticketing/seat-picker/?showtimeId={urllib.parse.quote(self.id)}"


@dataclass
class Snapshot:
    taken: datetime
    release_date: date
    film_ids: set[str]
    # Every business date ODEON lists Dune on at this cinema, and whether any
    # of that day's Dune showings are IMAX.
    dune_dates: dict[date, bool]
    # Days read in full: every showing at the cinema, any film.
    days: dict[date, list[Showing]] = field(default_factory=dict)

    def dune_on(self, day: date) -> list[Showing]:
        return [s for s in self.days.get(day, []) if s.dune]

    def others_on(self, day: date) -> list[Showing]:
        return [s for s in self.days.get(day, []) if not s.dune]

    @property
    def release_imax(self) -> list[Showing]:
        return [s for s in self.dune_on(self.release_date) if s.imax]


@dataclass
class Assessment:
    level: str | None  # what to alert about, or None to stay quiet
    keys: set[str]  # everything newly seen, to remember after notifying
    new_dates: list[date]


# ---------------------------------------------------------------- matching


def normalise(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return re.sub(r"\bpart 3\b", "part three", text)


def title_matches(title: str, wanted: list[str]) -> bool:
    have = normalise(title)
    return any(normalise(w) and normalise(w) in have for w in wanted)


def _screen_name(screen_id: str | None) -> str:
    """ "315-6" -> "Screen 6", for when a day's data leaves the name out."""
    number = (screen_id or "").rpartition("-")[2]
    return f"Screen {number}" if number.isdigit() else (screen_id or "?")


def _text(value) -> str:
    """ODEON wraps most strings as {"text": ..., "translations": [...]}."""
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


# ----------------------------------------------------------- reading a day


def sale_status(showtime: dict, rules: dict[str, dict], now: datetime) -> tuple[str, datetime | None, str]:
    """(status, when general sale opens if not yet, note) for one showtime."""
    restrictions = [r for r in showtime.get("restrictions") or [] if r]
    others = [r for r in restrictions if r != "FilmAdvanceBookingRule"]
    if others:
        return UNKNOWN, None, ", ".join(others)
    if not restrictions:
        return ON_SALE, None, ""

    rule = rules.get(showtime.get("filmAdvanceBookingRuleId") or "")
    if not rule:
        return UNKNOWN, None, "an advance-booking rule ODEON didn't include"

    periods = []
    for period in rule.get("bookingPeriods") or []:
        try:
            starts = datetime.fromisoformat(period["startsAt"])
        except (KeyError, TypeError, ValueError):
            continue
        public = (period.get("restriction") or "None") == "None"
        periods.append((starts, public))

    if any(public and starts <= now for starts, public in periods):
        return ON_SALE, None, ""
    public_later = sorted(starts for starts, public in periods if public and starts > now)
    if any(not public and starts <= now for starts, public in periods):
        return MEMBERS, (public_later[0] if public_later else None), ""
    later = public_later or sorted(starts for starts, _ in periods if starts > now)
    if later:
        return NOT_YET, later[0], ""
    return UNKNOWN, None, "an advance-booking rule with no booking window"


def parse_day(payload: dict, film_ids: set[str], cfg: Config, now: datetime) -> list[Showing]:
    related = payload.get("relatedData") or {}
    names = {a["id"]: _text(a.get("name")) for a in related.get("attributes", []) if "id" in a}
    imax_ids = {cfg.format_attribute_id} | {
        i for i, name in names.items() if cfg.format_name.lower() in name.lower()
    }
    titles = {f["id"]: _text(f.get("title")) for f in related.get("films", []) if "id" in f}
    screens = {s["id"]: _text(s.get("name")) for s in related.get("screens", []) if "id" in s}
    rules = {r["id"]: r for r in related.get("filmAdvanceBookingRules", []) if "id" in r}

    showings = []
    for st in payload.get("showtimes", []):
        try:
            schedule = st["schedule"]
            starts = datetime.fromisoformat(schedule["startsAt"])
            business_date = date.fromisoformat(schedule["businessDate"])
        except (KeyError, TypeError, ValueError):
            continue
        attrs = [a for a in st.get("attributeIds") or [] if a]
        sale, opens, note = sale_status(st, rules, now)
        film_id = st.get("filmId") or ""
        showings.append(
            Showing(
                id=str(st.get("id")),
                film_id=film_id,
                title=titles.get(film_id) or film_id,
                business_date=business_date,
                starts=starts,
                screen=screens.get(st.get("screenId")) or _screen_name(st.get("screenId")),
                imax=bool(imax_ids & set(attrs)),
                dune=film_id in film_ids,
                attributes=[names.get(a, a) for a in attrs],
                sold_out=bool(st.get("isSoldOut")),
                sale=sale,
                opens_at=opens,
                note=note,
                layout_id=st.get("seatLayoutId") or "",
            )
        )
    return sorted(showings, key=lambda s: s.starts)


# ------------------------------------------------------------- the snapshot


def take_snapshot(odeon, cfg: Config, now: datetime) -> Snapshot:
    """Everything one check needs, in as few requests as possible."""
    film_ids = {
        f["id"]
        for f in odeon.films()
        if "id" in f and title_matches(_text(f.get("title")), cfg.titles)
    }
    if not film_ids:
        # The film has been on ODEON's list for months. If it vanishes the
        # matching is broken, and "no tickets yet" would be a lie.
        raise OdeonError(f"ODEON's film list has nothing called {cfg.titles[0]!r} any more")

    dune_dates: dict[date, bool] = {}
    for entry in odeon.screening_dates(cfg.site_id):
        try:
            day = date.fromisoformat(entry["businessDate"])
        except (KeyError, TypeError, ValueError):
            continue
        for screening in entry.get("filmScreenings") or []:
            if screening.get("filmId") not in film_ids:
                continue
            for site in screening.get("sites") or []:
                if str(site.get("siteId")) == cfg.site_id:
                    imax = cfg.format_attribute_id in (site.get("showtimeAttributeIds") or [])
                    dune_dates[day] = dune_dates.get(day, False) or imax

    today = now.astimezone(LONDON).date()
    # The release day always; previews up to it and the opening weekend for
    # context. Later dates are only listed, not read, to keep a check light.
    wanted = {cfg.release_date} | {
        d for d in dune_dates if today <= d <= cfg.release_date + timedelta(days=2)
    }
    snap = Snapshot(now, cfg.release_date, film_ids, dune_dates)
    for day in sorted(d for d in wanted if d >= today):
        snap.days[day] = parse_day(odeon.showtimes(day, cfg.site_id), film_ids, cfg, now)
    return snap


def fill_seats(odeon, snap: Snapshot, limit: int = 12) -> None:
    """Seat counts for every Dune showing a message might mention, release
    day first. Bounded, best-effort, never raises."""
    days = sorted(snap.days, key=lambda d: (d != snap.release_date, d))
    showings = [s for d in days for s in snap.dune_on(d)]
    for showing in showings[:limit]:
        if showing.seats_left is None and showing.sale != NOT_YET:
            counts = odeon.seats_left(showing.id, showing.layout_id)
            if counts:
                showing.seats_left, showing.accessible_left = counts


# ------------------------------------------------------------ the decision


def assess(snap: Snapshot, seen: set[str], *, quiet_dates: bool = False) -> Assessment:
    """What's new since last time, and how loudly to say so.

    Keys go into state.json once notified, so each thing is announced once:
    ``on_sale:315-1234`` for a showing seen on sale, ``not_yet:315-1234`` for
    one seen listed. A showing that moves from listed to on sale gets a new
    key, so it alerts twice, which is the point.
    """
    keys: set[str] = set()
    levels: set[str] = set()

    for showing in snap.release_imax:
        key = f"{showing.sale}:{showing.id}"
        if key not in seen:
            keys.add(key)
            levels.add(showing.sale)

    if not snap.release_imax:
        on_sale = [s for s in snap.dune_on(snap.release_date) if s.sale in (ON_SALE, UNKNOWN)]
        key = f"{OTHER_FORMATS}:{snap.release_date.isoformat()}"
        if on_sale and key not in seen:
            keys.add(key)
            levels.add(OTHER_FORMATS)

    new_dates = sorted(
        d
        for d in snap.dune_dates
        if d != snap.release_date and f"date:{d.isoformat()}" not in seen
    )
    keys |= {f"date:{d.isoformat()}" for d in new_dates}
    # Once release-day tickets have been announced, ODEON adding January
    # dates is noise. Before then, any new date is a sign of movement.
    already_on_sale = any(k.startswith(f"{ON_SALE}:") for k in seen)
    if new_dates and not quiet_dates and not already_on_sale:
        levels.add(NEW_DATES)

    level = next((lvl for lvl in LEVEL_ORDER if lvl in levels), None)
    return Assessment(level, keys, new_dates)
