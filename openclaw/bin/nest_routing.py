"""Explicit, protected cutover switch shared by model-driven camera callers."""
from __future__ import annotations

from pathlib import Path
import importlib.util


def selected_binary(conventional: Path | str) -> Path:
    root = Path.home() / ".openclaw" / "nest-agent"
    if root.is_symlink():
        raise RuntimeError("Invalid Nest routing directory")
    path = root / "routing.json"
    if not root.exists():
        # Pre-cutover state, not a fallback after agent authentication fails.
        return Path(conventional)
    spec = importlib.util.spec_from_file_location("_nest_routing_oauth", Path(__file__).with_name("nest-oauth.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.directory(root)
    if not path.exists() and not path.is_symlink():
        return Path(conventional)
    config = module.read_json(path)
    if set(config) != {"version", "agent_enabled"} or type(config["version"]) is not int or config["version"] != 1 or type(config["agent_enabled"]) is not bool:
        raise RuntimeError("Invalid Nest routing configuration")
    if not config["agent_enabled"]:
        return Path(conventional)
    return Path.home() / ".openclaw" / "bin" / "nest-agent"
