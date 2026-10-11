#!/usr/bin/env python3
"""Exact multi-network Crosstown identities and read-only observation."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCANNER = Path(__file__).resolve().parents[1] / "workspace/scripts/presence-detect.sh"
FIRST = "02:00:00:00:00:11"
SECOND = "02:00:00:00:00:33"
JULIA = "02:00:00:00:00:22"


class CrosstownBindingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.home = self.root / "home"
        self.home.mkdir()
        self.config = self.root / "bindings.json"
        self.arp = self.root / "arp"
        self.ping = self.root / "ping"
        self.taildrop = self.root / "tailscale"
        self.rows = self.root / "rows"
        self.arp.write_text('#!/bin/sh\nexec /bin/cat "$FAKE_ARP_ROWS"\n')
        self.ping.write_text("#!/bin/sh\nexit 1\n")
        self.taildrop.write_text('#!/bin/sh\n/bin/touch "$HOME/unexpected-taildrop"\nexit 1\n')
        for program in (self.arp, self.ping, self.taildrop):
            program.chmod(0o700)
        self.environment = {
            **os.environ, "HOME": str(self.home),
            "PRESENCE_DEVICE_CONFIG": str(self.config),
            "PRESENCE_ARP_BIN": str(self.arp), "PRESENCE_PING_BIN": str(self.ping),
            "PRESENCE_TAILSCALE_BIN": str(self.taildrop),
            "PRESENCE_CROSSTOWN_SWEEP_HOSTS": "", "FAKE_ARP_ROWS": str(self.rows),
        }
        self.write_config(self.value())

    def value(self):
        return {"schema_version": 2, "site": "crosstown", "people": {
            "Dylan": {"kind": "mac", "values": [FIRST, SECOND]},
            "Julia": {"kind": "mac", "values": [JULIA]},
        }}

    def write_config(self, value):
        self.config.write_text(json.dumps(value))
        self.config.chmod(0o600)

    def run_scanner(self, mode):
        result = subprocess.run(["/bin/bash", str(SCANNER), mode, "crosstown"],
            env=self.environment, capture_output=True, text=True, timeout=15)
        self.assertFalse((self.home / ".openclaw").exists())
        self.assertFalse((self.home / "unexpected-taildrop").exists())
        for private in (FIRST, SECOND, JULIA):
            self.assertNotIn(private, result.stdout + result.stderr)
        return result

    def observe(self, devices):
        header = "Neighbor Linklayer Address Expire(O) Expire(I) Netif Refs Prbs\n"
        gateway = "192.168.165.1 aa:bb:cc:dd:ee:01 1m20s 40s en0 1\n"
        rows = [f"192.168.165.{100 + index} {address} 1m20s {inbound} {interface} 1\n"
                for index, (address, inbound, interface) in enumerate(devices)]
        self.rows.write_text(header + gateway + "".join(rows))
        result = self.run_scanner("observe")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(set(value), {"location", "timestamp", "presence", "totalDevices"})
        for person in ("Dylan", "Julia"):
            self.assertEqual(set(value["presence"][person]), {"present"})
        return {person: data["present"] for person, data in value["presence"].items()}

    def test_either_address_and_both_addresses_are_one_person(self):
        for addresses in ([FIRST], [SECOND], [FIRST, SECOND]):
            with self.subTest(addresses=addresses):
                result = self.observe([(address, "37s", "en0") for address in addresses])
                self.assertEqual(result, {"Dylan": True, "Julia": False})
        self.assertEqual(self.observe([(JULIA, "37s", "en0")]), {"Dylan": False, "Julia": True})

    def test_expired_wrong_interface_and_unknown_addresses_do_not_count(self):
        result = self.observe([(FIRST, "expired", "en0"), (SECOND, "37s", "en1"),
                               ("02:00:00:00:00:44", "37s", "en0")])
        self.assertEqual(result, {"Dylan": False, "Julia": False})
        result = self.observe([(FIRST, "37s", "en0"), (SECOND, "expired", "en0")])
        self.assertTrue(result["Dylan"])

    def test_v1_single_identity_is_compatible(self):
        self.write_config({"schema_version": 1, "site": "crosstown", "people": {
            "Dylan": {"kind": "mac", "value": FIRST},
            "Julia": {"kind": "mac", "value": JULIA},
        }})
        self.assertEqual(self.observe([(FIRST, "37s", "en0")]), {"Dylan": True, "Julia": False})

    def test_invalid_allowlists_fail_without_observation_or_publication(self):
        invalid = [[], [FIRST, FIRST], [FIRST, JULIA], [FIRST, None],
                   ["not-a-mac"], ["ff:ff:ff:ff:ff:ff"], ["00:00:00:00:00:00"],
                   [f"02:00:00:00:01:{index:02x}" for index in range(9)], FIRST]
        for addresses in invalid:
            with self.subTest(addresses=addresses):
                value = self.value()
                value["people"]["Dylan"]["values"] = addresses
                self.write_config(value)
                self.assertNotEqual(self.run_scanner("validate-config").returncode, 0)
        value = self.value()
        value["people"]["Dylan"]["value"] = FIRST
        self.write_config(value)
        self.assertNotEqual(self.run_scanner("validate-config").returncode, 0)

    def test_duplicate_case_and_cross_person_reuse_are_rejected(self):
        for first, second in ((["02:ab:00:00:00:11", "02:AB:00:00:00:11"], [JULIA]),
                              ([FIRST, SECOND], [SECOND])):
            value = self.value()
            value["people"]["Dylan"]["values"] = first
            value["people"]["Julia"]["values"] = second
            self.write_config(value)
            self.assertNotEqual(self.run_scanner("validate-config").returncode, 0)


if __name__ == "__main__":
    unittest.main()
