import json
rows = [json.loads(l) for l in open(r"out\smoke-profiles.jsonl", encoding="utf-8") if l.strip()]
website_count = sum(1 for r in rows if (r.get("evidence", {}).get("registry", {}).get("value") or {}).get("hjemmeside"))
print(f"Total profiles: {len(rows)}")
print(f"Profiles with official Brreg website: {website_count}")
