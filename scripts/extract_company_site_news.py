#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


NEWS_PATH = re.compile(r"/(?:news|company-news|press|aktuelt|nyheter|artikler|blog)(?:/|$)", re.I)
GENERIC_TITLES = {
    "news", "nyheter", "aktuelt", "press", "press releases", "blog", "articles",
    "news archive", "all news", "latest news", "nyhetsarkiv", "alle nyheter",
}
GENERIC_SLUGS = {"all", "archive", "archives", "latest", "index", "category", "tag"}


def observation(profile: dict) -> dict | None:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    if website.get("status") != "available" or not identity.get("publishable"):
        return None
    pages = [
        page for page in (value.get("pages") or [])
        if NEWS_PATH.search(urlparse(str(page.get("url") or "")).path)
        and page.get("published_at")
        and len(str(page.get("main_text_excerpt") or "").strip()) >= 80
    ]
    if not pages:
        return None

    def is_publishable_article(page: dict) -> bool:
        path_parts = [part.casefold() for part in urlparse(str(page.get("url") or "")).path.split("/") if part]
        section_index = next((index for index, part in enumerate(path_parts) if part in {"news", "company-news", "press", "aktuelt", "nyheter", "artikler", "blog"}), None)
        if section_index is None or section_index >= len(path_parts) - 1:
            return False
        if any(part in GENERIC_SLUGS for part in path_parts[section_index + 1:]):
            return False
        try:
            date.fromisoformat(str(page.get("published_at"))[:10])
        except ValueError:
            return False
        title = str(page.get("article_headline") or page.get("title") or "").strip()
        title_key = re.sub(r"\s+", " ", title.casefold()).strip(" |–—-:")
        if not title or title_key in GENERIC_TITLES:
            return False
        return True

    pages = [page for page in pages if is_publishable_article(page)]
    if not pages:
        return None
    # Prefer the deepest, dated individual article over a section/archive page.
    pages.sort(key=lambda page: (-len([part for part in urlparse(str(page.get("url") or "")).path.split("/") if part]), str(page.get("url") or "")))
    page = pages[0]
    url = str(page.get("url") or "")
    digest = str(page.get("content_sha256") or "")
    if not url.startswith(("http://", "https://")) or len(digest) != 64:
        return None
    org = str(profile["organisation_number"])
    title = str(page.get("article_headline") or page.get("title") or "").strip()
    published_at = date.fromisoformat(str(page["published_at"])[:10]).isoformat()
    body_excerpt = " ".join(str(page.get("main_text_excerpt") or "").split())[:500]
    return {
        "id": "company-site-news-" + hashlib.sha256(f"{org}|{url}".encode()).hexdigest()[:24],
        "organisation_number": org,
        "platform": "company_site",
        "signal_type": "public_post",
        "source_url": url,
        "retrieved_at": website.get("retrieved_at"),
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": [{"type": "website_identity_gate", "score": identity.get("score"), "method": identity.get("method")}],
        "acquisition_mode": "permitted_public_page",
        "rights_status": "approved",
        "source_class": "company_site",
        "evidence_span": f"Published {published_at}: {title}. {body_excerpt}"[:1200],
        "metrics": {
            "published_at": published_at,
            "article_type": page.get("article_type"),
            "captured_dated_article_pages": len(pages),
            "interpretation": "Dated company-owned article; not independent sentiment.",
        },
        "strategy": "company_site_activity",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract company-owned activity from exact-site bounded news pages.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [item for profile in profiles if (item := observation(profile))]
    Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    report = {
        "connector": "exact_company_site_news_activity_v1",
        "profiles": len(profiles),
        "companies_with_activity": len(rows),
        "observations": len(rows),
        "claim_boundary": "Company-owned activity only; never treated as independent sentiment.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
