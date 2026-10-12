#!/usr/bin/env python3
"""Classify captured batchModify output; never execute or retry Gmail writes."""

import argparse
import json
import sys

LIMIT = 65536


def accepted(exit_code, output):
    if exit_code != 0 or len(output) > LIMIT:
        return False
    if not output.strip():
        return True
    try:
        return json.loads(output) == {}
    except (ValueError, UnicodeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exit-code", required=True, type=int)
    args = parser.parse_args()
    valid = accepted(args.exit_code, sys.stdin.buffer.read(LIMIT + 1))
    print(json.dumps({"status": "accepted" if valid else "unconfirmed", "readbackRequired": True}))
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
