#!/usr/bin/env python3
"""Compile V6 enrichment: NAV workforce_jobs + time-capped OSM places_identity.

This script:
  1. Loads existing V5 envelopes (foundation already scored 1.0)
  2. Runs NAV workforce connector (from pre-cached snapshot or fresh fetch)
  3. Runs time-capped OSM Nominatim enrichment
  4. Merges observations into envelopes
  5. Re-scores as V6
  6. Saves v6-envelopes.jsonl and v6-score.json
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_nav_workforce_connector import (
    build_lookup_index,
    build_observation,
    fetch_feed_snapshot,
    fetch_nav_token,
    load_snapshot,
    match_company,
    save_snapshot,
)
from run_osm_places_connector import run_time_capped_osm
from norway_company_agent.external_control import development_score, run_company_control

log = logging.getLogger("compile_v6")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def build_verified_website_observations(profiles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Turn only identity-gated company websites into publishable observations.

    A registry-linked URL is not itself exact-company evidence.  This accepts it
    only after the existing deterministic identity gate marked it publishable.
    """
    observations: list[dict] = []
    ledger: list[dict] = []
    for profile in profiles:
        website = (profile.get("evidence") or {}).get("website") or {}
        value = website.get("value") or {}
        assessment = value.get("identity_assessment") or {}
        source_url = str(website.get("source_url") or value.get("final_url") or "")
        content_hash = str(website.get("content_sha256") or value.get("content_sha256") or "")
        retrieved_at = str(website.get("retrieved_at") or "")
        if not (
            website.get("status") == "available"
            and assessment.get("publishable") is True
            and source_url
            and len(content_hash) == 64
            and retrieved_at
        ):
            continue
        org = str(profile["organisation_number"])
        proof = {
            "type": "website_identity_gate",
            "value": {
                "legal_name": profile.get("name"),
                "status": assessment.get("status"),
                "score": assessment.get("score"),
                "method": assessment.get("method"),
                "reasons": assessment.get("reasons"),
            },
        }
        observations.append({
            "id": "website-identity-" + hashlib.sha256(f"{org}|{source_url}|{content_hash}".encode()).hexdigest()[:24],
            "organisation_number": org,
            "platform": "company_site",
            "signal_type": "company_profile",
            "source_url": source_url,
            "retrieved_at": retrieved_at,
            "content_sha256": content_hash,
            "exact_entity": True,
            "identity_proof": [proof],
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "source_class": "company_owned",
            "evidence_span": str(value.get("title") or value.get("description") or profile.get("name") or "")[:1000],
            "strategy": "company_site_identity",
            "metrics": {},
        })
        ledger.extend([
            {
                "org_nr": org,
                "field": "exact_external_identity",
                "value": source_url,
                "source_url": source_url,
                "extraction_point": "website_identity_gate",
                "fetched_at": retrieved_at,
                "identity_score": assessment.get("score"),
            },
            {
                "org_nr": org,
                "field": "freshness_evidence",
                "value": "retrieved_and_hashed",
                "source_url": source_url,
                "extraction_point": "website_identity_gate",
                "fetched_at": retrieved_at,
            },
        ])
    return observations, ledger


def append_ledger(path: Path, entries: list[dict]) -> None:
    """Append one run's auditable evidence records without overwriting history."""
    if not entries:
        return
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    from datetime import datetime, timezone

    started = time.monotonic()

    # Paths
    profiles_path = Path("out/smoke-profiles.jsonl")
    # V5 includes legacy heuristic component grants.  Start from the contract
    # envelopes and reconstruct external evidence through the strict gates.
    envelopes_path = Path("out/smoke-envelopes.jsonl")
    nav_snapshot_path = Path("out/nav-feed-snapshot.json")
    ledger_path = Path("evidence_ledger.jsonl")
    out_dir = Path("out")

    # 1. Load profiles and envelopes
    log.info("Loading profiles and envelopes...")
    profiles = read_jsonl(profiles_path)
    envelopes = read_jsonl(envelopes_path)
    profile_map = {str(p["organisation_number"]): p for p in profiles}
    envelope_map = {str(e["organisation_number"]): e for e in envelopes}
    log.info("Loaded %d profiles, %d envelopes", len(profiles), len(envelopes))

    website_observations, website_ledger = build_verified_website_observations(profiles)
    append_ledger(ledger_path, website_ledger)
    log.info("Website identity gate: %d/%d publishable exact-company observations",
             len(website_observations), len(profiles))

    # 2. NAV workforce_jobs
    log.info("=" * 60)
    log.info("PHASE 1: NAV Workforce Jobs")
    log.info("=" * 60)

    if nav_snapshot_path.exists():
        log.info("Loading cached NAV snapshot from %s", nav_snapshot_path)
        nav_items, nav_meta = load_snapshot(nav_snapshot_path)
        content_hash = hashlib.sha256(nav_snapshot_path.read_bytes()).hexdigest()
    else:
        log.info("Fetching NAV feed (this may take 15-25 min for the first time)...")
        token = fetch_nav_token()
        nav_items, nav_meta = fetch_feed_snapshot(
            token,
            max_pages=None,
            checkpoint_path=nav_snapshot_path.with_suffix(".checkpoint.json"),
        )
        content_hash = save_snapshot(nav_items, nav_meta, nav_snapshot_path)
        log.info("Saved NAV snapshot: %d active items", len(nav_items))

    fetched_at = nav_meta.get("fetched_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    # Build index and match
    nav_index = build_lookup_index(nav_items)
    log.info("NAV index: %d municipalities, %d active entries", len(nav_index), len(nav_items))

    nav_observations: list[dict] = []
    nav_matched = 0
    for profile in profiles:
        org = str(profile.get("organisation_number", ""))
        name = str(profile.get("name", ""))
        municipality = str(profile.get("municipality", ""))
        matched = match_company(name, municipality, nav_index)
        obs, ledger = build_observation(org, name, matched, content_hash, fetched_at)
        if obs:
            nav_observations.append(obs)
            nav_matched += 1
            append_ledger(ledger_path, ledger)

    log.info("NAV: %d/%d companies matched (%.1f%%)", nav_matched, len(profiles), 100 * nav_matched / len(profiles))

    # 3. OSM Places Identity (time-capped)
    log.info("=" * 60)
    log.info("PHASE 2: OSM Nominatim (time-capped)")
    log.info("=" * 60)

    osm_results, osm_meta = run_time_capped_osm(
        profiles,
        budget_seconds=300,  # 5 minutes max
        ledger_path=ledger_path,
    )
    osm_observations = [r["observation"] for r in osm_results if r.get("observation")]
    log.info("OSM: %d matched, %d blocked, %d no_match",
             osm_meta["matched"], osm_meta["blocked"], osm_meta["no_match"])

    # 4. Collect all observations per company
    log.info("=" * 60)
    log.info("PHASE 3: Merge & Score")
    log.info("=" * 60)

    # Load existing observations if any
    existing_obs_paths = [out_dir / "news-observations.jsonl", out_dir / "workforce-observations.jsonl"]
    all_observations: list[dict] = list(website_observations)
    for obs_path in existing_obs_paths:
        if obs_path.exists():
            obs = read_jsonl(obs_path)
            if obs:
                all_observations.extend(obs)
                log.info("Loaded %d existing observations from %s", len(obs), obs_path.name)

    all_observations.extend(nav_observations)
    all_observations.extend(osm_observations)

    obs_by_org: dict[str, list[dict]] = defaultdict(list)
    for obs in all_observations:
        obs_by_org[str(obs.get("organisation_number", ""))].append(obs)

    # 5. Score each company
    scored_results: list[dict] = []
    for profile in profiles:
        org = str(profile["organisation_number"])
        company_obs = obs_by_org.get(org, [])
        result = run_company_control(profile, company_obs, minimum_iterations=1)
        scored_results.append(result)

    # 6. Merge into envelopes
    for envelope in envelopes:
        org = str(envelope["organisation_number"])
        result = next((r for r in scored_results if str(r.get("organisation_number")) == org), None)
        if result:
            envelope.setdefault("final", {}).update({
                "components": result["final"]["components"],
                "experimental_components": result["final"]["experimental_components"],
                "score": result["final"]["score"],
            })

    # 7. Write outputs
    write_jsonl(out_dir / "v6-envelopes.jsonl", envelopes)
    write_jsonl(out_dir / "website-observations.jsonl", website_observations)
    write_jsonl(out_dir / "nav-observations.jsonl", nav_observations)
    write_jsonl(out_dir / "osm-observations.jsonl", osm_observations)
    log.info("Wrote %d NAV obs, %d OSM obs, %d envelopes",
             len(nav_observations), len(osm_observations), len(envelopes))

    # 8. Score using the completeness scorer
    try:
        from score_company_completeness import score_rows, summarize, ENRICHMENT_WEIGHTS, FOUNDATION_WEIGHTS

        scored = score_rows(profiles, scored_results)
        if len(scored) >= 2:
            half = len(scored) // 2
            base_summary = summarize(scored[:half])
            ext_summary = summarize(scored[half:])
            combined = summarize(scored)
            score_report = {
                "scorer": "signalpost_all_source_completeness_v1",
                "definition": "Per-company field completeness across official Norwegian records and verified external evidence. Missing fields earn zero; unavailable sentiment remains unavailable rather than neutral.",
                "weights": {"official_foundation": FOUNDATION_WEIGHTS, "external_enrichment": ENRICHMENT_WEIGHTS},
                "base": base_summary,
                "extension": ext_summary,
                "combined": combined,
                "transfer": {
                    "strict_mean_delta": round(ext_summary["strict_completeness_mean"] - base_summary["strict_completeness_mean"], 3),
                    "experimental_mean_delta": round(ext_summary["experimental_completeness_mean"] - base_summary["experimental_completeness_mean"], 3),
                    "strict_retention": round(ext_summary["strict_completeness_mean"] / base_summary["strict_completeness_mean"], 4) if base_summary["strict_completeness_mean"] else None,
                    "experimental_retention": round(ext_summary["experimental_completeness_mean"] / base_summary["experimental_completeness_mean"], 4) if base_summary["experimental_completeness_mean"] else None,
                },
                "claim_boundary": "Completeness, not correctness. Strict evidence passes the current publication policy; experimental evidence is rights- or qualification-pending. Hidden exact-entity labels remain a separate accuracy gate.",
            }
        else:
            combined = summarize(scored)
            score_report = {
                "scorer": "signalpost_all_source_completeness_v1",
                "combined": combined,
            }

        score_path = out_dir / "v6-score.json"
        score_path.write_text(json.dumps(score_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.info("V6 Score written to %s", score_path)
        print("\n" + "=" * 60)
        print("V6 SCORE REPORT")
        print("=" * 60)
        print(json.dumps(score_report, ensure_ascii=False, indent=2))
    except Exception as exc:
        log.error("Scoring failed: %s", exc)
        import traceback
        traceback.print_exc()

    elapsed = time.monotonic() - started
    log.info("Total V6 compile time: %.1fs (%.1f min)", elapsed, elapsed / 60)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"NAV workforce_jobs: {nav_matched}/{len(profiles)} companies matched")
    print(f"OSM places_identity: {osm_meta['matched']}/{len(profiles)} companies matched")
    print(f"Total runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
