#!/usr/bin/env python3
"""Fake-only tests for the dual-provider restaurant booking coordinator."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COORDINATOR_PATH = REPO_ROOT / "openclaw" / "bin" / "restaurant-book.py"
SCOPES_PATH = REPO_ROOT / "openclaw" / "cron" / "restaurant-booking-scopes.json"
SPEC = importlib.util.spec_from_file_location("restaurant_book_test_module", COORDINATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
restaurant_book = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = restaurant_book
SPEC.loader.exec_module(restaurant_book)


JOB_ID = "datenight-aug-farmtotable"
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class AuditBaseException(BaseException):
    pass


def canonical_scope() -> dict[str, object]:
    payload = json.loads(SCOPES_PATH.read_text(encoding="utf-8"))
    return restaurant_book.validate_scope(payload["jobs"][JOB_ID], JOB_ID)


def resy_free_slot(day: str = "2026-08-07", time_value: str = "19:00") -> dict[str, object]:
    return {
        "date": {"start": f"{day} {time_value}:00"},
        "config": {"token": "RESY_PRIVATE_TOKEN", "type": "Dining Room"},
        "payment": {
            "is_paid": False,
            "is_add_on_required": False,
            "cancellation_fee": None,
            "deposit_fee": None,
            "service_charge": None,
            "venue_share": None,
            "payment_structure": None,
            "secs_cancel_cut_off": None,
            "time_cancel_cut_off": None,
            "secs_change_cut_off": None,
            "time_change_cut_off": None,
            "options": [],
            "service_charge_options": [],
        },
    }


def resy_free_details() -> dict[str, object]:
    return {
        "payment": {
            "amounts": {
                "items": [],
                "reservation_charge": 0,
                "subtotal": 0,
                "add_ons": 0,
                "resy_fee": 0,
                "service_fee": 0,
                "service_charge": 0,
                "tax": 0,
                "total": 0,
                "surcharge": 0,
                "price_per_unit": 0,
                "quantity": 2,
            },
            "config": {"type": "free"},
            "display": {
                "balance": {"value": "", "modifier": ""},
                "buy": {
                    "action": "NOW",
                    "value": "RESERVE",
                    "init": "",
                    "before_modifier": "",
                    "after_modifier": "",
                },
                "description": [],
                "title": "Reserve",
                "total": "$0.00",
            },
            "options": [
                {
                    "amounts": {
                        "subtotal": 0,
                        "resy_fee": 0,
                        "service_fee": 0,
                        "service_charge": 0,
                        "tax": 0,
                        "total": 0,
                        "price_per_unit": 0,
                    },
                }
            ],
        },
        "cancellation": {
            "display": {"policy": ["While you won't be charged, please cancel promptly."]},
            "fee": None,
        },
    }


def opentable_availability(
    day: str = "2026-08-07",
    time_value: str = "19:00",
    *,
    card_required: bool = False,
    token: str = "OT_PRIVATE_TOKEN",
    slot_hash: str = "OT_PRIVATE_HASH",
) -> dict[str, object]:
    return {
        "suggestedAvailability": [
            {
                "timeslots": [
                    {
                        "available": True,
                        "dateTime": f"{day}T{time_value}:00",
                        "token": token,
                        "slotHash": slot_hash,
                        "type": "Standard",
                        "diningAreas": [{"id": "area-1", "environment": "Dining Room"}],
                        "isCreditCardRequired": card_required,
                    }
                ]
            }
        ]
    }


def free_payment(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "currency": "USD",
        "due_now_minor": 0,
        "deposit_minor": 0,
        "prepayment_minor": 0,
        "cancellation_fee_minor": 0,
        "no_show_fee_minor": 0,
        "nonrefundable": False,
        "card_guarantee": False,
        "terms_known": True,
    }
    result.update(overrides)
    return result


def candidate(
    platform: str,
    *,
    restaurant: str | None = None,
    day: str = "2026-08-07",
    time_value: str = "19:00",
    rank: int = 1,
    payment: dict[str, object] | None = None,
) -> object:
    return restaurant_book.Candidate(
        platform=platform,
        venue_id=f"{platform}-venue",
        restaurant=restaurant or f"{platform.title()} Farm",
        cuisine="Farm-to-Table American",
        location="Brookline, MA",
        date=day,
        time=time_value,
        party_size=2,
        seating="Standard",
        payment=payment or free_payment(),
        cancellation_policy="No cancellation fee",
        no_show_policy="No no-show fee",
        provider_rank=rank,
        private={"secret_token": "DO-NOT-PRINT"},
    )


def reservation(
    platform: str,
    *,
    day: str = "2026-08-07",
    time_value: str = "19:00",
    party_size: int = 2,
    restaurant: str = "Existing Farm",
    status: str = "confirmed",
) -> dict[str, object]:
    return {
        "platform": platform,
        "venue_id": f"{platform}-venue",
        "restaurant": restaurant,
        "date": day,
        "time": time_value,
        "party_size": party_size,
        "status": status,
    }


class FakeResy:
    def __init__(self, candidates: list[object] | None = None):
        self.current: list[dict[str, object]] = []
        self.candidates = candidates if candidates is not None else [candidate("resy")]
        self.reads = 0
        self.searches = 0
        self.mutations = 0
        self.fail_reads = False
        self.fail_search = False
        self.search_status = "ok"
        self.fail_after_boundary = False
        self.base_failure_after_boundary = False
        self.before_boundary = None
        self.confirmation_status = "confirmed"
        self.events: list[str] = []

    def reservations(self, *, final: bool = False) -> list[dict[str, object]]:
        self.reads += 1
        self.events.append("resy-final-read" if final else "resy-read")
        if self.fail_reads:
            raise restaurant_book.ProviderUnavailable("fake")
        return list(self.current)

    def search(self, scope: dict[str, object]):
        del scope
        self.searches += 1
        if self.fail_search:
            raise restaurant_book.ProviderUnavailable("fake")
        return list(self.candidates), self.search_status

    def prepare(self, selected: object):
        self.events.append("resy-prepare")
        return (selected, "fake-payment")

    def book(self, prepared: object, live_guard, mutation_boundary):
        del prepared
        self.events.append("resy-book-enter")
        if self.before_boundary:
            self.before_boundary()
        live_guard()
        mutation_boundary()
        self.events.append("resy-mutation")
        self.mutations += 1
        if self.fail_after_boundary:
            raise restaurant_book.ProviderUnavailable("fake ambiguity")
        if self.base_failure_after_boundary:
            raise AuditBaseException("unexpected post-boundary failure")
        selected = self.candidates[0]
        self.current = [
            reservation(
                "resy",
                day=selected.date,
                time_value=selected.time,
                party_size=selected.party_size,
                restaurant=selected.restaurant,
                status=self.confirmation_status,
            )
        ]
        return {"success": True}


class FakeOpenTable:
    def __init__(self, candidates: list[object] | None = None):
        self.current: list[dict[str, object]] = []
        self.candidates = candidates if candidates is not None else [candidate("opentable")]
        self.reads = 0
        self.searches = 0
        self.confirms = 0
        self.fail_reads = False
        self.fail_search = False
        self.search_status = "ok"
        self.stale = False
        self.before_boundary = None
        self.fail_after_boundary = False
        self.ambiguous = False
        self.base_failure_after_boundary = False
        self.fail_post_readback = False
        self.mismatch_post_readback = False
        self.mutation_completed = False
        self.events: list[str] = []

    def reservations(self, *, final: bool = False) -> list[dict[str, object]]:
        self.reads += 1
        self.events.append("opentable-final-read" if final else "opentable-read")
        if self.fail_reads:
            raise restaurant_book.ProviderUnavailable("fake")
        if self.fail_post_readback and self.mutation_completed:
            raise restaurant_book.ProviderUnavailable("fake post-book readback")
        if self.mismatch_post_readback and self.mutation_completed:
            return [reservation("opentable", time_value="20:00")]
        return list(self.current)

    def search(self, scope: dict[str, object]):
        del scope
        self.searches += 1
        if self.fail_search:
            raise restaurant_book.ProviderUnavailable("fake")
        return list(self.candidates), self.search_status

    def refresh(self, scope: dict[str, object], selected: object):
        del scope
        self.events.append("opentable-refresh")
        if self.stale:
            raise restaurant_book.ProviderUnavailable("fake changed slot")
        return selected

    def book(self, selected: object, live_guard, mutation_boundary):
        self.events.append("opentable-book-enter")
        self.confirms += 1
        if self.before_boundary:
            self.before_boundary()
        live_guard()
        mutation_boundary()
        self.events.append("opentable-mutation")
        if self.ambiguous or self.fail_after_boundary:
            raise restaurant_book.ProviderUnavailable("fake ambiguity")
        if self.base_failure_after_boundary:
            raise AuditBaseException("unexpected post-boundary failure")
        self.mutation_completed = True
        self.current = [
            reservation(
                "opentable",
                day=selected.date,
                time_value=selected.time,
                party_size=selected.party_size,
                restaurant=selected.restaurant,
            )
        ]
        return {
            "success": True,
            "reservation": {
                "reservationId": "ot-confirmation",
                "status": "confirmed",
            },
        }


class RestaurantBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        scope_parent = self.home / ".openclaw" / "restaurant-bookings"
        scope_parent.mkdir(mode=0o700, parents=True)
        self.scopes = scope_parent / "scopes.json"
        shutil.copyfile(SCOPES_PATH, self.scopes)
        self.scopes.chmod(0o600)
        self.resy = FakeResy()
        self.opentable = FakeOpenTable()
        self.coordinator = restaurant_book.Coordinator(
            home=self.home,
            scopes_path=self.scopes,
            resy=self.resy,
            opentable=self.opentable,
            now=lambda: NOW,
        )

    def test_registry_contains_exact_nine_dual_provider_scopes(self) -> None:
        payload = json.loads(SCOPES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(payload["jobs"]),
            {
                "datenight-aug-farmtotable",
                "datenight-sep-steakhouse",
                "datenight-oct-indian",
                "datenight-nov-american",
                "datenight-dec-upscale",
                "doubledate-q4-oct-mexican",
                "doubledate-q1-jan27-french",
                "qd-booking-2026-10-sep15",
                "qd-booking-2027-01-dec15",
            },
        )
        for job_id, raw_scope in payload["jobs"].items():
            with self.subTest(job_id=job_id):
                scope = restaurant_book.validate_scope(raw_scope, job_id)
                self.assertEqual(scope["providers"], ["resy", "opentable"])
                self.assertEqual(scope["authorization"]["max_mutation_attempts"], 1)
                self.assertEqual(scope["fees"]["unknown_terms"], "reject")
                self.assertEqual(scope["fees"]["max_due_now_minor"], 0)

    def test_open_table_reader_contract_allows_missing_venue_id_but_not_missing_core_facts(self) -> None:
        bin_dir = self.home / ".openclaw" / "bin"
        bin_dir.mkdir(mode=0o700, exist_ok=True)
        reader = bin_dir / "opentable-reservations"

        def write_reader(reservations: list[dict[str, object]]) -> None:
            payload = {
                "success": True,
                "provider": "opentable",
                "checked_at": restaurant_book.timestamp(restaurant_book.utc_now()),
                "reservations": reservations,
            }
            reader.write_text(
                "#!/usr/bin/env python3\nimport json\nprint(json.loads("
                + repr(json.dumps(json.dumps(payload)))
                + "))\n",
                encoding="utf-8",
            )
            reader.chmod(0o755)

        write_reader(
            [
                {
                    "platform": "opentable",
                    "reservation_id": "private-provider-id",
                    "restaurant": "Example Farm",
                    "date": "2026-08-07",
                    "time": "19:00",
                    "party_size": 2,
                    "status": "confirmed",
                    "location": "Brookline",
                }
            ]
        )
        provider = restaurant_book.OpenTableProvider(self.home)
        normalized = provider.reservations()
        self.assertIsNone(normalized[0]["venue_id"])
        self.assertNotIn("reservation_id", normalized[0])

        write_reader(
            [
                {
                    "platform": "opentable",
                    "reservation_id": "private-provider-id",
                    "restaurant": "Example Farm",
                    "date": "2026-08-07",
                    "time": "19:00",
                    "status": "confirmed",
                }
            ]
        )
        with self.assertRaises(restaurant_book.ProviderUnavailable):
            provider.reservations()

    def test_plan_reads_and_searches_both_without_state_or_mutation(self) -> None:
        result = self.coordinator.plan(JOB_ID)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["candidate"]["platform"], "resy")
        self.assertEqual(self.resy.reads, 1)
        self.assertEqual(self.opentable.reads, 1)
        self.assertEqual(self.resy.searches, 1)
        self.assertEqual(self.opentable.searches, 1)
        self.assertEqual(self.resy.mutations, 0)
        self.assertEqual(self.opentable.confirms, 0)
        self.assertFalse(self.coordinator.shared_state_root.exists())
        self.assertNotIn("DO-NOT-PRINT", json.dumps(result))

    def test_deterministic_ranking_searches_both_and_uses_resy_tiebreak(self) -> None:
        self.resy.candidates = [candidate("resy", time_value="19:15", rank=9)]
        self.opentable.candidates = [candidate("opentable", time_value="19:15", rank=1)]

        first = self.coordinator.plan(JOB_ID)
        second = self.coordinator.plan(JOB_ID)

        self.assertEqual(first["candidate"]["candidate_digest"], second["candidate"]["candidate_digest"])
        self.assertEqual(first["candidate"]["platform"], "resy")

    def test_either_reservation_reader_failure_blocks_all_search_and_booking(self) -> None:
        for provider_name in ("resy", "opentable"):
            with self.subTest(provider=provider_name):
                self.resy.fail_reads = provider_name == "resy"
                self.opentable.fail_reads = provider_name == "opentable"
                self.resy.searches = self.opentable.searches = 0
                result = self.coordinator.plan(JOB_ID)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], "reservation_guard_unavailable")
                self.assertEqual(self.resy.searches, 0)
                self.assertEqual(self.opentable.searches, 0)

    def test_existing_reservation_on_either_provider_blocks_before_search(self) -> None:
        for provider_name in ("resy", "opentable"):
            with self.subTest(provider=provider_name):
                self.resy.current = [reservation("resy")] if provider_name == "resy" else []
                self.opentable.current = [reservation("opentable")] if provider_name == "opentable" else []
                self.resy.searches = self.opentable.searches = 0
                result = self.coordinator.plan(JOB_ID)
                self.assertEqual(result["status"], "already_reserved")
                self.assertEqual(result["existing_reservation"]["platform"], provider_name)
                self.assertEqual(self.resy.searches, 0)
                self.assertEqual(self.opentable.searches, 0)

    def test_either_search_failure_blocks_instead_of_biasing_provider_choice(self) -> None:
        self.opentable.fail_search = True

        result = self.coordinator.plan(JOB_ID)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "provider_search_incomplete")
        self.assertEqual(self.resy.searches, 1)
        self.assertEqual(self.opentable.searches, 1)

    def test_partial_provider_search_blocks_deterministic_mutation(self) -> None:
        self.resy.search_status = "partial"

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "provider_search_incomplete")
        self.assertEqual(self.resy.mutations, 0)
        self.assertEqual(self.opentable.confirms, 0)

    def test_fee_and_unknown_term_candidates_are_rejected(self) -> None:
        bad_payments = (
            free_payment(deposit_minor=1),
            free_payment(prepayment_minor=1),
            free_payment(cancellation_fee_minor=1),
            free_payment(no_show_fee_minor=1),
            free_payment(nonrefundable=True),
            free_payment(terms_known=False),
        )
        for payment in bad_payments:
            with self.subTest(payment=payment):
                self.resy.candidates = [candidate("resy", payment=payment)]
                self.opentable.candidates = []
                result = self.coordinator.plan(JOB_ID)
                self.assertEqual(result["status"], "no_availability")

    def test_fee_parsers_reject_unpriced_or_unknown_terms(self) -> None:
        unpriced = restaurant_book.opentable_slot_payment(
            {"requiresCreditCard": False, "cancellationFee": "fee applies"}
        )
        unknown_resy = restaurant_book.resy_payment(
            {"payment": {"mystery_charge": 50}},
            {},
        )

        self.assertFalse(unpriced["terms_known"])
        self.assertFalse(unknown_resy["terms_known"])

    def test_resy_structured_free_and_charged_terms_are_parsed_without_invention(self) -> None:
        free = restaurant_book.resy_payment(resy_free_slot(), resy_free_details(), 2)
        cancellation, _no_show = restaurant_book.resy_policy_text(resy_free_details())

        self.assertTrue(free["terms_known"])
        self.assertEqual(free["cancellation_fee_minor"], 0)
        self.assertFalse(free["card_guarantee"])
        self.assertEqual(cancellation, "While you won't be charged, please cancel promptly.")

        charged_slot = resy_free_slot()
        charged_slot["payment"]["is_paid"] = True
        charged_slot["payment"]["cancellation_fee"] = 10.0
        charged_slot["payment"]["secs_cancel_cut_off"] = 86400
        charged_slot["payment"]["time_cancel_cut_off"] = "12:00:00"
        charged_slot["payment"]["secs_change_cut_off"] = 86400
        charged_slot["payment"]["time_change_cut_off"] = "12:00:00"
        charged_details = resy_free_details()
        charged_details["cancellation"]["fee"] = {
            "amount": 10.0,
            "tax": 0.0,
            "display": {"amount": "$10.00"},
            "date_cut_off": "2026-08-07T17:00:00",
        }
        charged = restaurant_book.resy_payment(charged_slot, charged_details, 2)

        self.assertTrue(charged["terms_known"])
        self.assertEqual(charged["cancellation_fee_minor"], 1000)
        self.assertTrue(charged["card_guarantee"])
        self.assertFalse(restaurant_book.fee_allowed(canonical_scope(), charged))

    def test_resy_unknown_add_on_or_non_free_structure_fails_closed(self) -> None:
        slot = resy_free_slot()
        slot["payment"]["is_add_on_required"] = True
        self.assertFalse(
            restaurant_book.resy_payment(slot, resy_free_details(), 2)["terms_known"]
        )

        details = resy_free_details()
        details["payment"]["options"][0]["type"] = "prepaid"
        self.assertFalse(
            restaurant_book.resy_payment(resy_free_slot(), details, 2)["terms_known"]
        )

        wrong_quantity = resy_free_details()
        wrong_quantity["payment"]["amounts"]["quantity"] = 4
        self.assertFalse(
            restaurant_book.resy_payment(resy_free_slot(), wrong_quantity, 2)["terms_known"]
        )

        no_show = resy_free_details()
        no_show["no_show_policy"] = "$25 per-person no-show fee"
        self.assertFalse(
            restaurant_book.resy_payment(resy_free_slot(), no_show, 2)["terms_known"]
        )

        for container in ("amounts", "config"):
            with self.subTest(container=container, key="mystery"):
                unknown_nested = resy_free_details()
                unknown_nested["payment"][container]["mystery"] = 25
                self.assertFalse(
                    restaurant_book.resy_payment(
                        resy_free_slot(), unknown_nested, 2
                    )["terms_known"]
                )

        unknown_option = resy_free_details()
        unknown_option["payment"]["options"][0]["mystery"] = 25
        self.assertFalse(
            restaurant_book.resy_payment(resy_free_slot(), unknown_option, 2)[
                "terms_known"
            ]
        )

    def test_mixed_zero_and_unknown_prose_fails_closed_for_both_providers(self) -> None:
        policies = (
            "No cancellation fee, but a no-show fee may apply",
            "Free cancellation. A no-show charge may apply",
            "No cancellation fee. A service charge applies",
        )
        for policy in policies:
            with self.subTest(provider="resy", policy=policy):
                details = resy_free_details()
                details["cancellation"]["display"]["policy"] = [policy]
                self.assertFalse(
                    restaurant_book.resy_payment(resy_free_slot(), details, 2)[
                        "terms_known"
                    ]
                )
            with self.subTest(provider="opentable", policy=policy):
                self.assertFalse(
                    restaurant_book.opentable_slot_payment(
                        {
                            "requiresCreditCard": False,
                            "cancellationPolicy": policy,
                        }
                    )["terms_known"]
                )

    def test_resy_search_prefilters_remote_hits_and_normalizes_live_location_shape(self) -> None:
        class FakeApi:
            def __init__(self) -> None:
                self.availability_calls: list[str] = []

            @staticmethod
            def search(_query: str):
                return {
                    "search": {
                        "hits": [
                            {
                                "id": {"resy": "remote"},
                                "name": "Remote Farm",
                                "cuisine": ["Farm to Table"],
                                "neighborhood": "New York",
                                "location": {"name": "Manhattan"},
                            },
                            {
                                "id": {"resy": "local"},
                                "name": "Local Farm",
                                "cuisine": ["Farm to Table"],
                                "neighborhood": "Brookline",
                                "locality": "Norfolk County",
                                "region": "Massachusetts",
                                "location": {"name": "Greater Boston"},
                            },
                        ]
                    }
                }

            def find_availability(self, venue_id: str, day: str, _party: int):
                self.availability_calls.append(venue_id)
                return {"results": {"venues": [{"slots": [resy_free_slot(day)]}]}}

            @staticmethod
            def get_details(_token: str, _day: str, _party: int):
                return resy_free_details()

        api = FakeApi()
        provider = restaurant_book.ResyProvider(Path("/unused"))
        provider.api = api
        found, status = provider.search(canonical_scope())

        self.assertEqual(status, "ok")
        self.assertTrue(found)
        self.assertEqual(set(api.availability_calls), {"local"})
        self.assertIn("Brookline", found[0].location)
        self.assertIn("Greater Boston", found[0].location)
        self.assertTrue(
            restaurant_book.metadata_matches(found[0].cuisine, ["Farm-to-Table"])
        )

    def test_resy_reservation_and_search_envelopes_fail_closed(self) -> None:
        class ReservationApi:
            @staticmethod
            def get_reservations():
                return {"success": False, "reservations": []}

        provider = restaurant_book.ResyProvider(Path("/unused"))
        provider.api = ReservationApi()
        with self.assertRaises(restaurant_book.ProviderUnavailable):
            provider.reservations()

        active = {
            "day": "2026-08-07",
            "time_slot": "19:00:00",
            "num_seats": 2,
            "status": {"finished": 0},
            "venue": {"currency": "USD", "id": 101},
        }
        finished = {
            "day": "2026-07-01",
            "time_slot": "18:30:00",
            "num_seats": 4,
            "status": {"finished": 1},
            "venue": {"currency": "USD", "id": 202},
        }
        complete_page = {
            "metadata": {"limit": 100, "offset": 0, "total": 2},
            "reservations": [active, finished],
            "venues": {
                "101": {"id": 101, "name": "Active Farm"},
                "202": {
                    "id": 202,
                    "name": "Finished Bistro",
                },
            },
        }

        class CompleteReservationApi:
            @staticmethod
            def get_reservations():
                return complete_page

        complete_provider = restaurant_book.ResyProvider(Path("/unused"))
        complete_provider.api = CompleteReservationApi()
        normalized = complete_provider.reservations()
        self.assertEqual(
            [(item["restaurant"], item["status"]) for item in normalized],
            [("Active Farm", "confirmed"), ("Finished Bistro", "finished")],
        )
        self.assertEqual([item["venue_id"] for item in normalized], ["101", "202"])

        canonical_string_page = {
            **complete_page,
            "reservations": [
                {**active, "venue": {"currency": "USD", "id": "101"}},
                finished,
            ],
        }
        canonical_string_provider = restaurant_book.ResyProvider(Path("/unused"))
        canonical_string_provider.api = type(
            "CanonicalStringReservationApi",
            (),
            {"get_reservations": staticmethod(lambda: canonical_string_page)},
        )()
        self.assertEqual(
            canonical_string_provider.reservations()[0]["venue_id"],
            "101",
        )

        empty_page = {
            "metadata": {"limit": 100, "offset": 0, "total": 0},
            "reservations": [],
            "venues": {},
        }
        empty_provider = restaurant_book.ResyProvider(Path("/unused"))
        empty_provider.api = type(
            "EmptyReservationApi",
            (),
            {"get_reservations": staticmethod(lambda: empty_page)},
        )()
        self.assertEqual(empty_provider.reservations(), [])

        malformed_pages = []
        for metadata in (
            {"limit": 100, "offset": 1, "total": 2},
            {"limit": 100, "offset": 0, "total": 3},
            {"limit": 1, "offset": 0, "total": 2},
        ):
            malformed_pages.append({**complete_page, "metadata": metadata})
        malformed_pages.append(
            {
                **complete_page,
                "metadata": {"limit": 100, "offset": 0, "total": 101},
                "reservations": [active] * 101,
            }
        )
        malformed_pages.extend(
            (
                {
                    **complete_page,
                    "venues": {
                        "202": complete_page["venues"]["202"]
                    },
                },
                {
                    **complete_page,
                    "venues": {
                        **complete_page["venues"],
                        "303": {
                            "id": 101,
                            "name": "Conflicting Farm",
                        },
                    },
                },
                {
                    **complete_page,
                    "reservations": [
                        {
                            **active,
                            "venue": {
                                "currency": "USD",
                                "id": 101,
                                "name": "Wrong Farm",
                            },
                        },
                        finished,
                    ],
                },
                {**complete_page, "error": {"message": "provider failure"}},
            )
        )
        malformed_pages.extend(
            {
                **complete_page,
                "venues": {
                    **complete_page["venues"],
                    bad_key: {"id": bad_id, "name": "Bad Identity"},
                },
            }
            for bad_key, bad_id in (
                ("0101", 101),
                ("303", "0303"),
                ("true", True),
            )
        )
        malformed_pages.extend(
            {
                **complete_page,
                "reservations": [
                    {
                        **active,
                        "venue": {"currency": "USD", "id": bad_id},
                    },
                    finished,
                ],
            }
            for bad_id in ("0101", "999", True)
        )
        for malformed_page in malformed_pages:
            with self.subTest(malformed_page=malformed_page):
                malformed_provider = restaurant_book.ResyProvider(Path("/unused"))
                malformed_provider.api = type(
                    "MalformedReservationApi",
                    (),
                    {"get_reservations": staticmethod(lambda page=malformed_page: page)},
                )()
                with self.assertRaises(restaurant_book.ProviderUnavailable):
                    malformed_provider.reservations()

        class SearchApi:
            def __init__(self, response: object) -> None:
                self.response = response

            def search(self, _query: str):
                return self.response

        scope = canonical_scope()
        scope["search"]["queries"] = ["Farm"]
        for response in ({}, {"success": False, "search": {"hits": []}}, {"search": {}}):
            with self.subTest(response=response):
                malformed = restaurant_book.ResyProvider(Path("/unused"))
                malformed.api = SearchApi(response)
                found, status = malformed.search(scope)
                self.assertEqual(found, [])
                self.assertEqual(status, "unavailable")

    def test_resy_positive_schema_drift_is_never_clean_empty_availability(self) -> None:
        base_hit = {
            "id": {"resy": "local"},
            "name": "Local Farm",
            "cuisine": ["Farm to Table"],
            "neighborhood": "Brookline",
            "location": {"name": "Brookline"},
            "price_range_id": 3,
        }

        class Api:
            def __init__(self, hit: dict[str, object], availability: object) -> None:
                self.hit = hit
                self.availability = availability

            def search(self, _query: str):
                return {"search": {"hits": [self.hit]}}

            def find_availability(self, _venue: str, _day: str, _party: int):
                return self.availability

        scope = canonical_scope()
        scope["search"]["queries"] = ["Farm"]
        cases = (
            ({**base_hit, "id": {}}, {"results": {"venues": []}}),
            (base_hit, {}),
            (
                base_hit,
                {
                    "results": {
                        "venues": [
                            {
                                "slots": [
                                    {
                                        **resy_free_slot(),
                                        "config": {"token": "", "type": "Dining Room"},
                                    }
                                ]
                            }
                        ]
                    }
                },
            ),
        )
        for hit, availability in cases:
            with self.subTest(hit=hit, availability=availability):
                provider = restaurant_book.ResyProvider(Path("/unused"))
                provider.api = Api(hit, availability)
                found, status = provider.search(scope)
                self.assertEqual(found, [])
                self.assertEqual(status, "unavailable")

    def test_resy_total_call_budget_and_time_prefilter_are_strict(self) -> None:
        class Api:
            def __init__(self, unsafe_details: bool = False) -> None:
                self.calls = 0
                self.details_calls = 0
                self.unsafe_details = unsafe_details

            def search(self, _query: str):
                self.calls += 1
                return {
                    "search": {
                        "hits": [
                            {
                                "id": {"resy": "local"},
                                "name": "Local Farm",
                                "cuisine": ["Farm to Table"],
                                "neighborhood": "Brookline",
                                "location": {"name": "Brookline"},
                                "price_range_id": 3,
                            }
                        ]
                    }
                }

            def find_availability(self, _venue: str, day: str, _party: int):
                self.calls += 1
                outside = [resy_free_slot(day, "16:00") for _ in range(20)]
                return {"results": {"venues": [{"slots": [*outside, resy_free_slot(day)]}]}}

            def get_details(self, _token: str, _day: str, _party: int):
                self.calls += 1
                self.details_calls += 1
                details = resy_free_details()
                if self.unsafe_details:
                    details["no_show_policy"] = "$25 per person"
                return details

        complete_scope = canonical_scope()
        complete_scope["search"]["queries"] = ["Farm"]
        complete_scope["search"]["max_search_attempts_per_provider"] = 1
        complete_scope["search"]["max_candidates_per_provider"] = 3
        complete_api = Api()
        complete = restaurant_book.ResyProvider(Path("/unused"))
        complete.api = complete_api
        found, status = complete.search(complete_scope)
        self.assertEqual(status, "ok")
        self.assertEqual(len(found), 3)
        self.assertEqual(complete_api.details_calls, 3)
        self.assertLessEqual(
            complete_api.calls, restaurant_book.resy_provider_call_budget(complete_scope)
        )

        exhausted_scope = canonical_scope()
        exhausted_scope["search"]["queries"] = ["Farm"]
        exhausted_scope["search"]["max_search_attempts_per_provider"] = 1
        exhausted_scope["search"]["max_candidates_per_provider"] = 1
        exhausted_api = Api(unsafe_details=True)
        exhausted = restaurant_book.ResyProvider(Path("/unused"))
        exhausted.api = exhausted_api
        found, status = exhausted.search(exhausted_scope)
        self.assertEqual(found, [])
        self.assertEqual(status, "unavailable")
        self.assertEqual(
            exhausted_api.calls, restaurant_book.resy_provider_call_budget(exhausted_scope)
        )

    def test_opentable_slot_terms_reject_holds_unknown_fees_and_prepayment(self) -> None:
        omitted_false_flag = restaurant_book.opentable_slot_payment(
            {"type": "Standard", "priceAmount": None}
        )
        free = restaurant_book.opentable_slot_payment(
            {"requiresCreditCard": False, "creditCardPolicyType": "NONE"}
        )
        hold = restaurant_book.opentable_slot_payment(
            {"requiresCreditCard": True, "creditCardPolicyType": "HOLD"}
        )
        fee = restaurant_book.opentable_slot_payment(
            {"requiresCreditCard": False, "cancellationFee": "$25 per person"}
        )
        prepaid = restaurant_book.opentable_slot_payment(
            {"requiresCreditCard": False, "isPrepaid": True}
        )

        self.assertTrue(omitted_false_flag["terms_known"])
        self.assertFalse(omitted_false_flag["card_guarantee"])
        self.assertTrue(free["terms_known"])
        self.assertFalse(free["card_guarantee"])
        self.assertFalse(hold["terms_known"])
        self.assertTrue(hold["card_guarantee"])
        self.assertFalse(fee["terms_known"])
        self.assertFalse(prepaid["terms_known"])
        self.assertFalse(
            restaurant_book.opentable_slot_payment(
                {"requiresCreditCard": False, "payment": {"mystery": 25}}
            )["terms_known"]
        )
        self.assertFalse(
            restaurant_book.opentable_slot_payment(
                {"requiresCreditCard": False, "costPerGuest": 25}
            )["terms_known"]
        )
        for key in ("surcharge", "newMonetaryTerm"):
            with self.subTest(key=key):
                self.assertFalse(
                    restaurant_book.opentable_slot_payment(
                        {"type": "Standard", key: 25}
                    )["terms_known"]
                )
        self.assertFalse(
            restaurant_book.opentable_slot_payment(
                {"type": "Standard", "newPaymentTerm": "none"}
            )["terms_known"]
        )
        self.assertFalse(
            restaurant_book.opentable_slot_payment(
                {
                    "requiresCreditCard": False,
                    "bookingTerms": {"mystery": "none"},
                }
            )["terms_known"]
        )
        self.assertTrue(
            restaurant_book.opentable_slot_payment(
                {
                    "requiresCreditCard": False,
                    "bookingTerms": {
                        "currency": "USD",
                        "dateCutoff": "2026-08-07T17:00:00",
                    },
                    "payment": {"amount": 0, "currency": "USD"},
                }
            )["terms_known"]
        )
        for extra in (
            {"cancellationPolicy": "$25 per-person no-show fee"},
            {"penalty": "Cancellation penalty applies"},
            {"bookingTerms": {"charge": "$10"}},
            {"noShowPolicy": "A per-person charge applies"},
        ):
            with self.subTest(extra=extra):
                payload = {"requiresCreditCard": False, **extra}
                self.assertFalse(
                    restaurant_book.opentable_slot_payment(payload)["terms_known"]
                )

    def test_opentable_browser_discovery_validates_exact_https_search_context(self) -> None:
        class BrowserProvider(restaurant_book.OpenTableProvider):
            def __init__(self, home: Path) -> None:
                super().__init__(home, sleep=lambda _seconds: None)
                self.commands: list[tuple[str, ...]] = []
                self.search_url = ""
                self.context_term_override: str | None = None
                self.restaurant_override: dict[str, object] | None = None

            def _browser_command(self, *arguments: str, timeout_seconds: int = 60) -> str:
                del timeout_seconds
                self.commands.append(arguments)
                if arguments[0] == "open":
                    self.search_url = arguments[2]
                    return "tab-1234"
                if arguments[0] == "close":
                    return ""
                if arguments[0] != "eval":
                    raise AssertionError(arguments)
                parsed = urllib.parse.urlparse(self.search_url)
                params = urllib.parse.parse_qs(parsed.query)
                payload = {
                    "origin": f"{parsed.scheme}://{parsed.netloc}",
                    "path": parsed.path,
                    "covers": params["covers"][0],
                    "dateTime": params["dateTime"][0],
                    "metroId": params["metroId"][0],
                    "term": self.context_term_override or params["term"][0],
                    "status": "ready",
                    "restaurants": [
                        self.restaurant_override
                        or {
                            "restaurant_id": "123",
                            "name": "Brookline Farm",
                            "cuisine": "Farm to Table",
                            "neighborhood": "Brookline Village",
                            "line1": "1 Main Street",
                            "city": "Brookline",
                            "state": "MA",
                            "dining_style": "Casual Dining",
                            "price_tier": 3,
                        }
                    ],
                }
                return json.dumps({"result": json.dumps(payload)})

        provider = BrowserProvider(self.home)
        scope = canonical_scope()
        discovered = provider._discover_query(
            "instance-1", scope, "Farm-to-Table Brookline", "2026-08-07"
        )

        self.assertEqual(discovered[0]["venue_id"], "123")
        self.assertIn("Brookline", discovered[0]["location"])
        parsed = urllib.parse.urlparse(provider.search_url)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.opentable.com")
        self.assertEqual(params["covers"], ["2"])
        self.assertEqual(params["dateTime"], ["2026-08-07T19:00:00"])
        self.assertEqual(params["term"], ["Farm-to-Table Brookline"])
        self.assertEqual([command[0] for command in provider.commands], ["open", "eval", "close"])

        provider.context_term_override = "different query"
        with self.assertRaises(restaurant_book.ProviderUnavailable):
            provider._discover_query(
                "instance-1", scope, "Farm-to-Table Brookline", "2026-08-07"
            )

        provider.context_term_override = None
        provider.restaurant_override = {
            "restaurant_id": "456",
            "name": "Boston Farm",
            "cuisine": "Farm to Table",
            "neighborhood": "",
            "line1": "500 Brookline Ave",
            "city": "Boston",
            "state": "MA",
            "dining_style": "Casual Dining",
            "price_tier": 3,
        }
        street_only = provider._discover_query(
            "instance-1", scope, "Farm-to-Table Brookline", "2026-08-07"
        )[0]
        self.assertEqual(street_only["location"], "Boston, MA")
        self.assertFalse(
            restaurant_book.venue_metadata_allowed(
                scope,
                restaurant=street_only["restaurant"],
                cuisine=street_only["cuisine"],
                location=street_only["location"],
                price_tier=street_only["price_tier"],
            )
        )

        provider.restaurant_override = {
            **provider.restaurant_override,
            "restaurant_id": "",
        }
        with self.assertRaises(restaurant_book.ProviderUnavailable):
            provider._discover_query(
                "instance-1", scope, "Farm-to-Table Brookline", "2026-08-07"
            )

    def test_opentable_search_filters_metadata_before_api_and_keeps_slot_secrets_private(self) -> None:
        class FakeApi:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []
                self.changed = False

            def find_availability(self, venue_id: str, day: str, _time: str, _party: int):
                self.calls.append((venue_id, day))
                time_value = "19:15" if self.changed else "19:00"
                return opentable_availability(day, time_value)

        class DiscoveryProvider(restaurant_book.OpenTableProvider):
            def __init__(self, home: Path, api: object) -> None:
                super().__init__(home, sleep=lambda _seconds: None)
                self.api = api
                self.commands: list[tuple[str, ...]] = []

            def _browser_command(self, *arguments: str, timeout_seconds: int = 60) -> str:
                del timeout_seconds
                self.commands.append(arguments)
                if arguments[0] == "acquire":
                    return "instance-1\t0"
                if arguments[0] == "release":
                    return ""
                raise AssertionError(arguments)

            def _discover_query(
                self,
                _instance_id: str,
                _scope: dict[str, object],
                _query: str,
                _requested_date: str,
            ) -> list[dict[str, str]]:
                return [
                    {
                        "venue_id": "remote",
                        "restaurant": "Remote Farm",
                        "cuisine": "Farm to Table",
                        "location": "New York, NY",
                        "dining_style": "Casual",
                        "price_tier": 2,
                    },
                    {
                        "venue_id": "local",
                        "restaurant": "Local Farm",
                        "cuisine": "Farm to Table",
                        "location": "Brookline, MA",
                        "dining_style": "Casual",
                        "price_tier": 3,
                    },
                ]

        api = FakeApi()
        provider = DiscoveryProvider(self.home, api)
        found, status = provider.search(canonical_scope())

        self.assertEqual(status, "ok")
        self.assertTrue(found)
        self.assertEqual({venue_id for venue_id, _day in api.calls}, {"local"})
        self.assertEqual([command[0] for command in provider.commands], ["acquire", "release"])
        encoded = json.dumps([item.safe() for item in found])
        self.assertNotIn("OT_PRIVATE_TOKEN", encoded)
        self.assertNotIn("OT_PRIVATE_HASH", encoded)

        api.changed = True
        with self.assertRaises(restaurant_book.ProviderUnavailable):
            provider.refresh(canonical_scope(), found[0])

    def test_price_tier_scope_and_opentable_positive_slot_drift_fail_closed(self) -> None:
        scope = canonical_scope()
        scope["search"]["minimum_price_tier"] = 3
        self.assertFalse(
            restaurant_book.venue_metadata_allowed(
                scope,
                restaurant="Local Farm",
                cuisine="Farm to Table",
                location="Brookline, MA",
                price_tier=2,
            )
        )
        self.assertTrue(
            restaurant_book.venue_metadata_allowed(
                scope,
                restaurant="Local Farm",
                cuisine="Farm to Table",
                location="Brookline, MA",
                price_tier=3,
            )
        )
        provider = restaurant_book.OpenTableProvider(self.home)
        malformed = opentable_availability()
        malformed["suggestedAvailability"][0]["timeslots"][0]["token"] = ""
        metadata = {
            "venue_id": "123",
            "restaurant": "Local Farm",
            "cuisine": "Farm to Table",
            "location": "Brookline, MA",
            "dining_style": "Casual",
            "price_tier": 3,
        }
        with self.assertRaises(restaurant_book.ProviderUnavailable):
            provider._availability_candidates(
                scope,
                metadata,
                "2026-08-07",
                malformed,
                1,
                closest_only=True,
            )

    def test_opentable_provider_book_wires_live_guard_before_durable_boundary(self) -> None:
        events: list[str] = []

        class FakeApi:
            def book(self, *_args: object):
                events.append("api-enter")
                self._pre_booking_guard()
                self._pre_mutation_check()
                events.append("api-send")
                return {
                    "success": True,
                    "reservation": {"reservationId": "r-1", "status": "confirmed"},
                }

        provider = restaurant_book.OpenTableProvider(self.home)
        provider.api = FakeApi()
        selected = candidate("opentable")
        selected.private = {
            "slot_token": "PRIVATE_TOKEN",
            "slot_hash": "PRIVATE_HASH",
            "slot_datetime": "2026-08-07T19:00:00",
            "dining_area_id": "area-1",
        }

        result = provider.book(
            selected,
            lambda: events.append("live-guard"),
            lambda: events.append("durable-boundary"),
        )

        self.assertEqual(events, ["api-enter", "live-guard", "durable-boundary", "api-send"])
        self.assertEqual(restaurant_book.strict_confirmation_id(result), "r-1")
        self.assertFalse(hasattr(provider.api, "_pre_booking_guard"))

    def test_run_before_authorization_does_not_touch_providers_or_state(self) -> None:
        coordinator = restaurant_book.Coordinator(
            home=self.home,
            scopes_path=self.scopes,
            resy=self.resy,
            opentable=self.opentable,
            now=lambda: datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        result = coordinator.run(JOB_ID)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "authorization_not_active")
        self.assertEqual(self.resy.reads, 0)
        self.assertEqual(self.opentable.reads, 0)
        self.assertFalse(coordinator.shared_state_root.exists())

    def test_resy_run_writes_attempt_before_one_mutation_then_receipt(self) -> None:
        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["booking"]["platform"], "resy")
        self.assertEqual(self.resy.mutations, 1)
        self.assertEqual(self.opentable.confirms, 0)
        state = self.coordinator.state_dir(JOB_ID)
        self.assertFalse((state / "booking-attempt.json").exists())
        receipt = json.loads((state / "confirmed.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["platform"], "resy")
        self.assertEqual(stat.S_IMODE((state / "confirmed.json").stat().st_mode), 0o600)
        run_state = json.loads((state / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_state["phase"], "selected")
        self.assertEqual(stat.S_IMODE((state / "run.json").stat().st_mode), 0o600)
        self.assertLess(self.resy.events.index("resy-final-read"), self.resy.events.index("resy-mutation"))
        self.assertIn("opentable-final-read", self.opentable.events)

    def test_ambiguous_resy_mutation_keeps_marker_and_never_falls_back(self) -> None:
        self.resy.fail_after_boundary = True
        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["mutation_attempted"])
        self.assertTrue(result["reservation_may_exist"])
        self.assertEqual(self.resy.mutations, 1)

    def test_unexpected_base_exception_after_resy_boundary_is_reported_unknown(self) -> None:
        self.resy.base_failure_after_boundary = True

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["mutation_attempted"])
        self.assertTrue(result["reservation_may_exist"])
        self.assertTrue((self.coordinator.state_dir(JOB_ID) / "booking-attempt.json").exists())
        self.assertEqual(self.opentable.confirms, 0)
        marker = self.coordinator.state_dir(JOB_ID) / "booking-attempt.json"
        self.assertTrue(marker.is_file())
        again = self.coordinator.run(JOB_ID)
        self.assertEqual(again["status"], "manual_review_required")
        self.assertEqual(self.resy.mutations, 1)

    def test_pending_resy_readback_never_becomes_a_confirmed_receipt(self) -> None:
        self.resy.confirmation_status = "pending"

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "manual_review_required")
        self.assertEqual(result["reason"], "exact_reservation_readback_failed")
        state = self.coordinator.state_dir(JOB_ID)
        self.assertTrue((state / "booking-attempt.json").exists())
        self.assertFalse((state / "confirmed.json").exists())
        self.assertEqual(self.opentable.confirms, 0)
        again = self.coordinator.run(JOB_ID)
        self.assertEqual(again["status"], "manual_review_required")
        self.assertEqual(self.resy.mutations, 1)

    def test_final_guard_conflict_removes_provisional_marker_without_mutation(self) -> None:
        def create_conflict() -> None:
            self.opentable.current = [reservation("opentable")]

        self.resy.before_boundary = create_conflict
        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "already_reserved")
        self.assertEqual(self.resy.mutations, 0)
        self.assertFalse((self.coordinator.state_dir(JOB_ID) / "booking-attempt.json").exists())

    def test_open_table_stale_exact_availability_fails_before_mutation(self) -> None:
        self.resy.candidates = []
        self.opentable.candidates = [candidate("opentable")]
        self.opentable.stale = True

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "guard_unavailable")
        self.assertEqual(result["reason"], "opentable_exact_availability_changed")
        self.assertEqual(self.opentable.confirms, 0)
        self.assertFalse((self.coordinator.state_dir(JOB_ID) / "booking-attempt.json").exists())

    def test_open_table_api_guard_and_boundary_precede_confirmed_receipt(self) -> None:
        self.resy.candidates = []
        self.opentable.candidates = [candidate("opentable")]

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["booking"]["platform"], "opentable")
        self.assertEqual(self.opentable.confirms, 1)
        state = self.coordinator.state_dir(JOB_ID)
        self.assertFalse((state / "booking-attempt.json").exists())
        receipt = json.loads((state / "confirmed.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["confirmation_id"], "ot-confirmation")
        context = json.loads((state / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(context["phase"], "selected")
        self.assertLess(
            self.opentable.events.index("opentable-final-read"),
            self.opentable.events.index("opentable-mutation"),
        )

    def test_open_table_cross_provider_final_guard_blocks_before_boundary(self) -> None:
        self.resy.candidates = []
        self.opentable.candidates = [candidate("opentable")]

        def create_conflict() -> None:
            self.resy.current = [reservation("resy")]

        self.opentable.before_boundary = create_conflict
        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "already_reserved")
        self.assertEqual(self.opentable.confirms, 1)
        self.assertNotIn("opentable-mutation", self.opentable.events)
        self.assertFalse((self.coordinator.state_dir(JOB_ID) / "booking-attempt.json").exists())

    def test_open_table_confirmation_rejects_nested_contradiction(self) -> None:
        selected = candidate("opentable")
        payload = {
            "success": True,
            "status": "confirmed",
            "confirmation_id": "confirmation",
            "restaurant": selected.restaurant,
            "venue_id": selected.venue_id,
            "confirmed_date": selected.date,
            "confirmed_time": selected.time,
            "party_size": selected.party_size,
            "result": {"error": {"message": "failed"}},
        }

        matches, _ = self.coordinator._opentable_confirmation_matches(payload, selected)

        self.assertFalse(matches)

    def test_open_table_confirmation_rejects_candidate_fact_contradictions(self) -> None:
        selected = candidate("opentable")
        base = {
            "success": True,
            "reservation": {
                "reservationId": "r-1",
                "status": "confirmed",
                "rid": selected.venue_id,
                "date": selected.date,
                "time": selected.time,
                "partySize": selected.party_size,
            },
        }
        matches, _ = self.coordinator._opentable_confirmation_matches(base, selected)
        self.assertTrue(matches)
        for key, value in (
            ("rid", "different"),
            ("date", "2026-08-08"),
            ("time", "20:00"),
            ("partySize", 4),
        ):
            payload = json.loads(json.dumps(base))
            payload["reservation"][key] = value
            with self.subTest(key=key):
                matches, _ = self.coordinator._opentable_confirmation_matches(
                    payload, selected
                )
                self.assertFalse(matches)

    def test_open_table_requires_exact_post_book_readback_before_receipt(self) -> None:
        for mode in ("unavailable", "mismatch"):
            with self.subTest(mode=mode):
                self.setUp()
                self.resy.candidates = []
                self.opentable.candidates = [candidate("opentable")]
                self.opentable.fail_post_readback = mode == "unavailable"
                self.opentable.mismatch_post_readback = mode == "mismatch"

                result = self.coordinator.run(JOB_ID)

                self.assertEqual(result["status"], "manual_review_required")
                self.assertEqual(
                    result["reason"], "opentable_exact_reservation_readback_failed"
                )
                state = self.coordinator.state_dir(JOB_ID)
                self.assertTrue((state / "booking-attempt.json").exists())
                self.assertFalse((state / "confirmed.json").exists())

    def test_unexpected_base_exception_after_opentable_boundary_is_reported_unknown(self) -> None:
        self.resy.candidates = []
        self.opentable.candidates = [candidate("opentable")]
        self.opentable.base_failure_after_boundary = True

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["mutation_attempted"])
        self.assertTrue(result["reservation_may_exist"])
        self.assertTrue((self.coordinator.state_dir(JOB_ID) / "booking-attempt.json").exists())

    def test_open_table_confirmation_requires_correlated_affirmation_and_one_id(self) -> None:
        cases = (
            ({"reservation": {"reservationId": "r-1", "status": "confirmed"}}, "r-1"),
            ({"success": True, "reservation": {"reservationId": "r-1"}}, "r-1"),
            ({"confirmationId": "r-1", "status": "confirmed"}, "r-1"),
            ({"reservation": {"reservationId": "r-1"}}, None),
            ({"success": True, "status": "confirmed"}, None),
            (
                {
                    "reservation": {"status": "confirmed"},
                    "payment": {"confirmationCode": "payment-1"},
                },
                None,
            ),
            (
                {
                    "success": True,
                    "reservation": {"reservationId": "r-1"},
                    "result": {"error": {"message": "failed"}},
                },
                None,
            ),
            (
                {
                    "success": True,
                    "reservation": {
                        "reservationId": "r-1",
                        "confirmationNumber": "different",
                    },
                },
                None,
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(restaurant_book.strict_confirmation_id(payload), expected)

    def test_ambiguous_open_table_confirmation_keeps_marker_without_resy_fallback(self) -> None:
        self.resy.candidates = []
        self.opentable.candidates = [candidate("opentable")]
        self.opentable.ambiguous = True

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.opentable.confirms, 1)
        self.assertEqual(self.resy.mutations, 0)
        self.assertTrue((self.coordinator.state_dir(JOB_ID) / "booking-attempt.json").exists())

    def test_unresolved_snipe_marker_blocks_coordinator(self) -> None:
        restaurant_book.secure_directory(self.coordinator.shared_state_root)
        other = self.coordinator.shared_state_root / "other-snipe"
        other.mkdir(mode=0o700)
        marker = other / "booking-attempt.json"
        marker.write_text(
            json.dumps({"date": "2026-08-14", "time": "19:00", "party_size": 2}),
            encoding="utf-8",
        )
        marker.chmod(0o600)

        result = self.coordinator.run(JOB_ID)

        self.assertEqual(result["status"], "manual_review_required")
        self.assertEqual(self.resy.mutations, 0)
        self.assertEqual(self.opentable.confirms, 0)

    def test_public_cli_has_no_arbitrary_scope_file_or_request_json(self) -> None:
        for argument in ("--request-json", "--scope-file"):
            with self.subTest(argument=argument):
                result = subprocess.run(
                    [str(COORDINATOR_PATH), "plan", "--job-id", JOB_ID, argument, "/tmp/unsafe"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
