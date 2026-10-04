import json
s = json.load(open(r"out\v4-score.json", encoding="utf-8"))["combined"]
print(f"Strict Mean: {s['strict_completeness_mean']}")
print("\n--- LEDGER SAMPLE ---")
print("".join(open("evidence_ledger.jsonl", encoding="utf-8").readlines()[:10]))
