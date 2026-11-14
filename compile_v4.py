#!/usr/bin/env python3
"""Retired historical experiment.

This script previously produced heuristic social-profile claims and must not
be used to create a Signalpost submission.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "compile_v4.py is retired because heuristic social-profile publishing "
        "is unsafe. Use scripts/run_signalpost_submission.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
