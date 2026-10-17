#!/usr/bin/env python3
"""Download new assets from public iCloud Shared Albums into local inboxes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_CONFIG_PATH = Path("~/.openclaw/icloud-shared-album-ingest/config.json")
STATE_FILENAME = ".icloud-shared-album-ingest.state.json"
HTTP_TIMEOUT_SECONDS = 20
HTTP_ATTEMPTS = 2
ASSET_BATCH_SIZE = 25
CLOUDKIT_QUERY_RESULTS_LIMIT = 200
CLOUDKIT_MAX_PAGES = 1000
CLOUDKIT_URL_REFRESH_LIMIT = 10
BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
USER_AGENT = "openclaw-icloud-shared-album-ingest/2.0"
ICLOUD_HEADERS = {
    "Accept": "*/*",
    "Cache-Control": "no-cache",
    "Content-Type": "text/plain",
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/sharedalbum/",
    "User-Agent": USER_AGENT,
}
CLOUDKIT_RESOLVE_URL = (
    "https://ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/"
    "production/public/records/resolve"
)
CLOUDKIT_DATABASE_PATH = "/database/1/com.apple.photos.cloud/production/shared/records/query"
ALLOWED_SHARE_HOSTS = {"share.icloud.com", "www.icloud.com", "icloud.com", "photos.icloud.com"}
CONTENT_TYPE_EXTENSIONS = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}
MEDIA_ASSET_TYPE_EXTENSIONS = {
    "com.adobe.raw-image": ".dng",
    "com.apple.quicktime-movie": ".mov",
    "public.avif": ".avif",
    "public.heic": ".heic",
    "public.heif": ".heif",
    "public.jpeg": ".jpg",
    "public.movie": ".mov",
    "public.mpeg-4": ".mp4",
    "public.png": ".png",
    "public.tiff": ".tiff",
}
CLOUDKIT_REFRESHABLE_DOWNLOAD_ERRORS = {
    "asset_download_http_401",
    "asset_download_http_403",
    "asset_download_http_404",
    "asset_download_http_410",
}


class IngestError(RuntimeError):
    """A safe-to-classify ingest failure."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    final_url: str


@dataclass(frozen=True)
class PendingAsset:
    guid: str
    checksum: str
    stem: str
    media_asset_type: str
    download_url: str | None = None


@dataclass(frozen=True)
class CloudKitShare:
    partition_url: str
    auth_token: str
    sharing_key: str
    zone_id: Mapping[str, object]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log_value(value: object) -> str:
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", text):
        return text
    return json.dumps(text, ensure_ascii=True)


def log(level: str, album: str, event: str, **fields: object) -> None:
    parts = [iso_now(), f"level={level}", f"album={log_value(album)}", f"event={event}"]
    parts.extend(f"{key}={log_value(value)}" for key, value in fields.items())
    stream = sys.stderr if level == "error" else sys.stdout
    print(" ".join(parts), file=stream, flush=True)


def classify_http_error(error: BaseException) -> str:
    if isinstance(error, HTTPError):
        return f"http_{error.code}"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, URLError):
        return "url_error"
    return "io_error"


def http_request(
    request: Request,
    album: str,
    operation: str,
    allowed_statuses: Sequence[int] = (),
) -> HttpResponse:
    last_error: BaseException | None = None
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return HttpResponse(
                    status=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                    final_url=response.geturl(),
                )
        except HTTPError as error:
            if error.code in allowed_statuses:
                return HttpResponse(
                    status=error.code,
                    body=error.read(),
                    headers=dict(error.headers.items()),
                    final_url=error.geturl(),
                )
            last_error = error
        except (OSError, TimeoutError, URLError) as error:
            last_error = error

        if attempt < HTTP_ATTEMPTS:
            log(
                "warning",
                album,
                "http_retry",
                operation=operation,
                attempt=attempt + 1,
                reason=classify_http_error(last_error),
            )
            time.sleep(1)

    raise IngestError(f"{operation}_{classify_http_error(last_error or OSError())}")


def post_json(
    url: str,
    payload: Mapping[str, object],
    album: str,
    operation: str,
    headers: Mapping[str, str] = ICLOUD_HEADERS,
) -> tuple[HttpResponse, dict]:
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    response = http_request(request, album, operation, allowed_statuses=(330,))
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IngestError(f"{operation}_invalid_json") from error
    if not isinstance(decoded, dict):
        raise IngestError(f"{operation}_invalid_payload")
    return response, decoded


def extract_token(url: str) -> str | None:
    parsed = urlparse(url)
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    token = None
    # share.icloud.com/photos/<token> and photos.icloud.com/shared/album/<token>
    # both put the token immediately after a known key. "album" catches the
    # newer /shared/album/<token> shape Apple generates for Shared Albums today.
    for key in ("photos", "album"):
        if key in path_parts:
            index = path_parts.index(key)
            if index + 1 < len(path_parts):
                token = path_parts[index + 1]
                break
    if not token and parsed.fragment:
        token = unquote(parsed.fragment).strip("/").split("/")[-1]
    if token and re.fullmatch(r"[0-9A-Za-z;_-]{3,256}", token):
        return token
    return None


def token_from_html(body: bytes) -> str | None:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    patterns = (
        r"(?:sharedalbum/)?#([0-9A-Za-z;_-]{3,256})",
        r'"(?:streamId|streamID|streamToken)"\s*:\s*"([0-9A-Za-z;_-]{3,256})"',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def slugify_token(token: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", token.lower()).strip("-")
    return slug or "album"


def album_log_id(token: str) -> str:
    return f"album-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]}"


def validate_share_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_SHARE_HOSTS:
        raise IngestError("invalid_public_share_url")


def is_cloudkit_share_url(url: str) -> bool:
    parsed = urlparse(url)
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    return (parsed.hostname or "").lower() == "photos.icloud.com" and path_parts[:2] == ["shared", "album"]


def resolve_token(share_url: str, album: str) -> str:
    validate_share_url(share_url)
    request = Request(share_url, headers={"User-Agent": USER_AGENT}, method="GET")
    response = http_request(request, album, "share_page")
    token = extract_token(response.final_url) or token_from_html(response.body) or extract_token(share_url)
    if not token:
        raise IngestError("share_token_not_found")
    return token


def base62_to_int(value: str) -> int:
    result = 0
    for character in value:
        try:
            digit = BASE62.index(character)
        except ValueError as error:
            raise IngestError("share_token_invalid") from error
        result = result * 62 + digit
    return result


def partition_host(token: str) -> str:
    partition_token = token.split(";", 1)[0]
    if len(partition_token) < 2:
        raise IngestError("share_token_invalid")
    encoded = partition_token[1] if partition_token[0] == "A" else partition_token[1:3]
    if not encoded:
        raise IngestError("share_token_invalid")
    partition = base62_to_int(encoded)
    return f"p{partition:02d}-sharedstreams.icloud.com"


def redirected_host(response: HttpResponse, payload: Mapping[str, object]) -> str | None:
    candidate = payload.get("X-Apple-MMe-Host")
    if not isinstance(candidate, str):
        candidate = next(
            (value for key, value in response.headers.items() if key.lower() == "x-apple-mme-host"),
            None,
        )
    if candidate is None:
        return None
    candidate = candidate.lower().rstrip(".")
    if not re.fullmatch(r"p[0-9]+-sharedstreams\.icloud\.com", candidate):
        raise IngestError("webstream_redirect_invalid")
    return candidate


def api_url(host: str, token: str, endpoint: str) -> str:
    return f"https://{host}/{token}/sharedstreams/{endpoint}"


def cloudkit_headers(share_url: str) -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Cache-Control": "no-cache",
        "Content-Type": "text/plain",
        "Origin": "https://photos.icloud.com",
        "Referer": share_url,
        "User-Agent": USER_AGENT,
    }


def cloudkit_share(share_url: str, token: str, album: str) -> CloudKitShare:
    query = urlencode(
        {
            "remapEnums": "true",
            "getCurrentSyncToken": "true",
            "sharing_url_key": token,
        }
    )
    response, payload = post_json(
        f"{CLOUDKIT_RESOLVE_URL}?{query}",
        {"shortGUIDs": [{"value": token}]},
        album,
        "cloudkit_resolve",
        cloudkit_headers(share_url),
    )
    results = payload.get("results")
    if response.status != 200 or not isinstance(results, list) or len(results) != 1:
        raise IngestError("cloudkit_resolve_invalid")
    result = results[0]
    if not isinstance(result, dict):
        raise IngestError("cloudkit_resolve_invalid")

    anonymous = result.get("anonymousPublicAccess")
    share = result.get("share")
    zone_id = result.get("zoneID")
    if not isinstance(anonymous, dict) or not isinstance(share, dict) or not isinstance(zone_id, dict):
        raise IngestError("cloudkit_resolve_invalid")
    stable_url = share.get("stableUrl")
    if not isinstance(stable_url, dict):
        raise IngestError("cloudkit_resolve_invalid")

    partition_url = anonymous.get("databasePartition")
    auth_token = anonymous.get("token")
    sharing_key = stable_url.get("encryptedPublicSharingKey")
    if not all(isinstance(value, str) and value for value in (partition_url, auth_token, sharing_key)):
        raise IngestError("cloudkit_resolve_invalid")
    parsed_partition = urlparse(str(partition_url))
    partition_host = (parsed_partition.hostname or "").lower()
    if (
        parsed_partition.scheme != "https"
        or parsed_partition.path not in {"", "/"}
        or not re.fullmatch(r"(?:p[0-9]+-)?ckdatabasews\.icloud\.com", partition_host)
    ):
        raise IngestError("cloudkit_partition_invalid")
    for key in ("ownerRecordName", "zoneName", "zoneType"):
        if not isinstance(zone_id.get(key), str) or not zone_id[key]:
            raise IngestError("cloudkit_zone_invalid")
    return CloudKitShare(
        partition_url=str(partition_url).rstrip("/"),
        auth_token=str(auth_token),
        sharing_key=str(sharing_key),
        zone_id=dict(zone_id),
    )


def cloudkit_query_url(context: CloudKitShare) -> str:
    query = urlencode(
        {
            "remapEnums": "true",
            "getCurrentSyncToken": "true",
            "publicAccessAuthToken": context.auth_token,
            "sharing_url_key": context.sharing_key,
        }
    )
    return f"{context.partition_url}{CLOUDKIT_DATABASE_PATH}?{query}"


def cloudkit_field(fields: Mapping[str, object], name: str) -> object:
    field = fields.get(name)
    return field.get("value") if isinstance(field, dict) else None


def cloudkit_timestamp(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise IngestError("asset_creation_date_missing")
    timestamp = float(value)
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        parsed = datetime.fromtimestamp(timestamp, timezone.utc)
    except (OSError, OverflowError, ValueError) as error:
        raise IngestError("asset_creation_date_invalid") from error
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def cloudkit_resource(fields: Mapping[str, object]) -> Mapping[str, object]:
    candidates: list[tuple[int, Mapping[str, object]]] = []
    for name, field in fields.items():
        if not isinstance(field, dict) or field.get("type") != "ASSETID":
            continue
        value = field.get("value")
        if not isinstance(value, dict) or not isinstance(value.get("downloadURL"), str):
            continue
        preference = 2 if name == "resOriginalRes" else 1
        candidates.append((preference, value))
    if not candidates:
        raise IngestError("asset_derivative_missing")
    return max(candidates, key=lambda candidate: (candidate[0], read_int(candidate[1].get("size"))))[1]


def cloudkit_pending_asset(asset: Mapping[str, object], master: Mapping[str, object]) -> PendingAsset:
    guid_value = asset.get("recordName")
    guid = safe_guid(guid_value)
    asset_fields = asset.get("fields")
    master_fields = master.get("fields")
    if not isinstance(asset_fields, dict) or not isinstance(master_fields, dict):
        raise IngestError("asset_metadata_invalid")
    resource = cloudkit_resource(master_fields)
    download_url = resource.get("downloadURL")
    parsed_url = urlparse(download_url) if isinstance(download_url, str) else None
    hostname = (parsed_url.hostname or "").lower() if parsed_url else ""
    if not parsed_url or parsed_url.scheme != "https" or not (
        hostname == "icloud-content.com" or hostname.endswith(".icloud-content.com")
    ):
        raise IngestError("asset_download_url_invalid")
    checksum = resource.get("fileChecksum") or resource.get("referenceChecksum")
    if not isinstance(checksum, str) or not checksum:
        raise IngestError("asset_checksum_missing")
    creation_date = cloudkit_field(asset_fields, "assetDate")
    if creation_date is None:
        creation_date = cloudkit_field(master_fields, "originalCreationDate")
    media_asset_type = cloudkit_field(master_fields, "itemType")
    return PendingAsset(
        guid=str(guid_value),
        checksum=checksum,
        stem=f"{cloudkit_timestamp(creation_date)}-{guid}",
        media_asset_type=str(media_asset_type or "image"),
        download_url=str(download_url),
    )


def fetch_cloudkit_assets(
    share_url: str,
    token: str,
    album: str,
) -> tuple[list[PendingAsset], int, int]:
    context = cloudkit_share(share_url, token, album)
    query_url = cloudkit_query_url(context)
    base_payload: dict[str, object] = {
        "query": {
            "recordType": "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted",
            "filterBy": [
                {
                    "fieldName": "direction",
                    "comparator": "EQUALS",
                    "fieldValue": {"value": "DESCENDING", "type": "STRING"},
                }
            ],
        },
        "zoneID": context.zone_id,
        "resultsLimit": CLOUDKIT_QUERY_RESULTS_LIMIT,
    }
    masters: dict[str, Mapping[str, object]] = {}
    assets: list[Mapping[str, object]] = []
    marker: str | None = None
    seen_markers: set[str] = set()
    for page in range(1, CLOUDKIT_MAX_PAGES + 1):
        request_payload = dict(base_payload)
        if marker:
            request_payload["continuationMarker"] = marker
        response, payload = post_json(
            query_url,
            request_payload,
            album,
            "cloudkit_query",
            cloudkit_headers(share_url),
        )
        records = payload.get("records")
        if response.status != 200 or not isinstance(records, list):
            raise IngestError("cloudkit_query_invalid")
        for record in records:
            if not isinstance(record, dict):
                continue
            record_name = record.get("recordName")
            if record.get("recordType") == "CPLMaster" and isinstance(record_name, str):
                masters[record_name] = record
            elif record.get("recordType") == "CPLAsset":
                assets.append(record)
        next_marker = payload.get("continuationMarker")
        log("info", album, "cloudkit_page", page=page, records=len(records), more=bool(next_marker))
        if next_marker is None:
            break
        if not isinstance(next_marker, str) or not next_marker or next_marker in seen_markers:
            raise IngestError("cloudkit_continuation_invalid")
        seen_markers.add(next_marker)
        marker = next_marker
    else:
        raise IngestError("cloudkit_page_limit")

    pending: list[PendingAsset] = []
    failures = 0
    for asset in assets:
        asset_fields = asset.get("fields")
        master_ref = cloudkit_field(asset_fields, "masterRef") if isinstance(asset_fields, dict) else None
        master_name = master_ref.get("recordName") if isinstance(master_ref, dict) else None
        master = masters.get(master_name) if isinstance(master_name, str) else None
        if master is None:
            failures += 1
            log("error", album, "asset_skipped", reason="cloudkit_master_missing")
            continue
        try:
            pending.append(cloudkit_pending_asset(asset, master))
        except IngestError as error:
            failures += 1
            log("error", album, "asset_skipped", reason=str(error))
    return pending, len(assets), failures


def fetch_webstream(token: str, album: str) -> tuple[str, dict]:
    host = partition_host(token)
    response, payload = post_json(
        api_url(host, token, "webstream"),
        {"streamCtag": None},
        album,
        "webstream",
    )
    redirect = redirected_host(response, payload)
    if response.status == 330 or redirect:
        if not redirect:
            raise IngestError("webstream_redirect_missing")
        host = redirect
        response, payload = post_json(
            api_url(host, token, "webstream"),
            {"streamCtag": None},
            album,
            "webstream_redirect",
        )
    if response.status != 200:
        raise IngestError(f"webstream_http_{response.status}")
    if not isinstance(payload.get("photos", []), list):
        raise IngestError("webstream_photos_invalid")
    return host, payload


def read_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def choose_derivative(photo: Mapping[str, object]) -> Mapping[str, object]:
    derivatives = photo.get("derivatives")
    if isinstance(derivatives, dict):
        candidates = list(derivatives.values())
    elif isinstance(derivatives, list):
        candidates = derivatives
    else:
        candidates = []
    valid = [candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("checksum")]
    if not valid:
        raise IngestError("asset_derivative_missing")
    return max(
        valid,
        key=lambda derivative: (
            read_int(derivative.get("fileSize")),
            read_int(derivative.get("width")) * read_int(derivative.get("height")),
        ),
    )


def compact_creation_date(photo: Mapping[str, object]) -> str:
    raw_value = next(
        (
            photo.get(key)
            for key in ("creationDate", "dateCreated", "batchDateCreated")
            if photo.get(key)
        ),
        None,
    )
    if not isinstance(raw_value, str):
        raise IngestError("asset_creation_date_missing")
    value = raw_value.strip()
    parsed = None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for date_format in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(value, date_format)
                break
            except ValueError:
                continue
    if parsed is None:
        raise IngestError("asset_creation_date_invalid")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_guid(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise IngestError("asset_guid_missing")
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-.")
    if not cleaned:
        raise IngestError("asset_guid_invalid")
    return cleaned


def pending_asset(photo: Mapping[str, object]) -> PendingAsset:
    guid_value = photo.get("photoGuid") or photo.get("assetGuid")
    guid = safe_guid(guid_value)
    derivative = choose_derivative(photo)
    checksum = derivative.get("checksum")
    if not isinstance(checksum, str) or not checksum:
        raise IngestError("asset_checksum_missing")
    return PendingAsset(
        guid=str(guid_value),
        checksum=checksum,
        stem=f"{compact_creation_date(photo)}-{guid}",
        media_asset_type=str(photo.get("mediaAssetType") or "image"),
    )


def atomic_write_json(path: Path, payload: Mapping[str, object], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "assets": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IngestError("state_invalid") from error
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("assets"), dict):
        raise IngestError("state_invalid")
    return payload


def fetch_asset_urls(host: str, token: str, assets: Sequence[PendingAsset], album: str) -> dict[str, str]:
    urls: dict[str, str] = {}
    for offset in range(0, len(assets), ASSET_BATCH_SIZE):
        batch = assets[offset : offset + ASSET_BATCH_SIZE]
        response, payload = post_json(
            api_url(host, token, "webasseturls"),
            {"photoGuids": [asset.guid for asset in batch]},
            album,
            "webasseturls",
        )
        if response.status != 200 or not isinstance(payload.get("items"), dict):
            raise IngestError("webasseturls_invalid")
        for checksum, item in payload["items"].items():
            if not isinstance(checksum, str) or not isinstance(item, dict):
                continue
            location = item.get("url_location")
            path = item.get("url_path")
            if not isinstance(location, str) or not isinstance(path, str):
                continue
            candidate = f"https://{location}{path}"
            parsed = urlparse(candidate)
            if parsed.scheme == "https" and parsed.hostname:
                urls[checksum] = candidate
    return urls


def extension_for(content_type: str | None, media_asset_type: str) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[normalized]
    media_extension = MEDIA_ASSET_TYPE_EXTENSIONS.get(media_asset_type.lower())
    if media_extension:
        return media_extension
    guessed = mimetypes.guess_extension(normalized) if normalized else None
    if guessed:
        return ".jpg" if guessed in {".jpe", ".jpeg"} else guessed
    if "video" in media_asset_type.lower():
        return ".mov"
    return ".jpg"


def download_asset(url: str, inbox: Path, asset: PendingAsset, album: str) -> Path:
    request = Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    last_error: BaseException | None = None
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        descriptor, temporary_name = tempfile.mkstemp(dir=inbox, prefix=f".{asset.stem}.", suffix=".part")
        temporary_path = Path(temporary_name)
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                content_type = response.headers.get("Content-Type")
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            destination = inbox / f"{asset.stem}{extension_for(content_type, asset.media_asset_type)}"
            os.replace(temporary_path, destination)
            os.chmod(destination, 0o644)
            return destination
        except (HTTPError, OSError, TimeoutError, URLError) as error:
            last_error = error
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            if isinstance(error, HTTPError) and error.code in {401, 403, 404, 410}:
                break
            if attempt < HTTP_ATTEMPTS:
                log(
                    "warning",
                    album,
                    "http_retry",
                    operation="asset_download",
                    attempt=attempt + 1,
                    reason=classify_http_error(error),
                )
                time.sleep(1)
                continue
            break
    raise IngestError(f"asset_download_{classify_http_error(last_error or OSError())}")


def existing_download(inbox: Path, stem: str) -> Path | None:
    candidates = [path for path in inbox.glob(f"{stem}.*") if path.is_file() and not path.name.endswith(".part")]
    return sorted(candidates)[0] if candidates else None


def album_inbox(album_config: Mapping[str, object], token: str) -> Path:
    configured = album_config.get("inboxDir")
    if configured is None:
        return Path(f"~/Pictures/openclaw-icloud-inbox/{slugify_token(token)}").expanduser()
    if not isinstance(configured, str) or not configured.strip():
        raise IngestError("inbox_dir_invalid")
    return Path(os.path.expandvars(configured)).expanduser()


def ingest_album(album_config: Mapping[str, object], initial_slug: str) -> bool:
    share_url = album_config.get("url")
    if not isinstance(share_url, str) or not share_url.strip():
        raise IngestError("album_url_missing")
    share_url = share_url.strip()
    token = resolve_token(share_url, initial_slug)
    slug = album_log_id(token)
    inbox = album_inbox(album_config, token)
    inbox.mkdir(parents=True, exist_ok=True, mode=0o755)
    state_path = inbox / STATE_FILENAME
    state_exists = state_path.exists()
    state = load_state(state_path)
    assets_state = state["assets"]

    host: str | None = None
    candidates: list[PendingAsset] = []
    discovered = 0
    failures = 0
    if is_cloudkit_share_url(share_url):
        candidates, discovered, failures = fetch_cloudkit_assets(share_url, token, slug)
    else:
        host, stream = fetch_webstream(token, slug)
        photos = stream.get("photos", [])
        discovered = len(photos)
        for photo in photos:
            if not isinstance(photo, dict):
                failures += 1
                log("error", slug, "asset_skipped", reason="asset_metadata_invalid")
                continue
            try:
                candidates.append(pending_asset(photo))
            except IngestError as error:
                failures += 1
                log("error", slug, "asset_skipped", reason=str(error))

    pending: list[PendingAsset] = []
    recovered = 0

    for asset in candidates:
        if asset.guid in assets_state:
            continue
        existing = existing_download(inbox, asset.stem)
        if existing:
            assets_state[asset.guid] = {"downloadedAt": iso_now(), "filename": existing.name, "recovered": True}
            atomic_write_json(state_path, state)
            recovered += 1
            log("info", slug, "asset_reconciled", guid=asset.guid, filename=existing.name)
            continue
        pending.append(asset)

    cloudkit_mode = is_cloudkit_share_url(share_url)
    if cloudkit_mode:
        signed_urls = {asset.guid: asset.download_url for asset in pending if asset.download_url}
    else:
        signed_urls = fetch_asset_urls(host, token, pending, slug) if pending and host else {}
    downloaded = 0
    cloudkit_refreshes = 0
    for index, asset in enumerate(pending):
        destination: Path | None = None
        failure_reason = "signed_url_missing"
        while True:
            signed_url = signed_urls.get(asset.guid if cloudkit_mode else asset.checksum)
            if signed_url:
                try:
                    destination = download_asset(signed_url, inbox, asset, slug)
                    break
                except (IngestError, OSError) as error:
                    failure_reason = str(error) if isinstance(error, IngestError) else "state_write_failed"
            if (
                not cloudkit_mode
                or failure_reason not in CLOUDKIT_REFRESHABLE_DOWNLOAD_ERRORS
                or cloudkit_refreshes >= CLOUDKIT_URL_REFRESH_LIMIT
            ):
                break
            try:
                refreshed_assets, _, refresh_failures = fetch_cloudkit_assets(share_url, token, slug)
            except (IngestError, OSError) as error:
                failure_reason = str(error) if isinstance(error, IngestError) else "cloudkit_refresh_failed"
                break
            if refresh_failures:
                failure_reason = "cloudkit_refresh_invalid"
                break
            refreshed_urls = {
                refreshed.guid: refreshed.download_url
                for refreshed in refreshed_assets
                if refreshed.download_url
            }
            signed_urls.update(
                {
                    remaining.guid: refreshed_urls[remaining.guid]
                    for remaining in pending[index:]
                    if remaining.guid in refreshed_urls
                }
            )
            cloudkit_refreshes += 1
            log(
                "info",
                slug,
                "cloudkit_urls_refreshed",
                refresh=cloudkit_refreshes,
                remaining=len(pending) - index,
            )
            if asset.guid not in signed_urls:
                failure_reason = "signed_url_missing"
                break

        if destination is None:
            failures += 1
            log("error", slug, "asset_failed", guid=asset.guid, reason=failure_reason)
            continue
        try:
            assets_state[asset.guid] = {"downloadedAt": iso_now(), "filename": destination.name}
            atomic_write_json(state_path, state)
        except OSError:
            failures += 1
            log("error", slug, "asset_failed", guid=asset.guid, reason="state_write_failed")
            continue
        downloaded += 1
        log("info", slug, "asset_downloaded", guid=asset.guid, filename=destination.name)

    if not state_exists and not state_path.exists():
        atomic_write_json(state_path, state)
    log(
        "info" if failures == 0 else "error",
        slug,
        "album_complete",
        discovered=discovered,
        downloaded=downloaded,
        recovered=recovered,
        failed=failures,
    )
    return failures == 0


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        atomic_write_json(config_path, {"albums": []})
        log("info", "-", "config_created", path=config_path)
    else:
        os.chmod(config_path, 0o600)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IngestError("config_invalid") from error
    if not isinstance(config, dict) or not isinstance(config.get("albums"), list):
        raise IngestError("config_invalid")
    return config


def run(config_path: Path) -> int:
    config_path = config_path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = config_path.parent / ".ingest.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        os.fchmod(lock_file.fileno(), 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("info", "-", "already_running", message="another ingest run is active")
            return 0

        try:
            config = load_config(config_path)
        except IngestError as error:
            log("error", "-", "config_error", reason=str(error))
            return 1
        albums = config["albums"]
        if not albums:
            log("info", "-", "no_albums", message="no albums configured")
            return 0

        failures = 0
        for index, album_config in enumerate(albums, start=1):
            initial_slug = f"album-{index}"
            if isinstance(album_config, dict):
                configured_url = album_config.get("url")
                if isinstance(configured_url, str):
                    configured_token = extract_token(configured_url)
                    if configured_token:
                        initial_slug = album_log_id(configured_token)
            else:
                album_config = {}
            try:
                if not ingest_album(album_config, initial_slug):
                    failures += 1
            except (IngestError, OSError) as error:
                failures += 1
                reason = str(error) if isinstance(error, IngestError) else "filesystem_error"
                log("error", initial_slug, "album_failed", reason=reason)
        return 1 if failures else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="config path (default: ~/.openclaw/icloud-shared-album-ingest/config.json)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv).config)


if __name__ == "__main__":
    sys.exit(main())
