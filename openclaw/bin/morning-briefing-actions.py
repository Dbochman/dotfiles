#!/usr/bin/env python3
"""List or explicitly close/reopen briefing reminders; never change the mailbox."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

spec = importlib.util.spec_from_file_location("morning_triage", Path(__file__).with_name("morning-triage.py"))
triage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(triage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "close", "reopen"))
    parser.add_argument("--owner", choices=tuple(triage.OWNER_JOBS), required=True)
    parser.add_argument("--key")
    parser.add_argument("--reason")
    args = parser.parse_args()
    db = Path(os.environ.get("OPENCLAW_STATE_DB", str(Path.home() / ".openclaw/state/openclaw.sqlite")))
    store = str(Path.home() / ".openclaw/cron/jobs.json")
    try:
        if args.command == "list":
            payload = triage.current_owner_payload(args.owner, db_path=db, store_key=store)
            closed = triage.load_resolutions(triage.HANDOFF_ROOT, args.owner, store)["closed"]
            keys = {entry["key"] for entry in closed}
            result = {"ok": True, "owner": args.owner,
                      "items": [{**item, "closed": item["key"] in keys} for item in triage.resolution_candidates(payload).values()]}
        else:
            if not args.key or not args.reason:
                parser.error("close/reopen require --key and --reason")
            result = triage.record_resolution(args.owner, args.key, args.reason, db_path=db,
                                             store_key=store, reopen=args.command == "reopen")
    except (OSError, ValueError, TypeError, sqlite3.Error):
        print(json.dumps({"ok": False, "error": "resolution_unavailable_or_selection_changed"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
