"""Conversion of the existing profile model to OUTPUT_CONTRACT."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .refresh import diff_profile

AVAILABILITY = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}


def _stringify_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _stringify_keys(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify_keys(item) for item in value]
    return value


def _dumps(value: Any, **kwargs) -> str:
    return json.dumps(_stringify_keys(value), ensure_ascii=False, sort_keys=True, default=str, **kwargs)


def _stable(prefix: str, value: Any) -> str:
    encoded = _dumps(value, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def availability(status: Any) -> str:
    """Map internal evidence statuses to the six public contract states."""
    return {"available": "available", "not_applicable": "not_applicable", "blocked": "blocked",
            "not_found": "not_available", "not_fetched": "not_available", "source_error": "failed",
            "failed": "failed", "ambiguous": "ambiguous"}.get(str(status), "failed")


map_availability = availability


def _span(record: dict[str, Any]) -> str | None:
    if record.get("note"):
        return str(record["note"])
    value = record.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        return value[:1000]
    return _dumps(value)[:1000]


def profile_to_contract(profile: dict[str, Any], *, run_id: str = "", started_at: str | None = None,
                        completed_at: str | None = None, previous_profile: dict[str, Any] | None = None,
                        operations: dict[str, Any] | None = None, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a deterministic, JSON-serialisable OUTPUT_CONTRACT object."""
    evidence_out: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    evidence_ids: dict[str, str] = {}
    for field in sorted((profile.get("evidence") or {})):
        record = profile["evidence"][field] or {}
        if not isinstance(record, dict):
            continue
        ev_id = _stable("ev", {"org": profile.get("organisation_number"), "field": field,
                               "url": record.get("source_url"), "retrieved_at": record.get("retrieved_at"),
                               "hash": record.get("content_sha256"), "value": record.get("value")})
        evidence_out.append({"id": ev_id, "source_url": record.get("source_url") or "",
            "source_class": record.get("source_class") or record.get("source_type") or "unknown",
            "retrieved_at": record.get("retrieved_at") or "", "content_sha256": record.get("content_sha256"),
             "claim_span": _span(record)})
        evidence_ids[field] = ev_id
        state = availability(record.get("status"))
        claim_value = record.get("value")
        if field == "website" and state == "available":
            assessment = (record.get("value") or {}).get("identity_assessment") or {}
            if assessment and not assessment.get("publishable"):
                state = "ambiguous"
                claim_value = {
                    "candidate_url": record.get("source_url"),
                    "identity_status": assessment.get("status"),
                    "identity_method": assessment.get("method"),
                    "note": "Registry-linked website captured but not attributed to this legal entity.",
                }
        confidence = 0.0 if state in {"failed", "not_available", "blocked", "not_applicable"} else 1.0
        if state == "ambiguous":
            confidence = 0.0
        claim = {"field": field, "value": claim_value, "availability": state,
                 "confidence": confidence, "evidence_ids": [ev_id]}
        claims.append(claim)
    registry = (profile.get("evidence") or {}).get("registry_live") or (profile.get("evidence") or {}).get("registry") or {}
    registry_value = registry.get("value") or {}
    website = (profile.get("evidence") or {}).get("website") or {}
    website_value = website.get("value") or {}
    if registry.get("status") == "available":
        name = profile.get("name") or registry_value.get("name") or "The company"
        industry = registry_value.get("industry") or profile.get("industry_label")
        description = website_value.get("description") if website_value.get("identity_assessment", {}).get("publishable", True) else None
        unknown = []
        if (profile.get("evidence") or {}).get("financials", {}).get("status") != "available":
            unknown.append("No verified annual-account record was returned in this run.")
        synthesis = {
            "what_it_does": description or (f"Registered activity: {industry.get('beskrivelse') if isinstance(industry, dict) else industry}." if industry else "No verified business description was available."),
            "what_changed": "No prior snapshot was supplied for comparison." if previous_profile is None else ([item for item in diff_profile(previous_profile, profile)] or "No tracked facts changed."),
            "what_is_unknown": unknown,
        }
        supporting = [value for key, value in evidence_ids.items() if key in {"registry", "registry_live", "website", "financials"}]
        claims.append({"field": "synthesis", "value": synthesis, "availability": "available", "confidence": 1.0, "evidence_ids": supporting})
    run_status = "completed" if not errors and not any(c["availability"] == "failed" for c in claims) else "completed_with_errors"
    changes = diff_profile(previous_profile, profile) if previous_profile is not None else []
    op = {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0}
    op.update({key: operations[key] for key in op if operations and key in operations})
    return {"organisation_number": str(profile.get("organisation_number") or ""),
            "run": {"run_id": run_id, "started_at": started_at or "", "completed_at": completed_at or "", "terminal_status": run_status},
            "claims": claims, "evidence": evidence_out, "changes": changes, "errors": list(errors or []), "operations": op}


to_output_contract = profile_to_contract
convert_profile = profile_to_contract
convert_to_output_contract = profile_to_contract