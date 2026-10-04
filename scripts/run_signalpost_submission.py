#!/usr/bin/env python3
"""Single-command, reproducible Signalpost run for a fresh Builderr batch."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from norway_company_agent.batch import read_organisation_inputs  # noqa: E402
from norway_company_agent.contract import profile_to_contract  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def command(*parts: str) -> list[str]:
    return [sys.executable, *parts]


def attach_places_evidence(profiles: list[dict], observations: list[dict]) -> None:
    field_for_signal = {
        "place_summary": "places_identity",
        "review_summary": "places_reviews",
        "profile_metrics": "places_profile_metrics",
        "buzz_metrics": "places_buzz",
    }
    by_org = {str(profile["organisation_number"]): profile for profile in profiles}
    for observation in observations:
        field = field_for_signal.get(str(observation.get("signal_type") or ""))
        profile = by_org.get(str(observation.get("organisation_number") or ""))
        if not field or not profile:
            continue
        profile.setdefault("evidence", {})[field] = evidence(
            field,
            "available",
            "licensed_google_places_api",
            str(observation["source_url"]),
            value={
                "metrics": observation.get("metrics") or {},
                "identity_proof": observation.get("identity_proof") or [],
                "evidence_span": observation.get("evidence_span"),
                "connector_version": observation.get("connector_version"),
                "source_label": "Google Places API (New)",
            },
            retrieved_at=observation.get("retrieved_at"),
            content_sha256=observation.get("content_sha256"),
        )


def attach_company_site_evidence(profiles: list[dict], observations: list[dict]) -> None:
    field_for_signal = {
        "profile_metrics": "company_site_surface",
        "public_post": "company_site_activity",
    }
    by_org = {str(profile["organisation_number"]): profile for profile in profiles}
    for observation in observations:
        field = field_for_signal.get(str(observation.get("signal_type") or ""))
        profile = by_org.get(str(observation.get("organisation_number") or ""))
        if not field or not profile:
            continue
        if not (
            observation.get("platform") == "company_site"
            and observation.get("exact_entity")
            and observation.get("rights_status") == "approved"
            and observation.get("acquisition_mode") == "permitted_public_page"
        ):
            continue
        profile.setdefault("evidence", {})[field] = evidence(
            field,
            "available",
            "company_site",
            str(observation["source_url"]),
            value={
                "metrics": observation.get("metrics") or {},
                "evidence_span": observation.get("evidence_span"),
                "identity_proof": observation.get("identity_proof") or [],
                "strategy": observation.get("strategy"),
            },
            retrieved_at=observation.get("retrieved_at"),
            content_sha256=observation.get("content_sha256"),
        )


def attach_nav_evidence(profiles: list[dict], observations: list[dict]) -> None:
    by_org = {str(profile["organisation_number"]): profile for profile in profiles}
    for observation in observations:
        profile = by_org.get(str(observation.get("organisation_number") or ""))
        proof = (observation.get("identity_proof") or [{}])[0].get("value") or {}
        if not profile or not (
            observation.get("platform") == "job_board"
            and observation.get("signal_type") == "job_posting"
            and observation.get("exact_entity")
            and observation.get("acquisition_mode") == "official_api"
            and observation.get("rights_status") == "approved"
            and float(proof.get("match_score") or 0) == 100.0
        ):
            continue
        profile.setdefault("evidence", {})["workforce_jobs"] = evidence(
            "workforce_jobs",
            "available",
            "official_nav_stilling_feed",
            str(observation["source_url"]),
            value={
                "active_job_count": (observation.get("metrics") or {}).get("active_job_count"),
                "job_titles": observation.get("evidence_span"),
                "identity_proof": observation.get("identity_proof") or [],
                "strategy": observation.get("strategy"),
            },
            retrieved_at=observation.get("retrieved_at"),
            content_sha256=observation.get("content_sha256"),
        )


def attach_osm_evidence(profiles: list[dict], observations: list[dict]) -> None:
    by_org = {str(profile["organisation_number"]): profile for profile in profiles}
    for observation in observations:
        profile = by_org.get(str(observation.get("organisation_number") or ""))
        proof = (observation.get("identity_proof") or [{}])[0].get("value") or {}
        if not profile or not (
            observation.get("platform") == "openstreetmap"
            and observation.get("signal_type") == "place_summary"
            and observation.get("exact_entity")
            and float(proof.get("match_score") or 0) >= 80.0
        ):
            continue
        profile.setdefault("evidence", {})["places_identity"] = evidence(
            "places_identity",
            "available",
            "open_geographic_database",
            str(observation["source_url"]),
            value={
                "evidence_span": observation.get("evidence_span"),
                "metrics": observation.get("metrics"),
                "identity_proof": observation.get("identity_proof") or [],
                "strategy": observation.get("strategy"),
            },
            retrieved_at=observation.get("retrieved_at"),
            content_sha256=observation.get("content_sha256"),
        )


def load_optional_nav_snapshot(path: Path) -> dict | None:
    """Use NAV only when a readable exhausted snapshot exists. Never abort the run."""
    if not path.exists():
        return None
    try:
        from run_nav_workforce_connector import load_snapshot
        _, metadata = load_snapshot(path)
    except (OSError, json.JSONDecodeError, EOFError, KeyError, ImportError):
        return None
    if metadata.get("feed_exhausted") is not True:
        return None
    return metadata


def run(args: argparse.Namespace) -> None:
    organisations = read_organisation_inputs(args.organisations)
    if len(organisations) != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} organisations, received {len(organisations)}")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base_profiles = out / "base-profiles.jsonl"
    base_output = out / "base-envelopes.jsonl"
    base_report = out / "base-report.json"
    crawled_profiles = out / "profiles.jsonl"
    crawl_events = out / "crawl-events.jsonl"
    crawl_report = out / "crawl-report.json"
    crawler_jobdir = out / "crawl-job"
    places_observations_path = out / "google-places-observations.jsonl"
    places_report_path = out / "google-places-report.json"
    site_activity_path = out / "company-site-activity.jsonl"
    site_activity_report_path = out / "company-site-activity-report.json"
    site_news_path = out / "company-site-news.jsonl"
    site_news_report_path = out / "company-site-news-report.json"
    nav_observations_path = out / "nav-observations.jsonl"
    nav_report_path = out / "nav-report.json"

    base = command(
        "scripts/run_competition_batch.py",
        "--organisations", args.organisations,
        "--bulk", args.bulk,
        "--profiles-output", str(base_profiles),
        "--output", str(base_output),
        "--report", str(base_report),
        "--run-id", args.run_id,
        "--expected-count", str(args.expected_count),
        "--workers", str(args.workers),
    )
    subprocess.run(base, cwd=ROOT, check=True)

    crawl = command(
        "scripts/run_scrapy_websites.py",
        "--input", str(base_profiles),
        "--output", str(crawled_profiles),
        "--events", str(crawl_events),
        "--jobdir", str(crawler_jobdir),
        "--report", str(crawl_report),
        "--concurrency", str(args.crawl_concurrency),
        "--per-domain", str(args.crawl_per_domain),
    )
    subprocess.run(crawl, cwd=ROOT, check=True)

    profiles = read_jsonl(crawled_profiles)
    site_activity_command = command(
        "scripts/extract_company_site_activity.py",
        "--profiles", str(crawled_profiles),
        "--output", str(site_activity_path),
        "--report", str(site_activity_report_path),
    )
    site_news_command = command(
        "scripts/extract_company_site_news.py",
        "--profiles", str(crawled_profiles),
        "--output", str(site_news_path),
        "--report", str(site_news_report_path),
    )
    subprocess.run(site_activity_command, cwd=ROOT, check=True)
    subprocess.run(site_news_command, cwd=ROOT, check=True)
    site_observations = read_jsonl(site_activity_path) + read_jsonl(site_news_path)
    attach_company_site_evidence(profiles, site_observations)
    site_summary = {
        "surface": json.loads(site_activity_report_path.read_text(encoding="utf-8")),
        "activity": json.loads(site_news_report_path.read_text(encoding="utf-8")),
    }

    nav_snapshot = Path(args.nav_snapshot)
    nav_metadata = load_optional_nav_snapshot(nav_snapshot)
    if nav_metadata is None:
        nav_summary = {
            "connector": "nav_stilling_feed_workforce_v1",
            "status": "skipped",
            "reason": "NAV snapshot missing, unreadable, or not exhausted; run continues without job claims from this cache",
        }
    else:
        nav_command = command(
            "scripts/run_nav_workforce_connector.py",
            "--profiles", str(crawled_profiles),
            "--snapshot", str(nav_snapshot),
            "--observations-output", str(nav_observations_path),
            "--report-output", str(nav_report_path),
            "--ledger", str(out / "nav-evidence-ledger.jsonl"),
            "--threshold", str(args.nav_threshold),
            "--bulk", str(args.bulk),
        )
        subprocess.run(nav_command, cwd=ROOT, check=True)
        attach_nav_evidence(profiles, read_jsonl(nav_observations_path))
        nav_summary = json.loads(nav_report_path.read_text(encoding="utf-8"))
        nav_summary["shipped_snapshot"] = str(nav_snapshot)
        nav_summary["snapshot_metadata"] = nav_metadata

    places_summary: dict | None = None
    external_paths: list[str] = [str(site_activity_path), str(site_news_path)]
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    rights_approved = os.environ.get("SIGNALPOST_GOOGLE_PLACES_RIGHTS", "").casefold() == "approved"
    if api_key and rights_approved:
        from run_google_places_api_connector import run as run_google_places

        observations, places_summary = run_google_places(
            profiles,
            api_key=api_key,
            workers=args.places_workers,
            budget_seconds=args.places_budget,
        )
        attach_places_evidence(profiles, observations)
        write_jsonl(places_observations_path, observations)
        places_report_path.write_text(json.dumps(places_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        external_paths.append(str(places_observations_path))
    else:
        places_summary = {
            "connector": "google_places_api_new_v1",
            "status": "not_run",
            "reason": "requires GOOGLE_PLACES_API_KEY and SIGNALPOST_GOOGLE_PLACES_RIGHTS=approved",
        }

    osm_observations_path = out / "osm-observations.jsonl"
    osm_report_path = out / "osm-report.json"
    osm_command = command(
        "scripts/run_osm_places_connector.py",
        "--profiles", str(crawled_profiles),
        "--observations-output", str(osm_observations_path),
        "--report-output", str(osm_report_path),
        "--ledger", str(out / "osm-evidence-ledger.jsonl"),
        "--budget", "300",
    )
    try:
        subprocess.run(osm_command, cwd=ROOT, check=True)
        attach_osm_evidence(profiles, read_jsonl(osm_observations_path))
        osm_summary = json.loads(osm_report_path.read_text(encoding="utf-8"))
        external_paths.append(str(osm_observations_path))
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        osm_summary = {"connector": "osm_nominatim", "status": "failed", "error": str(exc)[:300]}

    write_jsonl(crawled_profiles, profiles)
    base_envelopes = {row["organisation_number"]: row for row in read_jsonl(base_output)}
    contract_rows = []
    for profile in profiles:
        base_run = (base_envelopes.get(profile["organisation_number"]) or {}).get("run") or {}
        metric = profile.get("run_metrics") or {}
        contract_rows.append(profile_to_contract(
            profile,
            run_id=args.run_id,
            started_at=base_run.get("started_at"),
            completed_at=base_run.get("completed_at"),
            operations={
                "requests": metric.get("requests", 0),
                "runtime_ms": sum(metric.get("latencies_ms") or []),
                "third_party_cost_usd": 0,
            },
        ))
    output_path = Path(args.output)
    write_jsonl(output_path, contract_rows)

    viewer_path = Path(args.viewer)
    viewer_command = command("scripts/build_prototype.py", "--input", str(crawled_profiles), "--output", str(viewer_path))
    if external_paths:
        viewer_command.extend(["--external-observations", *external_paths])
    subprocess.run(viewer_command, cwd=ROOT, check=True)

    base_summary = json.loads(base_report.read_text(encoding="utf-8"))
    crawl_summary = json.loads(crawl_report.read_text(encoding="utf-8"))
    produced = [row.get("organisation_number") for row in contract_rows]
    expected = [row["organisation_number"] for row in organisations]
    validation = {
        "exact_expected_count": len(contract_rows) == len(expected),
        "unique_organisation_numbers": len(produced) == len(set(produced)),
        "preserves_input_order": produced == expected,
        "terminal_runs": all((row.get("run") or {}).get("terminal_status", "").startswith("completed") for row in contract_rows),
    }
    report = {
        "runner": "signalpost_submission_v1",
        "run_id": args.run_id,
        "expected_count": len(expected),
        "emitted_envelopes": len(contract_rows),
        "validation": {"passed": all(validation.values()), "checks": validation},
        "base": base_summary,
        "crawl": crawl_summary,
        "company_site_observations": site_summary,
        "nav_workforce": nav_summary,
        "google_places": places_summary,
        "osm_places": osm_summary,
        "output": str(output_path),
        "viewer": str(viewer_path),
        "claim_boundary": (
            "Official Brreg evidence plus robots-aware crawling of registry-linked company sites. "
            "Unqualified third-party discovery sources are not published by this runner."
        ),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"emitted_envelopes": len(contract_rows), "validation": report["validation"], "report": str(report_path)}, indent=2))
    if not report["validation"]["passed"]:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="One-command Signalpost official-run candidate")
    parser.add_argument("--organisations", required=True)
    parser.add_argument("--bulk", required=True)
    parser.add_argument("--output", required=True, help="OUTPUT_CONTRACT JSONL")
    parser.add_argument("--report", required=True)
    parser.add_argument("--viewer", required=True, help="Inspectable static HTML evidence browser")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--out-dir", default="out/submission-run")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--crawl-concurrency", type=int, default=16)
    parser.add_argument("--crawl-per-domain", type=int, default=2)
    parser.add_argument("--nav-snapshot", default="out/nav-feed-snapshot.json.gz")
    parser.add_argument("--nav-threshold", type=int, default=100, choices=range(100, 101))
    parser.add_argument("--places-workers", type=int, default=4)
    parser.add_argument("--places-budget", type=float, default=600.0)
    run(parser.parse_args())


if __name__ == "__main__":
    main()