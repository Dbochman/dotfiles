#!/usr/bin/env python3
"""Collect a bounded read-only inbox preview for Dylan's explicit account."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "dylan_inbox_review_shared", Path(__file__).with_name("julia-inbox-review.py")
)
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def main(argv=None):
    return review.main(argv, account=os.environ.get("DYLAN_EMAIL", ""))


if __name__ == "__main__":
    raise SystemExit(main())
