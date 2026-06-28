#!/usr/bin/env python3

import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "openclaw" / "bin" / "nest-dashboard.py"
SPEC = importlib.util.spec_from_file_location("nest_dashboard", MODULE_PATH)
nest_dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nest_dashboard)


class PresenceNormalizationTests(unittest.TestCase):
    def test_only_vacancy_people_reach_the_dashboard(self):
        state = {
            "timestamp": "2026-06-28T01:35:09.580Z",
            "people": {
                "Dylan": {
                    "cabin": False,
                    "crosstown": True,
                    "location": "crosstown",
                },
                "Julia": {
                    "cabin": False,
                    "crosstown": True,
                    "location": "crosstown",
                },
                "Potato": {
                    "cabin": True,
                    "crosstown": False,
                    "location": "cabin",
                    "source": "fi-collar",
                },
                "Guest": {
                    "cabin": True,
                    "crosstown": False,
                    "location": "cabin",
                },
            },
            "cabin": {
                "occupancy": "confirmed_vacant",
                "stateChangedAt": "2026-06-28T00:26:48.231Z",
                "scanAge": "1min",
                "fresh": True,
            },
            "crosstown": {
                "occupancy": "occupied",
                "stateChangedAt": "2026-06-28T00:26:30.734Z",
                "scanAge": "0min",
                "fresh": True,
            },
        }

        original = copy.deepcopy(state)
        normalized = nest_dashboard.normalize_presence_state(state)

        self.assertEqual(state, original)
        self.assertIsNot(normalized, state)
        self.assertIsNot(normalized["people"], state["people"])
        self.assertEqual(set(normalized["people"]), {"Dylan", "Julia"})
        self.assertEqual(normalized["people"]["Dylan"], state["people"]["Dylan"])
        self.assertEqual(normalized["people"]["Julia"], state["people"]["Julia"])
        self.assertEqual(
            normalized["cabin"]["occupancy"],
            state["cabin"]["occupancy"],
        )
        self.assertEqual(
            normalized["crosstown"]["occupancy"],
            state["crosstown"]["occupancy"],
        )

    def test_malformed_people_are_ignored_without_losing_location_state(self):
        state = {
            "people": {"Dylan": "crosstown", "Julia": None, "Potato": {}},
            "cabin": {"occupancy": "possibly_vacant"},
            "crosstown": {"occupancy": "unknown"},
        }

        normalized = nest_dashboard.normalize_presence_state(state)

        self.assertEqual(normalized["people"], {})
        self.assertEqual(normalized["cabin"], state["cabin"])
        self.assertEqual(normalized["crosstown"], state["crosstown"])

    def test_non_object_state_is_rejected(self):
        for state in (None, [], "occupied"):
            with self.subTest(state=state):
                self.assertIsNone(nest_dashboard.normalize_presence_state(state))


class PresenceHistoryTests(unittest.TestCase):
    def test_malformed_json_values_and_timestamps_are_skipped(self):
        now = datetime.now(timezone.utc)
        valid = {
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "cabin": {"occupancy": "confirmed_vacant", "people": []},
            "crosstown": {
                "occupancy": "occupied",
                "people": ["Dylan", "Julia"],
            },
        }
        records = [
            None,
            7,
            "occupied",
            [],
            {},
            {"timestamp": None},
            {"timestamp": 1234},
            {"timestamp": "not-a-timestamp"},
            {"timestamp": now.replace(tzinfo=None).isoformat()},
            valid,
        ]

        original_dir = nest_dashboard.PRESENCE_HISTORY_DIR
        try:
            with tempfile.TemporaryDirectory() as tempdir:
                nest_dashboard.PRESENCE_HISTORY_DIR = tempdir
                history_path = Path(tempdir) / f"{now:%Y-%m-%d}.jsonl"
                history_path.write_text(
                    "\n".join(json.dumps(record) for record in records) + "\n",
                    encoding="utf-8",
                )

                loaded = nest_dashboard.load_presence_history(24)
        finally:
            nest_dashboard.PRESENCE_HISTORY_DIR = original_dir

        self.assertEqual(loaded, [valid])


class PresenceHtmlContractTests(unittest.TestCase):
    def test_uses_canonical_vacancy_states_without_partial_state(self):
        html = nest_dashboard.DASHBOARD_HTML

        self.assertNotIn("isPartial", html)
        self.assertNotIn("presence-badge.partial", html)
        self.assertNotIn("> Partial<", html)
        for occupancy in ("occupied", "confirmed_vacant", "possibly_vacant"):
            with self.subTest(occupancy=occupancy):
                self.assertIn(occupancy, html)

    def test_presence_rendering_fails_closed_on_untrusted_values(self):
        html = nest_dashboard.DASHBOARD_HTML

        self.assertIn("switch (occupancy)", html)
        self.assertIn("default: return UNKNOWN_VACANCY_STATE_VIEW;", html)
        self.assertNotIn("VACANCY_STATE_VIEW[occupancy]", html)
        self.assertIn(
            "presenceState.people?.[name]?.[struct.loc] === true",
            html,
        )

        overlay = html.split("const presenceOverlayPlugin", 1)[1].split(
            "const chartDefaults", 1
        )[0]
        self.assertIn("p && typeof p === 'object'", overlay)
        self.assertIn("typeof p.timestamp === 'string'", overlay)
        self.assertIn("Number.isFinite(Date.parse(p.timestamp))", overlay)
        self.assertIn("p[loc] && typeof p[loc] === 'object'", overlay)
        self.assertLess(overlay.index(".filter(p =>"), overlay.index(".sort("))


if __name__ == "__main__":
    unittest.main()
