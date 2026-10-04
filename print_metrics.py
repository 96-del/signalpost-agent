import json
from pathlib import Path

def main():
    s = json.load(open("out/v3-score.json", encoding="utf-8"))["combined"]
    print("--- V3 SCORE ---")
    print(f"Strict Mean: {s['strict_completeness_mean']}")
    print(f"Exact Identity Coverage: {s['component_coverage']['strict_enrichment']['exact_external_identity']}")
    print(f"Freshness Coverage: {s['component_coverage']['strict_enrichment']['freshness_evidence']}")
    print(f"Handles Coverage: {s['component_coverage']['strict_enrichment']['verified_handles']}")
    print("\n--- REJECTIONS ---")
    log_path = Path("out/rejections.log")
    if log_path.exists():
        print(log_path.read_text(encoding="utf-8").strip())

if __name__ == "__main__":
    main()
