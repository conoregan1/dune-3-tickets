#!/usr/bin/env python3
"""Offline checks for everything that decides whether your phone buzzes.

Runs against real ODEON responses saved in tools/fixtures (captured 10 Oct
2026: the 15 Dec IMAX previews on sale, release day not yet listed), plus
release-day showings built from them for each way a sale can open. No
network, no token, about a second.

    python tools/selftest.py
"""

from __future__ import annotations

import base64
import copy
import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dune_tickets import messages  # noqa: E402
from dune_tickets.config import load_config  # noqa: E402
from dune_tickets.github import Notifier  # noqa: E402
from dune_tickets.odeon import Odeon, OdeonError, jwt_expiry, token_from_page  # noqa: E402
from dune_tickets.runner import run_once  # noqa: E402
from dune_tickets.state import State  # noqa: E402
from dune_tickets.watch import (  # noqa: E402
    MEMBERS,
    NEW_DATES,
    NOT_YET,
    ON_SALE,
    OTHER_FORMATS,
    UNKNOWN,
    assess,
    take_snapshot,
    title_matches,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAILURES: list[str] = []
CFG = load_config()
RELEASE = date(2026, 12, 18)
PREVIEW = date(2026, 12, 15)
IMAX = "0000000147"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if not ok else ""))
    if not ok:
        FAILURES.append(name)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# 09:30 in London (BST) on the day the fixtures were captured.
NOW = utc(2026, 10, 10, 8, 30)


class FakeOdeon:
    def __init__(self, release_day: dict | None = None, *, fail: str | None = None, queue=False):
        self.days = {
            PREVIEW: fixture("showtimes-2026-12-15.json"),
            RELEASE: release_day or fixture("showtimes-2026-12-18.json"),
        }
        self.fail = fail
        self.queue_active = queue
        self.requests = 0

    def films(self):
        if self.fail:
            raise OdeonError(self.fail)
        return fixture("films.json")["films"]

    def screening_dates(self, site_id):
        return fixture("screening-dates.json")["filmScreeningDates"]

    def showtimes(self, day, site_id):
        return self.days.get(day, {"businessDate": day.isoformat(), "showtimes": []})

    def seats_left(self, showtime_id, layout_id=""):
        return 100, 4


class FakeNotifier(Notifier):
    """Records instead of calling GitHub, and keeps track of open issues."""

    def __init__(self):
        super().__init__(None, CFG.github_user, log=lambda message: None)
        self.number = 100
        self.open: dict[int, str] = {}
        self.disabled = False

    def raise_issue(self, label, title, body):
        self.raised.append((label, title, body))
        self.number += 1
        self.open[self.number] = label
        return self.number

    def close_open(self, label, comment):
        for number, lbl in list(self.open.items()):
            if lbl == label:
                self.close(number, comment)

    def close(self, number, comment):
        self.open.pop(number, None)

    def is_open(self, number):
        return number in self.open

    def runs_since(self, since):
        return 250

    def disable(self):
        self.disabled = True

    def labels(self) -> list[str]:
        return [label for label, _, _ in self.raised]


def booking_rule(*periods: tuple[str, str]) -> dict:
    return {
        "id": "315-99-HO00009199",
        "filmId": "HO00009199",
        "siteId": "315",
        "bookingPeriods": [
            {"startsAt": s, "orderBookingModes": ["Paid"], "restriction": r, "rewardId": None}
            for s, r in periods
        ],
    }


def release_day(*, rule: dict | None = None, imax: bool = True, restrictions=None) -> dict:
    """The real 18 Dec data (Avengers only) with the real preview showings
    copied onto it, as ODEON would publish the release day."""
    day = copy.deepcopy(fixture("showtimes-2026-12-18.json"))
    preview = fixture("showtimes-2026-12-15.json")
    for i, st in enumerate(copy.deepcopy(preview["showtimes"])):
        st["id"] = f"315-9990{i}"
        st["schedule"] = {
            k: v.replace("2026-12-15", "2026-12-18") if isinstance(v, str) else v
            for k, v in st["schedule"].items()
        }
        if not imax:
            st["attributeIds"] = [a for a in st["attributeIds"] if a != IMAX]
            st["screenId"] = "315-5"
        if rule:
            st["restrictions"] = ["FilmAdvanceBookingRule"]
            st["filmAdvanceBookingRuleId"] = rule["id"]
        if restrictions is not None:
            st["restrictions"] = restrictions
        day["showtimes"].append(st)
    related = day["relatedData"]
    related["films"] += preview["relatedData"]["films"]
    have = {a["id"] for a in related["attributes"]}
    related["attributes"] += [a for a in preview["relatedData"]["attributes"] if a["id"] not in have]
    if rule:
        related["filmAdvanceBookingRules"].append(rule)
    return day


def run(state: State, odeon, gh: Notifier, now: datetime) -> None:
    run_once(CFG, state, odeon, gh, now, log=lambda message: None)


def fake_jwt(exp: int) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': exp})}.sig"


def main() -> int:
    print("matching the film")
    check("the real title matches", title_matches("Dune: Part Three", CFG.titles))
    check("a per-format variant matches", title_matches("DUNE PART 3 (IMAX 70mm)", CFG.titles))
    check("Part Two does not", not title_matches("Dune: Part Two", CFG.titles))

    print("ODEON's API token")
    exp = 1791626407
    page = f'<script>window.initialData = {{"api":{{"apiUrl":"https://vwc.example/WS","authToken":"{fake_jwt(exp)}"}}}}</script>'
    token = token_from_page(page)
    check("token and API address are read from the page", token.api_url == "https://vwc.example/WS")
    check("expiry comes from the token itself", token.expires == datetime.fromtimestamp(exp, timezone.utc))
    try:
        token_from_page("<html>queue page</html>")
        check("a page without a token is an error", False)
    except OdeonError:
        check("a page without a token is an error", True)
    try:
        jwt_expiry("not-a-jwt")
        check("a malformed token is an error", False)
    except OdeonError:
        check("a malformed token is an error", True)

    print("seat counts")

    class FixtureOdeon(Odeon):
        def api(self, path, **params):
            if path.startswith("showtimes/"):
                return fixture("seat-availability-315-11220.json")
            return fixture("seat-layout-315-6-90.json")

    counts = FixtureOdeon().seats_left("315-11220", "315-6-90")
    check(
        "wheelchair and companion spaces aren't counted as seats left",
        counts == (0, 8),
        f"got {counts}",
    )

    print("today's real data")
    snap = take_snapshot(FakeOdeon(), CFG, NOW)
    check("both ODEON entries for the film are watched", snap.film_ids == {"HO00009199", "HO00009200"})
    check("the 15 Dec IMAX preview date is seen", snap.dune_dates == {PREVIEW: True})
    previews = snap.dune_on(PREVIEW)
    check(
        "the previews are IMAX and on sale",
        len(previews) == 2 and all(s.imax and s.sale == ON_SALE for s in previews),
        f"got {[(s.id, s.imax, s.sale) for s in previews]}",
    )
    check("nothing on release day yet", snap.release_imax == [] and snap.dune_on(RELEASE) == [])
    check("Avengers on release day is not mistaken for Dune", len(snap.others_on(RELEASE)) == 16)
    first = assess(snap, set(), quiet_dates=True)
    check("first run notes the preview date quietly", first.level is None and first.keys == {"date:2026-12-15"})
    check("a preview date appearing later is an alert", assess(snap, set()).level == NEW_DATES)
    check("and only once", assess(snap, {"date:2026-12-15"}).level is None)
    check("IMAX screen reported free on release day", "kept for Dune" in messages.release_day_context(snap))

    print("release day: on sale")
    snap = take_snapshot(FakeOdeon(release_day()), CFG, NOW)
    check(
        "a screen the day's data doesn't name still reads well",
        snap.release_imax[0].screen == "Screen 6",
        f"got {snap.release_imax[0].screen!r}",
    )
    found = assess(snap, {"date:2026-12-15"})
    check("on sale is the alert", found.level == ON_SALE, f"got {found.level}")
    check("each showing is remembered", {"on_sale:315-99900", "on_sale:315-99901"} <= found.keys)
    check("and not alerted twice", assess(snap, found.keys | {"date:2026-12-15"}).level is None)
    title, body = messages.alert(found.level, snap, CFG, [])
    check("title says ON SALE", "ON SALE" in title)
    check("body mentions you", f"@{CFG.github_user}" in body)
    check("body links straight to seat picking", "seat-picker/?showtimeId=315-99900" in body)

    print("release day: listed, not on sale yet")
    rule = booking_rule(("2026-11-03T09:00:00+00:00", "None"))
    snap = take_snapshot(FakeOdeon(release_day(rule=rule)), CFG, NOW)
    found = assess(snap, set(), quiet_dates=True)
    check("listed-not-yet is the alert", found.level == NOT_YET, f"got {found.level}")
    opens = snap.release_imax[0].opens_at
    check("the opening time is read", opens == utc(2026, 11, 3, 9, 0), f"got {opens}")
    title, _ = messages.alert(found.level, snap, CFG, [])
    check("title gives the opening time", "Tue 3 Nov at 09:00" in title, title)
    later = take_snapshot(FakeOdeon(release_day(rule=rule)), CFG, utc(2026, 11, 3, 9, 1))
    check("once open it alerts again", assess(later, found.keys).level == ON_SALE)

    print("release day: members pre-sale")
    rule = booking_rule(
        ("2026-10-01T09:00:00+01:00", "Subscription"), ("2026-11-03T09:00:00+00:00", "None")
    )
    snap = take_snapshot(FakeOdeon(release_day(rule=rule)), CFG, NOW)
    found = assess(snap, set(), quiet_dates=True)
    check("members-only window is its own alert", found.level == MEMBERS, f"got {found.level}")
    check("general sale time is kept", snap.release_imax[0].opens_at == utc(2026, 11, 3, 9, 0))

    print("release day: oddities")
    snap = take_snapshot(FakeOdeon(release_day(restrictions=["SomethingNew"])), CFG, NOW)
    found = assess(snap, set(), quiet_dates=True)
    check("an unknown restriction still alerts", found.level == UNKNOWN, f"got {found.level}")
    check("...as on sale", "ON SALE" in messages.alert(found.level, snap, CFG, [])[0])
    snap = take_snapshot(FakeOdeon(release_day(imax=False)), CFG, NOW)
    found = assess(snap, set(), quiet_dates=True)
    check("Dune on sale but not in IMAX is flagged", found.level == OTHER_FORMATS, f"got {found.level}")
    check("...once", assess(snap, found.keys).level is None)

    print("a full run, day by day")
    state, gh = State(), FakeNotifier()
    run(state, FakeOdeon(), gh, utc(2026, 10, 10, 0, 5))
    check("first run sends the 'watching' note", gh.labels() == ["status"] and "👋" in gh.raised[0][1])
    check("first run remembers the preview quietly", "date:2026-12-15" in state.seen)
    run(state, FakeOdeon(), gh, utc(2026, 10, 10, 8, 30))
    check("no second note the same day", len(gh.raised) == 1)
    run(state, FakeOdeon(), gh, utc(2026, 10, 11, 7, 0))
    check("nothing before 9am the next day", len(gh.raised) == 1)
    run(state, FakeOdeon(), gh, utc(2026, 10, 11, 8, 5))
    check("daily note after 9am", gh.labels() == ["status", "status"])
    check("yesterday's note is closed", list(gh.open.values()) == ["status"])
    check("the note says still watching", "still watching" in gh.raised[-1][1])
    check("the note counts recent checks", "250 checks" in gh.raised[-1][2])
    run(state, FakeOdeon(release_day()), gh, utc(2026, 11, 3, 9, 2))
    check("tickets on sale raise an alert", gh.labels()[-1] == "tickets", f"got {gh.labels()}")
    check("the alert is remembered as the one to close", state.alert_issue == gh.number)
    check("no 'still watching' note straight after it", gh.labels().count("status") == 2)
    run(state, FakeOdeon(release_day()), gh, utc(2026, 11, 3, 9, 7))
    check("no repeat alert five minutes later", gh.labels().count("tickets") == 1)
    run(state, FakeOdeon(release_day()), gh, utc(2026, 11, 4, 9, 2))
    check("the daily note becomes a reminder", "Reminder" in gh.raised[-1][1])
    gh.close(state.alert_issue, "booked")
    run(state, FakeOdeon(release_day()), gh, utc(2026, 11, 4, 9, 7))
    check("closing the alert stops the watcher", state.finished and gh.disabled)

    print("ODEON going down")
    state, gh = State(started="x", seen=["date:2026-12-15"], last_status_day="2026-10-10"), FakeNotifier()
    t0 = utc(2026, 10, 10, 10, 0)
    run(state, FakeOdeon(fail="boom"), gh, t0)
    check("a failure is remembered", state.failing_since is not None and state.last_error == "boom")
    check("but a blip isn't reported", gh.raised == [])
    run(state, FakeOdeon(fail="boom"), gh, t0 + timedelta(minutes=61))
    check("an hour of failures is reported", gh.labels() == ["broken"])
    run(state, FakeOdeon(fail="boom"), gh, t0 + timedelta(minutes=66))
    check("once", gh.labels() == ["broken"])
    run(state, FakeOdeon(), gh, t0 + timedelta(minutes=71))
    check("recovery closes it", state.failing_since is None and not gh.open)
    state = State(started="x", seen=["date:2026-12-15"], failing_since=t0.isoformat(), last_error="boom")
    gh = FakeNotifier()
    run(state, FakeOdeon(fail="boom"), gh, utc(2026, 10, 11, 8, 5))
    check("the daily note still goes out while it's down", "⚠️" in gh.raised[-1][1])

    print("the queue")
    state, gh = State(started="x", seen=["date:2026-12-15"], last_status_day="2026-10-10"), FakeNotifier()
    run(state, FakeOdeon(queue=True), gh, NOW)
    run(state, FakeOdeon(queue=True), gh, NOW + timedelta(minutes=5))
    check("ODEON's queue going up is an alert, once a day", gh.labels() == ["tickets"])

    print("after the day")
    state, gh = State(started="x"), FakeNotifier()
    run(state, FakeOdeon(), gh, utc(2026, 12, 19, 12, 0))
    check("the watcher switches itself off", state.finished and gh.disabled and gh.raised == [])

    print("state.json")
    path = Path(tempfile.mkdtemp()) / "state.json"
    State(started="2026-10-10T00:05:00+00:00", seen=["b", "a", "a"], alert_issue=7).save(path)
    back = State.load(path)
    check("round trip", back.seen == ["a", "b"] and back.alert_issue == 7)

    print("config.toml")
    check("release date", CFG.release_date == RELEASE, f"got {CFG.release_date}")
    check("ODEON Luxe Bristol", CFG.site_id == "315")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed.")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
