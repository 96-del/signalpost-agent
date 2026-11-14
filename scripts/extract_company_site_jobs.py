#!/usr/bin/env python3
"""Publish one conservatively verified, company-owned job posting per profile."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


JOB_ROUTE_TERMS = {
    "career", "careers", "karriere", "jobb", "jobber", "job", "jobs",
    "stilling", "stillinger", "ledig-stilling", "ledige-stillinger", "vacancy", "vacancies",
}
GENERIC_SEGMENTS = {
    "career", "careers", "karriere", "jobb", "jobber", "job", "jobs",
    "stilling", "stillinger", "ledig-stilling", "ledige-stillinger", "vacancy", "vacancies",
    "all", "archive", "archives", "latest", "index", "category", "tag", "search",
}
GENERIC_TITLES = {"career", "careers", "karriere", "jobb", "jobber", "jobs", "stillinger", "vacancies"}


def _tokens(value: object) -> set[str]:
    normalized = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    normalized = normalized.casefold()
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1 and token not in {"as", "asa", "ans", "da", "enk", "og", "and"}}


def _direct_job_route(url: str) -> bool:
    parts = [part.casefold() for part in urlparse(url).path.split("/") if part]
    index = next((i for i, part in enumerate(parts) if part in JOB_ROUTE_TERMS), None)
    return index is not None and index < len(parts) - 1 and any(part not in GENERIC_SEGMENTS for part in parts[index + 1:])


def _open_at_capture(page: dict, retrieved_at: str | None) -> bool:
    deadline = str(page.get("job_valid_through") or "")
    if deadline:
        try:
            retrieved_day = date.fromisoformat(str(retrieved_at or "")[:10])
            if date.fromisoformat(deadline[:10]) < retrieved_day:
                return False
            return True
        except ValueError:
            return False
    return bool(page.get("job_application_signal"))


def _company_specific_enough(profile: dict, page: dict) -> bool:
    """Reject a clearly named parent/recruiter on an otherwise verified site."""
    legal_tokens = _tokens(profile.get("name"))
    publisher_tokens = _tokens(page.get("job_hiring_organization"))
    if not publisher_tokens or not legal_tokens:
        return True
    # Mere one-word overlap is unsafe: a group and a subsidiary frequently
    # share a brand token. Allow a shortened legal name, but reject a different
    # named parent/recruiter with extra identity tokens.
    return bool(legal_tokens <= publisher_tokens or publisher_tokens <= legal_tokens)


def observation(profile: dict) -> dict | None:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    retrieved_at = website.get("retrieved_at")
    if website.get("status") != "available" or identity.get("publishable") is not True:
        return None

    candidates = []
    for page in value.get("pages") or []:
        url = str(page.get("url") or "")
        title = str(page.get("job_title") or page.get("article_headline") or page.get("title") or "").strip()
        text = " ".join(str(page.get("main_text_excerpt") or "").split())
        title_key = re.sub(r"\s+", " ", title.casefold()).strip(" |–—-:")
        if (
            not _direct_job_route(url)
            or not page.get("job_posting_structured")
            or title_key in GENERIC_TITLES
            or len(text) < 120
        ):
            continue
        if not _open_at_capture(page, retrieved_at) or not _company_specific_enough(profile, page):
            continue
        digest = str(page.get("content_sha256") or "")
        if not url.startswith(("http://", "https://")) or len(digest) != 64:
            continue
        candidates.append((url, title, text, page, digest))
    if not candidates:
        return None

    # Prefer a structured JobPosting and then a page with an explicit deadline.
    candidates.sort(key=lambda item: (
        not bool(item[3].get("job_posting_structured")),
        not bool(item[3].get("job_valid_through")),
        item[0],
    ))
    url, title, text, page, digest = candidates[0]
    org = str(profile["organisation_number"])
    deadline = page.get("job_valid_through")
    posted = page.get("job_date_posted")
    span = f"Open company-site job posting: {title}."
    if deadline:
        span += f" Application deadline: {deadline}."
    elif posted:
        span += f" Posted: {posted}."
    span += " " + text[:800]
    return {
        "id": "company-site-job-" + hashlib.sha256(f"{org}|{url}".encode()).hexdigest()[:24],
        "organisation_number": org,
        "platform": "company_site",
        "signal_type": "job_posting",
        "source_url": url,
        "retrieved_at": retrieved_at,
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": [{
            "type": "verified_company_site_direct_job_detail",
            "score": identity.get("score"),
            "method": identity.get("method"),
        }],
        "acquisition_mode": "permitted_public_page",
        "rights_status": "approved",
        "source_class": "company_site",
        "evidence_span": span[:1200],
        "metrics": {
            "job_title": title,
            "date_posted": posted,
            "application_deadline": deadline,
            "structured_job_posting": bool(page.get("job_posting_structured")),
            "application_signal": bool(page.get("job_application_signal")),
            "hiring_organization": page.get("job_hiring_organization"),
            "interpretation": "Open posting published on the verified company site; not a third-party job-board claim.",
        },
        "strategy": "verified_company_site_job_detail_v1",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract exact-company open jobs from verified company-site detail pages.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [item for profile in profiles if (item := observation(profile))]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    report = {
        "connector": "verified_company_site_jobs_v1",
        "profiles": len(profiles),
        "companies_with_open_jobs": len(rows),
        "observations": len(rows),
        "claim_boundary": "Only direct, same-site job-detail pages from a verified company site; generic careers pages, expired postings, recruiters and third-party boards are excluded.",
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
