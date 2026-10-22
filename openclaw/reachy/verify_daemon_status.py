#!/usr/bin/env python3
"""Hardware-free regression checks against a specified installed Reachy package.

Usage: python verify_daemon_status.py /path/to/site-packages/reachy_mini
Only selected function ASTs are loaded: no daemon, motors, audio, or app starts.
"""

import ast
import asyncio
import logging
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(), filename=str(path))
    selected = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    if sorted(node.name for node in selected) != sorted(names):
        raise ValueError(f"Missing or ambiguous functions in {path}")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace


class BackendStatusTests(unittest.TestCase):
    def setUp(self):
        self.backend = SimpleNamespace(
            ready=threading.Event(), last_alive=None, error=None,
            motor_control_mode="disabled",
            _status=SimpleNamespace(ready=False, last_alive=None, error=None,
                                    motor_control_mode="disabled", control_loop_stats={"nb_error": 0}),
        )

    def test_initial_not_ready(self):
        status = backend_functions["get_status"](self.backend)
        self.assertFalse(status.ready)
        self.assertIsNone(status.last_alive)

    def test_ready_and_live_heartbeat(self):
        self.backend.ready.set()
        self.backend.last_alive = 100.0
        status = backend_functions["get_status"](self.backend)
        self.assertTrue(status.ready)
        self.assertEqual(status.last_alive, 100.0)
        self.backend.last_alive = 101.0
        self.assertEqual(backend_functions["get_status"](self.backend).last_alive, 101.0)

    def test_clear_readiness_preserves_last_heartbeat(self):
        self.backend.ready.set()
        self.backend.last_alive = 100.0
        backend_functions["get_status"](self.backend)
        self.backend.ready.clear()
        status = backend_functions["get_status"](self.backend)
        self.assertFalse(status.ready)
        self.assertEqual(status.last_alive, 100.0)

    def test_error_mode_and_stats_preserved(self):
        self.backend.error = "test error"
        self.backend.motor_control_mode = "enabled"
        status = backend_functions["get_status"](self.backend)
        self.assertEqual(status.error, "test error")
        self.assertEqual(status.motor_control_mode, "enabled")
        self.assertEqual(status.control_loop_stats, {"nb_error": 0})


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_wake_and_launches_only_once(self):
        manager = SimpleNamespace(start_app=AsyncMock())
        launch = startup_functions["make_startup_app_launcher"](manager, "clawbody")
        await asyncio.sleep(0)
        manager.start_app.assert_not_awaited()
        launch()
        launch()
        await asyncio.sleep(0)
        manager.start_app.assert_awaited_once_with("clawbody", evict_remote=False)

    async def test_wake_callback_failure_is_contained(self):
        manager = SimpleNamespace(start_app=AsyncMock(side_effect=RuntimeError("test failure")))
        await startup_functions["start_startup_app"](manager, "clawbody")
        manager.start_app.assert_awaited_once_with("clawbody", evict_remote=False)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    package = Path(sys.argv.pop())
    backend_functions = load_functions(
        package / "daemon/backend/robot/backend.py", {"get_status"}, {}
    )
    logger = logging.getLogger("reachy-status-test")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    startup_functions = load_functions(
        package / "daemon/app/startup_app.py",
        {"make_startup_app_launcher", "start_startup_app"},
        {"asyncio": asyncio, "logger": logger},
    )
    unittest.main(verbosity=2)
