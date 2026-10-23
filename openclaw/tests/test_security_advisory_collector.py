"""Offline security collector contract tests; no model, network, or delivery."""
import importlib.util
import copy
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

    def test_attended_retirements_are_not_active_matching_inputs(self):
        inventory = copy.deepcopy(self.inventory)
        for state in ('retired_from_active_cache','retired_from_application_path'):
            inventory['components'].append({**inventory['components'][0],
                'product':'Retired browser', 'state':state})
        inventory['components'].append({**inventory['components'][0],
            'product':'Unverified app', 'state':'unknown'})
        compact = mod.compact_inventory(inventory)
        self.assertEqual({row[0] for row in compact['components']}, {'Node.js','Unverified app'})
        self.assertEqual(compact['unverified'], [{'id':'offline','products':['Windows']}])
        self.assertEqual(len(inventory['components']),4)

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

    def test_discovery_retry_is_bounded_and_exposes_only_reason(self):
        calls, events = [], []
        def review(prompt, data):
            calls.append(data.copy())
            return {"matches": [{"slug": "node-test", "products": ["private-invalid-name"],
                                 "unknown_assets": []}]}
        with self.assertRaisesRegex(ValueError, "invalid_selection_products"):
            mod.collect(self.inventory, {}, fetcher=self.fetch, reviewer=review,
                        now=self.stamp, progress=lambda **v: events.append(v))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["retry_reason"], "invalid_selection_products")
        self.assertEqual(events[-1]["attempt"], 2)
        self.assertNotIn("private-invalid-name", json.dumps(events))
        self.assertNotIn("private-invalid-name", json.dumps(calls))

    def test_discovery_recovers_with_explicit_namespaces(self):
        calls = []
        def review(prompt, data):
            if prompt == mod.SELECT:
                calls.append(data.copy())
                self.assertEqual(data["allowed_products"], ["Node.js"])
                self.assertEqual(data["allowed_unknown_assets"], ["offline"])
                self.assertEqual(data["inventory"]["columns"], ["product", "ecosystem", "state", "asset_ids"])
                # Windows is an unverified product hint, NOT an observed product.
                return {"matches": [{"slug": "node-test",
                    "products": ["Windows"] if len(calls) == 1 else [],
                    "unknown_assets": ["offline"]}]}
            value = self.review(prompt, data)
            value["asset_ids"] = ["offline"]
            return value
        result = self.collect(reviewer=review)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["findings"]["node-test"]["asset_ids"], ["offline"])

    def test_discovery_compaction_keeps_full_verification_evidence(self):
        def review(prompt, data):
            if prompt == mod.SELECT:
                self.assertNotIn("1.2.3", json.dumps(data["inventory"]))
            else:
                self.assertEqual(data["components"][0][1], "1.2.3")
                self.assertEqual(data["components"][0][4], "binary_version_probe")
            return self.review(prompt, data)
        self.assertEqual(self.collect(reviewer=review)["matches"], 1)

    def test_selection_rejects_malformed_and_out_of_scope_values(self):
        good = {"slug": "node-test", "products": ["Node.js"], "unknown_assets": []}
        for value in (None, [], {}, {"matches": {}}, {"matches": [None]},
                      {"matches": [{**good, "products": "Node.js"}]},
                      {"matches": [{**good, "unknown_assets": [{}]}]},
                      {"matches": [{**good, "slug": "outside-batch"}]},
                      {"matches": [good, good]}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_selection_"):
                mod.validate_selection(value, {"node-test"}, {"Node.js"}, {"offline"})

    def test_selection_transport_failure_and_deadline_do_not_retry(self):
        for error in (ValueError("model_failed"), mod.CollectionDeadline("collection_deadline")):
            calls = []
            def fail(*_):
                calls.append(1)
                raise error
            with self.assertRaises(type(error)):
                self.collect(reviewer=fail)
            self.assertEqual(len(calls), 1)

    def test_selection_json_failure_gets_one_retry(self):
        calls = []
        def review(prompt, data):
            if prompt == mod.SELECT:
                calls.append(1)
                if len(calls) == 1:
                    raise json.JSONDecodeError("bad", "", 0)
            return self.review(prompt, data)
        self.assertEqual(self.collect(reviewer=review)["matches"], 1)
        self.assertEqual(len(calls), 2)

    def test_failed_discovery_preserves_cache_on_disk(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            state.chmod(0o700)
            mod.write_private(state / "cache.json", self.collect())
            mod.write_private(state / "inventory.json", self.inventory)
            before = (state / "cache.json").read_bytes()
            with patch.object(sys, "argv", ["collector", "--state-dir", str(state),
                                            "--inventory", str(state / "inventory.json")]), patch.object(
                    mod, "collect", side_effect=ValueError("invalid_selection_products")):
                self.assertEqual(mod.main(), 1)
            self.assertEqual((state / "cache.json").read_bytes(), before)
            self.assertEqual(mod.read_private(state / "health.json")["status"], "failed")

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

    def resolution(self, finding):
        return {"slug": finding["slug"], "finding_hash": mod.digest(finding),
                "asset_ids": finding["asset_ids"], "verified_at": self.stamp,
                "reason": "Attended update and version verification passed.",
                "evidence": ["plans/dependency-maintenance.md", self.url]}

    def resolution_cache(self):
        with patch.object(mod, "now_iso", return_value=self.stamp):
            return self.collect()

    def seed_summary(self, state, cache):
        mod.write_private(state / "cache.json", cache)
        mod.write_private(state / "health.json", {"status": "ok"})

    def test_resolved_and_not_applicable_never_enter_briefing_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = self.resolution_cache()
            finding = cache["findings"]["node-test"]
            for kind in ("new", "changed"):
                other = dict(finding, slug=kind, status="not_applicable", change_kind=kind)
                cache["findings"][kind] = other
            unknown = dict(finding, slug="unknown", status="unknown")
            cache["findings"]["unknown"] = unknown
            cache["backlog"] = 17
            self.seed_summary(state, cache)
            mod.write_private(state / "resolutions.json", {
                "schema_version": 1, "resolutions": [self.resolution(finding)]})
            before = {p.name: p.read_bytes() for p in state.iterdir()}
            with patch.object(mod, "fetch", side_effect=AssertionError("network")), patch.object(
                    mod, "infer", side_effect=AssertionError("model")):
                result = mod.briefing_summary(state, self.now)
            self.assertEqual(result["unresolved"], 1)
            self.assertEqual([f["status"] for f in result["changes"]], ["unknown"])
            self.assertEqual(result["additionalChanges"], 0)
            self.assertEqual(result["backlog"], 17)
            self.assertIn("verification_backlog", result["warnings"])
            self.assertFalse(result["clearance"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in state.iterdir()})

    def test_resolution_cannot_hide_changed_assessment_or_asset_set(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            original = self.resolution_cache()
            receipt = self.resolution(original["findings"]["node-test"])
            mod.write_private(state / "resolutions.json", {"schema_version": 1, "resolutions": [receipt]})
            for field, value in (("input_hash", "new-inventory"), ("asset_ids", ["mini", "offline"]),
                                 ("status", "possibly_affected"), ("summary", "New evidence"),
                                 ("checked_at", (self.now + timedelta(minutes=1)).isoformat())):
                with self.subTest(field=field):
                    cache = copy.deepcopy(original)
                    cache["findings"]["node-test"][field] = value
                    self.seed_summary(state, cache)
                    self.assertEqual(mod.briefing_summary(state, self.now)["unresolved"], 1)

    def test_partial_receipt_narrows_prose_without_closing_or_redating(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = self.resolution_cache()
            finding = cache['findings']['node-test']
            remaining = {'summary': 'Other bundled runtimes remain unverified.',
                         'action': 'Verify the remaining application runtimes.'}
            receipt = {**self.resolution(finding), 'remaining': remaining}
            self.seed_summary(state, cache)
            source = state/'attended.json'
            mod.write_private(source, {'schema_version':2, 'resolutions':[receipt]})
            mod.record_resolutions(source, state, self.now)
            self.assertEqual(mod.read_private(state/'resolutions.json')['schema_version'],2)
            with self.assertRaises(ValueError):
                mod.validate_resolutions({'schema_version':1,'resolutions':[receipt]},self.now)
            before = {p.name:p.read_bytes() for p in state.iterdir()}
            with patch.object(mod, 'fetch', side_effect=AssertionError('network')), patch.object(
                    mod, 'infer', side_effect=AssertionError('model')):
                result = mod.briefing_summary(state, self.now)
            self.assertEqual(result['unresolved'], 1)
            self.assertFalse(result['clearance'])
            change, = result['changes']
            for field in ('status','asset_ids','evidence','checked_at','changed_at'):
                self.assertEqual(change[field], finding[field])
            self.assertEqual(change['summary'], remaining['summary'])
            self.assertEqual(change['action'], remaining['action'])
            self.assertEqual(before, {p.name:p.read_bytes() for p in state.iterdir()})

    def test_partial_receipt_rejects_empty_or_status_changing_payload(self):
        finding = self.resolution_cache()['findings']['node-test']
        for remaining in (None, {}, {'summary':'', 'action':'Review'},
                          {'summary':'Open', 'action':'Review', 'status':'not_applicable'},
                          {'summary':'Open', 'action':'x'*2001}):
            with self.subTest(remaining=remaining), self.assertRaises(ValueError):
                mod.validate_resolutions({'schema_version':2, 'resolutions':[
                    {**self.resolution(finding), 'remaining':remaining}]}, self.now)

    def test_changed_or_expired_partial_receipt_restores_original_with_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = self.resolution_cache()
            finding = cache['findings']['node-test']
            receipt = {**self.resolution(finding), 'remaining':{
                'summary':'Remaining scope.', 'action':'Review remaining scope.'}}
            mod.write_private(state/'resolutions.json', {'schema_version':2, 'resolutions':[receipt]})
            for changed, now in ((False,self.now+timedelta(days=8)), (True,self.now)):
                current = copy.deepcopy(cache)
                if changed:
                    current['findings']['node-test']['input_hash'] = 'changed-inventory'
                self.seed_summary(state,current)
                outstanding, warnings = mod.outstanding_findings(current,state,now)
                self.assertEqual(outstanding,[current['findings']['node-test']])
                self.assertIn('resolution_review_needed',warnings)

    def test_resolution_expiry_reopens_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = self.resolution_cache()
            self.seed_summary(state, cache)
            mod.write_private(state / "resolutions.json", {
                "schema_version": 1, "resolutions": [self.resolution(cache["findings"]["node-test"])]})
            result = mod.briefing_summary(state, self.now + timedelta(days=8))
            self.assertEqual(result["unresolved"], 1)
            self.assertIn("resolution_review_needed", result["warnings"])
            self.assertEqual(result["status"], "stale")

    def test_invalid_or_unsafe_resolutions_preserve_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = self.resolution_cache()
            self.seed_summary(state, cache)
            valid = self.resolution(cache["findings"]["node-test"])
            invalid = [None, {}, {"schema_version": 1, "resolutions": [valid, valid]}]
            for field, value in (("evidence", []), ("asset_ids", "mini"),
                                 ("verified_at", (self.now + timedelta(minutes=1)).isoformat()),
                                 ("reason", ""), ("finding_hash", "bad")):
                invalid.append({"schema_version": 1, "resolutions": [dict(valid, **{field: value})]})
            for document in invalid:
                mod.write_private(state / "resolutions.json", document)
                result = mod.briefing_summary(state, self.now)
                self.assertEqual(result["unresolved"], 1)
                self.assertIn("resolution_evidence_unavailable", result["warnings"])
            mod.write_private(state / "resolutions.json", {"schema_version": 1, "resolutions": [valid]})
            (state / "resolutions.json").chmod(0o644)
            self.assertIn("resolution_evidence_unavailable", mod.briefing_summary(state, self.now)["warnings"])

    def test_attended_import_validates_snapshot_and_preserves_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = self.resolution_cache()
            self.seed_summary(state, cache)
            before = (state / "cache.json").read_bytes()
            receipt = self.resolution(cache["findings"]["node-test"])
            source = state / "attended.json"
            mod.write_private(source, {"schema_version": 1, "resolutions": [receipt]})
            self.assertEqual(mod.record_resolutions(source, state, self.now)["resolutions"], 1)
            ledger = (state / "resolutions.json").read_bytes()
            for field, value in (("finding_hash", "0" * 64), ("asset_ids", ["offline"]),
                                 ("verified_at", (self.now - timedelta(minutes=1)).isoformat())):
                mod.write_private(source, {"schema_version": 1, "resolutions": [dict(receipt, **{field: value})]})
                with self.assertRaisesRegex(ValueError, "resolution_snapshot_mismatch"):
                    mod.record_resolutions(source, state, self.now)
                self.assertEqual(ledger, (state / "resolutions.json").read_bytes())
            self.assertEqual(before, (state / "cache.json").read_bytes())

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

    def resume_fixture(self, count):
        incidents = [{**self.incident, "slug": f"node-test-{n}"} for n in range(count)]
        calls, checkpoints = [], []
        def fetch(url, hosts):
            if url.endswith("incidents.json"):
                return json.dumps({"generatedAt": self.stamp, "incidents": incidents})
            if url.endswith(".json"):
                return json.dumps({"sources": [{"url": self.url}]})
            return self.text
        def review(prompt, data):
            calls.append((prompt, copy.deepcopy(data)))
            if prompt == mod.SELECT:
                return {"matches": [{"slug": i["slug"], "products": ["Node.js"], "unknown_assets": []}
                                    for i in data["advisories"]]}
            return self.review(prompt, data)
        def run(previous=None, **kwargs):
            return mod.collect(self.inventory, previous or {}, fetcher=fetch, now=self.stamp,
                               reviewer=kwargs.pop("reviewer", review),
                               save_checkpoint=lambda v: checkpoints.append(copy.deepcopy(v)), **kwargs)
        return incidents, calls, checkpoints, review, run

    def test_discovery_resume_survives_feed_reorder_and_growth(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(21)
        with self.assertRaises(mod.CollectionPaused):
            run(should_pause=lambda: bool(checkpoints))
        self.assertEqual(len(checkpoints[-1]["discovery"]), 20)
        incidents.reverse()
        incidents.append({**self.incident, "slug": "new-advisory"})
        calls.clear()
        result = run(checkpoint=checkpoints[-1])
        selected = [data for prompt, data in calls if prompt == mod.SELECT]
        self.assertEqual(len(selected), 1)
        self.assertEqual({a["slug"] for a in selected[0]["advisories"]}, {"node-test-20", "new-advisory"})
        self.assertEqual(result["discovery_reused"], 20)
        self.assertEqual(result["advisories_screened"], 22)
        self.assertEqual(result["backlog"], 10)

    def test_hard_deadline_resumes_successful_reviews(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(3)
        def interrupt(prompt, data):
            if prompt == mod.VERIFY and len([c for c in calls if c[0] == mod.VERIFY]) == 1:
                raise mod.CollectionDeadline("collection_deadline")
            return review(prompt, data)
        with self.assertRaises(mod.CollectionDeadline):
            run(reviewer=interrupt)
        self.assertEqual(len(checkpoints[-1]["findings"]), 1)
        calls.clear()
        result = run(checkpoint=checkpoints[-1])
        self.assertFalse(any(prompt == mod.SELECT for prompt, _ in calls))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["resumed_reviews"], 1)
        self.assertEqual(result["backlog"], 0)
        self.assertEqual(len(result["findings"]), 3)

    def test_changed_incident_invalidates_only_its_selection_and_review(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(2)
        first = run()
        incidents[0]["summary"] = "Changed advisory"
        calls.clear()
        result = run(first, checkpoint=checkpoints[-1])
        self.assertEqual(result["discovery_reused"], 1)
        self.assertEqual([p for p, _ in calls], [mod.SELECT, mod.VERIFY])
        self.assertEqual(calls[0][1]["advisories"][0]["slug"], "node-test-0")

    def test_inventory_and_policy_changes_invalidate_checkpoint(self):
        for change in ("inventory", "policy"):
            with self.subTest(change=change):
                incidents, calls, checkpoints, review, run = self.resume_fixture(1)
                run()
                checkpoint = checkpoints[-1]
                if change == "inventory":
                    self.inventory["components"][0]["version"] = "2.0.0"
                else:
                    checkpoint["contract"] = "old-contract"
                calls.clear()
                result = run(checkpoint=checkpoint)
                self.assertEqual(result["discovery_reused"], 0)
                self.assertEqual(result["resumed_reviews"], 0)
                self.assertEqual([p for p, _ in calls], [mod.SELECT, mod.VERIFY])

    def test_new_observation_requires_review_even_for_same_version(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(1)
        self.inventory["components"][0]["observed_at"] = (self.now - timedelta(days=2)).isoformat()
        first = run()
        self.inventory["components"][0]["observed_at"] = self.stamp
        calls.clear()
        result = run(first, checkpoint=checkpoints[-1])
        self.assertEqual(result["discovery_reused"], 1)
        self.assertEqual([p for p, _ in calls], [mod.VERIFY])

    def test_non_oldest_observation_also_invalidates_review(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(1)
        self.inventory["components"][0]["observed_at"] = (self.now - timedelta(days=3)).isoformat()
        self.inventory["components"].append({**self.inventory["components"][0],
            "product": "Other", "observed_at": (self.now - timedelta(days=2)).isoformat()})
        first = run()
        self.inventory["components"][1]["observed_at"] = self.stamp
        calls.clear()
        result = run(first, checkpoint=checkpoints[-1])
        self.assertEqual(result["inventory_at"], first["inventory_at"])
        self.assertEqual([p for p, _ in calls], [mod.VERIFY])

    def test_prior_verified_work_survives_interruption_during_new_discovery(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(1)
        run()
        saved = checkpoints[-1]
        incidents.extend({**self.incident, "slug": f"new-{i}"} for i in range(21))
        def interrupt(prompt, data):
            if prompt == mod.SELECT and len(data["advisories"]) == 1:
                raise mod.CollectionDeadline("collection_deadline")
            return review(prompt, data)
        with self.assertRaises(mod.CollectionDeadline):
            run(checkpoint=saved, reviewer=interrupt)
        self.assertEqual(checkpoints[-1]["findings"], saved["findings"])
        calls.clear()
        result = run(checkpoint=checkpoints[-1])
        self.assertEqual(result["resumed_reviews"], 1)
        self.assertNotIn("node-test-0", [data["advisory"]["slug"] for prompt, data in calls if prompt == mod.VERIFY])

    def test_expired_checkpoint_review_is_not_reused(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(1)
        run()
        saved = checkpoints[-1]
        saved["findings"]["node-test-0"]["checked_at"] = (self.now - timedelta(days=8)).isoformat()
        calls.clear()
        result = run(checkpoint=saved)
        self.assertEqual(result["resumed_reviews"], 0)
        self.assertEqual([p for p, _ in calls], [mod.VERIFY])

    def test_expired_negative_discovery_is_rechecked(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(1)
        run(reviewer=lambda *_: {"matches": []})
        checkpoint = checkpoints[-1]
        calls.clear()
        self.assertEqual(run(checkpoint=checkpoint)["matches"], 0)
        self.assertEqual(calls, [])
        checkpoint["discovery"]["node-test-0"]["checked_at"] = (self.now - timedelta(hours=25)).isoformat()
        self.assertEqual(run(checkpoint=checkpoint)["matches"], 1)
        self.assertEqual([p for p, _ in calls], [mod.SELECT, mod.VERIFY])

    def test_invalid_batch_never_checkpointed(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(21)
        def invalid(prompt, data):
            if len(data["advisories"]) == 1:
                return {"matches": [{"slug": "outside", "products": [], "unknown_assets": []}]}
            return review(prompt, data)
        with self.assertRaisesRegex(ValueError, "invalid_selection_slug"):
            run(reviewer=invalid)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(len(checkpoints[-1]["discovery"]), 20)

    def test_soft_verification_stop_reports_remaining_backlog(self):
        incidents, calls, checkpoints, review, run = self.resume_fixture(3)
        first = run(should_pause=lambda: bool(checkpoints and checkpoints[-1]["findings"]))
        self.assertTrue(first["budget_exhausted"])
        self.assertEqual(first["backlog"], 2)
        calls.clear()
        second = run(first, checkpoint=checkpoints[-1])
        self.assertEqual(second["backlog"], 0)
        self.assertEqual([p for p, _ in calls], [mod.VERIFY, mod.VERIFY])

    def test_checkpoint_write_failure_is_not_a_review_failure(self):
        with self.assertRaises(OSError):
            mod.collect(self.inventory, {}, fetcher=self.fetch, reviewer=self.review,
                        save_checkpoint=lambda _: (_ for _ in ()).throw(OSError("disk_full")))

    def test_main_pause_keeps_published_cache_and_exposes_pause_warning(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            state.chmod(0o700)
            mod.write_private(state / "cache.json", self.collect())
            mod.write_private(state / "inventory.json", self.inventory)
            before = (state / "cache.json").read_bytes()
            original = mod.collect
            def paused(inventory, previous, **kwargs):
                self.assertEqual(kwargs["checkpoint"], {})
                # Complete one batch, then pause before the second.
                incidents = [{**self.incident, "slug": f"n-{i}"} for i in range(21)]
                def fetch(*_):
                    return json.dumps({"generatedAt": self.stamp, "incidents": incidents})
                kwargs["should_pause"] = lambda: (state / "checkpoint.json").exists()
                return original(inventory, previous, fetcher=fetch,
                                reviewer=lambda *_: {"matches": []}, **kwargs)
            with patch.object(sys, "argv", ["collector", "--state-dir", str(state),
                                            "--inventory", str(state / "inventory.json")]), patch.object(
                    mod, "collect", side_effect=paused):
                self.assertEqual(mod.main(), 0)
            self.assertEqual((state / "cache.json").read_bytes(), before)
            self.assertEqual((state / "checkpoint.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(mod.read_private(state / "checkpoint.json")["discovery"]), 20)
            self.assertIn("collection_paused", mod.briefing_summary(state)["warnings"])

    def test_running_collection_is_not_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            mod.write_private(state / "cache.json", self.collect())
            mod.write_private(state / "health.json", {"status": "running"})
            warnings = mod.briefing_summary(state)["warnings"]
            self.assertIn("collection_in_progress", warnings)
            self.assertNotIn("latest_collection_failed", warnings)

    def test_model_cleanup_is_bounded_and_preserves_original_deadline(self):
        from unittest.mock import Mock
        process = Mock(pid=123)
        process.communicate.side_effect = [mod.CollectionDeadline("collection_deadline"),
                                          mod.subprocess.TimeoutExpired("model", 5)]
        process.wait.side_effect = mod.subprocess.TimeoutExpired("model", 5)
        with patch.object(mod.subprocess, "Popen", return_value=process), patch.object(
                mod.os, "killpg") as kill:
            with self.assertRaises(mod.CollectionDeadline):
                mod.infer("test", {})
        kill.assert_called_once_with(123, mod.signal.SIGKILL)
        self.assertEqual([c.kwargs["timeout"] for c in process.communicate.call_args_list], [240, 5])
        process.wait.assert_called_once_with(timeout=5)
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_discovery_timeout_retries_once_and_recovers(self):
        calls, events = [], []
        def review(prompt, data):
            if prompt == mod.SELECT:
                calls.append(1)
                if len(calls) == 1:
                    raise mod.subprocess.TimeoutExpired("private prompt", 240)
            return self.review(prompt, data)
        result = mod.collect(self.inventory, {}, fetcher=self.fetch, reviewer=review,
                             progress=lambda **v: events.append(v))
        self.assertEqual(result["matches"], 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("model_timeout", json.dumps(events))
        self.assertNotIn("private prompt", json.dumps(events))

    def test_discovery_timeout_and_schema_error_share_two_attempt_budget(self):
        calls = []
        def fail(*_):
            calls.append(1)
            if len(calls) == 1:
                raise mod.subprocess.TimeoutExpired("model", 240)
            raise ValueError("invalid_selection_shape")
        with self.assertRaises(ValueError):
            self.collect(reviewer=fail)
        self.assertEqual(len(calls), 2)

    def test_discovery_repeated_timeout_has_only_two_attempts(self):
        from unittest.mock import Mock
        review = Mock(side_effect=mod.subprocess.TimeoutExpired("model", 240))
        with self.assertRaises(mod.subprocess.TimeoutExpired):
            self.collect(reviewer=review)
        self.assertEqual(review.call_count, 2)

    def test_verification_timeout_retry_cap_and_deadline_escape(self):
        from unittest.mock import Mock
        timeout = mod.subprocess.TimeoutExpired("model", 240)
        review = Mock(side_effect=[timeout, {"ok": True}])
        self.assertEqual(mod.review_with_timeout_retry(review, {}, lambda **_: None), {"ok": True})
        review = Mock(side_effect=timeout)
        with self.assertRaises(mod.subprocess.TimeoutExpired):
            mod.review_with_timeout_retry(review, {}, lambda **_: None)
        self.assertEqual(review.call_count, 2)
        for error in (mod.CollectionDeadline(), mod.CollectionStopped(), ValueError("invalid")):
            review = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                mod.review_with_timeout_retry(review, {}, lambda **_: None)
            self.assertEqual(review.call_count, 1)


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        self.args = SimpleNamespace(state_dir=self.state, inventory=self.state / "inventory.json")
        self.now = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
        self.calls = []

    def run_pass(self, health=None, advance=True):
        def run(_):
            self.calls.append(1)
            mod.write_private(self.state / "health.json", {
                "started_at": str(len(self.calls)), "status": "ok", "backlog": 10, **(health or {})})
            if advance:
                mod.write_private(self.state / "checkpoint.json", {"progress": len(self.calls)})
            return 0
        return run

    def continue_run(self, **kwargs):
        return mod.continue_collection(self.args, sleeper=lambda _: None, now=self.now, **kwargs)

    def ledger(self):
        return mod.read_private(self.state / "continuation.json")

    def test_cap_is_durable_and_restart_does_not_reset(self):
        self.assertEqual(self.continue_run(run=self.run_pass()), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.ledger()["status"], "capped")
        self.assertEqual(self.continue_run(run=self.run_pass()), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual((self.state / "continuation.json").stat().st_mode & 0o777, 0o600)

    def test_complete_and_failure_and_no_progress_stop(self):
        for health, advance, status in (({"backlog": 0}, True, "complete"),
                ({"status": "failed", "error_type": "ValueError"}, True, "blocked"),
                ({"status": "paused"}, False, "stalled")):
            with self.subTest(status=status):
                self.calls.clear()
                mod.write_private(self.state / "continuation.json", {})
                mod.write_private(self.state / "health.json", {})
                self.continue_run(run=self.run_pass(health, advance))
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.ledger()["status"], status)

    def test_timeout_after_progress_continues_but_operator_stop_does_not(self):
        self.continue_run(run=self.run_pass({"status": "failed", "error_type": "TimeoutExpired"}))
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(mod.pending_work({"status": "failed", "error_type": "CollectionStopped"}))

    def test_restart_after_crash_consumes_prior_reservation(self):
        mod.write_private(self.state / "continuation.json", {
            "window": "2026-10-22", "passes_used": 2, "pass_limit": 3, "status": "running"})
        self.continue_run(run=self.run_pass())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.ledger()["passes_used"], 3)

    def test_new_window_resets_budget(self):
        self.continue_run(run=self.run_pass())
        self.now += timedelta(days=1)
        self.continue_run(run=self.run_pass())
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(mod.continuation_window(self.now.replace(hour=7, minute=14)), "2026-10-22")
        self.assertEqual(mod.continuation_window(self.now.replace(hour=7, minute=15)), "2026-10-23")

    def test_stop_during_backoff_is_not_retried(self):
        def stop(_):
            raise mod.CollectionStopped()
        mod.continue_collection(self.args, run=self.run_pass(), sleeper=stop, now=self.now)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.ledger()["status"], "stopped")

    def test_backoff_and_cache_preservation(self):
        mod.write_private(self.state / "cache.json", {"sentinel": "published"})
        before = (self.state / "cache.json").read_bytes()
        sleeps = []
        mod.continue_collection(self.args, run=self.run_pass(), sleeper=sleeps.append, now=self.now)
        self.assertEqual(sleeps, [60, 60])
        self.assertEqual((self.state / "cache.json").read_bytes(), before)

    def test_concurrent_supervisor_does_not_reserve_another_pass(self):
        with (self.state / "continuation.lock").open("a") as lock:
            mod.fcntl.flock(lock, mod.fcntl.LOCK_EX | mod.fcntl.LOCK_NB)
            self.assertEqual(self.continue_run(run=self.run_pass()), 0)
            self.assertEqual(self.calls, [])
            self.assertFalse((self.state / "continuation.json").exists())

    def test_busy_or_abnormal_worker_does_not_loop(self):
        self.assertEqual(self.continue_run(run=lambda _: 0), 1)
        self.assertEqual(self.ledger()["status"], "blocked")
        self.assertEqual(self.ledger()["passes_used"], 1)

    def test_child_is_one_shot_with_bounded_wait(self):
        from unittest.mock import Mock
        process = Mock()
        process.wait.return_value = 0
        with patch.object(mod.subprocess, "Popen", return_value=process) as popen:
            mod.invoke_pass(self.args)
        self.assertNotIn("--continue-pending", popen.call_args.args[0])
        process.wait.assert_called_once_with(timeout=1830)

    def test_progress_reports_continuation_without_private_extra_fields(self):
        self.continue_run(run=self.run_pass())
        health = mod.read_private(self.state / "health.json")
        health.update(started_at=datetime.now(timezone.utc).isoformat(), checked_at=datetime.now(timezone.utc).isoformat())
        mod.write_private(self.state / "health.json", health)
        ledger = self.ledger()
        ledger["private"] = "secret"
        mod.write_private(self.state / "continuation.json", ledger)
        result = mod.progress_summary(self.state)
        self.assertEqual(result["continuation"]["status"], "capped")
        self.assertEqual(result["continuation"]["passes_used"], 3)
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
