#!/usr/bin/env python3
"""Read public provider metadata for a discovered content link; never control TV."""

import argparse
from datetime import datetime, timezone
from http.client import IncompleteRead
import json
import re
import sys
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 8 * 1024 * 1024
NETFLIX_HOSTS = {"netflix.com", "www.netflix.com"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def checked_url(value, hosts):
    url = urlsplit(value)
    if (url.scheme != "https" or url.hostname not in hosts
            or url.username is not None or url.password is not None
            or url.port not in (None, 443) or any(ord(c) < 32 for c in value)):
        raise ValueError("Use an HTTPS link on the provider's public website")
    return url


class ProviderRedirects(HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_once(url, hosts):
    checked_url(url, hosts)
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity", "Connection": "close",
    })
    # No cookie jar, credentials, JavaScript execution, or browser profile.
    with build_opener(ProviderRedirects(hosts)).open(request, timeout=20) as response:
        chunks, size = [], 0
        while True:
            chunk = response.read(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("Public metadata exceeded the size limit")
        data = b"".join(chunks)
    return data.decode("utf-8")


def fetch(url, hosts):
    # Observed Netflix transport truncation is intermittent. Discard the entire
    # partial response and retry once; never parse partial metadata as success.
    try:
        return fetch_once(url, hosts)
    except IncompleteRead:
        return fetch_once(url, hosts)


def netflix_id(value):
    if re.fullmatch(r"[1-9][0-9]{0,11}", value):
        return value
    url = checked_url(value, NETFLIX_HOSTS)
    match = re.fullmatch(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:title|watch)/([1-9][0-9]{0,11})/?", url.path)
    if not match:
        raise ValueError("Use a Netflix title/watch link or numeric ID")
    return match[1]


def youtube_id(value):
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    url = checked_url(value, YOUTUBE_HOSTS)
    if url.hostname == "youtu.be":
        candidate = url.path.removeprefix("/")
    elif url.path == "/watch":
        values = parse_qs(url.query).get("v", [])
        candidate = values[0] if len(values) == 1 else ""
    else:
        match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", url.path)
        candidate = match[1] if match else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        raise ValueError("Use one YouTube video link or video ID")
    return candidate


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def objects(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            yield item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def public_netflix_title(html, content_id):
    marker = re.search(r"\bnetflix\.reactContext\s*=\s*", html)
    if not marker:
        raise ValueError("Netflix public metadata is unavailable; use an observed app lookup")
    # Netflix emits a JSON-shaped object with JavaScript hex escapes. Convert
    # only unescaped \xHH escapes, then decode JSON as data; never eval the script.
    raw = re.sub(r"(?<!\\)((?:\\\\)*)\\x([0-9a-fA-F]{2})",
                 lambda match: match[1] + "\\u00" + match[2], html[marker.end():])
    context, _ = json.JSONDecoder().raw_decode(raw)
    candidates = [item for item in objects(context)
                  if str(item.get("videoId")) == content_id
                  and item.get("__typename") in {"Show", "Movie"}
                  and isinstance(item.get("title"), str)
                  and ("seasons" in item or "latestYear" in item)]
    if not candidates:
        raise ValueError("No matching title metadata; do not substitute a related title or series ID")
    summaries = [summarize_netflix(item) for item in candidates]
    if any(item != summaries[0] for item in summaries[1:]):
        raise ValueError("Conflicting title metadata; inspect the provider page")
    return summaries[0]


def summarize_netflix(item):
    episodes = []
    for edge in (item.get("seasons") or {}).get("edges", []):
        season = edge["node"]
        label = season.get("numberLabelV2", "")
        match = re.fullmatch(r"Season ([1-9][0-9]*)", label)
        # Preserve provider labels for Parts/Volumes/Limited Series. Never infer
        # a season number from array position or sort content IDs numerically.
        season_number = int(match[1]) if match else None
        for episode_edge in (season.get("episodes") or {}).get("edges", []):
            episode = episode_edge["node"]
            video_id = str(episode.get("videoId", ""))
            number = episode.get("number")
            title = episode.get("title")
            if (not re.fullmatch(r"[1-9][0-9]{0,11}", video_id)
                    or type(number) is not int or number < 1 or not isinstance(title, str)):
                raise ValueError("Incomplete episode metadata; refusing to infer an ID")
            episodes.append({"season": season_number, "season_label": label,
                             "season_title": season.get("title"), "episode": number,
                             "title": title, "content_id": video_id,
                             "url": "https://www.netflix.com/watch/" + video_id})
    return {"title": item["title"], "kind": item["__typename"],
            "content_id": str(item["videoId"]), "latest_year": item.get("latestYear"),
            "public_page_available": item.get("isAvailable"), "episodes": episodes}


def select_episode(title, season=None, episode=None, episode_title=None, season_label=None):
    requested = any(v is not None for v in (season, episode, episode_title, season_label))
    if not requested:
        return None
    if episode is None and episode_title is None:
        raise ValueError("Specify an episode number or title, not just a season")
    matches = [item for item in title["episodes"]
               if (season is None or item["season"] == season)
               and (season_label is None or normalized(item["season_label"]) == normalized(season_label))
               and (episode is None or item["episode"] == episode)
               and (episode_title is None or normalized(item["title"]) == normalized(episode_title))]
    if len(matches) != 1:
        raise ValueError("Episode selection is missing or ambiguous; inspect the episode list and clarify")
    return matches[0]


def lookup_netflix(value, **selectors):
    content_id = netflix_id(value)
    source = "https://www.netflix.com/title/" + content_id
    result = public_netflix_title(fetch(source, NETFLIX_HOSTS), content_id)
    selected = select_episode(result, **selectors)
    if selected is not None:
        result.pop("episodes")
    result.update(provider="netflix", source_url=source,
                  url=source, selected_episode=selected)
    return result


def lookup_youtube(value):
    content_id = youtube_id(value)
    url = "https://www.youtube.com/watch?v=" + content_id
    source = "https://www.youtube.com/oembed?" + urlencode({"url": url, "format": "json"})
    data = json.loads(fetch(source, {"www.youtube.com"}))
    if (data.get("type") != "video" or data.get("provider_name") != "YouTube"
            or not isinstance(data.get("title"), str) or not isinstance(data.get("author_name"), str)):
        raise ValueError("YouTube did not return video title/channel metadata")
    return {"provider": "youtube", "content_id": content_id, "url": url,
            "title": data["title"], "channel": data["author_name"],
            "channel_url": data.get("author_url"), "source_url": source}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="provider", required=True)
    netflix = commands.add_parser("netflix", help="Inspect a series/movie and resolve exact episodes")
    netflix.add_argument("link_or_id")
    netflix.add_argument("--season", type=int)
    netflix.add_argument("--season-label", help="Exact provider label, e.g. Limited Series or Part 1")
    netflix.add_argument("--episode", type=int)
    netflix.add_argument("--episode-title")
    youtube = commands.add_parser("youtube", help="Verify a discovered video's title and channel")
    youtube.add_argument("link_or_id")
    args = parser.parse_args()
    try:
        if args.provider == "netflix":
            for value in (args.season, args.episode):
                if value is not None and value < 1:
                    raise ValueError("Season and episode numbers must be positive")
            result = lookup_netflix(args.link_or_id, season=args.season, episode=args.episode,
                                    episode_title=args.episode_title, season_label=args.season_label)
        else:
            result = lookup_youtube(args.link_or_id)
        result.update(checked_at=datetime.now(timezone.utc).isoformat(),
                      account_availability_verified=False, playback_verified=False)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except Exception as exc:
        # Do not dump response bodies, anonymous session data, or URL parameters.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        print(json.dumps({"error": message, "resolved": False}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
