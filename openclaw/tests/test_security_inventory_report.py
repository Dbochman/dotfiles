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

    def test_disabled_binary_is_not_an_unknown_executable(self):
        host = self.host()
        host["node_binaries"] = [{"resolved": "/old/node", "executable": False,
                                  "version": {"status": "disabled_non_executable"}}]
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory, host))
        self.assertEqual(next(c for c in value["components"] if c["product"] == "Node.js")["state"], "disabled_non_executable")

    def test_removed_binary_does_not_hide_still_running_process(self):
        host = self.host()
        host["processes"]["processes"] = [{"executable": "node", "mapped_executables": ["/removed/node"]}]
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory, host))
        node = next(c for c in value["components"] if c["product"] == "Node.js")
        self.assertEqual(node["state"], "running")
        self.assertEqual(node["evidence"], "mapped_executable_missing_on_disk")
        self.assertIsNone(node["version"])

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

    def test_windows_build_defender_and_node_evidence(self):
        host = self.host()
        node = r"C:\Vendor\node.exe"
        host.update(os="Windows", os_release={"PRETTY_NAME": "Microsoft Windows 11 Home",
                    "VERSION_ID": "10.0.26300.9711"}, windows={"defender": {
                    "status": "observed", "platform": "4.18.26090.9", "engine": "1.1.26090.9"},
                    "apps": [{"name": "OpenAI.Codex", "version": "26.1016", "evidence": "appx_package_metadata"}],
                    "browsers": []})
        host["processes"]["processes"] = [{"executable": node, "mapped_executables": [node]}]
        host["node_binaries"] = [{"resolved": node, "version": {"text": "24.19.0"},
                                   "evidence": "running_path_file_version_not_memory_probe"}]
        with tempfile.TemporaryDirectory() as directory:
            value = report.build(self.fixture(directory, host))
        rows = {row["product"]: row for row in value["components"]}
        self.assertEqual(rows["Microsoft Windows 11 Home"]["version"], "10.0.26300.9711")
        self.assertEqual(rows["Microsoft Malware Protection Engine"]["version"], "1.1.26090.9")
        self.assertEqual(rows["Node.js"]["state"], "running")
        self.assertEqual(rows["Node.js"]["evidence"], "running_path_file_version_not_memory_probe")
        self.assertEqual(rows["OpenAI.Codex"]["state"], "installed_not_observed_running")
        self.assertFalse(value["vulnerability_clearance"])


if __name__ == "__main__":
    unittest.main()
