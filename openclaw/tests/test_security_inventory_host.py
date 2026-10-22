import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("survey", Path(__file__).parents[1] / "bin/security-inventory-host.py")
survey = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(survey)


class SurveyTests(unittest.TestCase):
    def test_package_manifest_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text(json.dumps({"name": "thing", "version": "1.2.3", "token": "must-not-leak", "scripts": {"start": "danger"}}))
            value = survey.metadata(path)
            self.assertEqual(value["version"], "1.2.3")
            self.assertNotIn("must-not-leak", json.dumps(value))
            self.assertNotIn("scripts", value)

    def test_missing_metadata_unknown(self):
        self.assertEqual(survey.metadata(Path("/does-not-exist/package.json"))["status"], "unavailable")

    def test_distributions_do_not_import_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lib/python3.14/site-packages"
            metadata = root / "example-1.dist-info/METADATA"
            metadata.parent.mkdir(parents=True)
            metadata.write_text("Name: example\nVersion: 1.2.3\n\nPrivate body omitted")
            (Path(directory) / "pyvenv.cfg").write_text("version = 3.14.3\nhome = /private/path\n")
            value = survey.distributions(root)
            self.assertEqual(value["python_version"], "3.14.3")
            self.assertEqual(value["packages"], [{"name": "example", "version": "1.2.3"}])

    def test_command_sanitizes_environment_and_errors(self):
        with patch.object(survey.subprocess, "run", side_effect=OSError("private detail")) as run:
            value = survey.command(["node", "--version"])
        self.assertEqual(value, {"status": "unavailable", "error": "OSError"})
        self.assertNotIn("NODE_OPTIONS", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["HOMEBREW_NO_AUTO_UPDATE"], "1")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_process_collection_never_requests_argv(self):
        with patch.object(survey, "command", return_value={"status": "observed", "text": "12 node\n34 unrelated"}) as command:
            value = survey.processes(True)
        self.assertEqual(command.call_args_list[0].args[0], ["/bin/ps", "-axo", "pid=,comm="])
        self.assertEqual(len(value["processes"]), 1)
        self.assertNotIn("args", value["processes"][0])


if __name__ == "__main__":
    unittest.main()
