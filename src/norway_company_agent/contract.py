"""Conversion of the existing profile model to OUTPUT_CONTRACT."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .refresh import diff_profile


AVAILABILITY = {
    "available",
    "not_available",
    "blocked",
    "not_applicable",
    "ambiguous",
    "failed",
}


def _json_safe(value: Any) -> Any:
    """Return a recursive, strict-JSON-safe representation of ``value``.

    Evaluator output must survive arbitrary source payloads. In particular,
    ``sort_keys=True`` cannot compare mixed key types and JSON's permissive
    NaN/Infinity tokens are not valid strict JSON.
    """
    if isinstance(value, dict):
        return {
            str(k): _json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]

    if isinstance(value, (set, frozenset)):
        items = [_json_safe(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if value is None or isinstance(value, (str, int, bool)):
        return value

    return str(value)


def _dumps(value: Any, **kwargs) -> str:
    """Serialize values deterministically as JSON."""
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        default=str,
        **kwargs,
    )


def _stable(prefix: str, value: Any) -> str:
    """Generate a deterministic ID from a JSON-serializable value."""
    encoded = _dumps(value, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def availability(status: Any) -> str:
    """Map internal evidence statuses to the six public contract states."""
    return {
        "available": "available",
        "not_applicable": "not_applicable",
        "blocked": "blocked",
        "not_found": "not_available",
        # An unattempted source is not proof that no information exists.
        # `failed` is the honest terminal contract state unless the caller can
        # establish that the field is genuinely not applicable.
        "not_fetched": "failed",
        "source_error": "failed",
        "failed": "failed",
        "ambiguous": "ambiguous",
    }.get(str(status), "failed")


map_availability = availability


def validate_output_envelopes(
    envelopes: list[dict[str, Any]],
    expected_organisation_numbers: list[str],
) -> dict[str, Any]:
    """Validate the submission payload, not just its row count.

    This deliberately validates relationships between claims and evidence as
    well as required envelope sections. A syntactically valid JSONL file can
    still be unscoreable if its citations dangle or its entity IDs drift.
    """
    errors: list[dict[str, str]] = []
    expected = [str(value) for value in expected_organisation_numbers]
    actual = [str(item.get("organisation_number") or "") if isinstance(item, dict) else "" for item in envelopes]
    allowed_terminal_runs = {"completed", "completed_with_errors"}

    def fail(index: int, path: str, reason: str) -> None:
        org = actual[index] if index < len(actual) else ""
        errors.append({"organisation_number": org, "path": path, "reason": reason})

    for index, row in enumerate(envelopes):
        if not isinstance(row, dict):
            fail(index, "$", "envelope is not an object")
            continue
        required = {
            "organisation_number", "legal_identity", "public_brand", "run",
            "claims", "evidence", "source_snapshots", "refresh", "changes",
            "errors", "operations",
        }
        missing = sorted(required - row.keys())
        if missing:
            fail(index, "$", f"missing required sections: {', '.join(missing)}")
        org = str(row.get("organisation_number") or "")
        if not re.fullmatch(r"\d{9}", org):
            fail(index, "organisation_number", "must be exactly nine digits")
        legal = row.get("legal_identity")
        if not isinstance(legal, dict) or str(legal.get("organisation_number") or "") != org:
            fail(index, "legal_identity.organisation_number", "must match the envelope organisation number")

        run = row.get("run")
        if not isinstance(run, dict):
            fail(index, "run", "must be an object")
            run = {}
        if run.get("terminal_status") not in allowed_terminal_runs:
            fail(index, "run.terminal_status", "must be a completed terminal status")
        parsed_times: dict[str, datetime] = {}
        for field in ("started_at", "completed_at"):
            raw = run.get(field)
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone missing")
                parsed_times[field] = parsed
            except (TypeError, ValueError):
                fail(index, f"run.{field}", "must be an ISO-8601 timestamp with timezone")
        if len(parsed_times) == 2 and parsed_times["completed_at"] < parsed_times["started_at"]:
            fail(index, "run.completed_at", "must not precede started_at")

        raw_evidence = row.get("evidence")
        evidence_rows = raw_evidence if isinstance(raw_evidence, list) else []
        if not isinstance(raw_evidence, list):
            fail(index, "evidence", "must be an array")
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for evidence_index, item in enumerate(evidence_rows):
            path = f"evidence[{evidence_index}]"
            if not isinstance(item, dict):
                fail(index, path, "must be an object")
                continue
            evidence_id = str(item.get("id") or "")
            if not evidence_id or evidence_id in evidence_by_id:
                fail(index, f"{path}.id", "must be nonempty and unique within the envelope")
            else:
                evidence_by_id[evidence_id] = item
            raw_url = str(item.get("source_url") or "")
            try:
                parsed_url = urlparse(raw_url)
                url_host = parsed_url.hostname
            except ValueError:
                parsed_url = None
                url_host = None
                fail(index, f"{path}.source_url", "must be a valid public HTTP(S) source URL")
            if parsed_url is not None and (parsed_url.scheme not in {"http", "https"} or not url_host):
                fail(index, f"{path}.source_url", "must be a public HTTP(S) source URL")
            elif parsed_url is not None and (parsed_url.username is not None or parsed_url.password is not None):
                fail(index, f"{path}.source_url", "must not embed credentials")
            elif parsed_url is not None and (url_host.casefold().rstrip(".") == "localhost" or url_host.casefold().endswith((".localhost", ".local"))):
                fail(index, f"{path}.source_url", "must not reference a local host")
            elif parsed_url is not None:
                try:
                    ip_address = ipaddress.ip_address(url_host)
                except ValueError:
                    ip_address = None
                if ip_address is not None and not ip_address.is_global:
                    fail(index, f"{path}.source_url", "must not reference a private or reserved address")
            if not item.get("source_class"):
                fail(index, f"{path}.source_class", "must identify the source class")
            if not item.get("extraction_method"):
                fail(index, f"{path}.extraction_method", "must identify the extraction method")
            if not item.get("claim_span"):
                fail(index, f"{path}.claim_span", "must retain a source span or an explicit absence/error note")
            if "content_sha256" not in item:
                fail(index, f"{path}.content_sha256", "must be present; use null only when no source bytes exist")
            retrieved = item.get("retrieved_at")
            try:
                parsed = datetime.fromisoformat(str(retrieved).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone missing")
                if "completed_at" in parsed_times and parsed > parsed_times["completed_at"]:
                    fail(index, f"{path}.retrieved_at", "must not be later than the envelope completion time")
            except (TypeError, ValueError):
                fail(index, f"{path}.retrieved_at", "must be an ISO-8601 timestamp with timezone")
            digest = item.get("content_sha256")
            if digest is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(digest)):
                fail(index, f"{path}.content_sha256", "must be a SHA-256 hex digest or null when no source bytes exist")

        raw_claims = row.get("claims")
        claims = raw_claims if isinstance(raw_claims, list) else []
        if not isinstance(raw_claims, list):
            fail(index, "claims", "must be an array")
        seen_fields: set[str] = set()
        for claim_index, claim in enumerate(claims):
            path = f"claims[{claim_index}]"
            if not isinstance(claim, dict):
                fail(index, path, "must be an object")
                continue
            field = str(claim.get("field") or "")
            if not field or field in seen_fields:
                fail(index, f"{path}.field", "must be nonempty and unique within the envelope")
            seen_fields.add(field)
            if claim.get("availability") not in AVAILABILITY:
                fail(index, f"{path}.availability", "is not an allowed OUTPUT_CONTRACT state")
            confidence = claim.get("confidence")
            if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                fail(index, f"{path}.confidence", "must be a finite number from 0 to 1")
            references = claim.get("evidence_ids")
            if not isinstance(references, list):
                fail(index, f"{path}.evidence_ids", "must be an array")
                references = []
            for reference in references:
                if reference not in evidence_by_id:
                    fail(index, f"{path}.evidence_ids", f"references missing evidence id {reference!r}")
            if field in {"financials", "financial_history"} and claim.get("availability") == "available":
                value = claim.get("value")
                period_items = value.get("records" if field == "financials" else "years") if isinstance(value, dict) else None
                if period_items and any(
                    not evidence_by_id.get(str(reference), {}).get("reporting_period")
                    for reference in references
                ):
                    fail(index, path, "available financial claims require an evidence reporting_period")
            if claim.get("availability") == "available" and (claim.get("value") is None or not references):
                fail(index, path, "available claims require a non-null value and supporting evidence")

        brand = row.get("public_brand")
        if not isinstance(brand, dict) or not isinstance(brand.get("verified"), bool):
            fail(index, "public_brand", "must report an explicit verified boolean")
        elif brand["verified"]:
            brand_evidence = evidence_by_id.get(str(brand.get("evidence_id") or ""))
            website_claim = next((claim for claim in claims if isinstance(claim, dict) and claim.get("field") == "website"), None)
            site_value = (website_claim or {}).get("value") or {}
            assessment = site_value.get("identity_assessment") if isinstance(site_value, dict) else None
            if not brand.get("name") or not brand.get("website_url") or not brand_evidence or not isinstance(assessment, dict) or assessment.get("publishable") is not True:
                fail(index, "public_brand", "verified brand must link to positively identity-gated website evidence")

        try:
            json.dumps(row, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            fail(index, "$", "must be strict JSON without NaN or unsupported values")

    checks = {
        "nonempty_input_batch": bool(expected),
        "one_envelope_per_input": len(envelopes) == len(expected),
        "unique_organisation_numbers": len(actual) == len(set(actual)),
        "input_order_preserved": actual == expected,
        "envelope_contract_valid": not errors,
    }
    return {"passed": all(checks.values()), "checks": checks, "error_count": len(errors), "errors": errors[:50]}


def _span(record: dict[str, Any]) -> str | None:
    """Return a bounded human-readable evidence span."""
    if record.get("note"):
        return str(record["note"])

    value = record.get("value")

    if value is None:
        return None

    if isinstance(value, str):
        return value[:1000]

    return _dumps(value)[:1000]


def _reporting_period(record: dict[str, Any], field: str) -> str | None:
    """Expose the actual filing/publication period when the source has one."""
    explicit = record.get("as_of") or record.get("effective_at")
    if explicit:
        return str(explicit)
    value = record.get("value")
    if not isinstance(value, dict):
        return None
    if field == "financials":
        records = value.get("records") or []
        if not records:
            return None
        periods = []
        for row in records:
            if not isinstance(row, dict):
                return None
            period = row.get("period")
            if not isinstance(period, dict):
                return None
            start = period.get("fraDato") or period.get("from") or period.get("start")
            end = period.get("tilDato") or period.get("to") or period.get("end")
            if not start or not end:
                return None
            periods.append(f"{start}/{end}")
        return "; ".join(sorted(set(periods)))
    if field == "financial_history":
        years = value.get("years") or [
            item.get("year") for item in value.get("pdfs") or [] if isinstance(item, dict)
        ]
        normalized = sorted({str(year) for year in years if year is not None})
        return ", ".join(normalized) or None
    return None


def profile_to_contract(
    profile: dict[str, Any],
    *,
    run_id: str = "",
    started_at: str | None = None,
    completed_at: str | None = None,
    previous_profile: dict[str, Any] | None = None,
    operations: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, JSON-serialisable OUTPUT_CONTRACT object."""

    evidence_out: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    evidence_ids: dict[str, str] = {}

    profile_evidence = profile.get("evidence") or {}

    for field in sorted(profile_evidence):
        record = profile_evidence[field] or {}

        if not isinstance(record, dict):
            continue

        # Evidence identity must remain stable when the same
        # source/content is retrieved again at a different time.
        #
        # retrieved_at is deliberately excluded from the identity.
        # content_sha256 is preferred as the content identity.
        content_hash = record.get("content_sha256")

        ev_identity = {
            "org": profile.get("organisation_number"),
            "field": field,
            "url": record.get("source_url"),
            "hash": content_hash,
        }

        # If there is no content hash, fall back to the actual
        # content fields available in the evidence record.
        if not content_hash:
            ev_identity["value"] = record.get("value")
            ev_identity["note"] = record.get("note")

        ev_id = _stable("ev", ev_identity)

        evidence_out.append(
            {
                "id": ev_id,
                "source_url": record.get("source_url") or "",
                "source_class": (
                    record.get("source_class")
                    or record.get("source_type")
                    or "unknown"
                ),
                "retrieved_at": record.get("retrieved_at") or "",
                "reporting_period": _reporting_period(record, field),
                "extraction_method": record.get("extraction_method") or record.get("source_type") or record.get("source_class") or "unknown",
                "content_sha256": record.get("content_sha256"),
                "claim_span": _span(record),
            }
        )

        evidence_ids[field] = ev_id

        state = availability(record.get("status"))
        claim_value = record.get("value")

        # A registry-linked website is not automatically attributable
        # to the legal entity. If identity verification fails, expose
        # the website claim as ambiguous.
        if field == "website" and state == "available":
            value = record.get("value") or {}

            if isinstance(value, dict):
                assessment = value.get("identity_assessment") or {}
            else:
                assessment = {}

            if assessment.get("publishable") is not True:
                state = "ambiguous"
                claim_value = {
                    "candidate_url": record.get("source_url"),
                    "identity_status": assessment.get("status"),
                    "identity_method": assessment.get("method"),
                    "note": (
                        "Registry-linked website captured but not "
                        "positively attributed to this legal entity."
                    ),
                }

        confidence = (
            0.0
            if state
            in {
                "failed",
                "not_available",
                "blocked",
                "not_applicable",
            }
            else 1.0
        )

        if state == "ambiguous":
            confidence = 0.0

        claims.append(
            {
                "field": field,
                "value": claim_value,
                "availability": state,
                "confidence": confidence,
                "evidence_ids": [ev_id],
            }
        )

    registry = (
        profile_evidence.get("registry_live")
        or profile_evidence.get("registry")
        or {}
    )

    registry_value = registry.get("value") or {}

    website = profile_evidence.get("website") or {}
    website_value = website.get("value") or {}
    website_identity = website_value.get("identity_assessment") if isinstance(website_value, dict) else None
    verified_website = (
        website.get("status") == "available"
        and isinstance(website_identity, dict)
        and website_identity.get("publishable") is True
    )

    if registry.get("status") == "available":
        name = (
            profile.get("name")
            or registry_value.get("name")
            or "The company"
        )

        industry = (
            registry_value.get("industry")
            or profile.get("industry_label")
        )

        if isinstance(website_value, dict):
            identity_assessment = (
                website_value.get("identity_assessment") or {}
            )
            description = (
                website_value.get("description")
                if identity_assessment.get("publishable") is True
                else None
            )
        else:
            description = None

        if industry:
            if isinstance(industry, dict):
                registered_activity = industry.get("beskrivelse")
            else:
                registered_activity = industry
        else:
            registered_activity = None

        if description:
            what_it_does = description
        elif registered_activity:
            what_it_does = (
                f"Registered activity: {registered_activity}."
            )
        else:
            what_it_does = (
                "No verified business description was available."
            )

        unknown = []

        financials = profile_evidence.get("financials") or {}
        financial_history = profile_evidence.get("financial_history") or {}
        website_evidence = profile_evidence.get("website") or {}

        if financials.get("status") != "available":
            unknown.append(
                "No verified annual-account record was returned in this run."
            )
        if financial_history.get("status") != "available":
            unknown.append(
                "No verified filing-year history was returned in this run."
            )
        if not verified_website:
            unknown.append(
                "No verified company website was attributed to this legal entity in this run."
            )
        if "workforce_jobs" not in profile_evidence:
            unknown.append(
                "No exact-company job posting was verified in this run."
            )
        if "places_identity" not in profile_evidence:
            unknown.append(
                "No exact-company place record was verified in this run."
            )
        if not unknown:
            unknown.append(
                "All tracked official families returned a record in this run; "
                "external jobs and place coverage remain opportunity-dependent."
            )

        synthesis = {
            "what_it_does": what_it_does,
            "what_changed": (
                "No prior snapshot was supplied for comparison."
                if previous_profile is None
                else "Prior snapshot was an output envelope; raw field comparison was unavailable."
                if not isinstance(previous_profile.get("evidence"), dict)
                else (
                    [
                        item
                        for item in diff_profile(
                            previous_profile,
                            profile,
                        )
                    ]
                    or "No tracked facts changed."
                )
            ),
            "what_is_unknown": unknown,
        }

        supporting = [
            evidence_id
            for key, evidence_id in evidence_ids.items()
            if key
            in {
                "registry",
                "registry_live",
                "website",
                "financials",
            }
            and (key != "website" or verified_website)
            and (profile_evidence.get(key) or {}).get("status") == "available"
        ]

        claims.append(
            {
                "field": "synthesis",
                "value": synthesis,
                "availability": "available",
                "confidence": 1.0,
                "evidence_ids": supporting,
            }
        )

    run_status = (
        "completed"
        if not errors
        and not any(
            claim["availability"] == "failed"
            for claim in claims
        )
        else "completed_with_errors"
    )

    previous_is_profile = previous_profile is not None and isinstance(previous_profile.get("evidence"), dict)
    changes = diff_profile(previous_profile, profile) if previous_is_profile else []

    registry_record = (
        profile_evidence.get("registry_live")
        or profile_evidence.get("registry")
        or {}
    )
    registry_value = registry_record.get("value") or {}
    website_record = profile_evidence.get("website") or {}
    website_value = website_record.get("value") or {}
    website_identity = website_value.get("identity_assessment") or {}
    verified_site = (
        website_record.get("status") == "available"
        and website_identity.get("publishable") is True
    )
    flat_address = {
        str(key).removeprefix("forretningsadresse."): value
        for key, value in registry_value.items()
        if str(key).startswith("forretningsadresse.") and value not in (None, "")
    }
    legal_identity = {
        "organisation_number": str(profile.get("organisation_number") or ""),
        "legal_name": profile.get("name") or registry_value.get("navn"),
        "legal_form": profile.get("legal_form") or registry_value.get("organisasjonsform.kode"),
        "registered_address": registry_value.get("forretningsadresse") or registry_value.get("business_address") or flat_address or None,
        "municipality": profile.get("municipality") or registry_value.get("forretningsadresse.kommune"),
        "municipality_number": profile.get("municipality_number") or registry_value.get("forretningsadresse.kommunenummer"),
        "industry_code": profile.get("industry_code") or registry_value.get("naeringskode1.kode"),
        "industry_label": profile.get("industry_label") or registry_value.get("naeringskode1.beskrivelse"),
    }
    public_brand = {
        "name": website_value.get("title") if verified_site else None,
        "website_url": website_value.get("final_url") or website_record.get("source_url") if verified_site else None,
        "verified": verified_site,
        "evidence_id": evidence_ids.get("website") if verified_site else None,
    }

    def snapshot_references(source_profile: dict[str, Any] | None) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        source_profile = source_profile or {}
        raw_evidence = source_profile.get("evidence", {})
        if isinstance(raw_evidence, list):
            raw_evidence = {
                str(item.get("field") or item.get("id") or index): item
                for index, item in enumerate(raw_evidence)
                if isinstance(item, dict)
            }
        if not raw_evidence and isinstance(source_profile.get("source_snapshots"), list):
            raw_evidence = {
                str(item.get("field") or index): item
                for index, item in enumerate(source_profile["source_snapshots"])
                if isinstance(item, dict)
            }
        if not isinstance(raw_evidence, dict):
            raw_evidence = {}
        for field, record in sorted(raw_evidence.items()):
            if not isinstance(record, dict):
                continue
            identity = {
                "org": (source_profile or {}).get("organisation_number"),
                "field": field,
                "url": record.get("source_url"),
                "hash": record.get("content_sha256"),
            }
            if not record.get("content_sha256") and "claim_span" not in record and "id" not in record:
                identity["value"] = record.get("value")
                identity["note"] = record.get("note")
            references.append({
                "id": _stable("ev", identity),
                "field": str(field),
                "source_url": record.get("source_url") or "",
                "retrieved_at": record.get("retrieved_at") or "",
                "reporting_period": _reporting_period(record, str(field)),
                "content_sha256": record.get("content_sha256"),
                "source_class": record.get("source_class") or record.get("source_type") or "unknown",
                "extraction_method": record.get("extraction_method") or record.get("source_type") or record.get("source_class") or "unknown",
                "claim_span": _span(record),
            })
        return references

    current_snapshots = snapshot_references(profile)
    previous_snapshots = snapshot_references(previous_profile)
    stable_snapshot = lambda refs: [
        {key: value for key, value in item.items() if key not in {"retrieved_at", "claim_span"}}
        for item in refs
    ]
    current_snapshot_id = _stable("snapshot", stable_snapshot(current_snapshots))
    previous_snapshot_id = _stable("snapshot", stable_snapshot(previous_snapshots)) if previous_profile is not None else None

    output_errors = list(errors or [])
    if previous_profile is not None and not previous_is_profile:
        output_errors.append({
            "type": "refresh_comparison_unavailable",
            "message": "Prior input was an output envelope; raw profile fields were unavailable for material-change comparison.",
        })
    if output_errors:
        run_status = "completed_with_errors"

    op = {
        "requests": 0,
        "runtime_ms": 0,
        "third_party_cost_usd": 0,
    }

    if operations:
        for key in op:
            if key in operations:
                op[key] = operations[key]

    return _json_safe({
        "organisation_number": str(
            profile.get("organisation_number") or ""
        ),
        "legal_identity": legal_identity,
        "public_brand": public_brand,
        "run": {
            "run_id": run_id,
            "started_at": started_at or "",
            "completed_at": completed_at or "",
            "terminal_status": run_status,
        },
        "claims": claims,
        "evidence": evidence_out,
        "source_snapshots": current_snapshots,
        "refresh": {
            "mode": "comparison" if previous_is_profile else "comparison_unavailable" if previous_profile is not None else "initial",
            "previous_snapshot_id": previous_snapshot_id,
            "current_snapshot_id": current_snapshot_id,
            "material_change_count": len(changes),
            "previous_evidence": previous_snapshots,
        },
        "changes": changes,
        "errors": output_errors,
        "operations": op,
    })


to_output_contract = profile_to_contract
convert_profile = profile_to_contract
convert_to_output_contract = profile_to_contract
