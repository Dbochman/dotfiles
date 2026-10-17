"""Regression tests for Cabin Shared Album photo reconciliation and tagging."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


catalog = load_script("cabin_photo_catalog", "cabin-photo-catalog.py")


def fingerprint(values):
    if len(values) != catalog.FINGERPRINT_SIDE * catalog.FINGERPRINT_SIDE:
        raise AssertionError("fixture has wrong size")
    return base64.b64encode(bytes(values)).decode("ascii")


class ReconciliationTests(unittest.TestCase):
    def test_exact_decisive_mutual_match_inherits_curated_record(self):
        image = [(index * 17) % 256 for index in range(256)]
        other = [255 - value for value in image]
        result = catalog.reconcile_fingerprints(
            {"asset-a": fingerprint(image)},
            {"legacy-a": fingerprint(image), "legacy-b": fingerprint(other)},
        )
        self.assertEqual(result["asset-a"]["classification"], "curated_match")
        self.assertEqual(result["asset-a"]["legacyId"], "legacy-a")
        self.assertTrue(result["asset-a"]["mutual"])
        self.assertTrue(result["asset-a"]["decisive"])

    def test_ambiguous_pixel_identical_records_are_one_duplicate_cluster(self):
        image = [(index * 13) % 256 for index in range(256)]
        result = catalog.reconcile_fingerprints(
            {"asset-a": fingerprint(image)},
            {"legacy-a": fingerprint(image), "legacy-b": fingerprint(image)},
        )
        self.assertEqual(result["asset-a"]["classification"], "duplicate_cluster")
        self.assertFalse(result["asset-a"]["decisive"])

    def test_visually_different_asset_stays_unmatched(self):
        left = [index % 256 for index in range(256)]
        right = list(reversed(left))
        result = catalog.reconcile_fingerprints(
            {"asset-a": fingerprint(left)},
            {"legacy-a": fingerprint(right)},
        )
        self.assertEqual(result["asset-a"]["classification"], "unmatched")

    def test_manual_editorial_skip_survives_reconciliation(self):
        image = [(index * 7) % 256 for index in range(256)]
        encoded = fingerprint(image)
        config = catalog.Config(
            inbox_dir=Path('/tmp/inbox'),
            legacy_manifest=Path('/tmp/manifest.json'),
            legacy_photo_dir=Path('/tmp/legacy'),
            catalog_path=Path('/tmp/catalog.json'),
            preview_dir=Path('/tmp/previews'),
            producer_catalog_path=Path('/tmp/producer.json'),
            status_path=Path('/tmp/status.json'),
            model='codex/test-model',
            tag_after='2026-09-25',
            tag_batch_limit=30,
        )
        state = {
            'assets': {
                'icloud-111111111111111111111111': {
                    'mediaKind': 'image',
                    'fingerprint': encoded,
                    'manualDecision': 'skip',
                },
            },
            'legacyFingerprints': {'fixture.jpeg': {'fingerprint': encoded}},
        }
        counts = catalog.apply_reconciliation(
            config,
            state,
            {catalog.legacy_id('fixture.jpeg'): encoded},
            retag_low_confidence=False,
        )
        self.assertEqual(state['assets']['icloud-111111111111111111111111']['status'], 'skipped_editorial')
        self.assertEqual(counts, {'skipped_editorial': 1})


class TagValidationTests(unittest.TestCase):
    def setUp(self):
        self.vocabulary = ["cabin-exterior", "dogs", "pets", "people", "iphone", "cover-candidate"]

    def valid_value(self):
        return {
            "caption": "A dog standing beside the cabin porch.",
            "tags": ["cabin-exterior", "dogs"],
            "people_identifiable": False,
            "pets_identifiable": True,
            "cover_candidate": False,
            "confidence": "high",
            "uncertainty": "",
        }

    def test_model_value_is_bounded_and_normalized(self):
        value = catalog.validate_model_value(self.valid_value(), self.vocabulary)
        self.assertEqual(value["confidence"], "high")
        self.assertTrue(value["petsIdentifiable"])
        tags = catalog.normalize_tags(value, {"camera": "Apple iPhone 18 Pro"})
        self.assertEqual(tags, ["cabin-exterior", "dogs", "iphone", "pets", "pets-identifiable"])

    def test_model_cover_signal_does_not_create_a_curated_cover_tag(self):
        value = self.valid_value()
        value["cover_candidate"] = True
        normalized = catalog.validate_model_value(value, self.vocabulary)
        self.assertNotIn("cover-candidate", catalog.normalize_tags(normalized, {"camera": "unknown"}))

    def test_model_cannot_supply_derived_tags(self):
        value = self.valid_value()
        value["tags"].append("cover-candidate")
        with self.assertRaisesRegex(catalog.CatalogError, "model_tags_invalid"):
            catalog.validate_model_value(value, self.vocabulary)

    def test_model_rejects_extra_fields(self):
        value = self.valid_value()
        value["person_name"] = "untrusted"
        with self.assertRaisesRegex(catalog.CatalogError, "model_result_fields_invalid"):
            catalog.validate_model_value(value, self.vocabulary)

    def test_envelope_binds_the_returned_image_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            expected = Path(temporary) / "preview.jpg"
            expected.write_bytes(b"fixture")
            envelope = {
                "ok": True,
                "capability": "image.describe",
                "outputs": [
                    {
                        "kind": "image.description",
                        "path": str(expected),
                        "model": "fixture-model",
                        "text": json.dumps(self.valid_value()),
                    }
                ],
            }
            value, model = catalog.parse_model_envelope(
                json.dumps(envelope).encode("utf-8"), expected, self.vocabulary
            )
            self.assertEqual(model, "fixture-model")
            self.assertEqual(value["caption"], self.valid_value()["caption"])


class ConfigTests(unittest.TestCase):
    def test_config_must_be_owner_only_and_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.json"
            payload = {
                "inboxDir": str(root / "inbox"),
                "legacyManifest": str(root / "manifest.json"),
                "legacyPhotoDir": str(root / "legacy"),
                "catalogPath": str(root / "catalog.json"),
                "previewDir": str(root / "previews"),
                "producerCatalogPath": str(root / "producer.json"),
                "statusPath": str(root / "status.json"),
                "model": "codex/test-model",
                "tagAfter": "2026-09-25",
                "tagBatchLimit": 30,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            parsed = catalog.load_config(path)
            self.assertEqual(parsed.tag_batch_limit, 30)
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(catalog.CatalogError, "config_permissions_invalid"):
                catalog.load_config(path)


if __name__ == "__main__":
    unittest.main()
