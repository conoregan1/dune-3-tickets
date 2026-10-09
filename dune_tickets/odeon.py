"""ODEON's booking API: the same one its website calls from your browser.

Every page on odeon.co.uk carries a short-lived bearer token (about 12 hours)
in ``window.initialData.api.authToken``. The site's own JavaScript sends it to
the Vista "OCAPI" at vwc.odeon.co.uk to draw showtimes and seat maps. This does
the same thing: read a page, lift the token, ask the API. No login, and nothing
the public site doesn't already hand to every visitor.

Endpoints used (all GET, all under ``{apiUrl}/ocapi/v1/``):

    films                                   every film ODEON knows about
    film-screening-dates?siteIds=315        which days each film is on, per cinema
    showtimes/by-business-date/{d}?siteIds  every showing at a cinema that day
    showtimes/{id}/seat-availability        seat-by-seat status for one showing
    seat-layouts/{id}                       a screen's seat map, for seat types
"""

from __future__ import annotations

import base64
import gzip
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

SITE = "https://www.odeon.co.uk"
DEFAULT_API = "https://vwc.odeon.co.uk/WSVistaWebClient"

# A plain browser string. The pages are public, but CDNs are quicker to turn
# away anything that announces itself as a script.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
TIMEOUT = 30

# Fetch a new token once the cached one has less than this left. Comfortably
# more than the gap between runs, so a run never starts on a token that dies
# halfway through.
REFRESH_MARGIN = 30 * 60

ACCESSIBLE_SEAT_TYPES = {"Wheelchair", "Companion"}


class OdeonError(RuntimeError):
    """ODEON couldn't be read. The message says why, in plain words."""


class QueueActive(OdeonError):
    """ODEON has its Queue-it waiting room switched on.

    Worth knowing in its own right: the queue goes up for big on-sales, and a
    Dune release-day IMAX sale is exactly that.
    """


@dataclass
class Token:
    value: str
    api_url: str
    expires: datetime

    def good_for(self, now: datetime, seconds: float) -> bool:
        return (self.expires - now).total_seconds() > seconds

    def to_json(self) -> dict:
        return {
            "value": self.value,
            "api_url": self.api_url,
            "expires": self.expires.isoformat(),
        }

    @classmethod
    def from_json(cls, data: dict) -> "Token":
        return cls(data["value"], data["api_url"], datetime.fromisoformat(data["expires"]))


def token_from_page(html: str) -> Token:
    """Pull the API token and base URL out of an odeon.co.uk page."""
    found = re.search(
        r'"authToken"\s*:\s*"([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)"', html
    )
    if not found:
        raise OdeonError(
            "the ODEON page had no API token in it, so ODEON may have changed its website"
        )
    api = re.search(r'"apiUrl"\s*:\s*"(https://[^"]+)"', html)
    value = found.group(1)
    return Token(value, (api.group(1) if api else DEFAULT_API).rstrip("/"), jwt_expiry(value))


def jwt_expiry(token: str) -> datetime:
    """When a JWT stops working, read from its own payload."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return datetime.fromtimestamp(exp, timezone.utc)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise OdeonError(f"couldn't read the API token's expiry ({exc})") from exc


class Odeon:
    """A small client for the handful of endpoints this needs."""

    def __init__(
        self,
        *,
        page: str = SITE + "/cinemas/bristol/",
        cache: Path | None = None,
        verbose: bool = False,
    ) -> None:
        self.page = page
        self.cache = cache
        self.verbose = verbose
        self.requests = 0
        # Set when the site redirected us into its waiting room, even if a
        # cached token let the check carry on regardless.
        self.queue_active = False
        self._token: Token | None = None
        self._layouts: dict[str, set[str]] = {}
        # Cookies matter: ODEON's Queue-it "safety net" bounces a first visit
        # through a redirect that sets a cookie, and without a jar that
        # redirect loops until the client gives up.
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    # ------------------------------------------------------------ endpoints

    def films(self) -> list[dict]:
        return self.api("films").get("films", [])

    def screening_dates(self, site_id: str) -> list[dict]:
        return self.api("film-screening-dates", siteIds=site_id).get("filmScreeningDates", [])

    def showtimes(self, business_date: date, site_id: str) -> dict:
        return self.api(f"showtimes/by-business-date/{business_date.isoformat()}", siteIds=site_id)

    def seats_left(self, showtime_id: str, layout_id: str = "") -> tuple[int, int] | None:
        """(ordinary seats free, wheelchair/companion spaces free), or None if
        ODEON wouldn't say. Never raises: a seat count is a nice-to-have on a
        message, never a reason to fail."""
        try:
            data = self.api(f"showtimes/{urllib.parse.quote(showtime_id)}/seat-availability")
        except OdeonError as exc:
            self._log(f"  seat count for {showtime_id} unavailable: {exc}")
            return None
        free = {
            s.get("seatId")
            for s in data.get("seatAvailabilities", [])
            if s.get("status") == "Available"
        }
        accessible = self._accessible_seats(layout_id) if layout_id else set()
        return len(free - accessible), len(free & accessible)

    def _accessible_seats(self, layout_id: str) -> set[str]:
        """Seat ids only bookable for a wheelchair user and their companion.

        Counted separately because they are the last to sell: the 15 Dec
        previews showed "8 left" when every ordinary seat had gone. A screen's
        layout never changes, so it is fetched once per run.
        """
        if layout_id not in self._layouts:
            seats: set[str] = set()
            try:
                data = self.api(f"seat-layouts/{urllib.parse.quote(layout_id)}")
            except OdeonError as exc:
                self._log(f"  seat layout {layout_id} unavailable: {exc}")
            else:
                stack = [data]
                while stack:
                    node = stack.pop()
                    if isinstance(node, dict):
                        if node.get("type") in ACCESSIBLE_SEAT_TYPES and "id" in node:
                            seats.add(node["id"])
                        stack.extend(node.values())
                    elif isinstance(node, list):
                        stack.extend(node)
            self._layouts[layout_id] = seats
        return self._layouts[layout_id]

    # ---------------------------------------------------------------- token

    def token(self, now: datetime | None = None) -> Token:
        now = now or datetime.now(timezone.utc)
        if self._token and self._token.good_for(now, 60):
            return self._token

        cached = self._read_cache()
        if cached and cached.good_for(now, REFRESH_MARGIN):
            self._token = cached
            return cached

        try:
            fresh = self._token_from_site()
        except OdeonError as exc:
            if isinstance(exc, QueueActive):
                self.queue_active = True
            # The website being unreachable doesn't mean the API is: during a
            # queue the API usually carries on, and a token that still has a
            # few minutes in it is enough for one check.
            if cached and cached.good_for(now, 60):
                self._log(f"Couldn't refresh the token ({exc}); using the cached one.")
                self._token = cached
                return cached
            raise

        self._token = fresh
        self._write_cache(fresh)
        return fresh

    def _token_from_site(self) -> Token:
        self._log(f"Fetching a fresh API token from {self.page}")
        try:
            body, final_url = self._get(self.page, {"Accept": "text/html"})
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise OdeonError(
                    "odeon.co.uk kept redirecting (its Queue-it check never settled)"
                ) from exc
            raise OdeonError(f"odeon.co.uk answered HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise OdeonError(f"couldn't reach odeon.co.uk ({_reason(exc)})") from exc

        if "queue-it" in urllib.parse.urlsplit(final_url).netloc:
            raise QueueActive("ODEON's website is holding visitors in its Queue-it waiting room")
        return token_from_page(body.decode("utf-8", "replace"))

    def _read_cache(self) -> Token | None:
        if not self.cache or not self.cache.exists():
            return None
        try:
            return Token.from_json(json.loads(self.cache.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, token: Token) -> None:
        if not self.cache:
            return
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps(token.to_json()), encoding="utf-8")

    def _drop_cache(self) -> None:
        self._token = None
        if self.cache and self.cache.exists():
            self.cache.unlink()

    # ------------------------------------------------------------ transport

    def api(self, path: str, **params: str) -> dict:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        for attempt in (1, 2, 3):
            token = self.token()
            url = f"{token.api_url}/ocapi/v1/{path}{query}"
            try:
                body, _ = self._get(
                    url, {"Authorization": f"Bearer {token.value}", "Accept": "application/json"}
                )
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 1:
                    # Revoked or expired early: fetch a new one and go again.
                    self._log("API said 401; fetching a new token.")
                    self._drop_cache()
                    continue
                if exc.code >= 500 and attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                raise OdeonError(f"ODEON's API answered HTTP {exc.code} for {path}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                raise OdeonError(f"couldn't reach ODEON's API ({_reason(exc)})") from exc

            try:
                return json.loads(body)
            except ValueError as exc:
                raise OdeonError(f"ODEON's API sent something that isn't JSON for {path}") from exc
        raise OdeonError(f"ODEON's API kept refusing the token for {path}")

    def _get(self, url: str, headers: dict[str, str]) -> tuple[bytes, str]:
        self.requests += 1
        self._log(f"  GET {url.split('?')[0]}")
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **headers}
        )
        with self._opener.open(request, timeout=TIMEOUT) as response:
            body = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            return body, response.geturl()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)


def _reason(exc: BaseException) -> str:
    return str(getattr(exc, "reason", None) or exc) or type(exc).__name__
