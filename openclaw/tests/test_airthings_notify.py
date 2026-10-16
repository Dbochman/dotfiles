from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


BIN = Path(__file__).resolve().parents[1] / "bin"
SPEC = importlib.util.spec_from_file_location("airthings_notify", BIN / "airthings-notify.py")
notify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notify)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "notifications"
        notify.initialize(self.root)
        self.now = datetime(2026, 10, 12, 14, tzinfo=timezone.utc)
        self.sender = mock.Mock()
        self.presence = mock.Mock(return_value=["dylan", "julia"])
        self.credentials = mock.Mock(return_value=({"dylan": "chat_id:1", "julia": "chat_id:2"}, {}))

    def configure(self, mode):
        notify.atomic_json(self.root / "config.json", {"schema": 1, "mode": mode})

    def record(self, minute=0, co2=1200, voc=300):
        return {"timestamp": notify.stamp(self.now + timedelta(minutes=minute)), "history_origin": notify.ORIGIN, "rooms": [{"source": "airthings", "history_origin": notify.ORIGIN, "structure": "Philly", "room": "Living Room", "connectivity": "ONLINE", "error": None, "co2_ppm": co2, "voc_ppb": voc, "humidity": 10, "air_quality": {"overall": "poor"}}]}

    def evaluate(self, minute=0, co2=1200, voc=300, record=None):
        return notify.evaluate(self.root, record or self.record(minute, co2, voc), self.now + timedelta(minutes=minute), self.presence, self.credentials, self.sender)

    def qualify(self, **kwargs):
        self.evaluate(0, **kwargs)
        self.evaluate(5, **kwargs)
        return self.evaluate(10, **kwargs)

    def state(self):
        return notify.read_json(self.root / "state.json")

    def recover(self, start=15):
        for minute in range(start, start + 16, 5):
            decision = self.evaluate(minute, co2=700, voc=200)
        return decision

    def test_shadow_is_default_and_has_no_credentials_or_delivery(self):
        decision = self.qualify()
        self.assertEqual(decision["reason"], "would_notify")
        self.assertEqual(decision["wouldNotify"], ["dylan", "julia"])
        self.sender.assert_not_called()
        self.credentials.assert_not_called()
        self.assertEqual(self.evaluate(15)["reason"], "episode_already_notified")

    def test_co2_requires_ten_minutes_not_a_single_spike(self):
        self.assertEqual(self.evaluate(0)["reason"], "observing")
        self.assertEqual(self.evaluate(5, co2=700)["reason"], "observing")
        self.assertEqual(self.evaluate(10)["reason"], "observing")
        self.assertEqual(self.evaluate(15)["reason"], "observing")
        self.assertEqual(self.evaluate(20)["reason"], "would_notify")

    def test_voc_requires_fifteen_minutes(self):
        for minute in (0, 5, 10):
            self.assertEqual(self.evaluate(minute, co2=700, voc=2500)["reason"], "observing")
        self.assertEqual(self.evaluate(15, co2=700, voc=2500)["triggers"], ["voc"])

    def test_humidity_only_poor_never_notifies(self):
        for minute in range(0, 35, 5):
            self.assertEqual(self.evaluate(minute, co2=700, voc=300)["reason"], "observing")
        self.presence.assert_not_called()

    def test_thresholds_are_inclusive(self):
        self.assertEqual(self.qualify(co2=1000)["reason"], "would_notify")

    def test_duplicate_readings_do_not_accumulate_time(self):
        record = self.record()
        self.evaluate(record=record)
        self.assertEqual(self.evaluate(5, record=record)["reason"], "duplicate_or_old_sample")
        self.assertEqual(self.evaluate(10, record=record)["reason"], "duplicate_or_old_sample")
        self.assertEqual(self.evaluate(15)["reason"], "observing")

    def test_sampling_gap_breaks_sustained_evidence(self):
        self.evaluate(0)
        self.evaluate(5)
        self.assertEqual(self.evaluate(15)["reason"], "observing")
        self.assertEqual(self.evaluate(20)["reason"], "observing")
        self.assertEqual(self.evaluate(25)["reason"], "would_notify")

    def test_stale_future_and_wrong_source_fail_closed(self):
        for patch in ({"timestamp": notify.stamp(self.now - timedelta(minutes=11))}, {"timestamp": notify.stamp(self.now + timedelta(minutes=2))}, {"history_origin": "csv_import"}):
            decision = self.evaluate(record={**self.record(), **patch})
            self.assertEqual(decision["reason"], "input_or_delivery_unavailable")
        self.sender.assert_not_called()

    def test_wrong_room_and_invalid_numbers_fail_closed(self):
        for patch in ({"room": "Bedroom"}, {"structure": "Crosstown"}, {"connectivity": "OFFLINE"}, {"co2_ppm": True}, {"voc_ppb": float("nan")}, {"co2_ppm": -1}):
            record = self.record()
            record["rooms"][0].update(patch)
            self.assertEqual(self.evaluate(record=record)["reason"], "input_or_delivery_unavailable")

    def test_live_delivery_is_once_per_recipient_across_restart(self):
        self.configure("live")
        self.assertEqual(self.qualify()["submitted"], ["dylan", "julia"])
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.evaluate(15)["reason"], "episode_already_notified")
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.state()["notified"], {"dylan": "submitted", "julia": "submitted"})

    def test_ambiguous_send_is_not_retried(self):
        self.configure("live")
        self.sender.side_effect = TimeoutError("private exception")
        self.assertEqual(self.qualify()["unconfirmed"], ["dylan", "julia"])
        self.evaluate(15)
        self.assertEqual(self.sender.call_count, 2)
        self.assertNotIn("private", json.dumps(self.state()))

    def test_claim_is_durable_before_send_and_survives_crash(self):
        self.configure("live")
        self.presence.return_value = ["dylan"]
        def crashed(*args):
            self.assertEqual(self.state()["notified"]["dylan"], "reserved")
            raise KeyboardInterrupt()
        self.sender.side_effect = crashed
        self.evaluate(0)
        self.evaluate(5)
        with self.assertRaises(KeyboardInterrupt):self.evaluate(10)
        self.sender.side_effect = None
        self.assertEqual(self.evaluate(15)["reason"], "episode_already_notified")
        self.assertEqual(self.sender.call_count, 1)

    def test_each_new_occupant_can_receive_once_without_resending_to_other(self):
        self.configure("live")
        self.presence.return_value = ["julia"]
        self.assertEqual(self.qualify()["submitted"], ["julia"])
        self.presence.return_value = ["dylan", "julia"]
        self.assertEqual(self.evaluate(15)["submitted"], ["dylan"])
        self.assertEqual(self.sender.call_count, 2)

    def test_no_occupants_no_send_and_presence_failure_notifies_nobody(self):
        self.configure("live")
        self.presence.return_value = []
        self.assertEqual(self.qualify()["reason"], "no_confirmed_occupants")
        self.presence.side_effect = notify.NotifyError("stale")
        self.assertEqual(self.evaluate(15)["reason"], "input_or_delivery_unavailable")
        self.sender.assert_not_called()

    def test_recheck_detects_departure_before_send(self):
        self.configure("live")
        self.evaluate(0)
        self.evaluate(5)
        self.presence.side_effect = [["dylan"], []]
        self.assertEqual(self.evaluate(10)["reason"], "delivery_recheck_suppressed")
        self.sender.assert_not_called()

    def test_recheck_honors_policy_disabled_during_evaluation(self):
        self.configure("live")
        def credentials():
            self.configure("off")
            return {"dylan": "chat_id:1", "julia": "chat_id:2"}, {}
        self.credentials.side_effect = credentials
        self.assertEqual(self.qualify()["reason"], "delivery_recheck_suppressed")
        self.sender.assert_not_called()

    def test_quiet_hours_do_not_queue_a_resolved_condition(self):
        self.now = datetime(2026, 10, 12, 11, 45, tzinfo=timezone.utc)
        self.assertEqual(self.qualify()["reason"], "quiet_hours")
        self.assertEqual(self.evaluate(15, co2=700)["reason"], "observing")
        self.sender.assert_not_called()

    def test_persistent_condition_is_rechecked_after_quiet_hours(self):
        self.now = datetime(2026, 10, 12, 11, 45, tzinfo=timezone.utc)
        self.qualify()
        self.assertEqual(self.evaluate(15)["reason"], "would_notify")

    def test_eastern_quiet_hours_handle_winter_offset(self):
        self.now = datetime(2026, 12, 12, 12, 45, tzinfo=timezone.utc)
        self.assertEqual(self.qualify()["reason"], "quiet_hours")
        self.assertEqual(self.evaluate(15)["reason"], "would_notify")

    def test_recovery_requires_both_metrics_low_for_fifteen_minutes(self):
        self.configure("live")
        self.qualify()
        for minute in (15, 20, 25):
            self.evaluate(minute, co2=800, voc=1500)
        self.assertIsNotNone(self.state()["episode"])
        for minute in (30, 35, 40):
            self.evaluate(minute, co2=700, voc=200)
            self.assertIsNotNone(self.state()["episode"])
        self.evaluate(45, co2=700, voc=200)
        self.assertIsNone(self.state()["episode"])
        for minute in (50, 55, 60):self.evaluate(minute)
        self.assertEqual(self.sender.call_count, 6)

    def test_paired_recovery_is_conditional_and_once_only(self):
        self.configure("live")
        self.qualify()
        decision = self.recover()
        self.assertEqual(decision["kind"], "recovery")
        self.assertEqual(decision["submitted"], ["dylan", "julia"])
        message = self.sender.call_args.args[1]
        self.assertIn("CO₂ and VOC readings", message)
        self.assertIn("15 minutes", message)
        self.assertIn("If you're done cooking", message)
        self.assertIn("probably close the windows", message)
        self.assertNotIn("safe", message)
        self.evaluate(30, co2=700, voc=200)
        self.evaluate(35, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 4)
        self.assertEqual(self.state()["recovery"]["notified"], {"dylan": "submitted", "julia": "submitted"})

    def test_good_air_without_an_alert_never_sends_recovery(self):
        self.configure("live")
        self.recover(0)
        self.sender.assert_not_called()
        self.presence.assert_not_called()
        self.assertIsNone(self.state()["recovery"])

    def test_recovery_excludes_new_arrivals_and_departed_recipients(self):
        self.configure("live")
        self.presence.return_value = ["dylan"]
        self.qualify()
        self.presence.return_value = ["julia"]
        self.assertEqual(self.recover()["submitted"], [])
        self.presence.return_value = ["dylan", "julia"]
        self.evaluate(35, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 1)

    def test_only_confirmed_initial_submissions_get_recovery(self):
        self.configure("live")
        self.sender.side_effect = [None, TimeoutError()]
        self.qualify()
        self.sender.side_effect = None
        self.assertEqual(self.recover()["submitted"], ["dylan"])
        self.assertEqual(self.sender.call_count, 3)

    def test_reserved_initial_alert_does_not_get_recovery(self):
        self.configure("live")
        self.sender.side_effect = KeyboardInterrupt()
        self.evaluate(0)
        self.evaluate(5)
        with self.assertRaises(KeyboardInterrupt):
            self.evaluate(10)
        self.sender.side_effect = None
        self.assertEqual(self.recover()["submitted"], [])
        self.assertEqual(self.sender.call_count, 1)

    def test_shadow_recovery_never_loads_credentials(self):
        self.qualify()
        self.assertEqual(self.recover()["wouldNotify"], ["dylan", "julia"])
        self.credentials.assert_not_called()
        self.sender.assert_not_called()

    def test_shadow_initial_alert_cannot_authorize_live_recovery(self):
        self.qualify()
        self.configure("live")
        self.assertEqual(self.recover()["submitted"], [])
        self.sender.assert_not_called()
        self.credentials.assert_not_called()

    def test_recovery_requires_strict_good_bands_for_both_metrics(self):
        self.configure("live")
        self.qualify()
        for minute in range(15, 35, 5):
            self.evaluate(minute, co2=800, voc=200)
        for minute in range(35, 55, 5):
            self.evaluate(minute, co2=700, voc=250)
        self.assertIsNotNone(self.state()["episode"])
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.recover(55)["submitted"], ["dylan", "julia"])

    def test_recovery_gap_and_duplicate_cannot_complete_timer(self):
        self.configure("live")
        self.qualify()
        self.evaluate(15, co2=700, voc=200)
        self.evaluate(20, co2=700, voc=200)
        self.evaluate(25, record=self.record(20, co2=700, voc=200))
        self.evaluate(30, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.recover(35)["submitted"], [])
        self.assertEqual(self.sender.call_count, 4)

    def test_stale_sample_resets_good_evidence_not_initial_receipts(self):
        self.configure("live")
        self.qualify()
        self.evaluate(15, co2=700, voc=200)
        self.evaluate(20, record=self.record(0, co2=700, voc=200))
        for minute in (25, 30, 35):
            self.evaluate(minute, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.evaluate(40, co2=700, voc=200)["submitted"], ["dylan", "julia"])

    def test_quiet_recovery_is_not_deferred_to_morning(self):
        self.now = datetime(2026, 10, 13, 1, 40, tzinfo=timezone.utc)
        self.configure("live")
        self.qualify()
        self.assertEqual(self.recover()["reason"], "quiet_hours")
        self.evaluate(620, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 2)

    def test_unknown_presence_at_recovery_does_not_queue_a_retry(self):
        self.configure("live")
        self.qualify()
        self.presence.side_effect = notify.NotifyError("stale")
        self.assertEqual(self.recover()["reason"], "input_or_delivery_unavailable")
        self.presence.side_effect = None
        self.evaluate(35, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 2)

    def test_recovery_rechecks_departure_and_disabled_mode(self):
        self.configure("live")
        self.qualify()
        for minute in (15, 20, 25):
            self.evaluate(minute, co2=700, voc=200)
        self.presence.side_effect = [["dylan", "julia"], [], []]
        self.assertEqual(self.evaluate(30, co2=700, voc=200)["reason"], "delivery_recheck_suppressed")
        self.assertEqual(self.sender.call_count, 2)
        self.presence.side_effect = None
        for minute in (35, 40, 45):
            self.evaluate(minute)
        def disable():
            self.configure("off")
            return {"dylan": "chat_id:1", "julia": "chat_id:2"}, {}
        self.credentials.side_effect = disable
        self.assertEqual(self.recover(50)["reason"], "delivery_recheck_suppressed")
        self.assertEqual(self.sender.call_count, 4)

    def test_ambiguous_recovery_is_not_retried(self):
        self.configure("live")
        self.qualify()
        self.sender.side_effect = TimeoutError()
        self.assertEqual(self.recover()["unconfirmed"], ["dylan", "julia"])
        self.sender.side_effect = None
        self.evaluate(35, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 4)
        self.assertEqual(self.state()["recovery"]["notified"], {"dylan": "unconfirmed", "julia": "unconfirmed"})

    def test_recovery_reservation_survives_crash_without_replay(self):
        self.configure("live")
        self.qualify()
        def crashed(*args):
            self.assertEqual(self.state()["recovery"]["notified"], {"dylan": "reserved"})
            self.assertIsNone(self.state()["episode"])
            raise KeyboardInterrupt()
        self.sender.side_effect = crashed
        with self.assertRaises(KeyboardInterrupt):
            self.recover()
        self.sender.side_effect = None
        self.evaluate(35, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 3)
        for minute in (40, 45, 50):
            self.evaluate(minute)
        self.assertEqual(self.sender.call_count, 5)

    def test_legacy_migration_preserves_alerts_but_resets_old_good_timer(self):
        self.configure("live")
        self.qualify()
        legacy = self.state()
        legacy.pop("recovery")
        legacy["schema"] = 1
        legacy["recoverySince"] = notify.stamp(self.now)
        notify.atomic_json(self.root / "state.json", legacy)
        self.evaluate(15, co2=700, voc=200)
        self.assertEqual(self.state()["schema"], 2)
        self.assertEqual(self.state()["notified"], legacy["notified"])
        self.assertEqual(self.state()["episode"], legacy["episode"])
        self.assertIsNone(self.state()["recovery"])
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.state()["recoverySince"], self.record(15)["timestamp"])
        self.assertEqual(self.recover(20)["submitted"], [])
        self.assertEqual(self.sender.call_count, 4)

    def test_closed_legacy_episode_does_not_generate_historical_recovery(self):
        legacy = notify.initial_state()
        legacy.pop("recovery")
        legacy["schema"] = 1
        notify.atomic_json(self.root / "state.json", legacy)
        self.configure("live")
        self.recover(0)
        self.sender.assert_not_called()

    def test_invalid_recovery_receipts_fail_closed(self):
        self.configure("live")
        self.qualify()
        self.recover()
        baseline = self.state()
        for patch in ({"recipients": ["guest"]}, {"shadowed": ["guest"]}, {"notified": {"dylan": "unknown"}}, {"sampleAt": self.record(40)["timestamp"]}, {"extra": True}, {"recipients": []}):
            value = {**baseline, "recovery": {**baseline["recovery"], **patch}}
            with self.assertRaises(notify.NotifyError):
                notify.validate_state(value)

    def test_invalid_legacy_shape_is_not_migrated(self):
        legacy = notify.initial_state()
        legacy["schema"] = 1
        with self.assertRaises(notify.NotifyError):
            notify.validate_state(legacy)
        legacy.pop("recovery")
        legacy["recoverySince"] = "invalid"
        with self.assertRaises(notify.NotifyError):
            notify.validate_state(legacy)

    def test_recovery_reaches_only_initial_recipients_still_present(self):
        self.configure("live")
        self.qualify()
        self.presence.return_value = ["julia"]
        self.assertEqual(self.recover()["submitted"], ["julia"])
        self.assertEqual(self.sender.call_count, 3)
        self.assertEqual(self.sender.call_args.args[0], "chat_id:2")

    def test_good_timer_restarts_when_either_metric_leaves_good_band(self):
        self.configure("live")
        self.qualify()
        self.evaluate(15, co2=700, voc=200)
        self.evaluate(20, co2=700, voc=200)
        self.evaluate(25, co2=700, voc=300)
        for minute in (30, 35, 40):
            self.evaluate(minute, co2=700, voc=200)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.evaluate(45, co2=700, voc=200)["submitted"], ["dylan", "julia"])

    def test_gap_or_stale_sample_never_erases_delivery_history(self):
        self.configure("live")
        self.qualify()
        self.evaluate(20, record=self.record(0))
        self.evaluate(30)
        self.evaluate(35)
        self.evaluate(40)
        self.assertEqual(self.sender.call_count, 2)

    def test_shadow_records_do_not_masquerade_as_real_delivery(self):
        self.qualify()
        self.assertEqual(self.state()["notified"], {})
        self.configure("live")
        self.assertEqual(self.evaluate(15)["submitted"], ["dylan", "julia"])

    def test_invalid_or_missing_state_never_reinitializes(self):
        path = self.root / "state.json"
        path.write_text("{}")
        with self.assertRaises(notify.NotifyError):self.evaluate()
        self.assertEqual(path.read_text(), "{}")
        path.unlink()
        with self.assertRaises(OSError):self.evaluate()
        self.assertFalse(path.exists())

    def test_private_files_and_no_double_initialization(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "state.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):notify.initialize(self.root)
        self.assertEqual(list(self.root.glob(".notify-*")), [])

    def test_lock_prevents_concurrent_evaluation(self):
        with notify.locked(self.root):
            with self.assertRaises(notify.NotifyError):self.evaluate()

    def test_unsafe_or_symlinked_state_is_rejected(self):
        path = self.root / "state.json"
        path.chmod(0o644)
        with self.assertRaises(notify.NotifyError):self.evaluate()
        path.chmod(0o600)
        renamed = self.root / "elsewhere.json"
        path.rename(renamed)
        path.symlink_to(renamed)
        with self.assertRaises(OSError):self.evaluate()

    def test_message_is_fixed_conditional_and_contains_only_triggering_metrics(self):
        sample = notify.sample_from(self.record(), self.now)
        text = notify.render_message(sample, ["co2"])
        self.assertIn("1", text)
        self.assertIn("weather and outdoor air quality permit", text)
        self.assertNotIn("VOCs", text)
        self.assertNotIn("humidity", text)

    def test_credentials_use_protected_distinct_routes_without_shell_evaluation(self):
        cache = self.base / "cache"
        cache.write_text("DYLAN_CHAT_ID='1'\nJULIA_CHAT_ID='2'\nOPENCLAW_GATEWAY_TOKEN='$(not-executed)'\nUNRELATED_SECRET=private\n")
        cache.chmod(0o600)
        routes, auth = notify.credentials(cache)
        self.assertEqual(routes, {"dylan": "chat_id:1", "julia": "chat_id:2"})
        self.assertEqual(auth, {"OPENCLAW_GATEWAY_TOKEN": "$(not-executed)"})
        cache.write_text(cache.read_text().replace("JULIA_CHAT_ID='2'", "JULIA_CHAT_ID='1'"))
        with self.assertRaises(notify.NotifyError):notify.credentials(cache)


class RecipientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = datetime(2026, 10, 12, 14, tzinfo=timezone.utc)
        self.canonical = {"timestamp": notify.stamp(self.now), "cabin": {"fresh": True, "occupancy": "occupied"}, "people": {name: {"location": "cabin", "cabin": True, "crosstown": False} for name in notify.PEOPLE.values()}}
        self.cabin = {"location": "cabin", "timestamp": notify.stamp(self.now), "presence": {name: {"present": True} for name in notify.PEOPLE.values()}}
        self.other = {"location": "crosstown", "timestamp": notify.stamp(self.now), "presence": {name: {"present": False} for name in notify.PEOPLE.values()}}

    def occupants(self):
        for name, value in (("state.json", self.canonical), ("cabin-scan.json", self.cabin), ("crosstown-scan.json", self.other)):
            path = self.root / name
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        return notify.occupants(self.now, self.root)

    def test_fresh_positive_evidence_only(self):
        self.assertEqual(self.occupants(), ["dylan", "julia"])
        self.cabin["presence"]["Dylan"]["present"] = False
        self.assertEqual(self.occupants(), ["julia"])

    def test_retained_location_is_not_presence(self):
        for name in notify.PEOPLE.values():self.cabin["presence"][name]["present"] = False
        self.assertEqual(self.occupants(), [])

    def test_conflicting_fresh_other_home_suppresses_only_affected_person(self):
        self.other["presence"]["Julia"]["present"] = True
        self.assertEqual(self.occupants(), ["dylan"])

    def test_stale_future_unknown_or_vacant_presence(self):
        self.cabin["timestamp"] = notify.stamp(self.now - timedelta(minutes=31))
        with self.assertRaises(notify.NotifyError):self.occupants()
        self.cabin["timestamp"] = notify.stamp(self.now + timedelta(minutes=2))
        with self.assertRaises(notify.NotifyError):self.occupants()
        self.cabin["timestamp"] = notify.stamp(self.now)
        self.canonical["cabin"]["occupancy"] = "confirmed_vacant"
        self.assertEqual(self.occupants(), [])

    def test_unenrolled_names_cannot_become_recipients(self):
        self.cabin["presence"]["Guest"] = {"present": True}
        self.canonical["people"]["Guest"] = {"location": "cabin", "cabin": True, "crosstown": False}
        self.assertEqual(self.occupants(), ["dylan", "julia"])

    def test_error_flag_rejects_positive_cached_presence(self):
        self.cabin["error"] = "scanner_failed"
        with self.assertRaises(notify.NotifyError):self.occupants()
        del self.cabin["error"]
        self.canonical["error"] = "evaluation_failed"
        with self.assertRaises(notify.NotifyError):self.occupants()
