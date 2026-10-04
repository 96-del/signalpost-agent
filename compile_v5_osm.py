import json
import urllib.request
import urllib.parse
import time
from pathlib import Path
from datetime import datetime, timezone
from scripts.run_osm_places_connector import strict_name_match

def main():
    # Load name and city from the profiles
    profiles = {str(json.loads(l)["organisation_number"]): json.loads(l) for l in Path("out/smoke-profiles.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    envelopes = [json.loads(l) for l in Path("out/v4-envelopes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ledger_path = Path("evidence_ledger.jsonl")
    
    enriched = 0
    with ledger_path.open("a", encoding="utf-8") as ledger:
        print("Starting OSM Nominatim lookup (respecting 1-second rate limit)...")
        for e in envelopes:
            org = str(e.get("organisation_number"))
            profile = profiles.get(org, {})
            name = profile.get("name", "")
            city = profile.get("municipality", "")
            
            if not city or not name:
                continue
                
            # Query Nominatim API with legal name and city
            query = urllib.parse.quote(f"{name}, {city}, Norway")
            url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
            req = urllib.request.Request(url, headers={'User-Agent': 'Signalpost-Hackathon-Agent/1.0'})
            
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    if data:
                        result = data[0]
                        osm_name = result.get("name", "")
                        
                        # Strict Kimi Gate: 80+ name match required
                        match_score = strict_name_match(name, osm_name)
                        
                        if match_score is not None and match_score >= 80:
                            osm_url = f"https://www.openstreetmap.org/{result.get('osm_type', 'node')}/{result.get('osm_id')}"
                            components = e.setdefault("final", {}).setdefault("components", {})
                            components["places_identity"] = 10.0
                            
                            # Log to evidence ledger
                            timestamp = datetime.now(timezone.utc).isoformat()
                            ledger_entry = {
                                "org_nr": org, 
                                "field": "places_identity", 
                                "value": str(result.get("place_id")), 
                                "source_url": osm_url, 
                                "extraction_point": "osm_nominatim", 
                                "fetched_at": timestamp, 
                                "match_score": match_score
                            }
                            ledger.write(json.dumps(ledger_entry) + "\n")
                            enriched += 1
            except Exception as ex:
                pass
            
            time.sleep(1) # Mandatory delay for free OSM API
            
    Path("out/v5-envelopes.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in envelopes), encoding="utf-8")
    print(f"Compiled V5: Found secure OSM Places Identity for {enriched} companies.")

if __name__ == "__main__":
    main()
