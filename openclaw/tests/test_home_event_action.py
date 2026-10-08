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
from unittest.mock import patch


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


def relocation_policy() -> dict:
    value = cat_policy()
    value["schema_version"] = 4
    for site in actions.SITES:
        entry = value["targets"][site]["feeding_schedule"]
        entry["trigger"] = "household_relocation"
        del entry["evidence_settle_seconds"]
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

    def configure_relocation(self) -> None:
        self.configure_cat_transfer()
        actions.install_policy(self.root, json.dumps(relocation_policy()).encode())
        state = json.loads(self.state_path.read_text())
        state["people"]["Julia"] = {"location": "crosstown"}
        self.update_canonical(state)
        (self.root / "state/whisker-adapter.json").unlink()

    def update_canonical(self, state: dict) -> None:
        self.private_json(self.state_path, state)
        producer = json.loads(self.producer_path.read_text())
        producer["state_hash"] = actions.state_hash(state)
        producer["evaluated_at"] = state["timestamp"]
        self.private_json(self.producer_path, producer)

    def test_relocation_needs_no_litter_data_and_runs_once_per_cycle(self) -> None:
        self.configure_relocation()
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 1)
        self.assertEqual(self.run_worker()["outcome"], "state_confirmed")
        self.assertEqual(self.petlibro_log.read_text().splitlines(), ["cabin-feeder off"])
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)
        self.assertEqual(self.run_worker()["mode"], "idle")
        self.assertEqual(self.petlibro_log.read_text().splitlines(), ["cabin-feeder off"])

    def test_relocation_requires_both_people_fresh_sites_and_exact_cycle(self) -> None:
        self.configure_relocation()
        original = json.loads(self.state_path.read_text())
        for change in ("one_person", "stale", "possibly_vacant", "split", "cycle", "destination_stale"):
            with self.subTest(change=change):
                state = json.loads(json.dumps(original))
                if change == "one_person":
                    state["people"]["Julia"]["location"] = "cabin"
                elif change == "stale":
                    state["timestamp"] = "2026-08-22T13:00:00Z"
                elif change == "possibly_vacant":
                    state["cabin"]["occupancy"] = "possibly_vacant"
                elif change == "split":
                    state["cabin"]["occupancy"] = "occupied"
                elif change == "cycle":
                    state["cabin"]["stateChangedAt"] = NOW
                else:
                    state["crosstown"]["fresh"] = False
                self.update_canonical(state)
                self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)
        self.assertFalse(self.petlibro_log.exists())

    def test_relocation_restores_owned_destination_before_origin_off(self) -> None:
        self.configure_relocation()
        owned = actions._empty_feeder_suspensions()
        owned["sites"]["crosstown"] = {
            "selector": "crosstown-feeder", "cycle_id": "cycle_" + "f" * 32,
            "phase": "suspended", "restore_owned": True,
            "occupancy_context": "origin_vacant", "updated_at": NOW, "last_error": None,
        }
        actions._write_feeder_suspensions(self.root, owned)
        devices = json.loads(self.petlibro_state.read_text())
        devices["crosstown-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(devices))
        self.reserve_cat_transfer()
        self.run_worker()
        self.assertEqual(self.petlibro_log.read_text().splitlines(), ["crosstown-feeder on", "cabin-feeder off"])

    def test_relocation_manual_destination_blocks_without_retry_or_origin_change(self) -> None:
        self.configure_relocation()
        devices = json.loads(self.petlibro_state.read_text())
        devices["crosstown-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(devices))
        self.reserve_cat_transfer()
        result = self.run_worker()
        self.assertEqual(self.database_row()["reason_code"], "destination_schedule_manually_disabled")
        self.assertFalse(self.petlibro_log.exists())
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)

    def test_relocation_manual_origin_requires_audited_adoption(self) -> None:
        self.configure_relocation()
        devices = json.loads(self.petlibro_state.read_text())
        devices["cabin-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(devices))
        self.reserve_cat_transfer()
        self.run_worker()
        self.assertEqual(actions._load_feeder_suspensions(self.root)["sites"], {})
        self.assertEqual(self.recover_feeder(dry_run=True)["mode"], "eligible")
        self.assertEqual(self.recover_feeder()["mode"], "returned_to_automation")
        self.assertFalse(self.petlibro_log.exists())

    def test_feeder_hold_preserves_other_policies_and_does_not_touch_devices(self) -> None:
        self.configure_relocation()
        before = actions.load_policy(self.root)[0]
        self.reserve_cat_transfer()
        actions.set_feeder_mode(self.root, "disabled")
        after = actions.load_policy(self.root)[0]
        for site in actions.SITES:
            self.assertEqual(after["targets"][site]["all_lights"], before["targets"][site]["all_lights"])
            self.assertEqual(after["targets"][site]["feeding_schedule"]["mode"], "disabled")
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)
        self.run_worker()
        self.assertFalse(self.petlibro_log.exists())
        actions.set_feeder_mode(self.root, "active")
        self.assertEqual(actions.load_policy(self.root)[0], before)
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)

    def test_relocation_litter_contradiction_is_advisory_not_a_control_gate(self) -> None:
        self.configure_relocation()
        self.enqueue_litter_activity("cabin")
        status = actions.safe_status(self.root, state_path=self.state_path,
            producer_path=self.producer_path, journal_root=self.journal, clock=lambda: NOW)
        site = status["cat_transfer_readiness"]["sites"]["cabin"]
        self.assertEqual(site["state"], "eligible")
        self.assertEqual(site["warning"], "litter_activity_at_vacant_home")
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 1)

    def test_relocation_requires_paired_policy_and_new_schema(self) -> None:
        self.configure_relocation()
        value = relocation_policy()
        value["schema_version"] = 3
        with self.assertRaises(actions.ActionError):
            actions.validate_policy(value)
        value = relocation_policy()
        value["targets"]["crosstown"]["feeding_schedule"]["mode"] = "disabled"
        actions.install_policy(self.root, json.dumps(value).encode())
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)

    def test_relocation_round_trip_and_split_household_preserve_feeding(self) -> None:
        self.configure_relocation()
        self.reserve_cat_transfer()
        self.run_worker()
        state = json.loads(self.state_path.read_text())
        state["cabin"]["occupancy"] = "occupied"
        state["cabin"]["stateChangedAt"] = "2026-08-22T14:40:00Z"
        state["people"]["Dylan"]["location"] = "cabin"
        self.update_canonical(state)
        self.run_worker()
        self.assertEqual(self.petlibro_log.read_text().splitlines(), ["cabin-feeder off"])
        state["people"]["Julia"]["location"] = "cabin"
        state["crosstown"]["occupancy"] = "confirmed_vacant"
        state["crosstown"]["stateChangedAt"] = "2026-08-22T14:50:00Z"
        self.update_canonical(state)
        self.private_json(self.journal / "cycles/crosstown.json", {
            "schema_version": 1, "site": "crosstown",
            "state_changed_at": state["crosstown"]["stateChangedAt"],
            "cycle_id": "cycle_" + "f" * 32,
        })
        self.assertEqual(self.reserve_cat_transfer()["reserved"], 1)
        self.run_worker()
        self.assertEqual(self.petlibro_log.read_text().splitlines(), [
            "cabin-feeder off", "cabin-feeder on", "crosstown-feeder off",
        ])

    def test_relocation_uncertain_write_is_not_retried(self) -> None:
        self.configure_relocation()
        self.reserve_cat_transfer()
        with patch.object(actions, "_petlibro_schedule_set",
                          side_effect=actions.ActionError("feeder_outcome_unknown", command_attempted=True)) as mutation:
            result = self.run_worker()
            self.assertEqual(result["outcome"], "outcome_unknown")
            self.assertEqual(self.reserve_cat_transfer()["reserved"], 0)
            self.run_worker()
            self.assertEqual(mutation.call_count, 1)

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

    def reserve_automations(self) -> dict:
        return actions.reserve_current_canary(
            self.root,
            "crosstown",
            "daily_automations",
            state_path=self.state_path,
            producer_path=self.producer_path,
            journal_root=self.journal,
            clock=lambda: NOW,
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

    def test_missing_automation_bindings_fail_without_command_or_retry(self) -> None:
        self.automation_state.write_text("{}", encoding="utf-8")
        self.reserve_automations()

        with patch.object(actions, "_hue_automation_set") as mutation:
            result = self.run_worker()
            second = self.run_worker()

        mutation.assert_not_called()
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["reason_code"], "automation_binding_missing")
        self.assertFalse(result["command_attempted"])
        self.assertEqual(second["mode"], "idle")
        self.assertEqual(self.reserve_automations()["status"], "duplicate")
        self.assertEqual(self.database_row()["status"], "complete")
        self.assertEqual(self.database_row()["command_attempted"], 0)
        self.assertFalse(actions._suspension_path(self.root).exists())

    def test_automation_inventory_failure_is_not_a_command_attempt(self) -> None:
        self.reserve_automations()

        with patch.object(
            actions,
            "_hue_automation_inventory",
            side_effect=actions.ActionError("automation_readback_unavailable"),
        ), patch.object(actions, "_hue_automation_set") as mutation:
            result = self.run_worker()

        mutation.assert_not_called()
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["command_attempted"])
        self.assertEqual(self.database_row()["command_attempted"], 0)

    def test_first_automation_command_failure_remains_unknown(self) -> None:
        self.reserve_automations()

        with patch.object(
            actions,
            "_hue_automation_set",
            side_effect=actions.ActionError("automation_command_failed"),
        ) as mutation:
            result = self.run_worker()

        self.assertEqual(mutation.call_count, 1)
        self.assertEqual(result["outcome"], "outcome_unknown")
        self.assertTrue(result["command_attempted"])
        self.assertEqual(self.database_row()["command_attempted"], 1)
        self.assertEqual(self.reserve_automations()["status"], "duplicate")

    def test_partial_automation_command_failure_remains_unknown(self) -> None:
        self.reserve_automations()

        with patch.object(
            actions,
            "_hue_automation_set",
            side_effect=[True, actions.ActionError("automation_command_failed")],
        ) as mutation:
            result = self.run_worker()

        self.assertEqual(mutation.call_count, 2)
        self.assertEqual(result["outcome"], "outcome_unknown")
        self.assertTrue(result["command_attempted"])
        self.assertEqual(self.database_row()["command_attempted"], 1)

    def test_automation_post_command_readback_failure_remains_unknown(self) -> None:
        self.reserve_automations()
        inventory = json.loads(self.automation_state.read_text())

        with patch.object(
            actions,
            "_hue_automation_inventory",
            side_effect=[inventory, actions.ActionError("automation_readback_unavailable")],
        ), patch.object(actions, "_hue_automation_set", return_value=True) as mutation:
            result = self.run_worker()

        self.assertEqual(mutation.call_count, 2)
        self.assertEqual(result["outcome"], "outcome_unknown")
        self.assertTrue(result["command_attempted"])
        self.assertEqual(self.database_row()["command_attempted"], 1)

    def test_already_disabled_automations_require_no_command(self) -> None:
        values = json.loads(self.automation_state.read_text())
        self.automation_state.write_text(json.dumps({name: False for name in values}))
        self.reserve_automations()

        with patch.object(actions, "_hue_automation_set") as mutation:
            result = self.run_worker()

        mutation.assert_not_called()
        self.assertEqual(result["outcome"], "state_confirmed")
        self.assertFalse(result["command_attempted"])

    def test_tracked_policy_retires_routines_but_preserves_lights_and_feeders(self) -> None:
        path = MODULE_PATH.parents[1] / "home-event-action-policy.json"
        value = actions.validate_policy(json.loads(path.read_text()))
        self.assertTrue(value["active"])
        self.assertNotIn("daily_automations", value["targets"]["crosstown"])
        for site, light_owner in (("cabin", "legacy"), ("crosstown", "bus")):
            targets = value["targets"][site]
            self.assertEqual(set(targets), {"all_lights", "feeding_schedule"})
            self.assertEqual(targets["all_lights"]["owner"], light_owner)
            self.assertEqual(targets["all_lights"]["desired_state"], "all_off")
            self.assertEqual(
                targets["feeding_schedule"],
                relocation_policy()["targets"][site]["feeding_schedule"],
            )
        actions.install_policy(self.root, json.dumps(value).encode())
        self.assertEqual(self.reserve_automations()["status"], "disabled")

    def prepare_feeder_recovery(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        devices = json.loads(self.petlibro_state.read_text())
        devices["cabin-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(devices))

    def recover_feeder(self, **options) -> dict:
        parameters = {
            "confirm_manual_pause": True,
            "expected_cycle_id": self.cycle_id,
            "state_path": self.state_path,
            "producer_path": self.producer_path,
            "journal_root": self.journal,
            "petlibro_bin": str(self.petlibro),
            "clock": lambda: NOW,
        }
        parameters.update(options)
        return actions.return_feeder_to_automation(self.root, "cabin", **parameters)

    def recovery_receipts(self) -> list[Path]:
        return sorted((self.root / "state").glob("feeder-recovery-*.json"))

    def test_feeder_recovery_is_audited_idempotent_and_commandless(self) -> None:
        self.prepare_feeder_recovery()
        self.reserve_cat_transfer()
        self.run_worker()
        prior_outcome = dict(self.database_row())
        before = actions._load_feeder_suspensions(self.root)
        with patch.object(actions, "_petlibro_schedule_set") as mutation:
            result = self.recover_feeder()
            self.assertEqual(result["mode"], "returned_to_automation")
            self.assertFalse(result["schedule_changed"])
            receipts = self.recovery_receipts()
            self.assertEqual(len(receipts), 2)
            original = {path: path.read_bytes() for path in receipts}
            for path in receipts:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            intent = json.loads(next(path for path in receipts if ".intent." in path.name).read_text())
            self.assertEqual(intent["authorization"], "explicit_operator_request")
            self.assertEqual(intent["before"], before)
            self.assertEqual(intent["cycle_id"], self.cycle_id)
            self.assertEqual(self.recover_feeder()["mode"], "already_managed")
            self.assertEqual({path: path.read_bytes() for path in receipts}, original)
            mutation.assert_not_called()
        self.assertEqual(dict(self.database_row()), prior_outcome)
        self.assertFalse(self.petlibro_log.exists())
        safe = actions.safe_status(self.root)["feeder_suspensions"]
        self.assertEqual(safe["active_sites"], ["cabin"])
        self.assertEqual(safe["sites"]["cabin"]["phase"], "suspended")
        self.assertFalse(safe["sites"]["cabin"]["attention"])

    def test_feeder_recovery_dry_run_does_not_adopt_or_write_receipts(self) -> None:
        self.prepare_feeder_recovery()
        before = actions._load_feeder_suspensions(self.root)
        result = self.recover_feeder(dry_run=True, confirm_manual_pause=False, expected_cycle_id=None)
        self.assertEqual(result["mode"], "eligible")
        self.assertEqual(result["cycle_id"], self.cycle_id)
        self.assertEqual(actions._load_feeder_suspensions(self.root), before)
        self.assertEqual(self.recovery_receipts(), [])
        self.assertFalse(self.petlibro_log.exists())

    def test_feeder_recovery_requires_explicit_confirmation_and_exact_cycle(self) -> None:
        self.prepare_feeder_recovery()
        for options in (
            {"confirm_manual_pause": False},
            {"expected_cycle_id": None},
            {"expected_cycle_id": "../../escape"},
            {"expected_cycle_id": "cycle_" + "b" * 32},
        ):
            with self.subTest(options=options), self.assertRaises(actions.ActionError):
                self.recover_feeder(**options)
        self.assertEqual(self.recovery_receipts(), [])
        self.assertFalse(actions._load_feeder_suspensions(self.root)["sites"])

    def test_feeder_recovery_rejects_unsafe_schedule_states_without_mutation(self) -> None:
        self.prepare_feeder_recovery()
        original = json.loads(self.petlibro_state.read_text())
        for selector, key, value in (
            ("cabin-feeder", "enabled", True),
            ("crosstown-feeder", "enabled", False),
            ("crosstown-feeder", "meals", 0),
        ):
            devices = json.loads(json.dumps(original))
            devices[selector][key] = value
            self.petlibro_state.write_text(json.dumps(devices))
            with self.subTest(selector=selector, key=key), self.assertRaises(actions.ActionError):
                self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])
        self.assertFalse(self.petlibro_log.exists())

    def test_feeder_recovery_preserves_all_evidence_gates(self) -> None:
        self.prepare_feeder_recovery()
        for reason in (
            "presence_state_stale", "presence_state_mismatch", "vacancy_cycle_mismatch",
            "whisker_coverage_incomplete", "whisker_coverage_stale",
            "origin_litter_activity_observed", "cat_transfer_not_settled",
        ):
            with self.subTest(reason=reason), patch.object(
                actions, "_cat_transfer_evidence", side_effect=actions.ActionError(reason)
            ), self.assertRaisesRegex(actions.ActionError, reason):
                self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])
        self.assertFalse(actions._load_feeder_suspensions(self.root)["sites"])

    def test_feeder_recovery_blocks_unsettled_real_event(self) -> None:
        self.prepare_feeder_recovery()
        self.enqueue_litter_activity("crosstown", occurred_at="2026-08-22T14:50:00Z")
        with self.assertRaisesRegex(actions.ActionError, "cat_transfer_not_settled"):
            self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])

    def test_feeder_recovery_blocks_pending_claimed_and_uncertain_feeder_actions(self) -> None:
        self.prepare_feeder_recovery()
        self.reserve_cat_transfer()
        store = actions.EventStore(actions.RuntimePaths(self.root))
        for status in ("pending", "claimed", "outcome_unknown"):
            with store.connect() as connection:
                connection.execute("UPDATE action_reservations SET status=?", (status,))
            with self.subTest(status=status), self.assertRaisesRegex(
                actions.ActionError, "feeder_recovery_unresolved_action"
            ):
                self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])

    def test_feeder_recovery_requires_both_active_policies(self) -> None:
        self.prepare_feeder_recovery()
        for mode in ("shadow", "disabled"):
            actions.install_policy(self.root, actions.canonical_json(cat_policy(crosstown_mode=mode)))
            with self.subTest(mode=mode), self.assertRaisesRegex(
                actions.ActionError, "feeder_recovery_policy_inactive"
            ):
                self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])

    def test_feeder_recovery_preserves_unrelated_unknown_lighting_action(self) -> None:
        self.reserve()
        store = actions.EventStore(actions.RuntimePaths(self.root))
        with store.connect() as connection:
            connection.execute("UPDATE action_reservations SET status='outcome_unknown'")
        prior = dict(self.database_row())
        self.prepare_feeder_recovery()
        self.assertEqual(self.recover_feeder()["mode"], "returned_to_automation")
        self.assertEqual(dict(self.database_row()), prior)

    def test_feeder_recovery_rejects_unavailable_readback(self) -> None:
        self.prepare_feeder_recovery()
        with patch.object(
            actions, "_petlibro_schedule_state",
            side_effect=actions.ActionError("feeder_readback_unavailable"),
        ), self.assertRaisesRegex(actions.ActionError, "feeder_readback_unavailable"):
            self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])
        self.assertFalse(actions._load_feeder_suspensions(self.root)["sites"])

    def test_feeder_recovery_rejects_existing_transition_or_destination_ownership(self) -> None:
        self.prepare_feeder_recovery()
        for site, phase in (("cabin", "suspending"), ("cabin", "restoring"), ("crosstown", "suspended")):
            state = actions._empty_feeder_suspensions()
            state["sites"][site] = {
                "selector": actions.FEEDER_SELECTORS[site],
                "cycle_id": self.cycle_id,
                "phase": phase,
                "restore_owned": True,
                "occupancy_context": "origin_vacant",
                "updated_at": NOW,
                "last_error": None,
            }
            actions._write_feeder_suspensions(self.root, state)
            with self.subTest(site=site, phase=phase), self.assertRaises(actions.ActionError):
                self.recover_feeder()
            self.assertEqual(actions._load_feeder_suspensions(self.root), state)
        self.assertEqual(self.recovery_receipts(), [])

    def test_feeder_recovery_receipt_creation_never_overwrites(self) -> None:
        path = self.root / "state/receipt.json"
        actions._write_recovery_receipt(path, {"original": True})
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            actions._write_recovery_receipt(path, {"original": False})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_nlink, 1)

    def test_feeder_recovery_obeys_worker_lock(self) -> None:
        self.prepare_feeder_recovery()
        paths = actions.validate_runtime(self.root)
        with paths.action_lock.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(actions.ActionError, "action_worker_busy"):
                self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])

    def test_feeder_recovery_rechecks_policy_before_ownership_write(self) -> None:
        self.prepare_feeder_recovery()
        original = actions._petlibro_schedule_state

        def changed_policy(*args, **kwargs):
            result = original(*args, **kwargs)
            actions.install_policy(self.root, actions.canonical_json(cat_policy(crosstown_mode="disabled")))
            return result

        with patch.object(actions, "_petlibro_schedule_state", side_effect=changed_policy):
            with self.assertRaisesRegex(actions.ActionError, "feeder_recovery_state_changed"):
                self.recover_feeder()
        self.assertEqual(self.recovery_receipts(), [])

    def test_feeder_recovery_requires_durable_intent_before_ownership(self) -> None:
        self.prepare_feeder_recovery()
        with patch.object(actions, "_write_recovery_receipt", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.recover_feeder()
        self.assertFalse(actions._load_feeder_suspensions(self.root)["sites"])

    def test_interrupted_feeder_recovery_is_not_replayed(self) -> None:
        self.prepare_feeder_recovery()
        with patch.object(actions, "_write_feeder_suspensions", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.recover_feeder()
        self.assertEqual(len(self.recovery_receipts()), 1)
        with self.assertRaisesRegex(actions.ActionError, "feeder_recovery_incomplete"):
            self.recover_feeder()
        self.assertFalse(actions._load_feeder_suspensions(self.root)["sites"])

    def test_feeder_recovery_incomplete_completion_is_not_reported_success(self) -> None:
        self.prepare_feeder_recovery()
        original = actions._write_recovery_receipt

        def fail_completion(path, value):
            if ".applied." in path.name:
                raise OSError("disk full")
            original(path, value)

        with patch.object(actions, "_write_recovery_receipt", side_effect=fail_completion):
            with self.assertRaises(OSError):
                self.recover_feeder()
        self.assertIn("cabin", actions._load_feeder_suspensions(self.root)["sites"])
        with self.assertRaisesRegex(actions.ActionError, "feeder_recovery_incomplete"):
            self.recover_feeder()
        self.assertFalse(self.petlibro_log.exists())

    def test_feeder_recovery_rejects_corrupt_or_symlink_receipts(self) -> None:
        self.prepare_feeder_recovery()
        self.recover_feeder()
        receipt = next(path for path in self.recovery_receipts() if ".applied." in path.name)
        original = receipt.read_bytes()
        receipt.write_text('{"schema_version":1,"intent_sha256":"wrong"}')
        with self.assertRaisesRegex(actions.ActionError, "feeder_recovery_receipt_invalid"):
            self.recover_feeder()
        receipt.unlink()
        target = self.home / "receipt-target"
        target.write_bytes(original)
        target.chmod(0o600)
        receipt.symlink_to(target)
        with self.assertRaises(actions.ActionError):
            self.recover_feeder()
        self.assertEqual(target.read_bytes(), original)

    def test_recovered_feeder_restores_through_normal_return_evidence(self) -> None:
        self.prepare_feeder_recovery()
        self.recover_feeder()
        self.write_presence()
        state = json.loads(self.state_path.read_text())
        state["crosstown"]["stateChangedAt"] = "2026-08-22T14:25:00Z"
        self.private_json(self.state_path, state)
        producer = json.loads(self.producer_path.read_text())
        producer["state_hash"] = actions.state_hash(state)
        self.private_json(self.producer_path, producer)
        cycle = json.loads((self.journal / "cycles/crosstown.json").read_text())
        cycle["state_changed_at"] = state["crosstown"]["stateChangedAt"]
        cycle["cycle_id"] = "cycle_" + "b" * 32
        self.private_json(self.journal / "cycles/crosstown.json", cycle)
        self.enqueue_litter_activity("cabin", occurred_at="2026-08-22T14:28:00Z")
        result = self.run_worker()
        self.assertEqual(result["feeder_reconcile"]["changed"], 1)
        self.assertEqual(self.petlibro_log.read_text().splitlines(), ["cabin-feeder on"])
        self.assertFalse(actions._load_feeder_suspensions(self.root)["sites"])
        self.assertEqual(len(self.recovery_receipts()), 2)

    def test_cat_transfer_resumes_owned_destination_before_disabling_origin(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        state = actions._empty_feeder_suspensions()
        state["sites"]["crosstown"] = {
            "selector": "crosstown-feeder",
            "cycle_id": "cycle_" + ("f" * 32),
            "phase": "suspended",
            "restore_owned": True,
            "occupancy_context": "origin_vacant",
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
        transfer = actions.safe_status(self.root)["cat_transfers"]["recent"][0]
        self.assertEqual(
            transfer,
            {
                "origin_site": "cabin",
                "destination_site": "crosstown",
                "occurred_at": NOW,
                "schedule_changed": True,
            },
        )

    def test_suspended_feeder_clears_recovered_readback_error(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        state = actions._empty_feeder_suspensions()
        state["sites"]["cabin"] = {
            "selector": "cabin-feeder",
            "cycle_id": self.cycle_id,
            "phase": "suspended",
            "restore_owned": True,
            "occupancy_context": "origin_vacant",
            "updated_at": "2026-08-22T14:55:00Z",
            "last_error": "feeder_readback_unavailable",
        }
        actions._write_feeder_suspensions(self.root, state)
        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["cabin-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")

        result = self.run_worker()

        self.assertEqual(result["feeder_reconcile"]["mode"], "verified")
        suspension = actions._load_feeder_suspensions(self.root)
        self.assertEqual(suspension["sites"]["cabin"]["phase"], "suspended")
        self.assertIsNone(suspension["sites"]["cabin"]["last_error"])
        self.assertFalse(
            actions.safe_status(self.root)["feeder_suspensions"]["sites"][
                "cabin"
            ]["attention"]
        )

    def test_unsettled_cat_return_is_waiting_not_attention(self) -> None:
        state = actions._empty_feeder_suspensions()
        state["sites"]["crosstown"] = {
            "selector": "crosstown-feeder",
            "cycle_id": "cycle_" + ("f" * 32),
            "phase": "suspended",
            "restore_owned": True,
            "occupancy_context": "origin_vacant",
            "updated_at": NOW,
            "last_error": "cat_transfer_not_settled",
        }
        actions._write_feeder_suspensions(self.root, state)

        safe = actions.safe_status(self.root)["feeder_suspensions"]["sites"][
            "crosstown"
        ]

        self.assertFalse(safe["attention"])
        self.assertEqual(safe["waiting_reason"], "cat_transfer_not_settled")
        self.assertEqual(safe["last_error"], "cat_transfer_not_settled")

    def test_split_household_keeps_cat_feeder_paused_without_attention(self) -> None:
        self.configure_cat_transfer()
        split_presence = {
            "timestamp": NOW,
            "cabin": {
                "occupancy": "occupied",
                "fresh": True,
                "stateChangedAt": "2026-08-22T13:00:00Z",
            },
            "crosstown": {
                "occupancy": "occupied",
                "fresh": True,
                "stateChangedAt": "2026-08-22T14:00:00Z",
            },
            "people": {
                "Dylan": {"location": "crosstown"},
                "Julia": {"location": "cabin"},
            },
        }
        self.private_json(self.state_path, split_presence)
        self.private_json(
            self.producer_path,
            {
                "schema_version": 1,
                "sequence": 4,
                "observation_id": "f" * 64,
                "state_hash": actions.state_hash(split_presence),
                "evaluated_at": NOW,
            },
        )
        suspension = actions._empty_feeder_suspensions()
        suspension["sites"]["crosstown"] = {
            "selector": "crosstown-feeder",
            "cycle_id": "cycle_" + ("a" * 32),
            "phase": "suspended",
            "restore_owned": True,
            "occupancy_context": "origin_vacant",
            "updated_at": "2026-08-22T14:55:00Z",
            "last_error": "site_not_confirmed_vacant",
        }
        actions._write_feeder_suspensions(self.root, suspension)
        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["crosstown-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")

        result = self.run_worker()

        self.assertEqual(result["feeder_reconcile"]["mode"], "verified")
        current = actions._load_feeder_suspensions(self.root)
        self.assertEqual(current["schema_version"], 2)
        self.assertEqual(
            current["sites"]["crosstown"]["occupancy_context"],
            "split_household",
        )
        self.assertIsNone(current["sites"]["crosstown"]["last_error"])
        safe = actions.safe_status(self.root)["feeder_suspensions"]["sites"][
            "crosstown"
        ]
        self.assertEqual(safe["occupancy_context"], "split_household")
        self.assertFalse(safe["attention"])
        self.assertFalse(self.petlibro_log.exists())

    def test_legacy_feeder_suspension_migrates_to_origin_vacant_context(self) -> None:
        normalized = actions._validate_feeder_suspensions(
            {
                "schema_version": 1,
                "sites": {
                    "crosstown": {
                        "selector": "crosstown-feeder",
                        "cycle_id": "cycle_" + ("a" * 32),
                        "phase": "suspended",
                        "restore_owned": True,
                        "updated_at": NOW,
                        "last_error": None,
                    }
                },
                "latest": None,
            }
        )

        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(
            normalized["sites"]["crosstown"]["occupancy_context"],
            "origin_vacant",
        )

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
        repeated = self.reserve_cat_transfer()
        self.assertEqual(repeated["reserved"], 0)
        self.assertEqual(repeated["retries"], 0)

    def test_new_settled_litter_event_retries_commandless_destination_block(self) -> None:
        self.configure_cat_transfer()
        self.enqueue_litter_activity("crosstown")
        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["crosstown-feeder"]["enabled"] = False
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")
        self.reserve_cat_transfer()
        first = self.run_worker()

        pet_state = json.loads(self.petlibro_state.read_text())
        pet_state["crosstown-feeder"]["enabled"] = True
        self.petlibro_state.write_text(json.dumps(pet_state), encoding="utf-8")
        self.enqueue_litter_activity(
            "crosstown", occurred_at="2026-08-22T14:25:00Z"
        )
        reserved = self.reserve_cat_transfer()
        second = self.run_worker()

        self.assertEqual(first["reason_code"], "destination_schedule_manually_disabled")
        self.assertFalse(first["command_attempted"])
        self.assertEqual(reserved["reserved"], 1)
        self.assertEqual(reserved["retries"], 1)
        self.assertEqual(second["outcome"], "state_confirmed")
        self.assertTrue(second["command_attempted"])
        self.assertEqual(
            self.petlibro_log.read_text().splitlines(), ["cabin-feeder off"]
        )
        with sqlite3.connect(self.root / "state/events.sqlite3") as connection:
            rows = connection.execute(
                """
                SELECT r.status, o.outcome, o.reason_code, o.command_attempted
                FROM action_reservations r
                JOIN action_outcomes o ON o.reservation_id = r.id
                WHERE r.target_alias='feeding_schedule'
                ORDER BY r.id
                """
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (
                    "complete",
                    "failed",
                    "destination_schedule_manually_disabled",
                    0,
                ),
                ("complete", "state_confirmed", "completed", 1),
            ],
        )

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

    def test_safe_status_exposes_vacancy_cycle_mismatch(self) -> None:
        self.configure_cat_transfer()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["cabin"]["stateChangedAt"] = "2026-08-22T14:05:00Z"
        producer = json.loads(self.producer_path.read_text(encoding="utf-8"))
        producer["state_hash"] = actions.state_hash(state)
        self.private_json(self.state_path, state)
        self.private_json(self.producer_path, producer)

        readiness = actions.safe_status(
            self.root,
            state_path=self.state_path,
            producer_path=self.producer_path,
            journal_root=self.journal,
            clock=lambda: NOW,
        )["cat_transfer_readiness"]["sites"]

        self.assertEqual(
            readiness["cabin"],
            {"state": "blocked", "reason": "vacancy_cycle_mismatch"},
        )
        self.assertEqual(
            readiness["crosstown"],
            {"state": "waiting", "reason": "site_not_confirmed_vacant"},
        )

    def test_safe_status_exposes_wait_for_destination_litter(self) -> None:
        self.configure_cat_transfer()

        readiness = actions.safe_status(
            self.root,
            state_path=self.state_path,
            producer_path=self.producer_path,
            journal_root=self.journal,
            clock=lambda: NOW,
        )["cat_transfer_readiness"]["sites"]

        self.assertEqual(
            readiness["cabin"],
            {"state": "waiting", "reason": "cat_transfer_not_settled"},
        )

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
        self.enqueue_litter_activity(
            "crosstown", occurred_at="2026-08-22T14:25:00Z"
        )
        retry = self.reserve_cat_transfer()

        self.assertEqual(result["outcome"], "outcome_unknown")
        self.assertTrue(result["command_attempted"])
        self.assertEqual(second["mode"], "idle")
        self.assertEqual(retry["reserved"], 0)
        self.assertEqual(retry["retries"], 0)
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
            "occupancy_context": "origin_vacant",
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
