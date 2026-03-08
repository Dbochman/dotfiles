#!/usr/bin/env python3
"""Sync iMessage group chat ROWIDs into OpenClaw config.

Queries ~/Library/Messages/chat.db for group chats (style=43) and adds
any missing ROWIDs to channels.imessage.groups in openclaw.json.

Preserves existing per-group config and the "*" wildcard entry.
Designed to run as a periodic cron/launchd job.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
LOG_FILE = Path.home() / ".openclaw" / "logs" / "group-sync.log"


def log(msg):
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {msg}\n"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", file=sys.stderr)


def get_group_rowids():
    """Return set of ROWID strings for all group chats in chat.db."""
    if not CHAT_DB.exists():
        log(f"chat.db not found at {CHAT_DB}")
        return set()
    try:
        conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
        cursor = conn.execute("SELECT ROWID FROM chat WHERE style = 43")
        rowids = {str(row[0]) for row in cursor.fetchall()}
        conn.close()
        return rowids
    except Exception as e:
        log(f"Failed to query chat.db: {e}")
        return set()


def sync_groups():
    if not OPENCLAW_CONFIG.exists():
        log(f"Config not found at {OPENCLAW_CONFIG}")
        return False

    # Read current config
    with open(OPENCLAW_CONFIG) as f:
        config = json.load(f)

    imessage = config.get("channels", {}).get("imessage")
    if not imessage:
        log("No channels.imessage in config")
        return False

    groups = imessage.get("groups", {})
    existing_ids = {k for k in groups if k != "*"}

    # Get all group ROWIDs from chat.db
    db_ids = get_group_rowids()
    if not db_ids:
        log("No group chats found in chat.db (or db unreadable)")
        return False

    # Find new groups
    new_ids = db_ids - existing_ids
    if not new_ids:
        log(f"No new groups to add ({len(existing_ids)} already configured)")
        return False

    # Add new groups with empty config (inherit defaults)
    for gid in sorted(new_ids, key=int):
        groups[gid] = {}

    # Ensure wildcard exists
    if "*" not in groups:
        groups["*"] = {}

    imessage["groups"] = groups
    config["channels"]["imessage"] = imessage

    # Write back
    with open(OPENCLAW_CONFIG, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    log(f"Added {len(new_ids)} new group(s): {sorted(new_ids, key=int)}")
    return True


if __name__ == "__main__":
    sync_groups()
