from __future__ import annotations

import hashlib
import json
import csv
import gzip
import io
from pathlib import Path
from typing import Any, Iterable

from .evidence import evidence, utc_now
from .official import accounting_obligation_assessment
from .sampling import iter_bulk


TERMINAL_STATES = {
    "complete",
    "not_applicable",
    "not_found",
    "blocked_policy",
    "blocked_robots",
    "source_error",
    "budget_exhausted",
    "submission_error",
}


def _read_input_text(source: Path) -> str:
    """Read a supplied batch whether it is plain text or gzip compressed."""
    with source.open("rb") as raw:
        is_gzip = raw.read(2) == b"\x1f\x8b"
    opener = gzip.open if is_gzip else open
    with opener(source, "rt", encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def _input_kind(source: Path) -> str:
    suffixes = list(source.suffixes)
    if suffixes and suffixes[-1].casefold() == ".gz":
        suffixes.pop()
    return suffixes[-1].casefold() if suffixes else ""


def _csv_organisation_inputs(text: str, source: Path) -> list[dict[str, Any] | str]:
    """Parse a documented evaluator CSV without silently choosing a column."""
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        # A one-column CSV has no delimiter for Sniffer to find.
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect=dialect))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []

    def normalized(name: str) -> str:
        return "".join(character for character in name.casefold() if character.isalnum())

    headers = [normalized(value.strip()) for value in rows[0]]
    accepted = {"organisationnumber", "organizationnumber", "organisasjonsnummer", "orgnr"}
    try:
        organisation_index = next(index for index, header in enumerate(headers) if header in accepted)
    except StopIteration:
        if any(len(row) != 1 for row in rows):
            raise ValueError(
                f"CSV organisation input needs an organisation-number header: {source}"
            )
        return [row[0].strip() for row in rows]

    optional_indexes = {
        key: index
        for index, header in enumerate(headers)
        for key in ("evaluation_split", "sample_slice")
        if header == normalized(key)
    }
    values: list[dict[str, Any]] = []
    for row in rows[1:]:
        if organisation_index >= len(row):
            raise ValueError(f"CSV row has no organisation number: {source}")
        value: dict[str, Any] = {"organisation_number": row[organisation_index].strip()}
        for key, index in optional_indexes.items():
            if index < len(row) and row[index].strip():
                value[key] = row[index].strip()
        values.append(value)
    return values


def read_organisation_inputs(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = _read_input_text(source)
    values: list[Any]
    kind = _input_kind(source)
    if kind == ".json":
        body = json.loads(text)
        values = body if isinstance(body, list) else body.get("organisation_numbers", [])
    elif kind == ".jsonl":
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif kind == ".csv":
        values = _csv_organisation_inputs(text, source)
    else:
        values = [line.strip() for line in text.splitlines() if line.strip()]
    records = []
    for value in values:
        org = value.get("organisation_number") if isinstance(value, dict) else value
        org = "".join(character for character in str(org or "") if character.isdigit())
        if len(org) != 9:
            raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
        record = {"organisation_number": org}
        if isinstance(value, dict):
            for key in ("evaluation_split", "sample_slice"):
                if value.get(key) is not None:
                    record[key] = value[key]
        records.append(record)
    orgs = [record["organisation_number"] for record in records]
    if len(orgs) != len(set(orgs)):
        raise ValueError("Organisation-number input contains duplicates")
    return records


def read_organisation_numbers(path: str | Path) -> list[str]:
    return [record["organisation_number"] for record in read_organisation_inputs(path)]


def profiles_from_bulk(path: str | Path, organisation_numbers: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested = list(organisation_numbers)
    wanted = set(requested)
    snapshot_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    retrieved_at = utc_now()
    found: dict[str, dict[str, Any]] = {}
    scanned = 0
    for profile in iter_bulk(path):
        scanned += 1
        org = profile["organisation_number"]
        if org not in wanted:
            continue
        raw = profile.pop("raw", {})
        profile["evidence"] = {
            "registry": evidence(
                "registry",
                "available",
                "official_registry_bulk",
                "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
                value=raw,
                retrieved_at=retrieved_at,
                content_sha256=snapshot_sha256,
                source_row_key=org,
            ),
            "accounting_obligation": accounting_obligation_assessment(profile),
        }
        found[org] = profile
        if len(found) == len(wanted):
            break
    missing = [org for org in requested if org not in found]
    if missing:
        raise ValueError(f"Organisation numbers absent from registry snapshot: {missing[:10]}")
    return [found[org] for org in requested], {
        "registry_snapshot_sha256": snapshot_sha256,
        "registry_rows_scanned": scanned,
        "requested": len(requested),
        "selected": len(found),
    }


def evidence_terminal_state(record: dict[str, Any] | None) -> str:
    if not record:
        return "submission_error"
    status = record.get("status")
    if status == "available":
        return "complete"
    if status == "not_applicable":
        return "not_applicable"
    if status == "not_found":
        return "not_found"
    if status == "blocked":
        note = str(record.get("note") or "").casefold()
        return "blocked_robots" if "robot" in note else "blocked_policy"
    if status == "source_error":
        return "source_error"
    return "submission_error"


def terminal_envelope(
    profile: dict[str, Any],
    *,
    run_id: str,
    modules: Iterable[str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    module_states = {}
    for module in modules:
        record = profile.get("evidence", {}).get(module)
        module_states[module] = {
            "state": evidence_terminal_state(record),
            "retry_count": int((record or {}).get("retry_count") or 0),
            "final_timestamp": (record or {}).get("retrieved_at") or completed_at,
        }
    entity_state = "submission_error" if any(item["state"] == "submission_error" for item in module_states.values()) else "complete"
    return {
        "run_id": run_id,
        "organisation_number": profile["organisation_number"],
        "state": entity_state,
        "started_at": started_at,
        "completed_at": completed_at,
        "modules": module_states,
        "profile": profile,
    }


def validate_envelopes(envelopes: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    orgs = [item.get("organisation_number") for item in envelopes]
    invalid_states = [
        {"organisation_number": item.get("organisation_number"), "state": state.get("state")}
        for item in envelopes
        for state in item.get("modules", {}).values()
        if state.get("state") not in TERMINAL_STATES
    ]
    checks = {
        "exact_expected_count": len(envelopes) == expected_count,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "all_entity_states_terminal": all(item.get("state") in TERMINAL_STATES for item in envelopes),
        "all_module_states_terminal": not invalid_states,
        "zero_silent_drops": len(envelopes) == expected_count and len(orgs) == len(set(orgs)),
    }
    return {"passed": all(checks.values()), "checks": checks, "invalid_states": invalid_states}


def profile_complete_for_modules(profile: dict[str, Any], modules: Iterable[str]) -> bool:
    records = profile.get("evidence", {})
    return all(module in records and records[module].get("status") != "not_fetched" for module in modules)
