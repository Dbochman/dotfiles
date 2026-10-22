"""Offline security collector contract tests; no model, network, or delivery."""
import importlib.util
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("advisories", Path(__file__).parents[1] / "bin/security-advisory-collector.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class AdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.stamp = self.now.isoformat()
        self.inventory = {"schema_version": 1, "generated_at": self.stamp,
            "components": [{"product": "Node.js", "version": "1.2.3", "ecosystem": "product",
                            "state": "running", "evidence": "binary_version_probe", "asset_id": "mini",
                            "context": "/private/secret/path"}],
            "unverified_assets": [{"id": "offline", "products": ["Windows"]}]}
        self.incident = {"slug": "node-test", "title": "Test advisory", "updatedAt": self.stamp}
        self.url = "https://nodejs.org/security"
        self.text = "Version 1.2.3 is affected."

    def fetch(self, url, hosts):
        if url.endswith("incidents.json"):
            return json.dumps({"generatedAt": self.stamp, "incidents": [self.incident]})
        if url.endswith("node-test.json"):
            return json.dumps({"sources": [{"url": self.url}]})
        return self.text

    def review(self, instructions, data):
        if instructions == mod.SELECT:
            return {"matches": [{"slug": "node-test", "products": ["Node.js"], "unknown_assets": []}]}
        return {"status": "affected", "asset_ids": ["mini"], "summary": "Observed affected version.",
                "action": "Review update.", "basis": "Version matches.",
                "evidence": [{"url": self.url, "quote": self.text}]}

    def collect(self, previous=None, **kwargs):
        return mod.collect(self.inventory, previous or {}, fetcher=kwargs.get("fetcher", self.fetch),
                           reviewer=kwargs.get("reviewer", self.review), now=self.stamp)

    def test_progress_stages_and_privacy(self):
        events = []
        mod.collect(self.inventory, {}, fetcher=self.fetch, reviewer=self.review,
                    now=self.stamp, progress=lambda **v: events.append(v))
        self.assertIn(("feed", "fetching"), [(e["phase"], e.get("step")) for e in events])
        self.assertIn(("verification", "model"), [(e["phase"], e.get("step")) for e in events])
        self.assertEqual(events[-1]["checked"], 1)
        self.assertEqual(events[-1]["review_limit"], 1)
        self.assertEqual(events[-1]["errors"], 0)
        for secret in ("node-test", "mini", "secret", self.url):
            self.assertNotIn(secret, json.dumps(events))

    def test_progress_counts_failed_attempts(self):
        events = []
        def fail(prompt, data):
            if prompt == mod.VERIFY:
                raise ValueError("model_failed")
            return self.review(prompt, data)
        result = mod.collect(self.inventory, {}, fetcher=self.fetch, reviewer=fail,
                             now=self.stamp, progress=lambda **v: events.append(v))
        self.assertEqual(events[-1]["errors"], 1)
        self.assertEqual(events[-1]["checked"], 1)
        self.assertEqual(result["backlog"], 1)

    def test_progress_summary_legacy_and_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            health = {"status": "running", "phase": "verification", "pending": 41,
                      "checked": 2, "checked_at": self.stamp, "private": "secret"}
            mod.write_private(state / "health.json", health)
            result = mod.progress_summary(state, self.now)
            self.assertEqual(result["review_limit"], 12)
            self.assertNotIn("elapsed_seconds", result)
            self.assertNotIn("private", result)
            health["started_at"] = (self.now - timedelta(seconds=100)).isoformat()
            mod.write_private(state / "health.json", health)
            result = mod.progress_summary(state, self.now)
            self.assertEqual(result["elapsed_seconds"], 100)
            self.assertEqual(result["deadline_remaining_seconds"], 1700)
            self.assertFalse(result["liveness_verified"])
            health["status"] = "failed"
            mod.write_private(state / "health.json", health)
            self.assertNotIn("deadline_remaining_seconds", mod.progress_summary(state, self.now))

    def test_verified_match_and_dedup(self):
        first = self.collect()
        second = self.collect(first)
        self.assertEqual(first["findings"], second["findings"])
        self.assertFalse(first["clearance"])
        self.assertFalse(first["delivery_confirmed"])
        self.assertEqual(first["backlog"], 0)

    def test_untrusted_paths_excluded(self):
        self.assertNotIn("secret", json.dumps(mod.compact_inventory(self.inventory)))

    def test_report_regeneration_does_not_refresh_old_observations(self):
        old = (self.now - timedelta(days=10)).isoformat()
        self.inventory["components"][0]["observed_at"] = old
        self.assertEqual(self.collect()["inventory_at"], old)

    def test_prose_revision_does_not_create_material_change(self):
        first = self.collect()
        self.incident["summary"] = "A rewritten feed summary."
        second = self.collect(first)
        self.assertNotEqual(first["findings"]["node-test"]["input_hash"], second["findings"]["node-test"]["input_hash"])
        self.assertEqual(first["findings"]["node-test"]["material_hash"], second["findings"]["node-test"]["material_hash"])

    def test_invalid_selection_cannot_invent_asset(self):
        with self.assertRaisesRegex(ValueError, "invalid_selection"):
            self.collect(reviewer=lambda *_: {"matches": [{"slug": "node-test", "products": [], "unknown_assets": ["invented"]}]})

    def test_false_citation_rejected(self):
        value = self.review(mod.VERIFY, {})
        value["evidence"][0]["quote"] = "Invented"
        with self.assertRaisesRegex(ValueError, "invalid_evidence"):
            mod.validate_assessment(value, {"mini"}, {self.url: self.text})

    def test_missing_evidence_cannot_be_affected(self):
        value = self.review(mod.VERIFY, {})
        value["evidence"] = []
        self.assertEqual(mod.validate_assessment(value, {"mini"}, {})["status"], "unknown")

    def test_unknown_asset_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_assets"):
            mod.validate_assessment(self.review(mod.VERIFY, {}), {"other"}, {self.url: self.text})

    def test_failed_verification_retains_old_and_reports_backlog(self):
        previous = self.collect()
        self.incident["updatedAt"] = "new revision"
        def fail(prompt, data):
            if prompt == mod.VERIFY:
                raise ValueError("bad_model")
            return self.review(prompt, data)
        result = self.collect(previous, reviewer=fail)
        self.assertEqual(result["findings"], previous["findings"])
        self.assertEqual(result["backlog"], 1)

    def test_unapproved_source_is_unknown(self):
        self.url = "https://evil.invalid/advisory"
        result = self.collect()
        self.assertEqual(result["findings"]["node-test"]["status"], "unknown")

    def test_disappearing_selection_does_not_clear_old_finding(self):
        previous = self.collect()
        result = self.collect(previous, reviewer=lambda *_: {"matches": []})
        self.assertEqual(result["findings"], previous["findings"])

    def test_ssrf_boundaries(self):
        for url in ("http://nodejs.org/a", "https://127.0.0.1/a", "https://nodejs.org.evil/a",
                    "https://user:pass@nodejs.org/a", "https://nodejs.org:444/a"):
            with self.assertRaises(ValueError):
                mod.validate_url(url, mod.PUBLISHERS)
        with patch.object(mod.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                mod.validate_url("https://nodejs.org/a", mod.PUBLISHERS)
        self.assertIsNone(mod.NoRedirect().redirect_request(None, None, None, None, None))

    def test_primary_cna_fallback_has_fixed_host_and_budget(self):
        incident = {"cveIds": ["CVE-2026-12345", "https://evil.invalid", "CVE-2026-54321"]}
        urls = mod.source_urls({"sources": [{"url": "https://evil.invalid/news"}]}, incident)
        self.assertEqual(urls, ["https://cveawg.mitre.org/api/cve/CVE-2026-12345",
                                "https://cveawg.mitre.org/api/cve/CVE-2026-54321"])
        detail = {"sources": [{"url": f"https://nodejs.org/{i}"} for i in range(10)]}
        self.assertEqual(len(mod.source_urls(detail, incident)), 3)

    def test_redirect_revalidates_target_before_request(self):
        from email.message import Message
        from io import BytesIO
        headers = Message()
        headers['Location'] = 'https://127.0.0.1/secrets'
        error = mod.urllib.error.HTTPError('https://nodejs.org/a', 301, 'redirect', headers, BytesIO())
        from unittest.mock import Mock
        opener = Mock()
        opener.open.side_effect = error
        addresses = [(0, 0, 0, '', ('8.8.8.8', 443))]
        with patch.object(mod.urllib.request, 'build_opener', return_value=opener), patch.object(
            mod.socket, 'getaddrinfo', return_value=addresses
        ):
            with self.assertRaisesRegex(ValueError, 'unapproved_source'):
                mod.fetch('https://nodejs.org/a', mod.PUBLISHERS)
        self.assertEqual(opener.open.call_count, 1)

    def test_cache_only_summary_staleness_and_repeated_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cache = self.collect()
            mod.write_private(path / "cache.json", cache)
            mod.write_private(path / "health.json", {"status": "ok"})
            with patch.object(mod, "fetch", side_effect=AssertionError("network")), patch.object(mod, "infer", side_effect=AssertionError("model")):
                first = mod.briefing_summary(path, self.now)
                self.assertEqual(first, mod.briefing_summary(path, self.now))
                self.assertEqual(len(first["changes"]), 1)
                stale = mod.briefing_summary(path, self.now + timedelta(days=8))
                self.assertEqual(stale["status"], "stale")
                self.assertIn("inventory_refresh_needed", stale["warnings"])
                self.assertEqual(stale["unresolved"], 1)
                self.assertFalse(stale["deliveryConfirmed"])
            (path / "cache.json").chmod(0o644)
            self.assertEqual(mod.briefing_summary(path)["status"], "unavailable")

    def test_missing_cache_and_future_date(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(mod.briefing_summary(Path(directory))["status"], "unavailable")
        with self.assertRaises(ValueError):
            mod.age_hours((self.now + timedelta(days=2)).isoformat())

    def test_old_feed_fails_not_empty_success(self):
        self.stamp = (self.now - timedelta(days=4)).isoformat()
        with self.assertRaisesRegex(ValueError, "stale_feed"):
            self.collect()

    def test_review_cap_and_backlog_drain(self):
        incidents = [{**self.incident, "slug": f"node-test-{n}"} for n in range(13)]
        def fetch(url, hosts):
            if url.endswith("incidents.json"):
                return json.dumps({"generatedAt": self.stamp, "incidents": incidents})
            if url.endswith(".json"):
                return json.dumps({"sources": [{"url": self.url}]})
            return self.text
        def review(prompt, data):
            if prompt == mod.SELECT:
                return {"matches": [{"slug": i["slug"], "products": ["Node.js"], "unknown_assets": []}
                                    for i in data["advisories"]]}
            return self.review(prompt, data)
        first = self.collect(fetcher=fetch, reviewer=review)
        self.assertEqual(len(first["findings"]), 12)
        self.assertEqual(first["backlog"], 1)
        second = self.collect(first, fetcher=fetch, reviewer=review)
        self.assertEqual(len(second["findings"]), 13)
        self.assertEqual(second["backlog"], 0)

    def test_changed_actionable_finding_precedes_unreviewed_backlog(self):
        first = self.collect()
        incidents = [{"slug": f"new-{n}", "title": "New advisory"} for n in range(12)]
        incidents.append({**self.incident, "summary": "updated evidence"})
        reviewed = []
        def fetch(url, hosts):
            if url.endswith("incidents.json"):
                return json.dumps({"generatedAt": self.stamp, "incidents": incidents})
            if url.endswith(".json"):
                return json.dumps({"sources": [{"url": self.url}]})
            return self.text
        def review(prompt, data):
            if prompt == mod.SELECT:
                return {"matches": [{"slug": i["slug"], "products": ["Node.js"], "unknown_assets": []}
                                    for i in data["advisories"]]}
            reviewed.append(data["advisory"]["slug"])
            return self.review(prompt, data)
        self.collect(first, fetcher=fetch, reviewer=review)
        self.assertEqual(reviewed[0], "node-test")


if __name__ == "__main__":
    unittest.main()
