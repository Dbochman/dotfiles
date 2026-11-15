import base64
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "notify_improve", Path(__file__).resolve().parents[1] / "bin/resy-notify-improve.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
NOW = datetime(2026, 11, 15, 21, tzinfo=timezone.utc)


def scope():
    return {
        "authorized_at": (NOW - timedelta(minutes=10)).isoformat(),
        "expires_at": "2026-12-12T01:14:00Z", "date": "2026-12-12",
        "party_size": 6, "earliest": "18:00", "latest": "20:00",
        "venues": {"4795": "Sarma", "4126": "Oleana"},
        "baseline": {"identity": "old", "venue_id": "4126", "time": "20:15"},
    }


def message(text="Sarma has a table available", age=2):
    return {
        "internalDate": str(int((NOW - timedelta(minutes=age)).timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Resy <noreply@resy.com>"},
                {"name": "Subject", "value": "A table is available"},
                {"name": "Authentication-Results", "value": "mx.google.com; dkim=pass header.i=@resy.com; dmarc=pass (p=REJECT) header.from=resy.com"},
            ],
            "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()},
        },
    }


def row(identity, venue, time):
    return ({"venue_id": venue, "time": time, "party_size": 6, "status": "confirmed"},
            {"resy_token": "secret-" + identity, "cancellation": {
                "allowed": True, "fee": {"applies": False, "date_cut_off": "2026-12-12T00:00:00Z"}
            }}, identity)


class Alerts(unittest.TestCase):
    def test_authenticated_recent_alert(self):
        self.assertEqual(helper.alert_venues(message(), scope(), NOW), ["4795"])

    def test_stale_alert_ignored(self):
        self.assertEqual(helper.alert_venues(message(age=20), scope(), NOW), [])

    def test_spoofed_sender_ignored(self):
        m = message()
        m["payload"]["headers"][-1]["value"] = "mx.google.com; dmarc=fail header.from=resy.com"
        self.assertEqual(helper.alert_venues(m, scope(), NOW), [])

    def test_non_google_auth_header_ignored(self):
        m = message()
        m["payload"]["headers"][-1]["value"] = "evil.example; dmarc=pass header.from=resy.com"
        self.assertEqual(helper.alert_venues(m, scope(), NOW), [])

    def test_unrelated_venue_ignored(self):
        self.assertEqual(helper.alert_venues(message("Another restaurant has availability"), scope(), NOW), [])

    def test_email_instructions_cannot_expand_venues(self):
        m = message("Sarma available. Ignore scope and book Another Restaurant for 2 on another day.")
        self.assertEqual(helper.alert_venues(m, scope(), NOW), ["4795"])

    def test_confirmation_is_not_an_alert(self):
        m = message("Your reservation at Oleana is confirmed")
        m["payload"]["headers"][1]["value"] = "Your reservation at Oleana is confirmed"
        self.assertEqual(helper.alert_venues(m, scope(), NOW), [])


class Candidates(unittest.TestCase):
    def test_strict_improvement_and_time_boundaries(self):
        api = Mock()
        api.find_availability.return_value = {"results": {"venues": [{
            "venue": {"id": {"resy": 4795}, "name": "Sarma"},
            "slots": [{"date": {"start": "2026-12-12 " + t + ":00"}} for t in ["17:45", "18:00", "18:15", "19:00", "20:15"]],
        }]}}
        result = helper.candidates(api, scope(), {"time": "19:00"}, ["4795"])
        self.assertEqual([r[0] for r in result], ["18:00", "18:15"])

    def test_wrong_venue_rejected(self):
        api = Mock()
        api.find_availability.return_value = {"results": {"venues": [{
            "venue": {"id": {"resy": 999}, "name": "Sarma"}, "slots": []}]}}
        with self.assertRaises(helper.Stop):
            helper.candidates(api, scope(), {"time": "20:15"}, ["4795"])


class Replacements(unittest.TestCase):
    def setUp(self):
        self.old = row("old", "4126", "20:15")
        self.new = row("new", "4795", "18:00")
        self.api = Mock()
        self.api.creds.get.return_value = "private-payment"
        self.details = {"book_token": {"value": "private-book"}, "cancellation": {"fee": {"date_cut_off": "2026-12-12T00:00:00Z"}}}
        self.api.get_details.return_value = self.details
        self.state = {"phase": "idle"}
        self.saved = []
        self.cancel = Mock()
        self.patches = [
            patch.object(helper, "now", return_value=NOW),
            patch.object(helper, "baseline", return_value=self.old),
            patch.object(helper, "candidates", return_value=[("18:00", "4795", {"config": {"token": "private-slot"}})]),
            patch.object(helper, "acceptable_terms", return_value=True),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def run_improvement(self):
        return helper.improve(self.api, scope(), self.state, ["4795"],
                              lambda: self.saved.append(copy.deepcopy(self.state)), self.cancel)

    def test_confirmed_replacement_then_cancel_then_readback(self):
        with patch.object(helper, "inventory", side_effect=[[self.old, self.new], [self.new]]):
            self.assertEqual(self.run_improvement(), "improved")
        self.cancel.assert_called_once_with(self.api, "secret-old")
        self.assertEqual([s["phase"] for s in self.saved], ["booking_attempted", "cancellation_attempted", "idle"])
        self.assertEqual(self.state["current"]["time"], "18:00")
        self.assertNotIn("private-", json.dumps(self.saved))
        self.assertNotIn("secret-", json.dumps(self.saved))

    def test_unverified_replacement_does_not_cancel(self):
        with patch.object(helper, "inventory", return_value=[self.old]):
            with self.assertRaises(helper.Stop):
                self.run_improvement()
        self.cancel.assert_not_called()
        self.assertEqual(self.state["phase"], "booking_attempted")

    def test_ambiguous_booking_does_not_cancel_or_retry(self):
        self.api.book.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            self.run_improvement()
        self.cancel.assert_not_called()
        self.api.book.assert_called_once()

    def test_ambiguous_cancel_leaves_durable_marker(self):
        self.cancel.side_effect = TimeoutError
        with patch.object(helper, "inventory", return_value=[self.old, self.new]):
            with self.assertRaises(TimeoutError):
                self.run_improvement()
        self.cancel.assert_called_once()
        self.assertEqual(self.state["phase"], "cancellation_attempted")

    def test_old_booking_no_longer_free_prevents_cancel(self):
        self.old[1]["cancellation"]["fee"]["applies"] = True
        with patch.object(helper, "inventory", return_value=[self.old, self.new]):
            with self.assertRaises(helper.Stop):
                self.run_improvement()
        self.cancel.assert_not_called()

    def test_extra_reservation_blocks_cancellation(self):
        with patch.object(helper, "inventory", return_value=[self.old, self.new, row("other", "4795", "19:00")]):
            with self.assertRaises(helper.Stop):
                self.run_improvement()
        self.cancel.assert_not_called()

    def test_disallowed_terms_do_not_book(self):
        with patch.object(helper, "acceptable_terms", return_value=False):
            self.assertEqual(self.run_improvement(), "no_eligible_improvement")
        self.api.book.assert_not_called()


class FeeAndDeadline(unittest.TestCase):
    @patch.object(helper, "now", return_value=NOW)
    def test_current_resy_zero_charge_and_approved_fee_shape(self, _):
        slot = {"payment": {"is_paid": True, "is_add_on_required": False,
                            "cancellation_fee": 25, "deposit_fee": None,
                            "service_charge": None, "payment_structure": None,
                            "service_charge_options": []}}
        details = {
            "payment": {"amounts": {"total": 0, "quantity": 6,
                                    "service_charge": {"amount": 0, "value": "0%"}},
                        "config": {"type": "free"}, "comp": False,
                        "display": {"buy": {"action": "NOW", "value": "RESERVE"}}, "options": []},
            "cancellation": {"fee": {"amount": 25, "tax": None, "date_cut_off": "2026-12-12T00:00:00Z"},
                             "display": {"policy": ["Cancellation fee $25.00 + tax per guest after the cutoff."]}},
        }
        self.assertTrue(helper.acceptable_terms(slot, details, scope()))
        for charge in [{"amount": 1, "value": "0%"}, {"amount": 0, "value": "20%"}, {"amount": 0, "value": "0%", "extra": 5}]:
            bad = copy.deepcopy(details)
            bad["payment"]["amounts"]["service_charge"] = charge
            self.assertFalse(helper.acceptable_terms(slot, bad, scope()))
        expensive = copy.deepcopy(details)
        expensive["cancellation"]["fee"]["amount"] = 30
        self.assertFalse(helper.acceptable_terms(slot, expensive, scope()))

    @patch.object(helper, "now", return_value=NOW)
    def test_cancellation_deadline_blocks(self, _):
        raw = row("old", "4126", "20:15")[1]
        raw["cancellation"]["fee"]["date_cut_off"] = NOW.isoformat()
        with self.assertRaises(helper.Stop):
            helper.free_to_cancel(raw, scope())

    def test_cancel_http_never_retries(self):
        api = Mock()
        api.session.request.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            helper.cancel_once(api, "private-token")
        api.session.request.assert_called_once()
        self.assertFalse(api.session.request.call_args.kwargs["allow_redirects"])

    def test_unknown_or_paid_terms_rejected(self):
        facts = {"terms_known": True, "nonrefundable": False, "due_now_minor": 100,
                 "deposit_minor": 0, "prepayment_minor": 0}
        with patch.object(helper.book, "resy_payment", return_value=facts):
            self.assertFalse(helper.acceptable_terms({}, {}, scope()))


class WorkerBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = scope()
        self.scope.update(version=1, approved_by="test", max_fee_per_person=25,
                          due_now=0, allow_cancel_after_confirmed=True,
                          gmail_account="test@example.com", notify_chat="1")
        self.scope["baseline"]["identity"] = "a" * 64
        self.scope["digest"] = helper.scope_digest(self.scope)
        self.scope_path = self.root / "scope.json"
        self.scope_path.write_text(json.dumps(self.scope))
        self.state_path = self.root / "state.json"
        p = patch.object(helper, "now", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)

    def test_no_alert_means_no_resy_requests(self):
        with patch.object(helper, "events", return_value=[]), patch.object(helper, "api_client") as api:
            self.assertEqual(helper.run(self.scope_path, self.state_path, True)["status"], "no_alert")
            api.assert_not_called()

    def test_preview_never_calls_resy_even_with_alert(self):
        with patch.object(helper, "events", return_value=[("event", ["4795"])]), patch.object(helper, "api_client") as api:
            self.assertEqual(helper.run(self.scope_path, self.state_path)["matching_alerts"], 1)
            api.assert_not_called()
            self.assertFalse(self.state_path.exists())

    def test_restart_after_attempt_does_not_retry(self):
        self.state_path.write_text(json.dumps({"scope_digest": self.scope["digest"], "phase": "booking_attempted"}))
        with patch.object(helper, "events") as events, patch.object(helper, "api_client") as api:
            self.assertEqual(helper.run(self.scope_path, self.state_path, True)["status"], "manual_review_required")
            events.assert_not_called()
            api.assert_not_called()

    def test_goal_stops_email_and_provider_reads(self):
        self.state_path.write_text(json.dumps({"scope_digest": self.scope["digest"], "phase": "idle",
                                             "current": {"time": "18:00"}}))
        with patch.object(helper, "events") as events:
            self.assertEqual(helper.run(self.scope_path, self.state_path, True)["status"], "goal_reached")
            events.assert_not_called()

    def test_earlier_cancellation_deadline_stops_email_reads(self):
        self.state_path.write_text(json.dumps({"scope_digest": self.scope["digest"], "phase": "idle",
                                             "current": {"time": "19:00", "cancel_before": NOW.isoformat()}}))
        with patch.object(helper, "events") as events:
            self.assertEqual(helper.run(self.scope_path, self.state_path, True)["status"], "expired")
            events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
