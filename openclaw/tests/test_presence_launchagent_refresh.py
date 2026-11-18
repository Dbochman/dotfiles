import importlib.util
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "refresh", Path(__file__).resolve().parents[1] / "bin/refresh-presence-launchagents.py"
)
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources, self.installed, self.state = [self.root / x for x in ("src", "dst", "state")]
        for directory in (self.sources, self.installed, self.state):
            directory.mkdir()
        self.label = "com.openclaw.vacancy-actions"
        self.filename = self.label + ".plist"
        self.definition = {"Label": self.label, "EnvironmentVariables": {"PATH": "/new/bin"}}
        self.data = plistlib.dumps(self.definition)
        (self.sources / self.filename).write_bytes(self.data)
        (self.installed / self.filename).write_bytes(self.data)
        self.calls = []
        self.loaded = True
        self.active = False
        self.path = "/new/bin"
        self.fail_bootstrap = False
        self.flag = None

    def runner(self, *args):
        self.calls.append(args)
        code, output = 0, ""
        if args[0] == "print":
            if not self.loaded:
                code = 113
            else:
                output = ("\tstate = " + ("running" if self.active else "not running")
                          + "\n\tdefault environment = {\n\t\tPATH => /default/bin\n\t}\n"
                          + "\tenvironment = {\n\t\tPATH => " + self.path
                          + ("\n\t\tHOME_EVENTS_PRESENCE_ENABLED => " + self.flag if self.flag is not None else "")
                          + "\n\t}\n")
        elif args[0] == "bootout":
            self.loaded = False
        elif args[0] == "bootstrap":
            if self.fail_bootstrap:
                self.fail_bootstrap = False
                code = 5
            else:
                self.loaded = True
                self.path = plistlib.loads((self.installed / self.filename).read_bytes())["EnvironmentVariables"]["PATH"]
                self.flag = plistlib.loads((self.installed / self.filename).read_bytes())["EnvironmentVariables"].get("HOME_EVENTS_PRESENCE_ENABLED")
        return subprocess.CompletedProcess(args, code, output, "private output must not be logged")

    def run_refresh(self, **kwargs):
        return refresh.refresh(self.label, self.sources, self.installed, self.state,
                               runner=self.runner, **kwargs)

    def test_initial_refresh_records_success_and_second_run_is_noop(self):
        self.assertEqual(self.run_refresh(), "refreshed")
        self.assertEqual(self.run_refresh(), "unchanged")
        self.assertEqual(sum(x[0] == "bootstrap" for x in self.calls), 1)

    def test_live_path_drift_detected_with_identical_disk_and_receipt(self):
        self.run_refresh()
        self.path = "/obsolete/bin"
        self.assertEqual(self.run_refresh(), "refreshed")
        self.assertEqual(self.path, "/new/bin")

    def test_new_plist_setting_forces_refresh(self):
        self.run_refresh()
        self.definition["StartInterval"] = 60
        (self.sources / self.filename).write_bytes(plistlib.dumps(self.definition))
        self.assertEqual(self.run_refresh(), "refreshed")
        self.assertEqual(plistlib.loads((self.installed / self.filename).read_bytes())["StartInterval"], 60)

    def test_active_job_deferred_without_overwriting_installed_file(self):
        self.active = True
        self.definition["StartInterval"] = 60
        (self.sources / self.filename).write_bytes(plistlib.dumps(self.definition))
        self.assertEqual(self.run_refresh(), "deferred_active")
        self.assertEqual((self.installed / self.filename).read_bytes(), self.data)
        self.assertEqual([x[0] for x in self.calls], ["print"])
        self.active = False
        self.assertEqual(self.run_refresh(), "refreshed")

    def test_unloaded_job_stays_unloaded_and_drops_stale_receipt(self):
        self.run_refresh()
        self.loaded = False
        self.calls.clear()
        self.assertEqual(self.run_refresh(), "preserved_unloaded")
        self.assertEqual([x[0] for x in self.calls], ["print"])
        self.assertFalse((self.state / (self.label + ".sha256")).exists())

    def test_missing_job_is_not_installed(self):
        (self.installed / self.filename).unlink()
        self.assertEqual(self.run_refresh(), "not_installed")
        self.assertEqual(self.calls, [])

    def test_failed_bootstrap_restores_previous_plist_and_service(self):
        self.definition["EnvironmentVariables"]["PATH"] = "/changed/bin"
        (self.sources / self.filename).write_bytes(plistlib.dumps(self.definition))
        self.fail_bootstrap = True
        with self.assertRaisesRegex(RuntimeError, "bootstrap_failed"):
            self.run_refresh()
        self.assertTrue(self.loaded)
        self.assertEqual(self.path, "/new/bin")
        self.assertEqual((self.installed / self.filename).read_bytes(), self.data)
        self.assertFalse((self.state / (self.label + ".sha256")).exists())

    def test_dry_run_does_not_change_service_or_files(self):
        self.assertEqual(self.run_refresh(dry_run=True), "would_refresh")
        self.assertEqual([x[0] for x in self.calls], ["print"])
        self.assertEqual(list(self.state.iterdir()), [])

    def presence_settings(self, installed_flag, source_flag="0"):
        self.label = "com.openclaw.presence-cabin"
        self.filename = self.label + ".plist"
        self.definition["Label"] = self.label
        self.definition["EnvironmentVariables"]["HOME_EVENTS_PRESENCE_ENABLED"] = source_flag
        (self.sources / self.filename).write_bytes(plistlib.dumps(self.definition))
        self.definition["EnvironmentVariables"]["HOME_EVENTS_PRESENCE_ENABLED"] = installed_flag
        (self.installed / self.filename).write_bytes(plistlib.dumps(self.definition))
        self.flag = installed_flag

    def test_refresh_preserves_enabled_publisher_and_updates_managed_path(self):
        self.presence_settings("1")
        self.path = "/obsolete/bin"
        self.assertEqual(self.run_refresh(), "refreshed")
        self.assertEqual((self.flag, self.path), ("1", "/new/bin"))
        self.assertEqual(self.run_refresh(), "unchanged")

    def test_refresh_preserves_disabled_publisher_against_enabled_template(self):
        self.presence_settings("0", "1")
        self.assertEqual(self.run_refresh(), "refreshed")
        self.assertEqual(self.flag, "0")

    def test_unloaded_publisher_preserves_enablement_without_starting(self):
        self.presence_settings("1")
        self.loaded = False
        self.assertEqual(self.run_refresh(), "preserved_unloaded")
        data = plistlib.loads((self.installed / self.filename).read_bytes())
        self.assertEqual(data["EnvironmentVariables"]["HOME_EVENTS_PRESENCE_ENABLED"], "1")
        self.assertEqual([x[0] for x in self.calls], ["print"])

    def test_invalid_installed_enablement_fails_before_any_service_change(self):
        self.presence_settings("unexpected")
        with self.assertRaisesRegex(ValueError, "presence_enable_flag_invalid"):
            self.run_refresh()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
