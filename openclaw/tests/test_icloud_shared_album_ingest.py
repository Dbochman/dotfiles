"""Regression tests for the public iCloud Shared Album ingest service."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ingest = load_script("icloud_shared_album_ingest", "icloud-shared-album-ingest.py")


class FakeResponse:
    def __init__(self, status, body, url, headers=None):
        self.status = status
        self._body = BytesIO(body)
        self._url = url
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False

    def geturl(self):
        return self._url

    def read(self, size=-1):
        return self._body.read(size)


class AlbumFixture:
    TOKEN = "A1FixtureToken"
    SHARE_URL = f"https://share.icloud.com/photos/{TOKEN}"
    REDIRECT_HOST = "p42-sharedstreams.icloud.com"

    def __init__(self):
        self.calls = []
        self.downloads = 0
        self.webasseturls = 0

    def __call__(self, request, timeout):
        self.calls.append((request.get_method(), request.full_url, timeout))
        url = request.full_url
        if url == self.SHARE_URL:
            return FakeResponse(200, b"<html>public album</html>", url)
        if url.endswith("/sharedstreams/webstream") and self.REDIRECT_HOST not in url:
            body = json.dumps({"X-Apple-MMe-Host": self.REDIRECT_HOST}).encode("utf-8")
            return FakeResponse(330, body, url)
        if url.endswith("/sharedstreams/webstream"):
            body = json.dumps(
                {
                    "photos": [
                        {
                            "photoGuid": "guid-photo",
                            "creationDate": "2026-10-11T14:15:16Z",
                            "mediaAssetType": "image",
                            "derivatives": {
                                "small": {
                                    "checksum": "checksum-small",
                                    "fileSize": "4",
                                    "width": "2",
                                    "height": "2",
                                },
                                "large": {
                                    "checksum": "checksum-photo",
                                    "fileSize": "10",
                                    "width": "4",
                                    "height": "3",
                                },
                            },
                        },
                        {
                            "photoGuid": "guid-png",
                            "dateCreated": "2026-10-10T10:09:08-04:00",
                            "mediaAssetType": "image",
                            "derivatives": {
                                "original": {
                                    "checksum": "checksum-png",
                                    "fileSize": "9",
                                    "width": "3",
                                    "height": "3",
                                }
                            },
                        },
                    ]
                }
            ).encode("utf-8")
            return FakeResponse(200, body, url)
        if url.endswith("/sharedstreams/webasseturls"):
            self.webasseturls += 1
            payload = json.loads(request.data.decode("utf-8"))
            if payload != {"photoGuids": ["guid-photo", "guid-png"]}:
                raise AssertionError(f"unexpected asset request: {payload}")
            body = json.dumps(
                {
                    "items": {
                        "checksum-photo": {
                            "url_location": "cdn.example",
                            "url_path": "/photo?signed=redacted",
                        },
                        "checksum-png": {
                            "url_location": "cdn.example",
                            "url_path": "/png?signed=redacted",
                        },
                    }
                }
            ).encode("utf-8")
            return FakeResponse(200, body, url)
        if url.startswith("https://cdn.example/photo"):
            self.downloads += 1
            return FakeResponse(200, b"jpeg fixture", url, {"Content-Type": "image/jpeg"})
        if url.startswith("https://cdn.example/png"):
            self.downloads += 1
            return FakeResponse(200, b"png fixture", url, {"Content-Type": "image/png; charset=binary"})
        raise AssertionError(f"unexpected URL: {url}")


class CloudKitAlbumFixture:
    TOKEN = "0CloudKitFixtureToken"
    SHARE_URL = f"https://photos.icloud.com/shared/album/{TOKEN}"
    PARTITION_URL = "https://p113-ckdatabasews.icloud.com"
    ZONE_ID = {
        "ownerRecordName": "owner-fixture",
        "zoneName": "zone-fixture",
        "zoneType": "REGULAR_CUSTOM_ZONE",
    }

    def __init__(self):
        self.downloads = 0
        self.resolve_calls = 0
        self.query_calls = 0

    @staticmethod
    def records(guid, master_name, timestamp, item_type, download_url, checksum):
        return [
            {
                "recordName": master_name,
                "recordType": "CPLMaster",
                "fields": {
                    "originalCreationDate": {"type": "TIMESTAMP", "value": timestamp},
                    "itemType": {"type": "STRING", "value": item_type},
                    "resOriginalRes": {
                        "type": "ASSETID",
                        "value": {
                            "downloadURL": download_url,
                            "fileChecksum": checksum,
                            "size": 20,
                        },
                    },
                },
            },
            {
                "recordName": guid,
                "recordType": "CPLAsset",
                "fields": {
                    "assetDate": {"type": "TIMESTAMP", "value": timestamp},
                    "masterRef": {
                        "type": "REFERENCE",
                        "value": {"recordName": master_name},
                    },
                },
            },
        ]

    def __call__(self, request, timeout):
        self.assert_timeout(timeout)
        url = request.full_url
        if url == self.SHARE_URL:
            return FakeResponse(200, b"<html>public album</html>", url)
        if "/production/public/records/resolve?" in url:
            self.resolve_calls += 1
            payload = json.loads(request.data.decode("utf-8"))
            if payload != {"shortGUIDs": [{"value": self.TOKEN}]}:
                raise AssertionError(f"unexpected resolve request: {payload}")
            body = json.dumps(
                {
                    "results": [
                        {
                            "anonymousPublicAccess": {
                                "databasePartition": self.PARTITION_URL,
                                "token": "temporary-public-access-token",
                            },
                            "share": {
                                "stableUrl": {
                                    "encryptedPublicSharingKey": "encrypted-sharing-key",
                                }
                            },
                            "zoneID": self.ZONE_ID,
                        }
                    ]
                }
            ).encode("utf-8")
            return FakeResponse(200, body, url)
        if "/production/shared/records/query?" in url:
            self.query_calls += 1
            payload = json.loads(request.data.decode("utf-8"))
            if payload.get("zoneID") != self.ZONE_ID or payload.get("resultsLimit") != 200:
                raise AssertionError(f"unexpected query request: {payload}")
            marker = payload.get("continuationMarker")
            if marker is None:
                records = self.records(
                    "cloud-photo",
                    "master-photo",
                    1_600_000_000_000,
                    "public.jpeg",
                    f"https://cvws-h2.icloud-content.com/photo?generation={self.resolve_calls}",
                    "cloud-checksum-photo",
                )
                body = {"records": records, "continuationMarker": "fixture-next-page"}
            elif marker == "fixture-next-page":
                records = self.records(
                    "cloud-video",
                    "master-video",
                    1_600_086_400_000,
                    "public.movie",
                    f"https://cvws-h2.icloud-content.com/video?generation={self.resolve_calls}",
                    "cloud-checksum-video",
                )
                body = {"records": records}
            else:
                raise AssertionError(f"unexpected continuation marker: {marker}")
            return FakeResponse(200, json.dumps(body).encode("utf-8"), url)
        if url.startswith("https://cvws-h2.icloud-content.com/photo?"):
            self.downloads += 1
            return FakeResponse(200, b"cloud jpeg fixture", url, {"Content-Type": "application/octet-stream"})
        if url.endswith("/video?generation=1"):
            raise HTTPError(url, 410, "Gone", {}, None)
        if url.startswith("https://cvws-h2.icloud-content.com/video?"):
            self.downloads += 1
            return FakeResponse(200, b"cloud movie fixture", url, {"Content-Type": "application/octet-stream"})
        raise AssertionError(f"unexpected URL: {url}")

    @staticmethod
    def assert_timeout(timeout):
        if timeout != 20:
            raise AssertionError(f"unexpected timeout: {timeout}")


class ICloudSharedAlbumIngestTests(unittest.TestCase):
    def test_media_type_extension_fallbacks_cover_cloudkit_originals(self):
        self.assertEqual(ingest.extension_for("application/octet-stream", "com.adobe.raw-image"), ".dng")
        self.assertEqual(ingest.extension_for(None, "public.heic"), ".heic")
        self.assertEqual(ingest.extension_for(None, "com.apple.quicktime-movie"), ".mov")

    def test_missing_config_is_created_empty_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(ingest.run(config_path), 0)

            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), {"albums": []})
            self.assertIn("event=no_albums", output.getvalue())
            self.assertIn("no albums configured", output.getvalue())

    def test_one_album_downloads_current_assets_once(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inbox = root / "inbox"
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"albums": [{"url": AlbumFixture.SHARE_URL, "inboxDir": str(inbox)}]}),
                encoding="utf-8",
            )
            fixture = AlbumFixture()
            first_output = StringIO()
            first_error = StringIO()
            with (
                patch.object(ingest, "urlopen", side_effect=fixture),
                redirect_stdout(first_output),
                redirect_stderr(first_error),
            ):
                self.assertEqual(ingest.run(config_path), 0)

            self.assertEqual(first_error.getvalue(), "")
            self.assertEqual(fixture.downloads, 2)
            self.assertEqual(fixture.webasseturls, 1)
            self.assertEqual(
                sorted(path.name for path in inbox.iterdir()),
                [
                    ".icloud-shared-album-ingest.state.json",
                    "20261010T140908Z-guid-png.png",
                    "20261011T141516Z-guid-photo.jpg",
                ],
            )
            state = json.loads((inbox / ingest.STATE_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(set(state["assets"]), {"guid-photo", "guid-png"})
            self.assertIn("event=album_complete discovered=2 downloaded=2 recovered=0 failed=0", first_output.getvalue())

            second_output = StringIO()
            with patch.object(ingest, "urlopen", side_effect=fixture), redirect_stdout(second_output):
                self.assertEqual(ingest.run(config_path), 0)

            self.assertEqual(fixture.downloads, 2)
            self.assertEqual(fixture.webasseturls, 1)
            self.assertIn(
                "event=album_complete discovered=2 downloaded=0 recovered=0 failed=0",
                second_output.getvalue(),
            )

    def test_new_shared_album_uses_cloudkit_pagination_and_downloads_once(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inbox = root / "inbox"
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {"albums": [{"url": CloudKitAlbumFixture.SHARE_URL, "inboxDir": str(inbox)}]}
                ),
                encoding="utf-8",
            )
            fixture = CloudKitAlbumFixture()
            first_output = StringIO()
            with patch.object(ingest, "urlopen", side_effect=fixture), redirect_stdout(first_output):
                self.assertEqual(ingest.run(config_path), 0)

            self.assertEqual(fixture.resolve_calls, 2)
            self.assertEqual(fixture.query_calls, 4)
            self.assertEqual(fixture.downloads, 2)
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                sorted(path.name for path in inbox.iterdir()),
                [
                    ".icloud-shared-album-ingest.state.json",
                    "20200913T122640Z-cloud-photo.jpg",
                    "20200914T122640Z-cloud-video.mov",
                ],
            )
            self.assertNotIn(CloudKitAlbumFixture.TOKEN, first_output.getvalue())
            self.assertIn("event=cloudkit_page page=2 records=2 more=False", first_output.getvalue())
            self.assertIn("event=cloudkit_urls_refreshed refresh=1 remaining=1", first_output.getvalue())
            self.assertIn(
                "event=album_complete discovered=2 downloaded=2 recovered=0 failed=0",
                first_output.getvalue(),
            )

            second_output = StringIO()
            with patch.object(ingest, "urlopen", side_effect=fixture), redirect_stdout(second_output):
                self.assertEqual(ingest.run(config_path), 0)

            self.assertEqual(fixture.downloads, 2)
            self.assertIn(
                "event=album_complete discovered=2 downloaded=0 recovered=0 failed=0",
                second_output.getvalue(),
            )

    def test_http_call_uses_twenty_second_timeout_and_one_retry(self):
        attempts = 0

        def flaky_open(request, timeout):
            nonlocal attempts
            attempts += 1
            self.assertEqual(timeout, 20)
            if attempts == 1:
                raise URLError("fixture failure")
            return FakeResponse(200, b"ok", request.full_url)

        output = StringIO()
        with (
            patch.object(ingest, "urlopen", side_effect=flaky_open),
            patch.object(ingest.time, "sleep"),
            redirect_stdout(output),
        ):
            response = ingest.http_request(Request("https://example.test"), "fixture", "test_call")

        self.assertEqual(response.body, b"ok")
        self.assertEqual(attempts, 2)
        self.assertIn("event=http_retry operation=test_call attempt=2", output.getvalue())


if __name__ == "__main__":
    unittest.main()
