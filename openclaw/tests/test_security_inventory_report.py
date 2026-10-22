import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("report", Path(__file__).parents[1] / "bin/security-inventory-report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def fixture(self, directory, host=None):
        root = Path(directory)
        scope = {"hosts": [{"id": "test", "file": "test.json"}], "unverified_assets": [],
                 "exclusions": [], "followups": [], "daily_monitor_design": {}, "projects": []}
        (root / "scope.json").write_text(json.dumps(scope))
        if host is not None:
            (root / "test.json").write_text(json.dumps(host))
        return root

    def host(self):
        return {"schema_version": 1, "observed_at": "2026-10-22T00:00:00Z", "os": "Darwin",
                "os_version": {"text": "27.0.1"}, "processes": {"processes": []}}

    def test_absent_host_unknown_not_empty_clearance(self):
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory))
        self.assertEqual(value["assets"][0]["coverage"], "unavailable")
        self.assertFalse(value["vulnerability_clearance"])

    def test_staging_does_not_mean_running(self):
        host = self.host()
        host["node_binaries"] = [{"resolved": "/staged/node", "version": {"text": "v24.22.1"}}]
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory, host))
        node = next(row for row in value["components"] if row["product"] == "Node.js")
        self.assertEqual(node["state"], "installed_not_observed_running")

    def test_running_requires_mapping(self):
        host = self.host()
        host["processes"]["processes"] = [{"executable": "node", "mapped_executables": ["/staged/node"]}]
        host["node_binaries"] = [{"resolved": "/staged/node", "version": {"text": "v24.22.1"}}]
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory, host))
        self.assertEqual(next(row for row in value["components"] if row["product"] == "Node.js")["state"], "running")

    def test_electron_does_not_invent_node_version(self):
        host = self.host()
        host["apps"] = {"applications": [{"name": "App", "path": "/Applications/App.app", "version": "1",
                                              "bundles": [{"product": "Electron", "version": "42"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory, host))
        node = next(row for row in value["components"] if row["product"] == "Node.js")
        self.assertIsNone(node["version"])
        self.assertEqual(node["state"], "unknown")

    def test_linux_release_and_kernel_distinct(self):
        host = self.host()
        host.update(os="Linux", kernel="6.12", os_release={"PRETTY_NAME": "Debian", "VERSION_ID": "13"})
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory, host))
        values = {row["product"]: row["version"] for row in value["components"]}
        self.assertEqual(values, {"Debian": "13", "Linux kernel": "6.12"})

    def test_python_name_normalization(self):
        self.assertEqual(report.canonical("Foo_Bar.BAZ", "pypi"), "foo-bar-baz")
        self.assertEqual(report.canonical("@scope/foo.bar", "npm"), "@scope/foo.bar")


if __name__ == "__main__":
    unittest.main()
