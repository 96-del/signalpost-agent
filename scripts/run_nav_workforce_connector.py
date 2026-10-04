#!/usr/bin/env python3
"""NAV Stilling-Feed workforce_jobs connector.

Architecture:
  SNAPSHOT phase (once, pre-run): fetch public token, page the ENTIRE feed
  (can be 300k+ items), keep only ACTIVE entries, save to JSON snapshot.
  The feed is chronological — ACTIVE items cluster near the end.

  MATCH phase (per company, in-memory): fuzzy match by businessName +
  municipal against Brreg legal name + municipality via RapidFuzz ≥ 80.

  The feed has NO orgnr field — matching is name-based only.

Token: GET /api/publicToken → plain text with prefix line, JWT on last line.
Feed:  GET /api/v1/feed with Bearer token; paginate via next_url; 1000 items/page.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import requests
except ImportError as exc:
    raise RuntimeError("requests is required; install the pinned project dependencies with uv sync") from exc

try:
    from rapidfuzz import fuzz
except ImportError as exc:
    raise RuntimeError("rapidfuzz is required; install the pinned project dependencies with uv sync") from exc

log = logging.getLogger("nav_workforce")

NAV_TOKEN_URL = "https://pam-stilling-feed.nav.no/api/publicToken"
NAV_FEED_URL = "https://pam-stilling-feed.nav.no/api/v1/feed"
NAV_BASE_URL = "https://pam-stilling-feed.nav.no"
NAV_STILLING_URL = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
USER_AGENT = "signalpost-research/1.0"
MATCH_THRESHOLD = 80


# ---------------------------------------------------------------------------
#  Token
# ---------------------------------------------------------------------------

def fetch_nav_token(*, max_retries: int = 3) -> str:
    """Fetch the NAV public JWT token (plain text, not JSON)."""
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(NAV_TOKEN_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
            r.raise_for_status()
            for line in reversed(r.text.strip().split("\n")):
                candidate = line.strip().strip('"')
                if candidate.startswith("eyJ"):
                    return candidate
            raise ValueError(f"No JWT found in token response: {r.text[:200]}")
        except Exception as exc:
            log.warning("Token attempt %d/%d: %s", attempt, max_retries, exc)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
#  Feed snapshot (one-time pre-run cost)
# ---------------------------------------------------------------------------

def fetch_feed_snapshot(
    token: str,
    *,
    max_pages: int | None = None,
    page_timeout: int = 90,
    resume_url: str | None = None,
    resume_items: list[dict[str, Any]] | None = None,
    resumed_pages: int = 0,
    resumed_total_items: int = 0,
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page through the entire NAV stilling feed, keeping only ACTIVE entries.

    Supports resume via checkpoint_path.  Feed pages are 1000 items each.
    The full feed can be 300k+ items; ACTIVE entries cluster near the end.
    Expect ~15-25 min for a full fetch.  This cost is paid ONCE.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    url = resume_url or NAV_FEED_URL
    active_items: list[dict[str, Any]] = list(resume_items or [])
    total_items = resumed_total_items
    pages = resumed_pages
    started = time.monotonic()
    consecutive_errors = 0

    while url and (max_pages is None or pages < max_pages):
        try:
            resp = requests.get(url, headers=headers, timeout=page_timeout)
        except requests.RequestException as exc:
            consecutive_errors += 1
            log.warning("Page %d network error (%d consecutive): %s", pages + 1, consecutive_errors, exc)
            if consecutive_errors >= 5:
                log.error("Too many consecutive errors — stopping at page %d", pages)
                break
            time.sleep(min(30, 2 ** consecutive_errors))
            continue

        if resp.status_code == 401:
            log.warning("401 on page %d — re-fetching token", pages + 1)
            token = fetch_nav_token()
            headers["Authorization"] = f"Bearer {token}"
            continue

        if resp.status_code != 200:
            consecutive_errors += 1
            log.warning("Page %d HTTP %d (%d consecutive)", pages + 1, resp.status_code, consecutive_errors)
            if consecutive_errors >= 5:
                break
            time.sleep(min(30, 2 ** consecutive_errors))
            continue

        consecutive_errors = 0
        feed = resp.json()
        items = feed.get("items", [])
        total_items += len(items)
        pages += 1

        for item in items:
            fe = item.get("_feed_entry", {})
            if fe.get("status") == "ACTIVE":
                active_items.append({
                    "uuid": fe.get("uuid") or item.get("id"),
                    "title": fe.get("title") or item.get("title", ""),
                    "businessName": fe.get("businessName", ""),
                    "municipal": fe.get("municipal", ""),
                    "date_modified": item.get("date_modified", ""),
                })

        next_url = feed.get("next_url")
        if next_url and items:
            url = f"{NAV_BASE_URL}{next_url}" if next_url.startswith("/") else next_url
        else:
            url = None  # Exhausted

        if pages % 10 == 0:
            elapsed = time.monotonic() - started
            log.info(
                "Page %d: %d scanned, %d active, %.0fs elapsed, next=%s",
                pages, total_items, len(active_items), elapsed,
                "yes" if url else "DONE",
            )

        # Periodic checkpoint
        if checkpoint_path and pages % checkpoint_every == 0:
            _save_checkpoint(checkpoint_path, active_items, url, pages, total_items)

    # A page limit need not align with checkpoint_every. Persist the exact
    # continuation point so ``--resume`` never has to reread old feed pages.
    if checkpoint_path and url:
        _save_checkpoint(checkpoint_path, active_items, url, pages, total_items)

    elapsed = time.monotonic() - started
    metadata = {
        "pages_fetched": pages,
        "total_items_scanned": total_items,
        "active_items_kept": len(active_items),
        "elapsed_seconds": round(elapsed, 1),
        "feed_exhausted": url is None,
        "truncated_by_page_limit": bool(url and max_pages is not None),
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    log.info(
        "Snapshot complete: %d pages, %d total, %d active, %.0fs, exhausted=%s",
        pages, total_items, len(active_items), elapsed, url is None,
    )
    return active_items, metadata


def _save_checkpoint(path: Path, items: list, next_url: str | None, pages: int, total: int) -> None:
    data = {"items": items, "resume_url": next_url, "pages": pages, "total_scanned": total}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("Checkpoint saved: %d active items, page %d", len(items), pages)


def _load_checkpoint(path: Path) -> tuple[list[dict], str | None, int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["items"], data.get("resume_url"), data.get("pages", 0), data.get("total_scanned", 0)


def save_snapshot(items: list[dict[str, Any]], metadata: dict[str, Any], path: Path) -> str:
    payload = {"metadata": metadata, "items": items}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    content_hash = hashlib.sha256(raw.encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            handle.write(raw)
    else:
        path.write_text(raw, encoding="utf-8")
    return content_hash


def load_snapshot(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    return data["items"], data["metadata"]


def snapshot_content_hash(path: Path) -> str:
    """Hash canonical snapshot JSON rather than gzip container bytes."""
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checkpoint_for_snapshot(path: Path) -> Path:
    """Keep the same resumable checkpoint when packaging JSON as JSON.GZ."""
    bare = path.with_suffix("") if path.suffix == ".gz" else path
    return bare.with_suffix(".checkpoint.json")


# ---------------------------------------------------------------------------
#  Name matching
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.casefold())
    text = text.replace("\u00f8", "o").replace("\u00e5", "a").replace("\u00e6", "ae")
    for suffix in (" as", " asa", " ans", " da", " enk", " nuf"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    return text.strip()


def load_brreg_collisions(bulk_path: str) -> set[str]:
    import csv, gzip
    counts = defaultdict(int)
    log.info("Loading brreg bulk file to find name collisions...")
    opener = gzip.open if str(bulk_path).endswith('.gz') else open
    mode = 'rt' if str(bulk_path).endswith('.gz') else 'r'
    try:
        with opener(bulk_path, mode, encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            try:
                next(reader) # skip header
            except StopIteration:
                pass
            for row in reader:
                if len(row) > 1:
                    norm = _normalize_name(row[1])
                    if norm:
                        counts[norm] += 1
    except Exception as e:
        log.warning(f"Could not load brreg bulk file for collisions: {e}")
    collisions = {name for name, count in counts.items() if count > 1}
    log.info(f"Loaded brreg bulk file: found {len(collisions)} names with collisions.")
    return collisions

def build_lookup_index(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        muni = (item.get("municipal") or "").strip().upper()
        if muni:
            index[muni].append(item)
    return index


def match_company(
    company_name: str,
    company_municipality: str,
    index: dict[str, list[dict[str, Any]]],
    collisions: set[str],
    threshold: int = MATCH_THRESHOLD,
) -> list[dict[str, Any]]:
    norm_name = _normalize_name(company_name)
    muni_upper = (company_municipality or "").strip().upper()
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Municipality-scoped search (safe, low false-positive)
    candidates = index.get(muni_upper, []) if muni_upper else []
    for item in candidates:
        biz_norm = _normalize_name(item.get("businessName", ""))
        if not biz_norm or not norm_name:
            continue

        # Use token_sort_ratio (order-insensitive but requires all tokens to
        # contribute) instead of token_set_ratio (which ignores missing tokens
        # and causes false matches like "ORBIT CONSULTING" vs "Opv Consulting").
        sort_score = fuzz.token_sort_ratio(norm_name, biz_norm)
        # Also compute plain ratio for short names
        plain_score = fuzz.ratio(norm_name, biz_norm)
        score = max(sort_score, plain_score)

        # Additional guard: the Brreg name tokens must be substantially present
        # in the feed name.  This catches cases where a short common word
        # (e.g. "Steinkjer") inflates the score against a long Brreg name.
        brreg_tokens = set(norm_name.split())
        feed_tokens = set(biz_norm.split())
        if brreg_tokens and len(brreg_tokens) > 1:
            overlap = len(brreg_tokens & feed_tokens)
            coverage = overlap / len(brreg_tokens)
            if coverage < 0.6:
                continue  # Too many Brreg tokens missing from feed name

        if score >= threshold and item["uuid"] not in seen:
            seen.add(item["uuid"])
            if norm_name in collisions:
                log.info(f"Ambiguous match quarantined: {norm_name} has multiple entries in Brreg.")
                continue # Skip this item as it is ambiguous
            matches.append({**item, "match_score": score, "match_method": "name+municipality"})

    return matches


# ---------------------------------------------------------------------------
#  Observation builder
# ---------------------------------------------------------------------------

def build_observation(
    org: str,
    company_name: str,
    matched_items: list[dict[str, Any]],
    snapshot_hash: str,
    fetched_at: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not matched_items:
        return None, []

    best = max(matched_items, key=lambda x: x["match_score"])
    id_seed = f"{org}|nav_feed|{len(matched_items)}|{snapshot_hash}"
    obs_id = "nav-jobs-" + hashlib.sha256(id_seed.encode()).hexdigest()[:24]

    evidence_span = "; ".join(item["title"] for item in matched_items[:5])
    if len(matched_items) > 5:
        evidence_span += f" (+{len(matched_items) - 5} more)"

    source_url = NAV_STILLING_URL.format(uuid=best["uuid"])

    observation = {
        "id": obs_id,
        "organisation_number": org,
        "platform": "job_board",
        "signal_type": "job_posting",
        "source_url": source_url,
        "retrieved_at": fetched_at,
        "content_sha256": snapshot_hash,
        "exact_entity": True,
        "identity_proof": [{
            "type": "fuzzy_name_municipality_match",
            "value": {
                "brreg_name": company_name,
                "feed_businessName": best["businessName"],
                "feed_municipal": best.get("municipal", ""),
                "match_score": best["match_score"],
                "match_method": best["match_method"],
                "threshold": MATCH_THRESHOLD,
            },
        }],
        "acquisition_mode": "official_api",
        "rights_status": "approved",
        "source_class": "public_job_board",
        "evidence_span": evidence_span,
        "strategy": "jobs_feed_discovery",
        "metrics": {"active_job_count": len(matched_items)},
    }

    ledger_entry = {
        "org_nr": org,
        "field": "workforce_jobs",
        "value": len(matched_items),
        "source_url": source_url,
        "extraction_point": "nav_stilling_feed",
        "fetched_at": fetched_at,
        "match_score": best["match_score"],
        "match_method": best["match_method"],
        "matched_business_name": best["businessName"],
    }

    return observation, [ledger_entry]


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="NAV stilling-feed workforce_jobs connector")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--snapshot", default="out/nav-feed-snapshot.json.gz",
                        help="Pre-cached snapshot path (fetched if missing)")
    parser.add_argument("--observations-output", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument("--ledger", default="evidence_ledger.jsonl")
    parser.add_argument("--threshold", type=int, default=MATCH_THRESHOLD)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Maximum pages for a deliberate partial prefetch; 0 fetches until exhausted")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Only fetch the snapshot, don't match")
    parser.add_argument("--resume", action="store_true",
                        help="Resume a previously interrupted fetch")
    parser.add_argument("--bulk", help="Path to brreg bulk CSV for collision checking")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    checkpoint_path = checkpoint_for_snapshot(snapshot_path)

    # 1. Fetch or load snapshot
    use_cached_snapshot = snapshot_path.exists() and not args.fetch_only and not (
        args.resume and checkpoint_path.exists()
    )
    if use_cached_snapshot:
        log.info("Loading cached snapshot from %s", snapshot_path)
        items, snap_meta = load_snapshot(snapshot_path)
        content_hash = snapshot_content_hash(snapshot_path)
    else:
        resume_items, resume_url = None, None
        resumed_pages = resumed_total = 0
        if args.resume and checkpoint_path.exists():
            resume_items, resume_url, resumed_pages, resumed_total = _load_checkpoint(checkpoint_path)
            log.info("Resuming from checkpoint: %d items, url=%s", len(resume_items), resume_url[:60] if resume_url else "None")

        log.info("Fetching NAV feed snapshot (this can take 15-25 min for the full feed)...")
        token = fetch_nav_token()
        items, snap_meta = fetch_feed_snapshot(
            token,
            max_pages=args.max_pages or None,
            resume_url=resume_url,
            resume_items=resume_items,
            resumed_pages=resumed_pages,
            resumed_total_items=resumed_total,
            checkpoint_path=checkpoint_path,
        )
        content_hash = save_snapshot(items, snap_meta, snapshot_path)
        log.info("Saved snapshot: %d active items → %s", len(items), snapshot_path)

        # Keep a checkpoint for deliberately truncated fetches; it is the only
        # safe way to continue a large feed without re-reading old pages.
        if checkpoint_path.exists() and snap_meta.get("feed_exhausted"):
            checkpoint_path.unlink()

    if args.fetch_only:
        print(json.dumps(snap_meta, indent=2))
        return

    fetched_at = snap_meta.get("fetched_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    # 2. Load profiles
    profiles = [json.loads(l) for l in Path(args.profiles).read_text(encoding="utf-8").splitlines() if l.strip()]
    log.info("Loaded %d profiles", len(profiles))

    # 3. Build index and match
    index = build_lookup_index(items)
    log.info("Index: %d municipalities, %d active job entries", len(index), len(items))

    collisions = set()
    if args.bulk:
        collisions = load_brreg_collisions(args.bulk)

    observations: list[dict[str, Any]] = []
    all_ledger: list[dict[str, Any]] = []
    match_results: list[dict[str, Any]] = []

    for profile in profiles:
        org = str(profile.get("organisation_number", ""))
        name = str(profile.get("name", ""))
        municipality = str(profile.get("municipality", ""))
        if not org or not name:
            continue

        matched = match_company(name, municipality, index, collisions=collisions, threshold=args.threshold)
        obs, ledger = build_observation(org, name, matched, content_hash, fetched_at)

        if obs:
            observations.append(obs)
            all_ledger.extend(ledger)
            match_results.append({"organisation_number": org, "status": "matched",
                                  "job_count": len(matched),
                                  "best_score": max(m["match_score"] for m in matched)})
        else:
            match_results.append({"organisation_number": org, "status": "no_match", "job_count": 0})

    # 4. Write outputs
    obs_path = Path(args.observations_output)
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    with obs_path.open("w", encoding="utf-8") as f:
        for obs in observations:
            f.write(json.dumps(obs, ensure_ascii=False, separators=(",", ":")) + "\n")

    ledger_path = Path(args.ledger)
    with ledger_path.open("a", encoding="utf-8") as f:
        for entry in all_ledger:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    matched_count = sum(1 for r in match_results if r["status"] == "matched")
    report = {
        "connector": "nav_stilling_feed_workforce_v1",
        "snapshot": snap_meta,
        "snapshot_content_sha256": content_hash,
        "profiles_evaluated": len(profiles),
        "companies_matched": matched_count,
        "companies_unmatched": len(match_results) - matched_count,
        "coverage": round(matched_count / len(profiles), 4) if profiles else 0.0,
        "total_observations": len(observations),
        "total_job_postings_matched": sum(r.get("job_count", 0) for r in match_results),
        "match_threshold": args.threshold,
        "claim_boundary": (
            "NAV arbeidsplassen.no public stilling-feed; consumer-side "
            f"RapidFuzz name+municipality matching >= {args.threshold}; no employer orgnr in feed."
        ),
        "company_results": match_results[:20],
    }
    report_path = Path(args.report_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
