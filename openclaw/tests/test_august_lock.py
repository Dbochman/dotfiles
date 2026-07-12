#!/usr/bin/env python3
"""Fake-only safety tests for the August lock wrapper and Node CLI."""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "august-lock"
WRAPPER = SKILL_DIR / "august"
BIN_WRAPPER = REPO_ROOT / "openclaw" / "bin" / "august"
NODE_CLI = SKILL_DIR / "august-cmd.js"
DOTFILES_PULL = REPO_ROOT / "openclaw" / "bin" / "dotfiles-pull.command"
LOCK_ID = "7EDFA965E0AE0CE19772AFA435364295"

LOCKED_CLOSED = {
    "lockID": LOCK_ID,
    "status": "kAugLockState_Locked",
    "doorState": "kAugDoorState_Closed",
    "state": {"locked": True, "unlocked": False, "closed": True, "open": False},
}
UNLOCKED_CLOSED = {
    "lockID": LOCK_ID,
    "status": "kAugLockState_Unlocked",
    "doorState": "kAugDoorState_Closed",
    "state": {"locked": False, "unlocked": True, "closed": True, "open": False},
}
LOCKED_OPEN = {
    "lockID": LOCK_ID,
    "status": "kAugLockState_Locked",
    "doorState": "kAugDoorState_Open",
    "state": {"locked": True, "unlocked": False, "closed": False, "open": True},
}


class AugustLockTests(unittest.TestCase):
    def test_bin_entrypoint_delegates_to_deployed_skill_copy(self) -> None:
        deployed = self.home / ".openclaw" / "skills" / "august-lock" / "august"
        deployed.parent.mkdir(parents=True)
        self._write_executable(
            deployed,
            "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
        )

        result = subprocess.run(
            [str(BIN_WRAPPER), "status", LOCK_ID],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(),
        )

        self.assertTrue(os.access(BIN_WRAPPER, os.X_OK))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["status", LOCK_ID])

    def test_remote_command_parser_is_in_the_mbp_sync_contract(self) -> None:
        deploy_script = DOTFILES_PULL.read_text(encoding="utf-8")
        self.assertIn(
            '"openclaw/skills/august-lock/august-cmd.js:'
            '.openclaw/august/august-cmd.js"',
            deploy_script,
        )

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.module_root = self.root / "modules"
        self.api_log = self.root / "august-api.log"
        self.ssh_log = self.root / "ssh.json"
        self.ssh_calls_log = self.root / "ssh-calls.jsonl"
        self.fs_log = self.root / "fs.log"
        self.approval_cache = self.root / "august-unlock-approvals"
        self.home.mkdir()
        self.fake_bin.mkdir()
        (self.module_root / "august-api").mkdir(parents=True)

        self._write_executable(
            self.fake_bin / "ssh",
            r'''#!/usr/bin/env python3
import base64
import json
import os
import re
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_SSH_LOG"], "w", encoding="utf-8") as handle:
    json.dump(args, handle)
with open(os.environ["FAKE_SSH_CALLS"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

match = re.search(r"--argv-base64 '([A-Za-z0-9+/=]+)'$", args[-1])
if match is None:
    print("RAW_SSH_PARSE_CANARY", file=sys.stderr)
    raise SystemExit(70)
remote_args = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))

locked_closed = {
    "lockID": "7EDFA965E0AE0CE19772AFA435364295",
    "status": "kAugLockState_Locked",
    "doorState": "kAugDoorState_Closed",
    "state": {"locked": True, "unlocked": False, "closed": True, "open": False},
}
unlocked_closed = {
    "ok": True,
    "action": "unlock",
    "verified": True,
    "lockID": "7EDFA965E0AE0CE19772AFA435364295",
    "status": "kAugLockState_Unlocked",
    "doorState": "kAugDoorState_Closed",
    "state": {"locked": False, "unlocked": True, "closed": True, "open": False},
}
sanitized_observation = {
    "ok": True,
    "alias": "front_door",
    "observed_at": "2026-01-01T12:00:00.000Z",
    "lock_state": "locked",
    "door_state": "closed",
    "battery_percent": 75,
}

if remote_args[0] == "status":
    if os.environ.get("FAKE_STATUS_FAIL") == "1":
        print("RAW_SSH_STATUS_CANARY", file=sys.stderr)
        raise SystemExit(71)
    print(os.environ.get("FAKE_REMOTE_STATUS", json.dumps(locked_closed)))
elif remote_args[0] == "unlock":
    if os.environ.get("FAKE_UNLOCK_FAIL") == "1":
        print("RAW_SSH_UNLOCK_CANARY", file=sys.stderr)
        raise SystemExit(72)
    print(os.environ.get("FAKE_UNLOCK_RESULT", json.dumps(unlocked_closed)))
elif remote_args[0] == "observe":
    if os.environ.get("FAKE_OBSERVE_STDERR") == "1":
        print("RAW_SSH_OBSERVE_STDERR_CANARY_7EDFA965E0AE0CE19772AFA435364295", file=sys.stderr)
    if os.environ.get("FAKE_OBSERVE_FAIL") == "1":
        print("RAW_SSH_OBSERVE_STDOUT_CANARY_7EDFA965E0AE0CE19772AFA435364295")
        raise SystemExit(73)
    print(os.environ.get("FAKE_REMOTE_OBSERVE", json.dumps(sanitized_observation)))
else:
    print('{"fake_ssh":true}')
''',
        )
        (self.module_root / "august-api" / "index.js").write_text(
            r'''const fs = require('fs')

function record(method, detail = {}) {
  fs.appendFileSync(process.env.FAKE_AUGUST_LOG, JSON.stringify({ method, ...detail }) + '\n')
}

class FakeAugust {
  constructor(config) {
    this.statusIndex = 0
    record('constructor', {
      hasInstallId: typeof config.installId === 'string',
      hasAugustId: typeof config.augustId === 'string',
      hasPassword: typeof config.password === 'string',
    })
  }

  async authorize() { record('authorize'); return { ok: true } }
  async validate(code) { record('validate', { code }); return { ok: true } }
  async locks() { record('locks'); return [{ lockID: 'fake' }] }
  async details(lockId) { record('details', { lockId }); return { lockID: lockId || 'fake' } }
  async lock(lockId) { record('lock', { lockId }); return { accepted: true } }
  async unlock(lockId) { record('unlock', { lockId }); return { accepted: true } }

  async status(lockId) {
    record('status', { lockId })
    const sequence = JSON.parse(process.env.FAKE_STATUS_SEQUENCE || '[]')
    const value = sequence[Math.min(this.statusIndex, sequence.length - 1)]
    this.statusIndex += 1
    if (value && value.throw) throw new Error('fake transient status failure')
    return value || {
      lockID: '7EDFA965E0AE0CE19772AFA435364295',
      status: 'kAugLockState_Locked',
      doorState: 'kAugDoorState_Closed',
      state: { locked: true, unlocked: false, closed: true, open: false },
    }
  }
}

module.exports = FakeAugust
''',
            encoding="utf-8",
        )
        self.fs_hooks = self.root / "fs-hooks.js"
        self.fs_hooks.write_text(
            r'''const fs = require('fs')

const hookLog = process.env.FAKE_FS_LOG
const originalOpen = fs.openSync
const originalFsync = fs.fsyncSync
const originalRename = fs.renameSync

fs.openSync = function (...args) {
  if (hookLog && String(args[0]).includes('.config.json.')) fs.appendFileSync(hookLog, 'open\n')
  return originalOpen.apply(this, args)
}
fs.fsyncSync = function (...args) {
  if (hookLog) fs.appendFileSync(hookLog, 'fsync\n')
  return originalFsync.apply(this, args)
}
fs.renameSync = function (...args) {
  if (hookLog && String(args[1]).endsWith('/config.json')) fs.appendFileSync(hookLog, 'rename\n')
  if (process.env.FAKE_RENAME_FAILURE === '1' && String(args[1]).endsWith('/config.json')) {
    throw new Error('fake rename failure')
  }
  return originalRename.apply(this, args)
}
''',
            encoding="utf-8",
        )

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @property
    def config_path(self) -> Path:
        return self.home / ".openclaw" / "august" / "config.json"

    def write_config(self, content: object | str, mode: int = 0o600) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.parent.chmod(0o700)
        text = content if isinstance(content, str) else json.dumps(content)
        self.config_path.write_text(text, encoding="utf-8")
        self.config_path.chmod(mode)

    def valid_config(self) -> dict[str, str]:
        return {
            "installId": "fake-install-id",
            "augustId": "fake@example.invalid",
            "password": "fake-password",
        }

    def environment(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "NODE_PATH": str(self.module_root),
                "NODE_OPTIONS": f"--require={self.fs_hooks}",
                "FAKE_AUGUST_LOG": str(self.api_log),
                "FAKE_SSH_LOG": str(self.ssh_log),
                "FAKE_SSH_CALLS": str(self.ssh_calls_log),
                "FAKE_FS_LOG": str(self.fs_log),
                "AUGUST_APPROVAL_CACHE_DIR": str(self.approval_cache),
                "AUGUST_VERIFY_DELAY_MS": "0",
            }
        )
        env.update(overrides)
        return env

    def run_node(self, *args: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", str(NODE_CLI), *args],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(**env_overrides),
        )

    def run_wrapper(
        self, *args: str, **env_overrides: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(WRAPPER), *args],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(**env_overrides),
        )

    def api_calls(self) -> list[dict]:
        if not self.api_log.exists():
            return []
        return [json.loads(line) for line in self.api_log.read_text().splitlines()]

    def ssh_calls(self) -> list[list[str]]:
        if not self.ssh_calls_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.ssh_calls_log.read_text(encoding="utf-8").splitlines()
        ]

    def decoded_remote_calls(self) -> list[list[str]]:
        decoded = []
        for ssh_args in self.ssh_calls():
            match = re.search(
                r"--argv-base64 '([A-Za-z0-9+/=]+)'$", ssh_args[-1]
            )
            self.assertIsNotNone(match)
            decoded.append(
                json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
            )
        return decoded

    @staticmethod
    def error_output(result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stderr.splitlines()[-1])

    @staticmethod
    def wrapper_output(result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stdout.splitlines()[-1])

    def test_wrapper_encodes_validated_arguments_for_ssh(self) -> None:
        result = self.run_wrapper("status", LOCK_ID)

        self.assertEqual(result.returncode, 0, result.stderr)
        ssh_args = json.loads(self.ssh_log.read_text(encoding="utf-8"))
        self.assertEqual(
            ssh_args[:9],
            [
                "-i",
                str(self.home / ".ssh" / "id_mini_to_mbp"),
                "-o",
                "IdentityAgent=none",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                "dylans-macbook-pro",
            ],
        )
        remote_command = ssh_args[-1]
        self.assertNotIn(LOCK_ID, remote_command)
        match = re.search(r"--argv-base64 '([A-Za-z0-9+/=]+)'$", remote_command)
        self.assertIsNotNone(match)
        decoded = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
        self.assertEqual(decoded, ["status", LOCK_ID])

    def test_wrapper_rejects_commands_codes_ids_and_invalid_approval_forms(self) -> None:
        invalid = [
            ("status;touch", "/tmp/unsafe"),
            ("validate", "12345x"),
            ("status", f"{LOCK_ID};touch"),
            ("unlock", "not-a-lock-id"),
            ("unlock", "--confirm"),
            ("unlock", "--confirm", "short"),
            ("unlock", LOCK_ID, "extra"),
        ]
        for args in invalid:
            with self.subTest(args=args):
                self.ssh_log.unlink(missing_ok=True)
                result = self.run_wrapper(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.ssh_log.exists())

    def test_unlock_preview_creates_protected_exact_approval(self) -> None:
        result = self.run_wrapper("unlock", LOCK_ID)

        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.wrapper_output(result)
        self.assertEqual(output["mode"], "preview")
        self.assertEqual(output["status"], "ready_to_confirm")
        self.assertEqual(output["lock_id"], LOCK_ID)
        self.assertEqual(output["observed_lock_state"], "locked")
        self.assertEqual(output["observed_door_state"], "closed")
        self.assertEqual(self.decoded_remote_calls(), [["status", LOCK_ID]])

        self.assertEqual(stat.S_IMODE(self.approval_cache.stat().st_mode), 0o700)
        state_files = list(self.approval_cache.glob("*.json"))
        self.assertEqual(len(state_files), 1)
        self.assertEqual(state_files[0].name, f'{output["approval_id"]}.json')
        self.assertEqual(stat.S_IMODE(state_files[0].stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE((self.approval_cache / ".lock").stat().st_mode), 0o600
        )
        state = json.loads(state_files[0].read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["lock_id"], LOCK_ID)
        self.assertEqual(state["lock_state"], "locked")
        self.assertEqual(state["door_state"], "closed")

    def test_unlock_confirmation_is_one_use_and_mutates_once(self) -> None:
        preview = self.run_wrapper("unlock", LOCK_ID)
        approval_id = self.wrapper_output(preview)["approval_id"]

        confirmed = self.run_wrapper("unlock", "--confirm", approval_id)

        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        output = self.wrapper_output(confirmed)
        self.assertEqual(output["mode"], "commit")
        self.assertEqual(output["status"], "confirmed")
        self.assertEqual(output["approval_id"], approval_id)
        self.assertEqual(output["lock_state"], "unlocked")
        self.assertEqual(
            self.decoded_remote_calls(),
            [
                ["status", LOCK_ID],
                ["status", LOCK_ID],
                ["unlock", "--confirm", LOCK_ID],
            ],
        )

        replay = self.run_wrapper("unlock", "--confirm", approval_id)
        self.assertNotEqual(replay.returncode, 0)
        self.assertEqual(self.wrapper_output(replay)["error_code"], "approval_replayed")
        self.assertEqual(len(self.decoded_remote_calls()), 3)

    def test_unlock_confirmation_rejects_changed_bound_facts(self) -> None:
        changed_states = (
            LOCKED_OPEN,
            {**LOCKED_CLOSED, "lockID": "A" * 32},
        )

        for changed_state in changed_states:
            with self.subTest(changed_state=changed_state):
                preview = self.run_wrapper("unlock", LOCK_ID)
                approval_id = self.wrapper_output(preview)["approval_id"]
                calls_before_confirm = len(self.decoded_remote_calls())

                changed = self.run_wrapper(
                    "unlock",
                    "--confirm",
                    approval_id,
                    FAKE_REMOTE_STATUS=json.dumps(changed_state),
                )

                self.assertNotEqual(changed.returncode, 0)
                self.assertEqual(
                    self.wrapper_output(changed)["error_code"],
                    "approval_facts_changed",
                )
                calls = self.decoded_remote_calls()
                self.assertEqual(len(calls), calls_before_confirm + 1)
                self.assertEqual(calls[-1], ["status", LOCK_ID])
                self.assertNotIn("unlock", [call[0] for call in calls[-1:]])

                replay = self.run_wrapper("unlock", "--confirm", approval_id)
                self.assertNotEqual(replay.returncode, 0)
                self.assertEqual(
                    self.wrapper_output(replay)["error_code"], "approval_replayed"
                )
                self.assertEqual(len(self.decoded_remote_calls()), len(calls))

    def test_unlock_ambiguous_remote_failure_is_nonretryable_and_sanitized(self) -> None:
        preview = self.run_wrapper("unlock", LOCK_ID)
        approval_id = self.wrapper_output(preview)["approval_id"]

        failed = self.run_wrapper(
            "unlock", "--confirm", approval_id, FAKE_UNLOCK_FAIL="1"
        )

        self.assertNotEqual(failed.returncode, 0)
        output = self.wrapper_output(failed)
        self.assertEqual(output["error_code"], "unlock_outcome_unknown")
        self.assertTrue(output["mutation_attempted"])
        self.assertTrue(output["non_retryable"])
        self.assertNotIn("RAW_SSH_UNLOCK_CANARY", failed.stdout + failed.stderr)
        self.assertEqual(
            self.decoded_remote_calls(),
            [
                ["status", LOCK_ID],
                ["status", LOCK_ID],
                ["unlock", "--confirm", LOCK_ID],
            ],
        )

        replay = self.run_wrapper("unlock", "--confirm", approval_id)
        self.assertNotEqual(replay.returncode, 0)
        self.assertEqual(self.wrapper_output(replay)["error_code"], "approval_replayed")
        self.assertEqual(len(self.decoded_remote_calls()), 3)

    def test_unlock_approval_expires_before_any_confirmation_call(self) -> None:
        preview = self.run_wrapper(
            "unlock", LOCK_ID, AUGUST_APPROVAL_TTL_SECONDS="1"
        )
        self.assertEqual(preview.returncode, 0, preview.stderr)
        approval_id = self.wrapper_output(preview)["approval_id"]
        time.sleep(1.1)

        expired = self.run_wrapper("unlock", "--confirm", approval_id)

        self.assertNotEqual(expired.returncode, 0)
        self.assertEqual(self.wrapper_output(expired)["error_code"], "approval_expired")
        self.assertEqual(self.decoded_remote_calls(), [["status", LOCK_ID]])

    def test_unlock_approval_rejects_insecure_malformed_and_symlink_state(self) -> None:
        corruptions = ("mode", "malformed", "symlink")
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                preview = self.run_wrapper("unlock", LOCK_ID)
                approval_id = self.wrapper_output(preview)["approval_id"]
                state_path = self.approval_cache / f"{approval_id}.json"
                expected_error = "approval_state_unsafe"
                if corruption == "mode":
                    state_path.chmod(0o644)
                elif corruption == "malformed":
                    state_path.write_text("{", encoding="utf-8")
                    state_path.chmod(0o600)
                    expected_error = "approval_state_invalid"
                else:
                    target = self.root / f"{approval_id}-target.json"
                    target.write_text(state_path.read_text(encoding="utf-8"), encoding="utf-8")
                    target.chmod(0o600)
                    state_path.unlink()
                    state_path.symlink_to(target)
                calls_before = len(self.decoded_remote_calls())

                result = self.run_wrapper("unlock", "--confirm", approval_id)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.wrapper_output(result)["error_code"], expected_error)
                self.assertEqual(len(self.decoded_remote_calls()), calls_before)

    def test_unlock_status_failure_is_sanitized(self) -> None:
        result = self.run_wrapper("unlock", LOCK_ID, FAKE_STATUS_FAIL="1")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.wrapper_output(result)["error_code"], "status_unavailable")
        self.assertNotIn("RAW_SSH_STATUS_CANARY", result.stdout + result.stderr)
        self.assertEqual(self.decoded_remote_calls(), [["status", LOCK_ID]])

    def test_unlock_preview_rejects_ambiguous_physical_state(self) -> None:
        contradictory = {
            **LOCKED_CLOSED,
            "state": {
                "locked": False,
                "unlocked": False,
                "closed": True,
                "open": False,
            },
        }

        result = self.run_wrapper(
            "unlock", LOCK_ID, FAKE_REMOTE_STATUS=json.dumps(contradictory)
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.wrapper_output(result)["error_code"], "status_invalid")
        self.assertEqual(list(self.approval_cache.glob("*.json")), [])
        self.assertEqual(self.decoded_remote_calls(), [["status", LOCK_ID]])

    def test_node_rejects_invalid_direct_and_encoded_invocations(self) -> None:
        self.write_config(self.valid_config())
        invalid_payload = base64.b64encode(b'{"not":"an array"}').decode("ascii")
        cases = [
            ("shell-command",),
            ("validate", "12345x"),
            ("status", "not-a-lock-id"),
            ("unlock",),
            ("--argv-base64", invalid_payload),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.api_log.unlink(missing_ok=True)
                result = self.run_node(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.api_calls(), [])

    def test_node_accepts_valid_encoded_invocation(self) -> None:
        self.write_config(self.valid_config())
        payload = base64.b64encode(
            json.dumps(["status", LOCK_ID], separators=(",", ":")).encode()
        ).decode("ascii")

        result = self.run_node("--argv-base64", payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["lockID"], LOCK_ID)
        self.assertEqual(
            [entry["method"] for entry in self.api_calls()],
            ["constructor", "status"],
        )

    def test_observe_returns_only_sanitized_bound_state(self) -> None:
        config = {
            **self.valid_config(),
            "observeLockId": LOCK_ID,
            "observeAlias": "front_door",
        }
        self.write_config(config)
        observed = {**LOCKED_OPEN, "batteryPercentage": 19}

        result = self.run_node(
            "observe", FAKE_STATUS_SEQUENCE=json.dumps([observed])
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(
            set(output),
            {
                "ok",
                "alias",
                "observed_at",
                "lock_state",
                "door_state",
                "battery_percent",
            },
        )
        self.assertEqual(output["alias"], "front_door")
        self.assertEqual(output["lock_state"], "locked")
        self.assertEqual(output["door_state"], "open")
        self.assertEqual(output["battery_percent"], 19)
        self.assertNotIn(LOCK_ID, result.stdout)
        self.assertEqual(
            self.api_calls()[-1], {"method": "status", "lockId": LOCK_ID}
        )

    def test_observe_requires_exact_protected_binding(self) -> None:
        self.write_config(self.valid_config())

        result = self.run_node("observe")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.error_output(result)["error_code"], "observe_binding_missing"
        )
        self.assertEqual(
            [entry["method"] for entry in self.api_calls()], ["constructor"]
        )

        self.write_config(
            {
                **self.valid_config(),
                "observeLockId": LOCK_ID,
                "observeAlias": "another_safe_alias",
            }
        )
        result = self.run_node("observe")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.error_output(result)["error_code"], "observe_binding_missing"
        )
        self.assertEqual(self.api_calls()[-1]["method"], "constructor")

    def test_wrapper_observe_uses_read_only_remote_command(self) -> None:
        result = self.run_wrapper("observe", FAKE_OBSERVE_STDERR="1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "ok": True,
                "alias": "front_door",
                "observed_at": "2026-01-01T12:00:00.000Z",
                "lock_state": "locked",
                "door_state": "closed",
                "battery_percent": 75,
            },
        )
        self.assertEqual(result.stderr, "")
        self.assertNotIn("7EDFA965E0AE0CE19772AFA435364295", result.stdout)
        self.assertEqual(self.decoded_remote_calls(), [["observe"]])

    def test_wrapper_observe_quarantines_remote_stdout_and_stderr(self) -> None:
        cases = (
            {
                "FAKE_OBSERVE_FAIL": "1",
                "FAKE_OBSERVE_STDERR": "1",
            },
            {
                "FAKE_REMOTE_OBSERVE": (
                    '{"ok":true,"alias":"front_door",'
                    '"observed_at":"2026-01-01T12:00:00.000Z",'
                    '"lock_state":"locked","door_state":"closed",'
                    f'"provider_lock_id":"{LOCK_ID}"}}'
                ),
                "FAKE_OBSERVE_STDERR": "1",
            },
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                result = self.run_wrapper("observe", **overrides)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.wrapper_output(result)["error_code"],
                    "observation_unavailable",
                )
                self.assertEqual(result.stderr, "")
                combined = result.stdout + result.stderr
                self.assertNotIn("RAW_SSH_OBSERVE", combined)
                self.assertNotIn(LOCK_ID, combined)

    def test_lock_retries_then_returns_verified_safe_state(self) -> None:
        self.write_config(self.valid_config())
        sequence = json.dumps([UNLOCKED_CLOSED, UNLOCKED_CLOSED, LOCKED_CLOSED])

        result = self.run_node("lock", LOCK_ID, FAKE_STATUS_SEQUENCE=sequence)

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["verified"])
        self.assertEqual(output["attempts"], 2)
        self.assertTrue(output["state"]["locked"])
        self.assertTrue(output["state"]["closed"])
        self.assertEqual(
            [entry["method"] for entry in self.api_calls()],
            ["constructor", "status", "lock", "status", "status"],
        )

    def test_lock_refuses_to_act_when_door_is_open(self) -> None:
        self.write_config(self.valid_config())

        result = self.run_node(
            "lock",
            LOCK_ID,
            FAKE_STATUS_SEQUENCE=json.dumps([LOCKED_OPEN]),
        )

        self.assertNotEqual(result.returncode, 0)
        error = self.error_output(result)
        self.assertEqual(error["error_code"], "door_not_closed")
        calls = [entry["method"] for entry in self.api_calls()]
        self.assertEqual(calls, ["constructor", "status"])
        self.assertTrue(error["observed"]["state"]["open"])

    def test_lock_rejects_contradictory_lock_or_door_state(self) -> None:
        self.write_config(self.valid_config())
        contradictory_states = (
            {
                **UNLOCKED_CLOSED,
                "state": {
                    "locked": True,
                    "unlocked": True,
                    "closed": True,
                    "open": False,
                },
            },
            {
                **UNLOCKED_CLOSED,
                "state": {
                    "locked": False,
                    "unlocked": True,
                    "closed": True,
                    "open": True,
                },
            },
        )

        for observed in contradictory_states:
            with self.subTest(observed=observed):
                self.api_log.unlink(missing_ok=True)
                result = self.run_node(
                    "lock",
                    LOCK_ID,
                    FAKE_STATUS_SEQUENCE=json.dumps([observed]),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.error_output(result)["error_code"],
                    "precondition_unavailable",
                )
                self.assertEqual(
                    [entry["method"] for entry in self.api_calls()],
                    ["constructor", "status"],
                )

    def test_lock_requires_known_unambiguous_closed_state(self) -> None:
        self.write_config(self.valid_config())
        unknown_states = (
            {
                "lockID": LOCK_ID,
                "status": "kAugLockState_Unknown",
                "doorState": "kAugDoorState_Closed",
                "state": {"closed": True, "open": False},
            },
            {
                "lockID": LOCK_ID,
                "status": "kAugLockState_Unlocked",
                "state": {"locked": False, "unlocked": True},
            },
        )

        for observed in unknown_states:
            with self.subTest(observed=observed):
                self.api_log.unlink(missing_ok=True)
                result = self.run_node(
                    "lock",
                    LOCK_ID,
                    FAKE_STATUS_SEQUENCE=json.dumps([observed]),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.error_output(result)["error_code"],
                    "precondition_unavailable",
                )
                self.assertNotIn(
                    "lock", [entry["method"] for entry in self.api_calls()]
                )

    def test_lock_noops_when_already_locked_and_closed(self) -> None:
        self.write_config(self.valid_config())

        result = self.run_node(
            "lock",
            LOCK_ID,
            FAKE_STATUS_SEQUENCE=json.dumps([LOCKED_CLOSED]),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["verified"])
        self.assertTrue(output["alreadySatisfied"])
        self.assertEqual(output["attempts"], 0)
        self.assertEqual(
            [entry["method"] for entry in self.api_calls()],
            ["constructor", "status"],
        )

    def test_invalid_verification_delay_fails_before_physical_action(self) -> None:
        self.write_config(self.valid_config())

        result = self.run_node("lock", AUGUST_VERIFY_DELAY_MS="unbounded")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.error_output(result)["error_code"], "invalid_verify_delay")
        self.assertEqual(
            [entry["method"] for entry in self.api_calls()],
            ["constructor"],
        )

    def test_unlock_requires_confirmation_and_verifies_known_door_state(self) -> None:
        self.write_config(self.valid_config())
        unconfirmed = self.run_node("unlock")
        self.assertNotEqual(unconfirmed.returncode, 0)
        self.assertEqual(self.error_output(unconfirmed)["error_code"], "confirmation_required")
        self.assertEqual(self.api_calls(), [])

        confirmed = self.run_node(
            "unlock",
            "--confirm",
            LOCK_ID,
            FAKE_STATUS_SEQUENCE=json.dumps([LOCKED_CLOSED, UNLOCKED_CLOSED]),
        )
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        output = json.loads(confirmed.stdout)
        self.assertTrue(output["verified"])
        self.assertTrue(output["state"]["unlocked"])
        self.assertTrue(output["state"]["closed"])

    def test_new_config_is_fsynced_renamed_and_mode_0600(self) -> None:
        result = self.run_node(
            "status",
            AUGUST_ID="fake@example.invalid",
            AUGUST_PASSWORD="fake-password",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.config_path.is_file())
        self.assertEqual(stat.S_IMODE(self.config_path.stat().st_mode), 0o600)
        stored = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(set(stored), {"installId"})
        self.assertEqual(self.fs_log.read_text().splitlines(), ["open", "fsync", "rename"])
        self.assertEqual(list(self.config_path.parent.glob("*.tmp")), [])

    def test_malformed_or_insecure_config_is_never_overwritten(self) -> None:
        malformed = '{"installId":'
        self.write_config(malformed)
        result = self.run_node(
            "status",
            AUGUST_ID="fake@example.invalid",
            AUGUST_PASSWORD="fake-password",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), malformed)
        self.assertEqual(self.api_calls(), [])

        self.write_config(self.valid_config(), mode=0o644)
        result = self.run_node("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.error_output(result)["error_code"], "insecure_config")
        self.assertEqual(stat.S_IMODE(self.config_path.stat().st_mode), 0o644)

        self.write_config(self.valid_config())
        self.config_path.parent.chmod(0o755)
        result = self.run_node("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.error_output(result)["error_code"], "insecure_config")
        self.assertEqual(self.api_calls(), [])

    def test_atomic_save_failure_is_nonzero_and_cleans_temp_file(self) -> None:
        result = self.run_node(
            "status",
            AUGUST_ID="fake@example.invalid",
            AUGUST_PASSWORD="fake-password",
            FAKE_RENAME_FAILURE="1",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.error_output(result)["error_code"], "config_save_failed")
        self.assertFalse(self.config_path.exists())
        self.assertEqual(list(self.config_path.parent.glob("*.tmp")), [])
        self.assertEqual(self.api_calls(), [])


if __name__ == "__main__":
    unittest.main()
