#!/usr/bin/env python3
"""Retired historical experiment.

This script previously published heuristic social-profile claims. It is kept
only so old links fail clearly; it is not part of the submission pipeline.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "run_v2.py is retired because heuristic social-profile publishing is "
        "not safe for Signalpost. Use scripts/run_signalpost_submission.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
