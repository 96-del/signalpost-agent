#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

# Install rapidfuzz for strict name matching as required by Phase 1b
try:
    from rapidfuzz import fuzz
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "rapidfuzz"])
    from rapidfuzz import fuzz

BLOCKLIST_PATTERNS = [
    "linkedin.com/share", "linkedin.com/sharing", "facebook.com/sharer",
    "facebook.com/share", "twitter.com/intent", "/plugins/", "addthis", "sharethis"
]

def extract_slug(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    return parts[-1] if parts else ""

def accept_handle(legal_name: str, url: str, threshold: int = 70) -> tuple[bool, str, int]:
    for pattern in BLOCKLIST_PATTERNS:
        if pattern in url.lower():
            return False, f"Blocked by pattern: {pattern}", 0
    
    slug = extract_slug(url)
    if not slug or len(slug) < 2:
        return False, "Slug too short or missing", 0
        
    slug_norm = slug.replace("-", " ").replace("_", " ").lower()
    # Strip common Norwegian corporate suffixes to ensure clean matching
    legal_norm = legal_name.lower().replace(" as", "").replace(" asa", "").strip()
    
    score = fuzz.token_set_ratio(legal_norm, slug_norm)
    if score >= threshold:
        return True, "Name match above threshold", int(score)
    return False, "Name match failed", int(score)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    path = Path(args.input)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    
    ledger_path = Path("evidence_ledger.jsonl")
    rejections_path = Path("out/rejections.log")
    
    # Clear previous logs for this run
    if ledger_path.exists(): ledger_path.unlink()
    if rejections_path.exists(): rejections_path.unlink()
    
    before = after = 0
    with ledger_path.open("a", encoding="utf-8") as ledger, rejections_path.open("a", encoding="utf-8") as rej_log:
        for row in rows:
            legal_name = row.get("name", "")
            org_nr = str(row.get("organisation_number", ""))
            
            website_ev = row.get("evidence", {}).get("website", {})
            value = website_ev.get("value") or {}
            source_url = website_ev.get("source_url") or value.get("url") or ""
            
            raw_links = value.get("discovered_social_links") or value.get("social_links") or []
            if not raw_links:
                continue
                
            before += len(raw_links)
            valid_links = []
            
            for link in raw_links:
                url = link.get("url", "")
                platform = link.get("platform", "unknown")
                
                is_accepted, reason, score = accept_handle(legal_name, url)
                
                if is_accepted:
                    valid_links.append({"platform": platform, "url": url})
                    ledger_entry = {
                        "org_nr": org_nr,
                        "field": f"verified_handles.{platform}",
                        "value": url,
                        "source_url": source_url,
                        "extraction_point": "fuzz_name_match",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "name_match_score": score
                    }
                    ledger.write(json.dumps(ledger_entry) + "\n")
                else:
                    rej_log.write(f"REJECTED {org_nr} ({legal_name}): {url} -> {reason} (Score: {score})\n")
            
            value["social_links"] = valid_links
            value["discovered_social_links"] = valid_links
            after += len(valid_links)
            
    # Write back safely
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)
    
    print(json.dumps({"profiles": len(rows), "links_before": before, "links_after": after, "removed": before - after}))

if __name__ == "__main__":
    main()
