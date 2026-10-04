import json
from pathlib import Path

def main():
    obs = [json.loads(l) for l in Path("out/crawl-observations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    envelopes = [json.loads(l) for l in Path("out/smoke-envelopes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    obs_map = {str(o["organisation_number"]): o for o in obs}
    
    enriched = 0
    for e in envelopes:
        org = str(e["organisation_number"])
        if org in obs_map:
            obs_row = obs_map[org]
            components = e.get("final", {}).get("components", {})
            
            website_ev = obs_row.get("evidence", {}).get("website", {})
            # Safely handle JSON null values
            value = website_ev.get("value") or {}
            
            identity = value.get("identity_assessment") or {}
            if website_ev.get("status") == "available" and identity.get("publishable") is True:
                # Kimi Phase 2: Domain verified by Brreg and crawled today
                components["exact_external_identity"] = 10.0
                components["freshness_evidence"] = 10.0
                
            # Only award if handles survived the RapidFuzz gate
            valid_links = value.get("social_links", []) if identity.get("publishable") is True else []
            if valid_links:
                components["verified_handles"] = 10.0
            
            if components:
                e.setdefault("final", {})["components"] = components
                enriched += 1
                
    Path("out/v3-envelopes.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in envelopes), encoding="utf-8")
    print(f"Compiled V3: Safely added strict evidence to {enriched} companies.")

if __name__ == "__main__":
    main()
