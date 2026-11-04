"""Fake-only OAuth exchange, cache isolation, and caller routing regression tests."""
from __future__ import annotations

import importlib.util
import importlib.machinery
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import nest_routing

SPEC = importlib.util.spec_from_file_location("nest_oauth_test", BIN / "nest-oauth.py")
oauth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oauth)


class NestOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.home.mkdir(mode=0o700)
        self.root = self.home / ".openclaw" / "nest-agent"
        for directory in [self.home / ".openclaw", self.root, self.root / "credentials", self.root / "tokens", self.root / "config"]:
            directory.mkdir(mode=0o700)
        self.cache = self.root / "tokens"
        self.config = dict(version=1, client_id="222-agent.apps.googleusercontent.com", client_secret="fake-agent-secret",
            project_id="agent-project", redirect_uri="https://www.google.com", refresh_token="fake-agent-refresh")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start(); self.addCleanup(self.env.stop)
        patch = mock.patch.object(oauth, "ROOT", self.root)
        patch.start(); self.addCleanup(patch.stop)
        self.legacy = self.home / ".openclaw" / ".secrets-cache"
        self.legacy.write_text("NEST_CLIENT_ID=111-legacy.apps.googleusercontent.com\nNEST_PROJECT_ID=legacy-project\nNEST_CLIENT_SECRET=fake-legacy-secret\nNEST_REFRESH_TOKEN=fake-legacy-refresh\n")
        self.legacy.chmod(0o600)
        oauth.write_json(self.root / "credentials/client.json", self.config)
        self.response = dict(access_token="fake-agent-access", refresh_token="new-refresh", expires_in=3600)

    def callback(self, **changes):
        url = oauth.begin(self.config, self.cache)
        state = parse_qs(urlsplit(url).query)["state"][0]
        fields = dict(state=state, code="code+encoded/slash")
        fields.update(changes)
        return "https://www.google.com/?" + urlencode(fields)

    def test_state_bound_exchange_and_atomic_token_bundle(self):
        callback = self.callback()
        with mock.patch.object(oauth, "exchange", return_value=self.response) as exchange:
            oauth.finish(self.config, self.cache, callback)
        self.assertEqual(exchange.call_args.args[0]["code"], "code+encoded/slash")
        record = oauth.read_json(self.cache / "oauth-tokens.json")
        self.assertEqual(record["identity"], oauth.identity(self.config))
        self.assertEqual(record["refresh_token"], "new-refresh")
        self.assertFalse((self.cache / "pending-authorization.json").exists())
        with self.assertRaises(FileNotFoundError):
            oauth.finish(self.config, self.cache, callback)

    def test_exchange_uses_fixed_https_body_and_bounded_transport(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(self.response).encode()
        opener = mock.Mock()
        opener.open.return_value = response
        form = dict(client_secret="fake-private-secret", code="fake+private/code")
        with mock.patch.object(oauth.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(oauth.exchange(form), self.response)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, oauth.TOKEN_URL)
        self.assertEqual(parse_qs(request.data.decode()), {k: [v] for k,v in form.items()})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 20)
        response.__enter__.return_value.read.assert_called_once_with(oauth.LIMIT + 1)
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(h, oauth.NoRedirect) for h in handlers))
        self.assertTrue(any(isinstance(h, oauth.urllib.request.ProxyHandler) and h.proxies == {} for h in handlers))
        with self.assertRaisesRegex(oauth.AuthError, "^oauth_redirect_rejected$"):
            oauth.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_provider_errors_do_not_leak_response_details(self):
        opener = mock.Mock()
        opener.open.side_effect = oauth.urllib.error.URLError("PRIVATE-TOKEN-IN-PROVIDER-ERROR")
        with mock.patch.object(oauth.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(oauth.AuthError, "^oauth_exchange_failed$"):
                oauth.exchange(dict(code="fake-code"))

    def test_invalid_callbacks_never_reach_network(self):
        valid = self.callback()
        cases = [valid.replace("www.google.com", "evil.example"), valid + "#fragment",
                 valid + "&state=duplicate", valid + "&code=duplicate", self.callback(state="wrong"), "bare-code"]
        for value in cases:
            with self.subTest(value=value), mock.patch.object(oauth, "exchange") as exchange:
                with self.assertRaises(oauth.AuthError):
                    oauth.finish(self.config, self.cache, value)
                exchange.assert_not_called()

    def test_expired_or_changed_authorization_rejected(self):
        callback = self.callback()
        with mock.patch.object(oauth.time, "time", return_value=time.time() + 601):
            with self.assertRaises(oauth.AuthError):
                oauth.finish(self.config, self.cache, callback)
        changed = dict(self.config, project_id="other-project")
        with self.assertRaises(oauth.AuthError):
            oauth.finish(changed, self.cache, callback)

    def test_ambiguous_failure_consumes_state_and_preserves_tokens(self):
        oauth.save_tokens(self.config, self.cache, self.response)
        original = (self.cache / "oauth-tokens.json").read_bytes()
        callback = self.callback()
        with mock.patch.object(oauth, "exchange", side_effect=oauth.AuthError("oauth_exchange_failed")):
            with self.assertRaises(oauth.AuthError):
                oauth.finish(self.config, self.cache, callback)
        self.assertEqual((self.cache / "oauth-tokens.json").read_bytes(), original)
        self.assertFalse((self.cache / "pending-authorization.json").exists())

    def test_partial_response_preserves_existing_bundle(self):
        oauth.save_tokens(self.config, self.cache, self.response)
        original = (self.cache / "oauth-tokens.json").read_bytes()
        with self.assertRaises(oauth.AuthError):
            oauth.save_tokens(self.config, self.cache, dict(access_token="new-access", expires_in=3600))
        self.assertEqual((self.cache / "oauth-tokens.json").read_bytes(), original)

    def test_tokens_cannot_cross_client_or_project(self):
        oauth.save_tokens(self.config, self.cache, self.response)
        for changed in [dict(self.config, project_id="another-project"), dict(self.config, client_id="333-other.apps.googleusercontent.com")]:
            with mock.patch.object(oauth, "exchange") as exchange:
                with self.assertRaises(oauth.AuthError):
                    oauth.token(changed, self.cache)
                exchange.assert_not_called()

    def test_expiry_refreshes_and_saves_rotated_refresh_token(self):
        record = oauth.save_tokens(self.config, self.cache, self.response)
        record["expires_at"] = time.time() - 1
        oauth.write_json(self.cache / "oauth-tokens.json", record)
        with mock.patch.object(oauth, "exchange", return_value=dict(self.response, refresh_token="rotated")) as exchange:
            self.assertEqual(oauth.token(self.config, self.cache), "fake-agent-access")
        self.assertEqual(exchange.call_args.args[0]["refresh_token"], "new-refresh")
        self.assertEqual(oauth.read_json(self.cache / "oauth-tokens.json")["refresh_token"], "rotated")

    def test_agent_rejects_legacy_identity(self):
        for changed in [dict(self.config, project_id="legacy-project"), dict(self.config, client_id="111-legacy.apps.googleusercontent.com")]:
            with self.assertRaises(oauth.AuthError):
                oauth.validate(changed, True)

    def test_private_file_permissions_symlinks_and_hardlinks(self):
        target = self.root / "credentials/client.json"
        target.chmod(0o644)
        with self.assertRaises(oauth.AuthError):
            oauth.load_context("agent")
        target.chmod(0o600)
        alias = target.with_name("alias.json")
        alias.symlink_to(target)
        with self.assertRaises(OSError):
            oauth.read_json(alias)
        alias.unlink(); os.link(target, alias)
        with self.assertRaises(oauth.AuthError):
            oauth.read_json(target)

    def test_concurrent_oauth_attempt_is_rejected(self):
        with oauth.locked(self.cache):
            with self.assertRaises(oauth.AuthError):
                with oauth.locked(self.cache):
                    pass

    def test_routing_default_enabled_and_invalid(self):
        old = Path("/opt/homebrew/bin/nest")
        self.assertEqual(nest_routing.selected_binary(old), old)
        oauth.write_json(self.root / "routing.json", dict(version=1, agent_enabled=True))
        self.assertEqual(nest_routing.selected_binary(old), self.home / ".openclaw/bin/nest-agent")
        # Missing agent credentials do not cause selector fallback.
        (self.root / "credentials/client.json").unlink()
        self.assertEqual(nest_routing.selected_binary(old).name, "nest-agent")
        oauth.write_json(self.root / "routing.json", dict(version=1, agent_enabled="true"))
        with self.assertRaises(RuntimeError):
            nest_routing.selected_binary(old)

    def run_agent(self, *arguments, extra=None):
        env = os.environ.copy()
        env.update(extra or {})
        return subprocess.run(["bash", str(BIN / "nest-agent"), *arguments], capture_output=True, text=True, env=env, timeout=10)

    def test_missing_agent_context_cannot_use_inherited_legacy_secrets(self):
        (self.root / "credentials/client.json").unlink()
        result = self.run_agent("raw", extra=dict(NEST_CLIENT_ID="legacy", NEST_CLIENT_SECRET="DO-NOT-PRINT", NEST_PROJECT_ID="legacy-project"))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("DO-NOT-PRINT", result.stdout + result.stderr)

    def test_agent_blocks_service_and_history_commands(self):
        for args in [("snapshot",), ("dashboard", "start"), ("camera", "snap", "Kitchen", "/tmp/no")]:
            self.assertNotEqual(self.run_agent(*args).returncode, 0)

    def test_cli_rejects_code_in_argv_without_echoing_it(self):
        result = subprocess.run(["bash", str(BIN / "nest"), "reauth", "PRIVATE-AUTH-CODE"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PRIVATE-AUTH-CODE", result.stdout + result.stderr)

    def test_bash_passes_credentials_on_fd_and_callback_on_stdin(self):
        scripts = self.home / "scripts"; scripts.mkdir()
        shutil.copy2(BIN / "nest", scripts / "nest")
        (scripts / "nest-oauth.py").write_text('''import os,sys,json
values=os.read(3,65536)
callback=sys.stdin.read()
print(json.dumps({"private_args":any("fake-legacy-secret" in a or "PRIVATE-CODE" in a for a in sys.argv),"secret_pipe":b"fake-legacy-secret" in values,"callback_stdin":"PRIVATE-CODE" in callback}))
''')
        result = subprocess.run(["bash", str(scripts / "nest"), "reauth", "--stdin"], input="https://www.google.com/?code=PRIVATE-CODE&state=state", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(private_args=False, secret_pipe=True, callback_stdin=True))

    def test_agent_ignores_old_cache_and_routes_api_to_new_project(self):
        oauth.save_tokens(self.config, self.cache, self.response)
        fake_bin = self.home / "fake-bin"; fake_bin.mkdir()
        log = self.home / "fake-request.json"
        (fake_bin / "curl").write_text("#!/usr/bin/env python3\nimport sys,json\nfrom pathlib import Path\ntext=sys.stdin.read()\nPath(" + repr(str(log)) + ").write_text(text)\nprint('{\"devices\":[]}')\n")
        (fake_bin / "curl").chmod(0o700)
        result = self.run_agent("raw", extra=dict(PATH=str(fake_bin)+":"+os.environ["PATH"], NEST_PROJECT_ID="legacy-project", NEST_CACHE_DIR=str(self.home / "old-cache")))
        self.assertEqual(result.returncode, 0, result.stderr)
        request = log.read_text()
        self.assertIn("enterprises/agent-project/devices", request)
        self.assertIn("fake-agent-access", request)
        self.assertNotIn("legacy-project", request)

    def test_agent_requires_attended_exact_field_configuration(self):
        with mock.patch.object(oauth.sys.stdin, "isatty", return_value=False), mock.patch.object(oauth.subprocess, "run") as run:
            with self.assertRaises(oauth.AuthError):
                oauth.configure_agent()
            run.assert_not_called()

    def test_materialization_reads_exact_fields_and_preserves_config_on_failure(self):
        refs = {key: "op://OpenClaw/OpenClaw Nest Agent/" + key for key in ("client_id", "client_secret", "project_id")}
        refs["redirect_uri"] = self.config["redirect_uri"]
        oauth.write_json(self.root / "credentials/references.json", refs)
        def read(args, **kwargs):
            self.assertEqual(args[:2], ["op", "read"])
            return subprocess.CompletedProcess(args, 0, self.config[args[2].rsplit("/", 1)[1]], "")
        with mock.patch.object(oauth.sys.stdin, "isatty", return_value=True), mock.patch.object(oauth.sys.stdout, "isatty", return_value=True), mock.patch("builtins.input", return_value="CONFIGURE"), mock.patch("builtins.print"), mock.patch.object(oauth.subprocess, "run", side_effect=read) as run:
            oauth.configure_agent()
            self.assertEqual(run.call_count, 3)
            original = (self.root / "credentials/client.json").read_bytes()
            run.side_effect = subprocess.TimeoutExpired("op", 60)
            with self.assertRaises(subprocess.TimeoutExpired):
                oauth.configure_agent()
            self.assertEqual((self.root / "credentials/client.json").read_bytes(), original)

    def test_camera_rejects_old_project_policy_and_ignores_override(self):
        entries = [("Kitchen", "Cabin", "live"), ("Living Room", "Crosstown", "event"), ("Living Room Wired", "Crosstown", "event")]
        policy = dict(version=1, cameras=[dict(alias=a, site=s, capture=c, resource=f"enterprises/legacy-project/devices/{i}") for i, (a,s,c) in enumerate(entries)])
        oauth.write_json(self.root / "config/cameras.json", policy)
        # Fake transport is a tripwire; no API should run for an invalid binding.
        fake_bin = self.home / "fake-bin"; fake_bin.mkdir()
        tripwire = self.home / "network-called"
        (fake_bin / "curl").write_text("#!/bin/sh\ntouch " + shlex.quote(str(tripwire)) + "\nexit 99\n")
        (fake_bin / "curl").chmod(0o700)
        for command in [("snap-config", "Kitchen", str(self.home / "out.jpg")), ("clip-config", "Kitchen", "2", str(self.home / "out.mp4"))]:
            result = self.run_agent("camera", *command, extra=dict(PATH=str(fake_bin)+":"+os.environ["PATH"], NEST_EVENT_CONFIG="/tmp/ignored-policy"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not authorize capture", result.stderr)
            self.assertFalse(tripwire.exists())

    def test_all_camera_callers_select_agent_and_fail_without_fallback(self):
        oauth.write_json(self.root / "routing.json", dict(version=1, agent_enabled=True))
        target = self.home / ".openclaw/bin/nest-agent"
        target.parent.mkdir(); target.write_text("#!/bin/sh\nexit 1\n"); target.chmod(0o700)
        for filename, cls in [("nest-camera-image", None), ("nest-activity-reviewer.py", "ProcessCommands"), ("cabin-entry-verifier.py", "VerifierCommands"), ("home-event-camera.py", "CameraCommands")]:
            with self.subTest(caller=filename):
                loader = importlib.machinery.SourceFileLoader("routing_test_" + filename.replace("-", "_").replace(".", "_"), str(BIN / filename))
                spec = importlib.util.spec_from_loader(loader.name, loader)
                module = importlib.util.module_from_spec(spec); sys.modules[loader.name] = module; loader.exec_module(module)
                if cls is None:
                    with mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
                        with self.assertRaises(module.PublicError):
                            module.capture_image("Kitchen", media_directory=self.home / "media", reaper=lambda *a: None)
                    self.assertEqual(run.call_count, 1)
                    self.assertEqual(run.call_args.args[0][0], str(target))
                else:
                    commands = object.__new__(getattr(module, cls))
                    with mock.patch.object(commands, "_environment", return_value={}), mock.patch.object(commands, "_run", side_effect=RuntimeError("transport failed")) as run:
                        with self.assertRaises(RuntimeError):
                            if cls == "CameraCommands":
                                commands.capture("nest", "Cabin", "Kitchen", self.home / "out.jpg")
                            else:
                                commands.capture(self.home / "out.jpg")
                    self.assertEqual(run.call_count, 1)
                    self.assertEqual(run.call_args.args[0][0], str(target))


if __name__ == "__main__":
    unittest.main()
