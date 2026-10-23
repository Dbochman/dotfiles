import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "openclaw/bin/pinchtab-browser-prepare.py"
spec = importlib.util.spec_from_file_location("prepare_browser", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeBackend:
    def __init__(self, probes=(False, True)):
        self.probes = iter(probes)
        self.events = []
        self.active = False
        self.exists = False
        self.failure = None

    def create(self, name):
        self.events.append("create")
        if self.exists:
            raise mod.PreparationError("profile_already_exists")
        return "prof_test"

    def instances(self, profile_id):
        assert profile_id == "prof_test"
        return [{"id": "inst_test", "profileId": profile_id, "mode": "headless", "status": "running"}] if self.active else []

    def start(self, name):
        self.events.append("start")
        self.active = True
        if self.failure:
            raise self.failure

    def probe(self, row, timeout):
        self.events.append("probe")
        result = next(self.probes, False)
        if isinstance(result, Exception):
            raise result
        return result

    def stop_owned(self, profile_id):
        assert profile_id == "prof_test"
        self.events.append("stop")
        self.active = False


class PreparationTests(unittest.TestCase):
    def verify_backend(self, probes=(False, True), rows=None):
        backend = MagicMock()
        backend.cli_json.return_value = rows if rows is not None else [
            {"id": "inst_test", "profileId": "prof_test", "status": "running", "mode": "headless"}]
        backend.probe.side_effect = probes
        return backend

    def run_verify(self, backend, timeout=60):
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
        return mod.verify_existing('inst_test', backend, timeout, clock=lambda: elapsed[0], sleep=sleep)

    def test_existing_verification_only_probes_exact_instance(self):
        backend = self.verify_backend()
        self.run_verify(backend)
        self.assertEqual(backend.probe.call_count, 2)
        backend.create.assert_not_called()
        backend.start.assert_not_called()
        backend.stop_owned.assert_not_called()

    def test_existing_verification_retries_only_one_probe_deadline(self):
        deadline = mod.PreparationError('trust_probe_deadline_exceeded')
        backend = self.verify_backend((deadline, True))
        self.run_verify(backend)
        for probes in [(deadline, deadline), (mod.PreparationError('trust_probe_inconclusive'),), (False, False, False)]:
            backend = self.verify_backend(probes)
            with self.assertRaises(mod.PreparationError):
                self.run_verify(backend)
            backend.stop_owned.assert_not_called()

    def test_existing_verification_rejects_missing_foreign_or_changed_identity(self):
        base = {"id": "inst_test", "profileId": "prof_test", "status": "running", "mode": "headless"}
        for rows in ([], [base, base], [{**base, 'mode':'headed'}], [{**base, 'attached':True}], [{**base, 'status':'starting'}]):
            backend = self.verify_backend(rows=rows)
            with self.assertRaises(mod.PreparationError):
                self.run_verify(backend)
            backend.probe.assert_not_called()
        backend = self.verify_backend()
        backend.cli_json.side_effect = [[base], [{**base, 'profileId':'prof_changed'}]]
        with self.assertRaises(mod.PreparationError):
            self.run_verify(backend)
        self.assertEqual(backend.probe.call_count, 1)

    def test_existing_verification_bounds_before_access(self):
        for instance, timeout in [('inst_test', 59), ('inst_test', 121), ('../other', 60)]:
            backend = self.verify_backend()
            with self.assertRaises(mod.PreparationError):
                mod.verify_existing(instance, backend, timeout)
            backend.cli_json.assert_not_called()

    def run_prepare(self, backend, timeout=60):
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
        return mod.prepare("audit-new", backend, timeout, clock=lambda: elapsed[0], sleep=sleep)

    def report(self, code=0, change=None):
        rows = [{"host": h, "result": "control_passed" if h in mod.HOSTS[:2] else "rejected"}
                for h in mod.HOSTS]
        if change:
            rows[change[0]]["result"] = change[1]
        return subprocess.CompletedProcess([], code, json.dumps({"results": rows}), "")

    def test_complete_evidence_required_even_for_zero_exit(self):
        self.assertTrue(mod.gate_state(self.report()))
        self.assertFalse(mod.gate_state(self.report(1, (2, "accepted"))))
        for result in [self.report(0, (2, "accepted")), self.report(1, (0, "inconclusive")),
                       self.report(1, (2, "inconclusive")), self.report(2),
                       subprocess.CompletedProcess([], 0, "", ""),
                       subprocess.CompletedProcess([], 0, '{"results":[]}', "")]:
            with self.subTest(result=result), self.assertRaises(mod.PreparationError):
                mod.gate_state(result)

    def test_delayed_readiness_stops_before_returning_prepared_profile(self):
        backend = FakeBackend()
        result = self.run_prepare(backend)
        self.assertEqual(backend.events, ["create", "start", "probe", "probe", "stop"])
        self.assertFalse(backend.active)
        self.assertNotIn("instance", result)
        self.assertTrue(result["requires_acquisition_check"])

    def test_timeout_and_probe_error_and_interruption_stop_only_owned_instance(self):
        for probes in [(), (mod.PreparationError("probe_error"),),
                       (mod.PreparationError("preparation_interrupted"),)]:
            backend = FakeBackend(probes)
            with self.assertRaises(mod.PreparationError):
                self.run_prepare(backend)
            self.assertEqual(backend.events[-1], "stop")
            self.assertFalse(backend.active)

    def test_start_timeout_still_cleans_up(self):
        backend = FakeBackend()
        backend.failure = subprocess.TimeoutExpired("pinchtab", 15)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_prepare(backend)
        self.assertEqual(backend.events[-1], "stop")

    def test_existing_profile_and_foreign_start_are_never_stopped(self):
        for field in ["exists", "active"]:
            backend = FakeBackend()
            setattr(backend, field, True)
            with self.assertRaises(mod.PreparationError):
                self.run_prepare(backend)
            self.assertEqual(backend.events, ["create"])

    def test_cleanup_failure_cannot_report_success(self):
        backend = FakeBackend((True,))
        with patch.object(backend, "stop_owned", side_effect=mod.PreparationError("cleanup_unverified")):
            with self.assertRaisesRegex(mod.PreparationError, "cleanup_unverified"):
                self.run_prepare(backend)

    def test_invalid_names_and_unbounded_deadlines_never_touch_profile(self):
        for name, timeout in [("../finance", 60), ("", 60), ("audit", 601), ("audit", 30)]:
            backend = FakeBackend()
            with self.assertRaises(mod.PreparationError):
                mod.prepare(name, backend, timeout)
            self.assertEqual(backend.events, [])

    def test_agent_session_is_not_silently_promoted_to_operator(self):
        result = subprocess.run(["bash", str(ROOT / "openclaw/bin/pinchtab-headless-instance"),
                                 "prepare", "audit-new"], capture_output=True, text=True,
                                env={**os.environ, "PINCHTAB_SESSION": "restricted-test"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("operator authorization", result.stderr)

    def test_backend_start_scopes_domains_and_never_navigates(self):
        backend = object.__new__(mod.Backend)
        backend.cli = "pinchtab"
        with patch.object(backend, "run", return_value="{}") as run:
            backend.start("audit-new")
        args = run.call_args.args[0]
        self.assertEqual(args[:7], ["pinchtab", "instance", "start", "--profile", "audit-new", "--mode", "headless"])
        self.assertEqual(args[7::2], ["--allow-domain"] * 14)
        self.assertEqual(args[8::2], mod.HOSTS)

    def test_create_empty_default_port_and_no_proxy_or_redirect_credentials(self):
        backend = object.__new__(mod.Backend)
        backend.cli = "pinchtab"
        backend.config = {"server": {"port": "", "token": "test-only"}}
        backend.deadline = None
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 201
        with patch.object(backend, "run"), patch.object(backend, "cli_json", side_effect=[
            [], [{"name": "audit-new", "id": "prof_test"}]
        ]), patch.dict(os.environ, {"PINCHTAB_TOKEN": ""}), patch.object(
            mod.urllib.request, "build_opener", return_value=opener
        ) as build:
            self.assertEqual(backend.create("audit-new"), "prof_test")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:9867/profiles")
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsNone(build.call_args.args[1].redirect_request(None, None, 302, "", {}, "https://example.org"))

    def test_existing_profile_rejected_before_post(self):
        backend = object.__new__(mod.Backend)
        backend.cli = "pinchtab"
        with patch.object(backend, "run"), patch.object(backend, "cli_json", return_value=[
            {"name": "audit-new", "id": "prof_existing"}
        ]), patch.object(mod.urllib.request, "build_opener") as build:
            with self.assertRaisesRegex(mod.PreparationError, "profile_already_exists"):
                backend.create("audit-new")
            build.assert_not_called()

    def test_each_subprocess_is_clamped_to_remaining_budget(self):
        backend = object.__new__(mod.Backend)
        backend.deadline = 105
        with patch.object(mod.time, "monotonic", return_value=100), patch.object(
            mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok", "")
        ) as run:
            self.assertEqual(backend.run(["pinchtab", "health"], timeout=10), "ok")
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
        with patch.object(mod.time, "monotonic", return_value=106):
            with self.assertRaisesRegex(mod.PreparationError, "deadline"):
                backend.run(["pinchtab", "health"])

    def test_probe_deadline_is_distinct_and_never_clearance(self):
        result = subprocess.CompletedProcess([], 2, "", "Browser trust check deadline exceeded")
        with self.assertRaisesRegex(mod.PreparationError, "trust_probe_deadline_exceeded"):
            mod.gate_state(result)

    def test_single_probe_deadline_retries_but_requires_complete_fresh_pass(self):
        backend = FakeBackend((mod.PreparationError("trust_probe_deadline_exceeded"), True))
        result = self.run_prepare(backend, timeout=120)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(backend.events.count("probe"), 2)
        self.assertEqual(backend.events[-1], "stop")

    def test_second_probe_deadline_aborts_and_cleans_up(self):
        backend = FakeBackend((mod.PreparationError("trust_probe_deadline_exceeded"),
                               mod.PreparationError("trust_probe_deadline_exceeded"), True))
        with self.assertRaisesRegex(mod.PreparationError, "trust_probe_deadline_exceeded"):
            self.run_prepare(backend, timeout=120)
        self.assertEqual(backend.events.count("probe"), 2)
        self.assertEqual(backend.events[-1], "stop")

    def test_cleanup_stops_exact_owned_instance_and_checks_it_is_gone(self):
        backend = object.__new__(mod.Backend)
        backend.cli = "pinchtab"
        with patch.object(backend, "instances", side_effect=[[{"id": "inst_owned"}], []]) as instances, patch.object(
            backend, "run"
        ) as run:
            backend.stop_owned("prof_owned")
        self.assertEqual([c.args for c in instances.call_args_list], [("prof_owned",), ("prof_owned",)])
        run.assert_called_once_with(["pinchtab", "instance", "stop", "inst_owned"])

    def test_ambiguous_cleanup_never_stops_any_instance(self):
        backend = object.__new__(mod.Backend)
        backend.cli = "pinchtab"
        with patch.object(backend, "instances", return_value=[{"id": "inst_a"}, {"id": "inst_b"}]), patch.object(
            backend, "run"
        ) as run:
            with self.assertRaisesRegex(mod.PreparationError, "cleanup_identity_ambiguous"):
                backend.stop_owned("prof_owned")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
