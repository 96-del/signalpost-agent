#!/usr/bin/env python3
"""Licensed Google Places API connector with conservative legal-entity proof.

This connector intentionally does not scrape Google Maps.  It uses Places API
(New), only when a server-side API key and an approved data-rights declaration
are supplied.  Search finds a candidate; Place Details is requested only after
the candidate passes the legal-name plus registered-address gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from normalize_google_maps_results import candidate_score, normalize_company

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"
SEARCH_FIELDS = "places.id,places.displayName,places.formattedAddress,places.googleMapsUri"
DETAIL_FIELDS = "id,displayName,formattedAddress,googleMapsUri,internationalPhoneNumber,websiteUri,rating,userRatingCount"
RIGHTS_ENV = "SIGNALPOST_GOOGLE_PLACES_RIGHTS"
KEY_ENV = "GOOGLE_PLACES_API_KEY"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def request_json(url: str, *, api_key: str, field_mask: str, body: dict[str, Any] | None = None, timeout: float = 20.0) -> tuple[dict[str, Any], bytes]:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": field_mask,
            "User-Agent": "signalpost-research/1.0 (+https://builderr.ai)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_000)
    return json.loads(raw), raw


def query_for(profile: dict[str, Any]) -> str:
    registry = ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}
    address = " ".join(filter(None, [
        str(registry.get("forretningsadresse.adresse") or ""),
        str(registry.get("forretningsadresse.postnummer") or ""),
        str(registry.get("forretningsadresse.poststed") or profile.get("municipality") or ""),
        "Norway",
    ]))
    return " ".join(filter(None, [str(profile.get("name") or ""), address]))


def candidate_from_place(place: dict[str, Any]) -> dict[str, Any]:
    return {
        "place_id": place.get("id"),
        "title": (place.get("displayName") or {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "link": place.get("googleMapsUri", ""),
        "phone": place.get("internationalPhoneNumber", ""),
        "web_site": place.get("websiteUri", ""),
        "review_rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
    }


def fetch_company(profile: dict[str, Any], *, api_key: str, timeout: float = 20.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve one place only when a pre-detail candidate has exact proof."""
    org = str(profile.get("organisation_number") or "")
    try:
        search, _ = request_json(
            SEARCH_URL,
            api_key=api_key,
            field_mask=SEARCH_FIELDS,
            body={"textQuery": query_for(profile), "languageCode": "no", "regionCode": "NO", "pageSize": 5},
            timeout=timeout,
        )
        candidates = [candidate_from_place(item) for item in search.get("places") or []]
        prequalified = [item for item in candidates if item.get("place_id") and candidate_score(profile, item)["accepted"]]
        if len(prequalified) != 1:
            return [], {"organisation_number": org, "status": "ambiguous_or_no_exact_candidate", "candidates": len(candidates), "prequalified": len(prequalified)}
        detail, _ = request_json(
            DETAIL_URL.format(place_id=urllib.parse.quote(str(prequalified[0]["place_id"]), safe="")),
            api_key=api_key,
            field_mask=DETAIL_FIELDS,
            timeout=timeout,
        )
        candidate = candidate_from_place(detail)
        if not candidate_score(profile, candidate)["accepted"]:
            return [], {"organisation_number": org, "status": "detail_identity_mismatch", "candidates": len(candidates)}
        observations, match = normalize_company(profile, [candidate], utc_now())
        # normalize_company is shared with the experimental importer; promote
        # only this official API path and preserve its strict proof unchanged.
        for observation in observations:
            observation["acquisition_mode"] = "licensed_api"
            observation["rights_status"] = "approved"
            observation["source_class"] = "licensed_places_api"
            observation["connector_version"] = "google_places_api_new_v1"
        return observations, {"organisation_number": org, "status": "exact_match", "candidates": len(candidates), **match}
    except urllib.error.HTTPError as exc:
        return [], {"organisation_number": org, "status": "blocked" if exc.code in {401, 403, 429} else "failed", "http_status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return [], {"organisation_number": org, "status": "failed", "error": type(exc).__name__}


def run(profiles: list[dict[str, Any]], *, api_key: str, workers: int = 4, timeout: float = 20.0, budget_seconds: float = 600.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    # Submit bounded waves so the deadline prevents new paid calls. Existing
    # calls carry a fixed timeout and are allowed to finish their terminal state.
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures: dict[Any, str] = {}
        next_index = 0
        while next_index < len(profiles) or futures:
            while len(futures) < max(1, min(workers, 8)) and time.monotonic() < started + budget_seconds:
                if next_index >= len(profiles):
                    break
                profile = profiles[next_index]
                next_index += 1
                future = pool.submit(fetch_company, profile, api_key=api_key, timeout=timeout)
                futures[future] = str(profile.get("organisation_number") or "")
            if not futures:
                break
            future = next(as_completed(futures))
            futures.pop(future)
            rows, status = future.result()
            observations.extend(rows)
            statuses.append(status)
        unresolved = {str(profile.get("organisation_number") or "") for profile in profiles[next_index:]}
    statuses.extend({"organisation_number": org, "status": "blocked", "reason": "time_budget_exhausted"} for org in sorted(unresolved))
    order = {str(profile.get("organisation_number") or ""): index for index, profile in enumerate(profiles)}
    observations.sort(key=lambda row: (order.get(str(row.get("organisation_number")), 10**9), row["id"]))
    statuses.sort(key=lambda row: order.get(str(row.get("organisation_number")), 10**9))
    exact = sum(item.get("status") == "exact_match" for item in statuses)
    return observations, {
        "connector": "google_places_api_new_v1",
        "profiles": len(profiles),
        "exact_matches": exact,
        "coverage": round(exact / len(profiles), 4) if profiles else 0.0,
        "status_counts": {key: sum(row.get("status") == key for row in statuses) for key in sorted({row.get("status") for row in statuses})},
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "budget_seconds": budget_seconds,
        "company_results": statuses,
        "claim_boundary": "Places API (New), called with a server-side key only after source-rights approval. Exact legal name plus registered address, phone, or verified-domain proof is required before publication.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Licensed Google Places API connector")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--budget", type=float, default=600.0)
    args = parser.parse_args()
    api_key = os.environ.get(KEY_ENV, "")
    if not api_key or os.environ.get(RIGHTS_ENV, "").casefold() != "approved":
        raise SystemExit(f"Set {KEY_ENV} and {RIGHTS_ENV}=approved after confirming your Places API licence and attribution obligations.")
    observations, report = run(read_jsonl(Path(args.profiles)), api_key=api_key, workers=args.workers, timeout=args.timeout, budget_seconds=args.budget)
    Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in observations), encoding="utf-8")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "company_results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
