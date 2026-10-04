import urllib.request, json, time
from pathlib import Path

def main():
    print("=== A. NAV API PROBE ===")
    try:
        req = urllib.request.Request("https://pam-stilling-feed.nav.no/api/publicToken")
        token = json.loads(urllib.request.urlopen(req).read().decode())["token"]
        req2 = urllib.request.Request("https://pam-stilling-feed.nav.no/api/v1/feed", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        feed = json.loads(urllib.request.urlopen(req2).read().decode())
        print(json.dumps(feed["items"][:2], indent=2))
    except Exception as e:
        print(f"NAV API Error: {e}")

    print("\n=== C. EVIDENCE LEDGER ===")
    ledger = Path("evidence_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    print(f"Total lines: {len(ledger)}")
    print("\n".join(ledger[:20]))

    print("\n=== E. BRREG DOMAIN-LESS VERDICT ===")
    profiles = [json.loads(l) for l in Path("out/smoke-profiles.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    with_web = [{"orgnr": r.get("organisation_number"), "web": r.get("evidence", {}).get("registry", {}).get("value", {}).get("hjemmeside")} for r in profiles if r.get("evidence", {}).get("registry", {}).get("value", {}).get("hjemmeside")]
    print(json.dumps(with_web, indent=2))

    print("\n=== G. FULL DOMAIN-LESS ENVELOPE (935095190) ===")
    envelopes = [json.loads(l) for l in Path("out/v5-envelopes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    domainless = next((e for e in envelopes if str(e.get("organisation_number")) == "935095190"), None)
    print(json.dumps(domainless, indent=2))

if __name__ == "__main__":
    main()
