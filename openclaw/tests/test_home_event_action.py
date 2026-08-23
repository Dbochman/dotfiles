#!/usr/bin/env python3
"""Focused tests for exact vacancy action reservations and Hue readback."""

from __future__ import annotations

import importlib.util
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin/home_event_action.py"
SPEC = importlib.util.spec_from_file_location("home_event_action", MODULE_PATH)
assert SPEC and SPEC.loader
actions = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = actions
SPEC.loader.exec_module(actions)


NOW = "2026-08-22T15:00:00Z"


def policy(*, crosstown_owner: str = "bus") -> dict:
    entry = {
        "action": "turn_off",
        "desired_state": "all_off",
        "expiry_seconds": 600,
        "settle_seconds": 2,
    }
    return {
        "schema_version": 2,
        "active": True,
        "targets": {
            "cabin": {"all_lights": {**entry, "owner": "legacy"}},
            "crosstown": {
                "all_lights": {**entry, "owner": crosstown_owner},
                "daily_automations": {
                    **entry,
                    "owner": crosstown_owner,
                    "action": "suspend_restore",
                    "desired_state": "vacancy_suspended",
                    "automations": [
                        "Bedroom lights After dark",
                        "Master Bath Off",
                        "Potato Nightlight",
                    ],
                },
            },
        },
    }


class HomeEventActionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / "home-events"
        self.presence = self.home / "presence"
        self.journal = self.home / "vacancy-actions/journal"
        self.presence.mkdir(mode=0o700)
        (self.presence / "home-events-outbox").mkdir(mode=0o700)
        (self.journal / "cycles").mkdir(parents=True, mode=0o700)
        from home_event_bus import initialize_runtime

        initialize_runtime(self.root, clock=lambda: NOW)
        self.state_path = self.presence / "state.json"
        self.producer_path = self.presence / "home-events-outbox/producer-state.json"
        self.cycle_id = "cycle_" + ("a" * 32)
        self.write_presence()
        actions.install_policy(
            self.root,
            json.dumps(policy(), separators=(",", ":")).encode(),
        )
        self.hue_state = self.home / "hue-state"
        self.hue_state.write_text("off\n", encoding="utf-8")
        self.hue_log = self.home / "hue-log"
        self.automation_state = self.home / "automation-state.json"
        self.automation_state.write_text(
            json.dumps(
                {
                    "Bedroom lights After dark": True,
                    "Master Bath Off": False,
                    "Potato Nightlight": True,
                }
            ),
            encoding="utf-8",
        )
        self.hue = self.home / "hue"
        self.hue.write_text(
            "#!/bin/bash\n"
            "set -eu\n"
            "if [[ \"${2:-}\" == raw ]]; then\n"
            "  state=$(tr -d '\\n' < \"$FAKE_HUE_STATE\")\n"
            "  if [[ \"$state\" == on ]]; then printf '%s\\n' '{\"state\":{\"any_on\":true}}'; else printf '%s\\n' '{\"state\":{\"any_on\":false}}'; fi\n"
            "elif [[ \"${2:-}\" == all-off ]]; then\n"
            "  printf '%s\\n' \"$*\" >> \"$FAKE_HUE_LOG\"\n"
            "  printf '%s\\n' off > \"$FAKE_HUE_STATE\"\n"
            "elif [[ \"${2:-}\" == automations ]]; then\n"
            "  python3 - \"$FAKE_AUTOMATION_STATE\" <<'PY'\n"
            "import json,sys\n"
            "v=json.load(open(sys.argv[1]))\n"
            "print(json.dumps({'ok':True,'site':'crosstown','automations':[{'name':k,'enabled':x} for k,x in v.items()]}))\n"
            "PY\n"
            "elif [[ \"${2:-}\" == automation ]]; then\n"
            "  python3 - \"$FAKE_AUTOMATION_STATE\" \"${3:-}\" \"${4:-}\" <<'PY'\n"
            "import json,sys\n"
            "path,action,name=sys.argv[1:]\n"
            "v=json.load(open(path)); desired=action=='enable'; changed=v[name]!=desired; v[name]=desired\n"
            "open(path,'w').write(json.dumps(v))\n"
            "print(json.dumps({'ok':True,'site':'crosstown','name':name,'enabled':desired,'changed':changed}))\n"
            "PY\n"
            "else exit 2; fi\n",
            encoding="utf-8",
        )
        self.hue.chmod(0o700)
        self.old_env = dict(os.environ)
        os.environ["FAKE_HUE_STATE"] = str(self.hue_state)
        os.environ["FAKE_HUE_LOG"] = str(self.hue_log)
        os.environ["FAKE_AUTOMATION_STATE"] = str(self.automation_state)
        self.addCleanup(self.restore_env)

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    @staticmethod
    def private_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def write_presence(self, *, occupancy: str = "confirmed_vacant") -> None:
        state = {
            "timestamp": NOW,
            "cabin": {
                "occupancy": "occupied",
                "fresh": True,
                "stateChangedAt": "2026-08-22T13:00:00Z",
            },
            "crosstown": {
                "occupancy": occupancy,
                "fresh": True,
                "stateChangedAt": "2026-08-22T14:00:00Z",
            },
            "people": {
                "Dylan": {
                    "location": "crosstown" if occupancy == "occupied" else "cabin"
                }
            },
        }
        digest = actions.state_hash(state)
        self.private_json(self.state_path, state)
        self.private_json(
            self.producer_path,
            {
                "schema_version": 1,
                "sequence": 2,
                "observation_id": "b" * 64,
                "state_hash": digest,
                "evaluated_at": NOW,
            },
        )
        self.private_json(
            self.journal / "cycles/crosstown.json",
            {
                "schema_version": 1,
                "site": "crosstown",
                "state_changed_at": "2026-08-22T14:00:00Z",
                "cycle_id": self.cycle_id,
            },
        )

    def reserve(self) -> dict:
        return actions.reserve_current_canary(
            self.root,
            "crosstown",
            "all_lights",
            state_path=self.state_path,
            producer_path=self.producer_path,
            journal_root=self.journal,
            clock=lambda: NOW,
        )

    def run_worker(self) -> dict:
        return actions.run_worker_once(
            self.root,
            state_path=self.state_path,
            producer_path=self.producer_path,
            journal_root=self.journal,
            hue_bin=str(self.hue),
            clock=lambda: NOW,
            sleeper=lambda _seconds: None,
        )

    def database_row(self) -> sqlite3.Row:
        connection = sqlite3.connect(self.root / "state/events.sqlite3")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT r.*, o.outcome, o.verification, o.command_attempted
            FROM action_reservations r
            LEFT JOIN action_outcomes o ON o.reservation_id = r.id
            ORDER BY r.id DESC LIMIT 1
            """
        ).fetchone()

    def test_policy_exposes_only_exact_legacy_or_bus_ownership(self) -> None:
        self.assertEqual(
            actions.ownership(self.root, "crosstown", "all_lights"), "bus"
        )
        self.assertEqual(actions.ownership(self.root, "cabin", "all_lights"), "legacy")
        invalid = policy()
        invalid["targets"]["crosstown"]["all_lights"]["desired_state"] = "on"
        with self.assertRaisesRegex(actions.ActionError, "action_policy_invalid"):
            actions.validate_policy(invalid)

    def test_already_satisfied_canary_confirms_without_a_command(self) -> None:
        self.assertEqual(self.reserve()["status"], "reserved")

        result = self.run_worker()

        self.assertEqual(result["outcome"], "state_confirmed")
        self.assertEqual(result["reason_code"], "already_satisfied")
        self.assertFalse(result["command_attempted"])
        self.assertFalse(self.hue_log.exists())
        row = self.database_row()
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["verification"], "state_confirmed")

    def test_canary_issues_one_command_then_requires_readback(self) -> None:
        self.hue_state.write_text("on\n", encoding="utf-8")
        self.reserve()

        result = self.run_worker()

        self.assertEqual(result["outcome"], "state_confirmed")
        self.assertTrue(result["command_attempted"])
        self.assertEqual(self.hue_log.read_text().splitlines(), ["--crosstown all-off"])
        self.assertEqual(self.database_row()["command_attempted"], 1)

    def test_presence_change_cancels_claimed_action_without_command(self) -> None:
        self.reserve()
        self.write_presence(occupancy="occupied")

        result = self.run_worker()

        self.assertEqual(result["outcome"], "cancelled")
        self.assertEqual(result["reason_code"], "site_not_confirmed_vacant")
        self.assertFalse(self.hue_log.exists())

    def test_prior_claim_is_terminal_unknown_and_never_retried(self) -> None:
        self.reserve()
        with sqlite3.connect(self.root / "state/events.sqlite3") as connection:
            connection.execute(
                """
                UPDATE action_reservations
                SET status='claimed', attempt_count=1, claimed_at=?
                """,
                (NOW,),
            )

        result = self.run_worker()

        self.assertEqual(result["mode"], "idle")
        self.assertEqual(result["recovered"], 1)
        row = self.database_row()
        self.assertEqual(row["status"], "outcome_unknown")
        self.assertEqual(row["outcome"], "outcome_unknown")
        self.assertFalse(self.hue_log.exists())

    def test_parallel_worker_leaves_pending_action_untouched(self) -> None:
        self.reserve()
        lock_path = self.root / "state/action.lock"
        descriptor = os.open(lock_path, os.O_RDWR)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        result = self.run_worker()

        self.assertEqual(result["mode"], "already_running")
        self.assertEqual(self.database_row()["status"], "pending")
        self.assertFalse(self.hue_log.exists())

    def test_daily_automations_suspend_and_restore_only_prior_enabled_set(self) -> None:
        reserved = actions.reserve_current_canary(
            self.root,
            "crosstown",
            "daily_automations",
            state_path=self.state_path,
            producer_path=self.producer_path,
            journal_root=self.journal,
            clock=lambda: NOW,
        )
        self.assertEqual(reserved["status"], "reserved")

        suspended = self.run_worker()

        self.assertEqual(suspended["outcome"], "state_confirmed")
        values = json.loads(self.automation_state.read_text())
        self.assertTrue(all(value is False for value in values.values()))
        status = actions.safe_status(self.root)
        self.assertEqual(status["automation_suspensions"]["active_sites"], ["crosstown"])
        self.assertEqual(status["automation_suspensions"]["latest"]["count"], 2)

        self.write_presence(occupancy="occupied")
        restored = self.run_worker()

        self.assertEqual(restored["mode"], "idle")
        values = json.loads(self.automation_state.read_text())
        self.assertTrue(values["Bedroom lights After dark"])
        self.assertFalse(values["Master Bath Off"])
        self.assertTrue(values["Potato Nightlight"])
        self.assertEqual(
            actions.safe_status(self.root)["automation_suspensions"]["active_count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
