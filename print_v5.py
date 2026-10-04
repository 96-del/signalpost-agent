import json
s = json.load(open(r"out\v5-score.json", encoding="utf-8"))["combined"]
print(f"Strict Mean: {s['strict_completeness_mean']}")
print(f"OSM Places Coverage: {s['component_coverage']['strict_enrichment']['places_identity']}")
