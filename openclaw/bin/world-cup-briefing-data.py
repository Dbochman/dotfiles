#!/usr/bin/env python3
"""Fetch compact, bounded World Cup data for the daily OpenClaw briefing."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/"
    "fifa.world/scoreboard?dates={date}"
)
STANDINGS_URL = (
    "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings"
)
FIFA_REFERENCE_URL = (
    "https://www.fifa.com/en/tournaments/mens/worldcup/"
    "canadamexicousa2026/scores-fixtures"
)
EASTERN = ZoneInfo("America/New_York")
DATE_OFFSETS = (-1, 0, 1, 2, 3)
DEFAULT_TIMEOUT_SECONDS = 6.0
USER_AGENT = "OpenClaw-World-Cup-Briefing/1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch normalized World Cup scores, fixtures, and standings."
    )
    parser.add_argument("briefing_date", type=dt.date.fromisoformat)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".openclaw" / "cache" / "world-cup",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds (default: 6).",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response root is not an object")
    return payload


def eastern_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(EASTERN).isoformat(timespec="minutes")


def broadcast_names(competition: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for broadcast in competition.get("broadcasts", []):
        if broadcast.get("market") not in (None, "national"):
            continue
        names.extend(str(name) for name in broadcast.get("names", []) if name)
    for broadcast in competition.get("geoBroadcasts", []):
        if broadcast.get("region") not in (None, "us"):
            continue
        name = broadcast.get("media", {}).get("shortName")
        if name:
            names.append(str(name))
    return list(dict.fromkeys(names))


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions else {}
    competitors = {
        item.get("homeAway"): item
        for item in competition.get("competitors", [])
        if item.get("homeAway") in {"home", "away"}
    }

    def team(side: str) -> dict[str, Any]:
        competitor = competitors.get(side, {})
        details = competitor.get("team", {})
        return {
            "name": details.get("displayName") or details.get("name"),
            "abbreviation": details.get("abbreviation"),
            "score": competitor.get("score"),
            "winner": competitor.get("winner") is True,
        }

    status = competition.get("status") or event.get("status") or {}
    status_type = status.get("type", {})
    venue = competition.get("venue") or event.get("venue") or {}
    address = venue.get("address", {})
    return {
        "id": event.get("id"),
        "kickoff_et": eastern_timestamp(
            competition.get("date") or event.get("date")
        ),
        "round": competition.get("altGameNote")
        or event.get("season", {}).get("slug"),
        "status": status_type.get("description") or status_type.get("name"),
        "status_detail": status_type.get("detail") or status_type.get("shortDetail"),
        "completed": status_type.get("completed") is True,
        "home": team("home"),
        "away": team("away"),
        "broadcasts_us": broadcast_names(competition),
        "venue": venue.get("fullName"),
        "city": address.get("city"),
    }


def normalize_scoreboard(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("scoreboard response has no events array")
    normalized = [normalize_event(event) for event in events]
    return sorted(normalized, key=lambda event: event.get("kickoff_et") or "")


def entry_stats(entry: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for stat in entry.get("stats", []):
        name = stat.get("name")
        if name:
            result[str(name)] = stat.get("displayValue", stat.get("value"))
    return result


def normalize_standings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("children")
    if not isinstance(groups, list):
        raise ValueError("standings response has no children array")
    normalized: list[dict[str, Any]] = []
    for group in groups:
        rows: list[dict[str, Any]] = []
        entries = group.get("standings", {}).get("entries", [])
        for entry in entries:
            stats = entry_stats(entry)
            team = entry.get("team", {})
            rows.append(
                {
                    "rank": stats.get("rank"),
                    "team": team.get("displayName") or team.get("name"),
                    "abbreviation": team.get("abbreviation"),
                    "played": stats.get("gamesPlayed"),
                    "wins": stats.get("wins"),
                    "draws": stats.get("ties"),
                    "losses": stats.get("losses"),
                    "goals_for": stats.get("pointsFor"),
                    "goals_against": stats.get("pointsAgainst"),
                    "goal_difference": stats.get("pointDifferential"),
                    "points": stats.get("points"),
                    "qualification_note": entry.get("note", {}).get("description"),
                }
            )
        normalized.append({"group": group.get("name"), "teams": rows})
    return normalized


def load_cache(path: Path) -> dict[str, Any] | None:
    try:
        cached = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return cached if isinstance(cached, dict) else None


def write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def collect(briefing_date: dt.date, timeout: float, cache_path: Path) -> dict[str, Any]:
    dates = [briefing_date + dt.timedelta(days=offset) for offset in DATE_OFFSETS]
    targets = {
        f"scoreboard:{date.isoformat()}": SCOREBOARD_URL.format(
            date=date.strftime("%Y%m%d")
        )
        for date in dates
    }
    targets["standings"] = STANDINGS_URL

    raw: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {
            pool.submit(fetch_json, url, timeout): name
            for name, url in targets.items()
        }
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                raw[name] = future.result()
            except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
                errors[name] = f"{type(error).__name__}: {error}"

    matches_by_date: dict[str, list[dict[str, Any]]] = {}
    for date in dates:
        date_key = date.isoformat()
        source_key = f"scoreboard:{date_key}"
        if source_key not in raw:
            continue
        try:
            matches_by_date[date_key] = normalize_scoreboard(raw[source_key])
        except ValueError as error:
            errors[source_key] = str(error)

    standings: list[dict[str, Any]] | None = None
    if "standings" in raw:
        try:
            standings = normalize_standings(raw["standings"])
        except ValueError as error:
            errors["standings"] = str(error)

    cache = load_cache(cache_path)
    cached_sources: list[str] = []
    if cache:
        cached_matches = cache.get("matches_by_date", {})
        for date in dates:
            date_key = date.isoformat()
            if date_key not in matches_by_date and date_key in cached_matches:
                matches_by_date[date_key] = cached_matches[date_key]
                cached_sources.append(f"scoreboard:{date_key}")
        if standings is None and isinstance(cache.get("standings"), list):
            standings = cache["standings"]
            cached_sources.append("standings")

    now = dt.datetime.now(EASTERN)
    result = {
        "briefing_date": briefing_date.isoformat(),
        "generated_at_et": now.isoformat(timespec="seconds"),
        "timezone": "America/New_York",
        "source_status": {
            "live": sorted(set(raw) - set(errors)),
            "cached": sorted(cached_sources),
            "errors": errors,
        },
        "matches_by_date": dict(sorted(matches_by_date.items())),
        "standings": standings or [],
        "source_urls": {
            "scoreboard_template": SCOREBOARD_URL,
            "standings": STANDINGS_URL,
            "official_fifa_reference": FIFA_REFERENCE_URL,
        },
    }

    expected_dates = {date.isoformat() for date in dates}
    if not errors and set(matches_by_date) == expected_dates and standings is not None:
        write_cache(cache_path, result)
    return result


def main() -> int:
    args = parse_args()
    if not 0.5 <= args.timeout <= 15:
        raise SystemExit("--timeout must be between 0.5 and 15 seconds")
    cache_path = args.cache_dir / f"{args.briefing_date.isoformat()}.json"
    result = collect(args.briefing_date, args.timeout, cache_path)
    print(
        json.dumps(
            result,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
            sort_keys=True,
        )
    )
    current = args.briefing_date.isoformat()
    if current not in result["matches_by_date"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
