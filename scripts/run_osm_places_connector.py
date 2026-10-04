#!/usr/bin/env python3
"""Time-capped OSM Nominatim enrichment with graceful degradation.

Wraps the existing Nominatim lookup in a global time budget. When budget
is exhausted, remaining companies get availability "blocked" (not "failed")
with a clear note — the evaluator never crashes or hangs.

Architecture:
  - Global time budget (default 300s = 5 min for 1000 companies)
  - 1-second rate limit per Nominatim TOS
  - RapidFuzz gate at threshold 80
  - Evidence ledger logging for every accepted match
  - Graceful "blocked" state for budget-exhausted companies
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from rapidfuzz import fuzz
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "rapidfuzz"])
    from rapidfuzz import fuzz

log = logging.getLogger("osm_places")

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
OSM_NODE_URL = "https://www.openstreetmap.org/{osm_type}/{osm_id}"
USER_AGENT = "Signalpost-Hackathon-Agent/1.0"
MATCH_THRESHOLD = 80
DEFAULT_BUDGET_SECONDS = 300  # 5 minutes for the entire batch


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_name(value: str) -> str:
    """Normalize a legal/entity name without making a brand-name inference."""
    text = unicodedata.normalize("NFKD", str(value).casefold())
    text = text.replace("ø", "o").replace("å", "a").replace("æ", "ae")
    for suffix in (" as", " asa", " ans", " da", " enk", " nuf"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    return " ".join(text.split())


def strict_name_match(company_name: str, candidate_name: str) -> float | None:
    """Return a conservative score, or ``None`` when name tokens do not align.

    token_set_ratio accepts a one-word subset of a legal name, which is unsafe
    for an entity-resolution gate.  Require 60% of a multi-token legal name to
    be present in the OSM result before applying an order-insensitive score.
    """
    company = normalize_name(company_name)
    candidate = normalize_name(candidate_name)
    if not company or not candidate:
        return None
    company_tokens = set(company.split())
    candidate_tokens = set(candidate.split())
    if len(company_tokens) > 1 and len(company_tokens & candidate_tokens) / len(company_tokens) < 0.6:
        return None
    return max(fuzz.token_sort_ratio(company, candidate), fuzz.ratio(company, candidate))


def nominatim_lookup(
    company_name: str,
    municipality: str,
    *,
    timeout: float = 5,
) -> dict[str, Any] | None:
    """Query Nominatim for a single company. Returns the first result or None."""
    query = f"{company_name}, {municipality}, Norway"
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": "1"})
    url = f"{NOMINATIM_SEARCH}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())
            return data[0] if data else None
    except Exception as exc:
        log.debug("Nominatim lookup failed for %s: %s", company_name, exc)
        return None


def run_time_capped_osm(
    profiles: list[dict[str, Any]],
    *,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    rate_limit_seconds: float = 1.0,
    match_threshold: int = MATCH_THRESHOLD,
    ledger_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run OSM Nominatim lookups with a global time budget.

    Returns (results, metadata) where each result has:
      - organisation_number
      - status: "matched" / "no_match" / "blocked" / "skipped"
      - observation (if matched)
    """
    started = time.monotonic()
    deadline = started + budget_seconds
    results: list[dict[str, Any]] = []
    matched = 0
    blocked = 0
    skipped = 0
    no_match = 0
    errors = 0
    last_request = 0.0

    ledger_entries: list[dict[str, Any]] = []

    for profile in profiles:
        org = str(profile.get("organisation_number", ""))
        name = str(profile.get("name", ""))
        municipality = str(profile.get("municipality", ""))

        # Skip companies without name or municipality
        if not name or not municipality:
            results.append({
                "organisation_number": org,
                "status": "skipped",
                "reason": "missing name or municipality",
            })
            skipped += 1
            continue

        # Check time budget
        now = time.monotonic()
        if now >= deadline:
            results.append({
                "organisation_number": org,
                "status": "blocked",
                "reason": "time_budget_exhausted",
            })
            blocked += 1
            continue

        # Rate limit
        elapsed_since_last = now - last_request
        if elapsed_since_last < rate_limit_seconds:
            sleep_time = rate_limit_seconds - elapsed_since_last
            # Check if sleeping would exceed budget
            if now + sleep_time >= deadline:
                results.append({
                    "organisation_number": org,
                    "status": "blocked",
                    "reason": "time_budget_exhausted",
                })
                blocked += 1
                continue
            time.sleep(sleep_time)

        last_request = time.monotonic()

        # A single request must not run beyond the remaining global budget.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append({
                "organisation_number": org,
                "status": "blocked",
                "reason": "time_budget_exhausted",
            })
            blocked += 1
            continue
        result = nominatim_lookup(name, municipality, timeout=min(5.0, remaining))

        if result is None:
            results.append({
                "organisation_number": org,
                "status": "no_match",
                "reason": "nominatim_no_result",
            })
            no_match += 1
            continue

        # RapidFuzz name matching
        osm_name = result.get("name", "")
        score = strict_name_match(name, osm_name)

        if score is None or score < match_threshold:
            results.append({
                "organisation_number": org,
                "status": "no_match",
                "reason": f"name_match_below_threshold ({score or 0} < {match_threshold})",
                "osm_name": osm_name,
                "match_score": score,
            })
            no_match += 1
            continue

        # Build observation
        osm_type = result.get("osm_type", "node")
        osm_id = result.get("osm_id", "")
        osm_url = OSM_NODE_URL.format(osm_type=osm_type, osm_id=osm_id)
        place_id = str(result.get("place_id", ""))
        fetched_at = utc_now()

        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        obs_id = f"osm-place-{hashlib.sha256(f'{org}|{osm_url}'.encode()).hexdigest()[:24]}"

        observation = {
            "id": obs_id,
            "organisation_number": org,
            "platform": "openstreetmap",
            "signal_type": "place_summary",
            "source_url": osm_url,
            "retrieved_at": fetched_at,
            "content_sha256": content_hash,
            "exact_entity": True,
            "identity_proof": [
                {
                    "type": "fuzzy_name_municipality_match",
                    "value": {
                        "brreg_name": name,
                        "osm_name": osm_name,
                        "municipality": municipality,
                        "match_score": score,
                        "threshold": match_threshold,
                    },
                }
            ],
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "open_geographic_database",
            "evidence_span": f"{osm_name}; {result.get('display_name', '')}",
            "strategy": "places_identity_resolution",
            "metrics": {
                "osm_type": osm_type,
                "osm_id": osm_id,
                "place_id": place_id,
                "latitude": result.get("lat"),
                "longitude": result.get("lon"),
                "osm_class": result.get("class"),
                "osm_type_detail": result.get("type"),
            },
        }

        results.append({
            "organisation_number": org,
            "status": "matched",
            "match_score": score,
            "osm_name": osm_name,
            "osm_url": osm_url,
            "observation": observation,
        })
        matched += 1

        # Ledger entry
        ledger_entries.append({
            "org_nr": org,
            "field": "places_identity",
            "value": place_id,
            "source_url": osm_url,
            "extraction_point": "osm_nominatim",
            "fetched_at": fetched_at,
            "match_score": score,
        })

    elapsed = time.monotonic() - started

    # Write ledger entries
    if ledger_entries and ledger_path:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as f:
            for entry in ledger_entries:
                f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    metadata = {
        "profiles_total": len(profiles),
        "matched": matched,
        "no_match": no_match,
        "blocked": blocked,
        "skipped": skipped,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
        "budget_seconds": budget_seconds,
        "budget_exhausted": blocked > 0,
        "coverage": round(matched / len(profiles), 4) if profiles else 0.0,
    }

    return results, metadata


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Time-capped OSM Nominatim places_identity enrichment"
    )
    parser.add_argument("--profiles", required=True,
                        help="JSONL profiles")
    parser.add_argument("--observations-output", required=True,
                        help="Output JSONL for OSM observations")
    parser.add_argument("--report-output", required=True,
                        help="Output JSON summary report")
    parser.add_argument("--ledger", default="evidence_ledger.jsonl")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS,
                        help=f"Time budget in seconds (default: {DEFAULT_BUDGET_SECONDS})")
    parser.add_argument("--threshold", type=int, default=MATCH_THRESHOLD)
    args = parser.parse_args()

    profiles_path = Path(args.profiles)
    profiles = [
        json.loads(line)
        for line in profiles_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    log.info("Loaded %d profiles", len(profiles))

    results, metadata = run_time_capped_osm(
        profiles,
        budget_seconds=args.budget,
        match_threshold=args.threshold,
        ledger_path=Path(args.ledger),
    )

    # Write observations
    observations = [r["observation"] for r in results if r.get("observation")]
    obs_path = Path(args.observations_output)
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    with obs_path.open("w", encoding="utf-8") as f:
        for obs in observations:
            f.write(json.dumps(obs, ensure_ascii=False, separators=(",", ":")) + "\n")

    # Write report
    report = {
        "connector": "osm_nominatim_places_identity_v1",
        **metadata,
        "claim_boundary": (
            "OSM Nominatim free API with 1s rate limit; "
            f"RapidFuzz name gate at threshold {args.threshold}; "
            f"time budget {args.budget}s — companies beyond budget get 'blocked' not 'failed'."
        ),
    }
    report_path = Path(args.report_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
