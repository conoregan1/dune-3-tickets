"""Settings, read from config.toml at the repository root."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.toml"


@dataclass
class Config:
    site_id: str
    cinema_name: str
    cinema_page: str
    titles: list[str]
    release_date: date
    film_page: str
    format_name: str
    format_attribute_id: str
    repo: str
    github_user: str
    status_hour: int
    broken_after_minutes: int
    stale_after_hours: float
    quiet_from: int
    quiet_until: int


def load_config(path: Path | None = None) -> Config:
    raw = tomllib.loads((path or CONFIG).read_text(encoding="utf-8"))
    cinema, film, fmt, notify = raw["cinema"], raw["film"], raw["format"], raw["notify"]
    watchdog = raw.get("watchdog", {})

    release = film["release_date"]
    # Accept a quoted "2026-12-18" too, so a hand edit can't silently break it.
    if isinstance(release, str):
        release = date.fromisoformat(release)

    titles = film["titles"]
    if isinstance(titles, str):
        titles = [titles]

    return Config(
        site_id=str(cinema["site_id"]),
        cinema_name=cinema["name"],
        cinema_page=cinema["page"],
        titles=list(titles),
        release_date=release,
        film_page=film["page"],
        format_name=fmt["name"],
        format_attribute_id=str(fmt["attribute_id"]),
        repo=notify["repo"],
        github_user=notify["github_user"],
        status_hour=int(notify["status_hour"]),
        broken_after_minutes=int(notify["broken_after_minutes"]),
        stale_after_hours=float(watchdog.get("stale_after_hours", 3)),
        quiet_from=int(watchdog.get("quiet_from", 23)),
        quiet_until=int(watchdog.get("quiet_until", 8)),
    )
