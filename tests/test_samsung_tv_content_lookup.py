"""Public content resolution tests; no network, credentials, or TV access."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.request import Request


spec = spec_from_file_location("content_lookup", Path(__file__).resolve().parents[1]
                              / "openclaw/skills/samsung-tv/scripts/content_lookup.py")
lookup = module_from_spec(spec)
spec.loader.exec_module(lookup)


def show_fixture():
    def season(label, entries):
        return {"node": {"numberLabelV2": label, "title": "Example",
                         "episodes": {"edges": [{"node": {"videoId": vid,
                             "number": number, "title": title}}
                             for vid, number, title in entries]}}}
    return {"__typename": "Show", "videoId": 123, "title": "Example",
            "latestYear": 2024, "isAvailable": True, "seasons": {"edges": [
                season("Season 1", [(903, 1, "Arrival"), (901, 2, "Forests")]),
                season("Season 2", [(905, 1, "Arrival")])]}}


def html_fixture(*items):
    return '<script>netflix.reactContext = ' + json.dumps({"videos": items}) + ';</script>'


class ContentResolutionTests(unittest.TestCase):
    def test_episode_uses_explicit_numbers_not_id_or_array_order(self):
        show = show_fixture()
        show["seasons"]["edges"].reverse()
        title = lookup.public_netflix_title(html_fixture(show), "123")
        self.assertEqual(lookup.select_episode(title, season=1, episode=2)["content_id"], "901")
        self.assertEqual(lookup.select_episode(title, episode_title="  FORESTS  ")["content_id"], "901")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            lookup.select_episode(title, episode_title="Arrival")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            lookup.select_episode(title, season=1, episode=2, episode_title="Arrival")
        with self.assertRaises(ValueError):
            lookup.select_episode(title, season=1)

    def test_missing_episode_never_falls_back_to_series(self):
        with patch.object(lookup, "fetch", return_value=html_fixture(show_fixture())):
            with self.assertRaises(ValueError):
                lookup.lookup_netflix("123", season=1, episode=99)

    def test_nonstandard_season_label_is_not_guessed(self):
        show = show_fixture()
        show["seasons"]["edges"][0]["node"]["numberLabelV2"] = "Limited Series"
        title = lookup.public_netflix_title(html_fixture(show), "123")
        with self.assertRaises(ValueError):
            lookup.select_episode(title, season=1, episode=2)
        result = lookup.select_episode(title, season_label="Limited Series", episode=2)
        self.assertEqual(result["content_id"], "901")
        self.assertIsNone(result["season"])

    def test_exact_title_identity_excludes_related_shows(self):
        unrelated = show_fixture()
        unrelated["videoId"] = 456
        self.assertEqual(lookup.public_netflix_title(
            html_fixture(unrelated, show_fixture()), "123")["content_id"], "123")
        with self.assertRaises(ValueError):
            lookup.public_netflix_title(html_fixture(unrelated), "123")
        conflicting = show_fixture()
        conflicting["title"] = "Different"
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            lookup.public_netflix_title(html_fixture(show_fixture(), conflicting), "123")

    def test_movie_and_script_escapes_without_execution(self):
        movie = {"__typename": "Movie", "videoId": 456,
                 "title": "Café & Forests", "latestYear": 2022}
        html = html_fixture(movie).replace(" & ", r"\x20\x26\x20")
        html += '<script>throw new Error("must never execute")</script>'
        result = lookup.public_netflix_title(html, "456")
        self.assertEqual(result["title"], "Café & Forests")
        self.assertEqual(result["episodes"], [])
        movie["title"] = r"Literal \x20"
        self.assertEqual(lookup.public_netflix_title(html_fixture(movie), "456")["title"], movie["title"])

    def test_changed_or_partial_metadata_fails(self):
        for raw in ("<html>sign in</html>", "netflix.reactContext = {", "netflix.reactContext = alert(1)"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                lookup.public_netflix_title(raw, "123")
        show = show_fixture()
        del show["seasons"]["edges"][0]["node"]["episodes"]["edges"][0]["node"]["number"]
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            lookup.public_netflix_title(html_fixture(show), "123")

    def test_provider_links_are_exact_and_normalized(self):
        self.assertEqual(lookup.netflix_id("https://www.netflix.com/gb/title/123?tracking=ignored"), "123")
        self.assertEqual(lookup.youtube_id("https://youtu.be/XmtXC_n6X6Q?t=20"), "XmtXC_n6X6Q")
        for value in ("https://www.netflix.com.evil.test/title/123", "http://netflix.com/title/123",
                      "https://user:password@netflix.com/title/123", "https://netflix.com:8080/title/123",
                      "https://netflix.com/title/123\n", "file:///tmp/private"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lookup.netflix_id(value)
        with self.assertRaises(ValueError):
            lookup.youtube_id("https://youtube.com/watch?v=XmtXC_n6X6Q&v=eyZEqSPeMwg")

    def test_oembed_returns_only_public_identity_not_html(self):
        data = {"type": "video", "provider_name": "YouTube", "title": "Example",
                "author_name": "Creator", "author_url": "https://www.youtube.com/@Creator",
                "html": "<iframe>untrusted embed</iframe>"}
        with patch.object(lookup, "fetch", return_value=json.dumps(data)):
            result = lookup.lookup_youtube("XmtXC_n6X6Q")
        self.assertEqual(result["channel"], "Creator")
        self.assertNotIn("html", result)
        with patch.object(lookup, "fetch", return_value='{}'), self.assertRaises(ValueError):
            lookup.lookup_youtube("XmtXC_n6X6Q")

    def test_fetch_size_limit_and_redirect_boundary(self):
        response = io.BytesIO(b"a" * 33)
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(lookup, "MAX_BYTES", 32), patch.object(lookup, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "size limit"):
                lookup.fetch("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS)
        handler = lookup.ProviderRedirects(lookup.NETFLIX_HOSTS)
        for target in ("http://www.netflix.com/title/123", "https://127.0.0.1/private", "https://example.com/"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(Request("https://www.netflix.com/title/123"), None, 302,
                                         "Found", {}, target)

    def test_truncated_response_retries_then_uses_one_complete_fallback(self):
        failure = lookup.IncompleteRead(b"partial metadata")
        with patch.object(lookup, "fetch_once", side_effect=[failure, "complete"]) as fetch:
            self.assertEqual(lookup.fetch("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS), "complete")
            self.assertEqual(fetch.call_count, 2)
        with patch.object(lookup, "fetch_once", side_effect=failure) as fetch, \
                patch.object(lookup, "fetch_curl", return_value="complete") as fallback:
            self.assertEqual(lookup.fetch("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS), "complete")
            self.assertEqual(fetch.call_count, 2)
            fallback.assert_called_once()

    def curl_response(self, body=b"complete", status=b"200\n", code=0):
        def run(args, **kwargs):
            self.assertEqual(args[:2], ["/usr/bin/curl", "-q"])
            self.assertNotIn("--location", args)
            self.assertIn("--max-filesize", args)
            self.assertLessEqual(kwargs["timeout"], 26)
            Path(args[args.index("--output") + 1]).write_bytes(body)
            return lookup.subprocess.CompletedProcess(args, code, stdout=status, stderr=b"private response")
        return run

    def test_curl_fallback_returns_only_complete_successful_body(self):
        with patch.object(lookup.subprocess, "run", side_effect=self.curl_response()):
            self.assertEqual(lookup.fetch_curl("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS), "complete")
        for code in (18, 28, 63):  # Partial response, timeout, size exceeded.
            with self.subTest(code=code), patch.object(lookup.subprocess, "run",
                    side_effect=self.curl_response(body=b"partial", code=code)) as run:
                with self.assertRaisesRegex(ValueError, "download failed"):
                    lookup.fetch_curl("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS)
                self.assertEqual(run.call_count, 1)

    def test_curl_fallback_checks_each_redirect_before_requesting_it(self):
        for target in ("https://evil.test/private", "http://www.netflix.com/title/123"):
            with self.subTest(target=target), patch.object(lookup.subprocess, "run",
                    side_effect=self.curl_response(status=("302\n" + target).encode())) as run:
                with self.assertRaises(ValueError):
                    lookup.fetch_curl("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS)
                self.assertEqual(run.call_count, 1)
        responses = [self.curl_response(status=b"302\nhttps://www.netflix.com/us/title/123"),
                     self.curl_response()]
        with patch.object(lookup.subprocess, "run", side_effect=lambda *a, **k: responses.pop(0)(*a, **k)):
            self.assertEqual(lookup.fetch_curl("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS), "complete")

    def test_curl_fallback_bounds_redirects_and_body(self):
        with patch.object(lookup.subprocess, "run", side_effect=self.curl_response(
                status=b"302\nhttps://www.netflix.com/title/123")) as run:
            with self.assertRaisesRegex(ValueError, "redirect limit"):
                lookup.fetch_curl("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS)
            self.assertEqual(run.call_count, 6)
        with patch.object(lookup, "MAX_BYTES", 3), patch.object(lookup.subprocess, "run",
                side_effect=self.curl_response()):
            with self.assertRaisesRegex(ValueError, "size limit"):
                lookup.fetch_curl("https://www.netflix.com/title/123", lookup.NETFLIX_HOSTS)


if __name__ == "__main__":
    unittest.main()
