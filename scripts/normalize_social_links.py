#!/usr/bin/env python3
"""Retired historical social-profile normalizer.

Name-similarity is not enough to prove that a social account belongs to an
organisation. The production runner deliberately abstains from these claims.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "normalize_social_links.py is retired. The submission runner does not "
        "publish heuristic social-profile claims.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
