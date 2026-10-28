from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

EvidenceStatus = Literal[
    "available",
    "not_found",
    "not_applicable",
    "not_fetched",
    "source_error",
    "blocked",
]

DEFAULT_EXTRACTION_METHODS = {
    "official_rule_interpretation": "versioned_official_rule_interpretation_v1",
    "official_registry_bulk": "brreg_registry_csv_row_v1",
    "official_registry_live": "brreg_entity_api_json_v1",
    "official_annual_accounts": "brreg_annual_accounts_api_json_v1",
    "official_annual_account_copies": "brreg_filing_history_api_json_v1",
    "official_roles": "brreg_roles_api_json_v1",
    "official_group_structure": "brreg_group_api_json_v1",
    "official_subunits": "brreg_subunits_api_json_v1",
    "registry_linked_company_website": "robots_aware_bounded_html_extraction_v1",
    "company_site": "verified_company_site_bounded_page_extraction_v1",
    "official_nav_stilling_feed": "exhausted_nav_feed_name_municipality_match_v1",
    "licensed_google_places_api": "licensed_google_places_field_mask_v1",
    "open_geographic_database": "osm_name_address_match_v1",
    "connector_runtime": "per_company_connector_failure_isolation_v1",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Evidence:
    field: str
    status: EvidenceStatus
    source_type: str
    source_class: str
    source_url: str
    retrieved_at: str
    value: Any = None
    as_of: str | None = None
    note: str | None = None
    content_sha256: str | None = None
    source_row_key: str | None = None
    effective_at: str | None = None
    extraction_method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence(
    field: str,
    status: EvidenceStatus,
    source_type: str,
    source_url: str,
    *,
    value: Any = None,
    as_of: str | None = None,
    note: str | None = None,
    retrieved_at: str | None = None,
    content_sha256: str | None = None,
    source_row_key: str | None = None,
    effective_at: str | None = None,
    extraction_method: str | None = None,
) -> dict[str, Any]:
    return Evidence(
        field=field,
        status=status,
        source_type=source_type,
        source_class=source_type,
        source_url=source_url,
        retrieved_at=retrieved_at or utc_now(),
        value=value,
        as_of=as_of,
        note=note,
        content_sha256=content_sha256,
        source_row_key=source_row_key,
        effective_at=effective_at,
        extraction_method=extraction_method or DEFAULT_EXTRACTION_METHODS.get(source_type, source_type),
    ).to_dict()
