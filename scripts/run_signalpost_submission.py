#!/usr/bin/env python3
"""Single-command, reproducible Signalpost run for a fresh Builderr batch."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from norway_company_agent.batch import read_organisation_inputs  # noqa: E402
from norway_company_agent.contract import profile_to_contract, validate_output_envelopes  # noqa: E402
from norway_company_agent.evidence import evidence, utc_now  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_optional_site_outputs(
    observations_path: Path,
    report_path: Path,
    *,
    expected_profiles: int,
) -> tuple[list[dict], dict]:
    """Validate optional local extractor artifacts before they enter the run."""
    if not observations_path.is_file() or not report_path.is_file():
        raise ValueError("site extractor did not create both output files")
    rows = read_jsonl(observations_path)
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("site extractor returned a non-object observation")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("profiles") != expected_profiles:
        raise ValueError("site extractor returned an invalid profile count")
    if report.get("observations") != len(rows):
        raise ValueError("site extractor observation count does not match its output")
    if "companies_with_activity" in report and report["companies_with_activity"] != len(rows):
        raise ValueError("site extractor activity count does not match its output")
    return rows, report


def load_previous_profile_map(
    path: Path,
    current_organisation_numbers: list[str],
    *,
    explicit: bool,
) -> tuple[dict[str, dict], str | None]:
    """Load a prior snapshot before the runner overwrites its intermediate files."""
    if not path.exists():
        if explicit:
            raise SystemExit(f"Previous-profile file does not exist: {path}")
        return {}, None
    try:
        rows = read_jsonl(path)
        previous: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Previous-profile JSONL rows must be objects")
            organisation_number = str(row.get("organisation_number") or "")
            if not organisation_number:
                raise ValueError("Previous profile is missing organisation_number")
            if organisation_number in previous:
                raise ValueError(f"Previous profiles contain duplicate organisation {organisation_number}")
            previous[organisation_number] = row
        current = set(current_organisation_numbers)
        return {org: row for org, row in previous.items() if org in current}, None
    except Exception as exc:
        if explicit:
            raise SystemExit(f"Could not read previous profiles from {path}: {type(exc).__name__}: {exc}") from exc
        return {}, f"Ignored unreadable automatic previous snapshot {path}: {type(exc).__name__}: {str(exc)[:200]}"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def command(*parts: str) -> list[str]:
    return [sys.executable, *parts]


def crawl_artifact_key(run_id: str, organisation_numbers: list[str], invocation_started_at: str) -> str:
    """Isolate crawler state by invocation, even if a caller reuses its run ID."""
    payload = json.dumps(
        {
            "run_id": str(run_id),
            "invocation_started_at": invocation_started_at,
            "organisation_numbers": organisation_numbers,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def run_optional_crawler(
    crawl_command: list[str],
    *,
    base_profiles: Path,
    crawled_profiles: Path,
    events_path: Path,
    report_path: Path,
    expected_organisation_numbers: list[str],
) -> tuple[dict, str | None]:
    """Run the breadth crawler without making it a single point of batch failure."""
    try:
        subprocess.run(crawl_command, cwd=ROOT, check=True)
        rows = read_jsonl(crawled_profiles)
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("optional website crawler returned a non-object profile")
        actual = [str(row.get("organisation_number") or "") for row in rows]
        if actual != expected_organisation_numbers:
            raise ValueError("optional website crawler returned missing, duplicated, or reordered profiles")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or not isinstance(report.get("requests"), int) or report["requests"] < 0:
            raise ValueError("optional website crawler returned an invalid request report")
        return report, None
    except (subprocess.CalledProcessError, OSError, ValueError, json.JSONDecodeError) as exc:
        # A broken optional crawler must not erase the official registry result
        # or prevent terminal envelopes for the supplied batch.
        write_jsonl(crawled_profiles, read_jsonl(base_profiles))
        write_jsonl(events_path, [])
        message = f"{type(exc).__name__}: {str(exc)[:240]}"
        report = {
            "status": "failed",
            "reason": message,
            "profiles": len(expected_organisation_numbers),
            "website_seeds_selected": 0,
            "requests": 0,
            "request_count_complete": False,
            "crawl_complete": False,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report, message


def crawler_runtime_available() -> bool:
    """Scrapy is an optional enhancement, never a prerequisite to score."""
    return importlib.util.find_spec("scrapy") is not None


def third_party_cost_report(places_enabled: bool) -> tuple[float | None, str]:
    """Never report a zero estimate when a paid connector was actually called."""
    if places_enabled:
        return None, "unknown_requires_provider_billing_data"
    return 0.0, "zero_paid_api_calls"


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
            effective_at=(observation.get("metrics") or {}).get("published_at") if observation.get("signal_type") == "public_post" else None,
        )


def attach_nav_evidence(profiles: list[dict], observations: list[dict]) -> None:
    by_org = {str(profile["organisation_number"]): profile for profile in profiles}
    for observation in observations:
        profile = by_org.get(str(observation.get("organisation_number") or ""))
        proof_record = (observation.get("identity_proof") or [{}])[0]
        proof = proof_record.get("value") or {}
        if not profile or not (
            observation.get("platform") == "job_board"
            and observation.get("signal_type") == "job_posting"
            and observation.get("exact_entity")
            and observation.get("acquisition_mode") == "official_api"
            and observation.get("rights_status") == "approved"
            and float(proof.get("match_score") or 0) == 100.0
            and proof_record.get("type") == "exact_normalized_name_municipality_match"
            and proof.get("match_method") == "exact_normalized_name+municipality"
        ):
            continue
        profile.setdefault("evidence", {})["workforce_jobs"] = evidence(
            "workforce_jobs",
            "available",
            "official_nav_stilling_feed",
            str(observation["source_url"]),
            value={
                "active_job_count": (observation.get("metrics") or {}).get("active_job_count"),
                "snapshot_as_of": observation.get("retrieved_at"),
                "job_titles": observation.get("evidence_span"),
                "identity_proof": observation.get("identity_proof") or [],
                "strategy": observation.get("strategy"),
            },
            retrieved_at=observation.get("retrieved_at"),
            as_of=observation.get("retrieved_at"),
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
    except Exception:
        # This cache is optional and local; a malformed but syntactically valid
        # JSON shape must be just as harmless as a missing or truncated file.
        return None
    if not isinstance(metadata, dict) or metadata.get("feed_exhausted") is not True:
        return None
    return metadata


def run(args: argparse.Namespace) -> None:
    run_started_monotonic = time.monotonic()
    run_started_at = utc_now()
    # Resolve caller-supplied paths before invoking children with cwd=ROOT.
    # Relative paths therefore retain their meaning regardless of launch cwd.
    for name in ("organisations", "bulk", "output", "report", "viewer", "out_dir", "nav_snapshot", "previous_profiles"):
        value = getattr(args, name, None)
        if value:
            setattr(args, name, str(Path(value).expanduser().resolve()))

    configured_expected_count = args.expected_count
    organisations = read_organisation_inputs(args.organisations)
    if not organisations:
        raise SystemExit("The evaluator organisation batch must contain at least one valid organisation number")
    args.expected_count = len(organisations)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    org_numbers = [item["organisation_number"] for item in organisations]
    previous_path = Path(args.previous_profiles) if args.previous_profiles else out / "profiles.jsonl"
    previous_profiles, previous_profiles_warning = load_previous_profile_map(
        previous_path,
        org_numbers,
        explicit=bool(args.previous_profiles),
    )
    # A refresh reuses the output directory for prior snapshots, but must not
    # reuse Scrapy's request-fingerprint/job state or old events as if they
    # were fresh evidence. Reuse is limited to retrying this exact run ID/batch.
    crawl_key = crawl_artifact_key(args.run_id, org_numbers, run_started_at)
    base_profiles = out / "base-profiles.jsonl"
    base_output = out / "base-envelopes.jsonl"
    base_report = out / "base-report.json"
    profiles_snapshot = out / "profiles.jsonl"
    crawled_profiles = out / f"crawled-profiles-{crawl_key}.jsonl"
    crawl_events = out / f"crawl-events-{crawl_key}.jsonl"
    crawl_report = out / f"crawl-report-{crawl_key}.json"
    crawler_jobdir = out / f"crawl-job-{crawl_key}"
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
        "--modules", args.modules,
    )
    subprocess.run(base, cwd=ROOT, check=True)

    connector_errors: list[dict[str, str]] = []
    request_accounting_complete = True
    if crawler_runtime_available():
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
        crawl_summary, crawl_error = run_optional_crawler(
            crawl,
            base_profiles=base_profiles,
            crawled_profiles=crawled_profiles,
            events_path=crawl_events,
            report_path=crawl_report,
            expected_organisation_numbers=org_numbers,
        )
        if crawl_error:
            request_accounting_complete = False
            connector_errors.append({"type": "connector_error", "field": "website", "message": crawl_error})
    else:
        # run_competition_batch already fetches and identity-gates the
        # registry-linked homepage. Preserve that evidence when the optional
        # breadth crawler is not installed, rather than aborting the batch.
        write_jsonl(crawled_profiles, read_jsonl(base_profiles))
        write_jsonl(crawl_events, [])
        crawl_report.write_text(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": "optional Scrapy runtime is not installed",
                    "profiles": len(organisations),
                    "website_seeds_selected": 0,
                    "requests": 0,
                    "crawl_complete": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

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
    site_errors = []
    site_reports: list[dict] = []
    site_observations: list[dict] = []
    for extraction_command, observations_path, site_report_path in (
        (site_activity_command, site_activity_path, site_activity_report_path),
        (site_news_command, site_news_path, site_news_report_path),
    ):
        try:
            subprocess.run(extraction_command, cwd=ROOT, check=True)
            observations, site_report = read_optional_site_outputs(
                observations_path,
                site_report_path,
                expected_profiles=len(profiles),
            )
            site_observations.extend(observations)
            site_reports.append(site_report)
        except Exception as exc:
            message = f"{Path(extraction_command[1]).name}: {type(exc).__name__}: {str(exc)[:200]}"
            site_errors.append(message)
            connector_errors.append({
                "type": "connector_error",
                "field": "company_site_activity" if observations_path == site_activity_path else "company_site_news",
                "message": message,
            })
            write_jsonl(observations_path, [])
            failed_report = {"status": "failed", "profiles": len(profiles), "observations": 0, "error": site_errors[-1]}
            site_report_path.parent.mkdir(parents=True, exist_ok=True)
            site_report_path.write_text(json.dumps(failed_report, indent=2) + "\n", encoding="utf-8")
            site_reports.append(failed_report)
    attach_company_site_evidence(profiles, site_observations)
    site_summary = {
        "surface": site_reports[0],
        "activity": site_reports[1],
        "errors": site_errors,
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
        try:
            subprocess.run(nav_command, cwd=ROOT, check=True)
            attach_nav_evidence(profiles, read_jsonl(nav_observations_path))
            nav_summary = json.loads(nav_report_path.read_text(encoding="utf-8"))
            nav_summary["shipped_snapshot"] = str(nav_snapshot)
            nav_summary["snapshot_metadata"] = nav_metadata
        except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
            nav_summary = {"connector": "nav_stilling_feed_workforce_v1", "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
            connector_errors.append({"type": "connector_error", "field": "workforce_jobs", "message": nav_summary["error"]})

    places_summary: dict | None = None
    external_paths: list[str] = [str(site_activity_path), str(site_news_path)]
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    rights_approved = os.environ.get("SIGNALPOST_GOOGLE_PLACES_RIGHTS", "").casefold() == "approved"
    places_enabled = bool(api_key and rights_approved)
    if places_enabled:
        try:
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
        except Exception as exc:
            places_summary = {"connector": "google_places_api_new_v1", "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
            request_accounting_complete = False
            connector_errors.append({"type": "connector_error", "field": "places_identity", "message": places_summary["error"]})
    else:
        places_summary = {
            "connector": "google_places_api_new_v1",
            "status": "not_run",
            "reason": "requires GOOGLE_PLACES_API_KEY and SIGNALPOST_GOOGLE_PLACES_RIGHTS=approved",
        }

    # Public Nominatim is not suitable for recurring official evaluation runs:
    # its policy treats periodic app requests as bulk geocoding and restricts
    # regularly repeated scripts to 4 requests/minute. Keep it out of this
    # reproducible entrypoint; the standalone connector is for one-time local
    # research only.
    osm_summary = {
        "connector": "osm_nominatim",
        "status": "not_run",
        "reason": "omitted from recurring official runs under the public Nominatim usage policy",
    }

    third_party_cost_usd, third_party_cost_status = third_party_cost_report(places_enabled)

    write_jsonl(profiles_snapshot, profiles)
    viewer_path = Path(args.viewer)
    viewer_command = command("scripts/build_prototype.py", "--input", str(profiles_snapshot), "--output", str(viewer_path))
    if external_paths:
        viewer_command.extend(["--external-observations", *external_paths])
    viewer_error = None
    try:
        subprocess.run(viewer_command, cwd=ROOT, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        viewer_error = f"{type(exc).__name__}: {str(exc)[:240]}"
    if viewer_error is None and (not viewer_path.is_file() or viewer_path.stat().st_size == 0):
        viewer_error = "viewer command succeeded but did not produce a nonempty artifact"

    run_completed_at = utc_now()
    contract_rows = []
    for profile in profiles:
        metric = profile.get("run_metrics") or {}
        profile_errors = [
            {
                "type": "source_error",
                "field": field,
                "source_url": record.get("source_url"),
                "message": str(record.get("note") or "Source request failed")[:300],
            }
            for field, record in (profile.get("evidence") or {}).items()
            if isinstance(record, dict) and record.get("status") == "source_error"
        ]
        profile_errors.extend(connector_errors)
        contract_rows.append(profile_to_contract(
            profile,
            run_id=args.run_id,
            started_at=run_started_at,
            completed_at=run_completed_at,
            previous_profile=previous_profiles.get(str(profile["organisation_number"])),
            operations={
                "requests": metric.get("requests", 0),
                "runtime_ms": sum(metric.get("latencies_ms") or []),
                "third_party_cost_usd": third_party_cost_usd,
            },
            errors=profile_errors,
        ))
    output_path = Path(args.output)
    write_jsonl(output_path, contract_rows)

    base_summary = json.loads(base_report.read_text(encoding="utf-8"))
    base_operations = base_summary.get("operations") if isinstance(base_summary, dict) else None
    request_accounting_complete = (
        request_accounting_complete
        and isinstance(base_operations, dict)
        and base_operations.get("request_count_complete") is True
    )
    crawl_summary = json.loads(crawl_report.read_text(encoding="utf-8"))
    produced = [row.get("organisation_number") for row in contract_rows]
    expected = [row["organisation_number"] for row in organisations]
    contract_validation = validate_output_envelopes(contract_rows, expected)
    validation = {
        "exact_expected_count": len(contract_rows) == len(expected),
        "unique_organisation_numbers": len(produced) == len(set(produced)),
        "preserves_input_order": produced == expected,
        "terminal_runs": all((row.get("run") or {}).get("terminal_status") in {"completed", "completed_with_errors"} for row in contract_rows),
        "output_contract_valid": contract_validation["passed"],
        "viewer_artifact_generated": viewer_error is None,
    }
    elapsed_seconds = round(time.monotonic() - run_started_monotonic, 3)
    request_count = int((base_summary.get("operations") or {}).get("requests") or 0)
    request_count += int(crawl_summary.get("requests") or 0)
    request_count += int((places_summary or {}).get("requests") or 0)
    request_limit = int(args.max_requests_per_100 * len(expected) / 100)
    resource_budget = {
        "time_limit_seconds": args.time_budget_seconds,
        "elapsed_seconds": elapsed_seconds,
        "time_within_limit": elapsed_seconds <= args.time_budget_seconds,
        "request_limit": request_limit,
        "requests_observed": request_count,
        "requests_within_limit": request_count <= request_limit,
        "request_count_complete": request_accounting_complete,
        "passed": elapsed_seconds <= args.time_budget_seconds and request_count <= request_limit and request_accounting_complete,
        "request_count_note": "Includes official HTTP attempts, website crawler requests, and enabled Google Places calls reported by their connector; DNS lookups are not HTTP requests. The standalone Nominatim research connector is not part of this runner.",
    }
    report = {
        "runner": "signalpost_submission_v1",
        "run_id": args.run_id,
        "expected_count": len(expected),
        "emitted_envelopes": len(contract_rows),
        "started_at": run_started_at,
        "completed_at": run_completed_at,
        "validation": {"passed": all(validation.values()), "checks": validation},
        "contract_validation": contract_validation,
        "resource_budget": resource_budget,
        "base": base_summary,
        "crawl": crawl_summary,
        "company_site_observations": site_summary,
        "nav_workforce": nav_summary,
        "google_places": places_summary,
        "osm_places": osm_summary,
        "third_party_cost_usd": third_party_cost_usd,
        "third_party_cost_status": third_party_cost_status,
        "output": str(output_path),
        "viewer": str(viewer_path),
        "viewer_status": "generated" if viewer_error is None else "failed",
        "viewer_error": viewer_error,
        "previous_profiles_source": str(previous_path) if previous_path.is_file() and previous_profiles_warning is None else None,
        "previous_profiles_count": len(previous_profiles),
        "previous_profiles_warning": previous_profiles_warning,
        "configured_expected_count": configured_expected_count,
        "expected_count_warning": (
            f"Configured count {configured_expected_count} differed from input count {len(organisations)}; processed every supplied row."
            if configured_expected_count != len(organisations) else None
        ),
        "claim_boundary": (
            "Official Brreg evidence plus robots-aware crawling of registry-linked company sites. "
            "Unqualified third-party discovery sources are not published by this runner."
        ),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"emitted_envelopes": len(contract_rows), "validation": report["validation"], "report": str(report_path)}, indent=2))
    if not report["validation"]["passed"] or not report["resource_budget"]["passed"]:
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
    parser.add_argument("--previous-profiles", help="Optional prior-run JSONL profiles for refresh diffs")
    parser.add_argument("--out-dir", default="out/submission-run")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--crawl-concurrency", type=int, default=16)
    parser.add_argument("--crawl-per-domain", type=int, default=2)
    parser.add_argument("--nav-snapshot", default="out/nav-feed-snapshot.json.gz")
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,financials,financial_history,roles,group,locations,website")
    parser.add_argument("--nav-threshold", type=int, default=100, choices=range(100, 101))
    parser.add_argument("--places-workers", type=int, default=4)
    parser.add_argument("--places-budget", type=float, default=600.0)
    parser.add_argument("--time-budget-seconds", type=int, default=45 * 60)
    parser.add_argument("--max-requests-per-100", type=int, default=2000)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
