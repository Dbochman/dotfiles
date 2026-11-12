import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "checked", Path(__file__).resolve().parents[1] / "bin/presence-checked-run.py"
)
checked = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checked)


class CheckedRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.node = self.root / "node"
        self.node.write_text("#!/bin/sh\nexit 0\n")
        self.node.chmod(0o700)
        self.config = self.root / "bindings.json"
        self.config.write_text("{}")
        self.config.chmod(0o600)
        self.scanner = self.root / "scanner.sh"
        self.scanner.write_text("exit 0\n")
        self.env = {**os.environ, "PRESENCE_DEVICE_CONFIG": str(self.config)}

    def invoke(self, mode="validate-config"):
        args = ["checked", "--scanner", str(self.scanner), mode]
        if mode != "cabin":
            args.append("cabin")
        error = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(checked, "node_command", return_value=str(self.node)), \
                patch("sys.argv", args), contextlib.redirect_stderr(error):
            code = checked.main()
        return code, error.getvalue()

    def test_disabled_runtime_is_not_an_enrollment_error(self):
        self.node.chmod(0o600)
        code, error = self.invoke()
        self.assertEqual(code, 78)
        self.assertEqual(json.loads(error)["reason"], "node_not_executable")

    def test_runtime_start_failure_is_classified(self):
        self.node.write_text("#!/bin/sh\nexit 126\n")
        self.assertIn("node_start_failed", self.invoke()[1])

    def test_missing_runtime_is_classified(self):
        self.node.unlink()
        self.assertIn("node_missing", self.invoke()[1])

    def test_insecure_file_and_parent_are_classified(self):
        self.config.chmod(0o644)
        self.assertIn("config_permissions", self.invoke()[1])
        self.config.chmod(0o600)
        self.root.chmod(0o755)
        self.assertIn("config_permissions", self.invoke()[1])

    def test_missing_config_is_classified(self):
        self.config.unlink()
        self.assertIn("config_missing", self.invoke()[1])

    def test_invalid_binding_has_safe_diagnostic(self):
        self.scanner.write_text("echo private-device-value >&2\nexit 1\n")
        code, error = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(error)["reason"], "config_invalid")
        self.assertNotIn("private-device-value", error)

    def test_network_failure_is_not_misreported_as_bad_enrollment(self):
        self.scanner.write_text('[[ "$1" == validate-config ]] && exit 0\nexit 1\n')
        code, error = self.invoke("cabin")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(error)["reason"], "scanner_failed")

    def test_success_keeps_scanner_arguments_and_no_failure_diagnostic(self):
        record = self.root / "args"
        self.scanner.write_text('printf "%s\\n" "$@" > "' + str(record) + '"\n')
        self.assertEqual(self.invoke(), (0, ""))
        self.assertEqual(record.read_text(), "validate-config\ncabin\n")


if __name__ == "__main__":
    unittest.main()
