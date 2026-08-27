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


def cat_policy(*, cabin_mode: str = "active", crosstown_mode: str = "active") -> dict:
    value = actions.validate_policy(policy())
    for site, mode in (("cabin", cabin_mode), ("crosstown", crosstown_mode)):
        destination = actions.OTHER_SITE[site]
        value["targets"][site]["feeding_schedule"] = {
            "owner": "bus",
            "mode": mode,
            "trigger": "cat_transfer",
            "action": "suspend_restore",
            "selector": actions.FEEDER_SELECTORS[site],
            "destination_site": destination,
            "destination_selector": actions.FEEDER_SELECTORS[destination],
            "desired_state": "vacant_disabled",
            "evidence_settle_seconds": 1800,
            "expiry_seconds": 600,
            "settle_seconds": 3,
        }
    return value


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
        self.petlibro_state = self.home / "petlibro-state.json"
        self.petlibro_state.write_text(
            json.dumps(
                {
                    "cabin-feeder": {"enabled": True, "meals": 3},
                    "crosstown-feeder": {"enabled": True, "meals": 3},
                }
            ),
            encoding="utf-8",
        )
        self.petlibro_log = self.home / "petlibro-log"
        self.petlibro = self.home / "petlibro"
        self.petlibro.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "path=os.environ['FAKE_PETLIBRO_STATE']; state=json.load(open(path))\n"
            "args=sys.argv[1:]; args=args[1:] if args and args[0]=='--json' else args\n"
            "if args[0]=='schedule-state':\n"
            " selector=args[1]; item=state[selector]; site=selector.split('-',1)[0]\n"
            " if os.environ.get('FAKE_PETLIBRO_FAIL_READ_AFTER_SET')==selector and os.path.exists(os.environ['FAKE_PETLIBRO_LOG']) and selector+' on' in open(os.environ['FAKE_PETLIBRO_LOG']).read().splitlines():\n"
            "  print(json.dumps({'success':False,'error':'schedule_state_unavailable'})); raise SystemExit(1)\n"
            " print(json.dumps({'success':True,'selector':selector,'site':site,'online':True,'scheduleEnabled':item['enabled'],'enabledMealCount':item['meals'],'observedAt':os.environ['FAKE_PETLIBRO_NOW']}))\n"
            "elif args[0]=='schedule-set':\n"
            " selector=args[1]; desired=args[2]=='on'\n"
            " with open(os.environ['FAKE_PETLIBRO_LOG'],'a') as h: h.write(selector+' '+args[2]+'\\n')\n"
            " if os.environ.get('FAKE_PETLIBRO_FAIL_SELECTOR')==selector:\n"
            "  print(json.dumps({'success':False,'error':'schedule_outcome_unknown'})); raise SystemExit(1)\n"
            " changed=state[selector]['enabled']!=desired; state[selector]['enabled']=desired\n"
            " open(path,'w').write(json.dumps(state))\n"
            " print(json.dumps({'success':True,'device':selector,'location':selector.split('-',1)[0],'scheduleEnabled':desired,'action':'feeding_schedule_enabled' if desired else 'feeding_schedule_disabled','accepted':True,'verified':True,'mutation_attempted':changed}))\n"
            "else: raise SystemExit(2)\n",
            encoding="utf-8",
        )
        self.petlibro.chmod(0o700)
        self.old_env = dict(os.environ)
        os.environ["FAKE_HUE_STATE"] = str(self.hue_state)
        os.environ["FAKE_HUE_LOG"] = str(self.hue_log)
        os.environ["FAKE_AUTOMATION_STATE"] = str(self.automation_state)
        os.environ["FAKE_PETLIBRO_STATE"] = str(self.petlibro_state)
        os.environ["FAKE_PETLIBRO_LOG"] = str(self.petlibro_log)
        os.environ["FAKE_PETLIBRO_NOW"] = NOW
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

    def configure_cat_transfer(self, *, mode: str = "active") -> None:
        actions.install_policy(
            self.root,
            json.dumps(
                cat_policy(cabin_mode=mode, crosstown_mode=mode),
                separators=(",", ":"),
            ).encode(),
        )
        state = {
            "timestamp": NOW,
            "cabin": {
                "occupancy": "confirmed_vacant",
                "fresh": True,
                "stateChangedAt": "2026-08-22T14:00:00Z",
            },
            "crosstown": {
                "occupancy": "occupied",
                "fresh": True,
                "stateChangedAt": "2026-08-22T13:00:00Z",
            },
            "people": {"Dylan": {"location": "crosstown"}},
        }
        self.private_json(self.state_path, state)
        self.private_json(
            self.producer_path,
            {
                "schema_version": 1,
                "sequence": 3,
                "observation_id": "d" * 64,
                "state_hash": actions.state_hash(state),
                "evaluated_at": NOW,
            },
        )
        self.private_json(
            self.journal / "cycles/cabin.json",
            {
                "schema_version": 1,
                "site": "cabin",
                "state_changed_at": "2026-08-22T14:00:00Z",
                "cycle_id": self.cycle_id,
            },
        )
        self.private_json(
            self.root / "state/whisker-adapter.json",
            {
                "schema_version": 1,
                "sites": {
                    site: {
                        "enabled": True,
                        "baselined": True,
                        "health": "ok",
                        "coverage_start": "2026-08-22T13:30:00Z",
                        "last_successful_poll": NOW,
                        "anchor": "e" * 64,
                        "fingerprints": ["e" * 64],
                        "last_error": None,
                    }
                    for site in ("cabin", "crosstown")
                },
            },
        )

    def enqueue_litter_activity(
        self, site: str, *, occurred_at: str = "2026-08-22T14:20:00Z"
    ) -> None:
        from home_event_bus import enqueue_event, ingest_once

        alias = actions.WHISKER_ALIASES[site]
        enqueue_event(
            self.root,
            "whisker",
            json.dumps(
                {
                    "source_event_id": f"test:{site}:{occurred_at}",
                    "event_type": "pet.litter_box_activity",
                    "site": site,
                    "entity_kind": "litter_box",
                    "entity_alias": alias,
                    "occurred_at": occurred_at,
                    "observed_at": NOW,
                    "time_precision": "source",
                    "attributes": {"classification": "cat_detected"},
                }
            ).encode(),
            clock=lambda: NOW,
        )
        ingest_once(self.root, clock=lambda: NOW)

    def reserve_cat_transfer(self) -> dict:
        from home_event_bus import EventStore, RuntimePaths

        store = EventStore(RuntimePaths(self.root), clock=lambda: NOW)
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = actions.reserve_cat_transfers(
                connection,
                root=self.root,
                state_path=self.state_path,
                producer_path=self.producer_path,
                journal_root=self.journal,
                clock=lambda: NOW,
            )
            connection.commit()
        return result

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
            petlibro_bin=str(self.petlibro),
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

    def test_cat_transfer_resumes_owned_destination_before_disabling_origin(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        state = actions._empty_feeder_suspensions()
        state["sites"]["crosstown"] = {
            "selector": "crosstown-feeder",
            "cycle_id": "cycle_" + ("f" * 32),
            "phase": "suspended",
            "restore_owned": True,
            "updated_at": NOW,
            "last_error": None,
        }
        actions._write_feeder_suspensions(self.root, state)
        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["crosstown-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")

        reserved = self.reserve_cat_transfer()
        result = self.run_worker()

        self.assertEqual(reserved["reserved"], 1)
        self.assertEqual(result["outcome"], "state_confirmed")
        self.assertEqual(
            self.petlibro_log.read_text().splitlines(),
            ["crosstown-feeder on", "cabin-feeder off"],
        )
        final_devices = json.loads(self.petlibro_state.read_text())
        self.assertTrue(final_devices["crosstown-feeder"]["enabled"])
        self.assertFalse(final_devices["cabin-feeder"]["enabled"])
        suspension = actions._load_feeder_suspensions(self.root)
        self.assertEqual(set(suspension["sites"]), {"cabin"})
        self.assertEqual(suspension["sites"]["cabin"]["phase"], "suspended")

    def test_manually_disabled_destination_blocks_origin_without_mutation(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["crosstown-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")
        self.reserve_cat_transfer()

        result = self.run_worker()

        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["reason_code"], "destination_schedule_manually_disabled")
        self.assertFalse(result["command_attempted"])
        self.assertFalse(self.petlibro_log.exists())
        final_devices = json.loads(self.petlibro_state.read_text())
        self.assertTrue(final_devices["cabin-feeder"]["enabled"])

    def test_manually_disabled_origin_is_not_claimed_for_resume(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["cabin-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")
        self.reserve_cat_transfer()

        result = self.run_worker()

        self.assertEqual(result["reason_code"], "already_satisfied_manual")
        self.assertFalse(result["command_attempted"])
        self.assertEqual(
            actions.safe_status(self.root)["feeder_suspensions"]["active_count"],
            0,
        )

    def test_origin_litter_activity_or_incomplete_coverage_blocks_reservation(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        self.enqueue_litter_activity("cabin", occurred_at="2026-08-22T14:25:00Z")

        blocked = self.reserve_cat_transfer()

        self.assertEqual(blocked["reserved"], 0)
        state = json.loads(
            (self.root / "state/whisker-adapter.json").read_text(encoding="utf-8")
        )
        state["sites"]["cabin"]["coverage_start"] = "2026-08-22T14:05:00Z"
        self.private_json(self.root / "state/whisker-adapter.json", state)
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)

    def test_recent_destination_activity_restarts_the_quiet_settle_window(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        self.enqueue_litter_activity(
            "crosstown", occurred_at="2026-08-22T14:45:00Z"
        )

        result = self.reserve_cat_transfer()

        self.assertEqual(result["reserved"], 0)

    def test_shadow_transfer_records_no_action_reservation(self) -> None:
        self.configure_cat_transfer(mode="shadow")
        self.enqueue_litter_activity("crosstown")

        result = self.reserve_cat_transfer()

        self.assertEqual(result["shadowed"], 1)
        self.assertEqual(result["reserved"], 0)
        with sqlite3.connect(self.root / "state/events.sqlite3") as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM action_reservations").fetchone()[0],
                0,
            )

    def test_unknown_feeder_mutation_is_not_retried(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        self.reserve_cat_transfer()
        os.environ["FAKE_PETLIBRO_FAIL_SELECTOR"] = "cabin-feeder"

        result = self.run_worker()
        second = self.run_worker()

        self.assertEqual(result["outcome"], "outcome_unknown")
        self.assertTrue(result["command_attempted"])
        self.assertEqual(second["mode"], "idle")
        self.assertEqual(
            self.petlibro_log.read_text().splitlines(), ["cabin-feeder off"]
        )
        suspension = actions._load_feeder_suspensions(self.root)
        self.assertEqual(suspension["sites"]["cabin"]["phase"], "suspending")
        self.assertEqual(
            suspension["sites"]["cabin"]["last_error"],
            "feeder_outcome_unknown",
        )

    def test_destination_restore_readback_failure_records_attempt_and_stops(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        state = actions._empty_feeder_suspensions()
        state["sites"]["crosstown"] = {
            "selector": "crosstown-feeder",
            "cycle_id": "cycle_" + ("f" * 32),
            "phase": "suspended",
            "restore_owned": True,
            "updated_at": NOW,
            "last_error": None,
        }
        actions._write_feeder_suspensions(self.root, state)
        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["crosstown-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")
        self.reserve_cat_transfer()
        os.environ["FAKE_PETLIBRO_FAIL_READ_AFTER_SET"] = "crosstown-feeder"

        result = self.run_worker()
        second = self.run_worker()

        self.assertEqual(result["mode"], "deferred")
        self.assertEqual(result["feeder_reconcile"]["outcome_unknown"], 1)
        self.assertEqual(second["mode"], "deferred")
        self.assertEqual(self.database_row()["status"], "pending")
        self.assertEqual(
            self.petlibro_log.read_text().splitlines(), ["crosstown-feeder on"]
        )
        self.assertTrue(json.loads(self.petlibro_state.read_text())["cabin-feeder"]["enabled"])

    def test_feeder_policy_rejects_rebound_selector(self) -> None:
        invalid = cat_policy()
        invalid["targets"]["cabin"]["feeding_schedule"]["selector"] = (
            "crosstown-feeder"
        )

        with self.assertRaisesRegex(actions.ActionError, "action_policy_invalid"):
            actions.validate_policy(invalid)


if __name__ == "__main__":
    unittest.main()
