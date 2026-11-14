#!/usr/bin/env python3
"""Retired historical experiment.

This script previously treated discovered social handles as publishable claims.
It is intentionally fail-closed and is not part of the submission pipeline.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "compile_v3.py is retired because discovered social handles are not "
        "safe claims. Use scripts/run_signalpost_submission.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
