#!/usr/bin/env python3
"""Dedicated Ring FCM ingress service entry point."""

import asyncio
import importlib.util
import sys
from pathlib import Path


def _load_runtime():
    path = Path(__file__).with_name("service-runtime.py")
    spec = importlib.util.spec_from_file_location("openclaw_home_automation_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ring_event_runtime_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    runtime = _load_runtime()
    runtime._install_stderr_guard()
    try:
        asyncio.run(runtime.ring_event_listener_main())
    except KeyboardInterrupt:
        runtime.log("Interrupted — shutting down")
    except Exception as exc:
        runtime.log(f"FATAL: {exc}")
        sys.exit(1)
