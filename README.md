# Signalpost reference agent

This is a runnable starting point for the Signalpost company-research challenge. It is intentionally a solid baseline, not a winning submission.

The public universe contains 411,160 eligible companies. Run the starter on 100 companies before submitting. Larger local tests, including 1,000 or more companies, are encouraged but their precomputed profiles are not submitted or scored.

## What it already does

- reads a batch of Norwegian organisation numbers;
- anchors identity in the Brønnøysund bulk registry;
- fetches official financials, roles, group links and registered workplaces;
- visits the registry-listed website and rejects weak entity matches;
- emits one terminal JSONL envelope per input;
- records sources, retrieval times, content hashes, request counts and latency;
- supports checkpoint/resume and a deterministic refresh replay;
- includes examples for external-footprint discovery and an evidence-bounded research agent.

## First run: try one saved example

Requires Python 3.11+ (Python 3.12 is also supported). Open a terminal inside this extracted folder.

Before downloading company data or running a full crawl, try the bundled public
sample. It uses saved responses: no API key, registry download or live web requests.

```bash
python3 scripts/run_refresh_replay.py \
  --manifest tests/fixtures/refresh-snapshots.json \
  --output out/refresh-demo.json
```

Open `out/refresh-demo.json`. The `events` list shows what changed between two
versions of one company profile and the source evidence for each change. The sample
should find two expected changes, no false changes, and no extra changes when the
same data is checked again.

The report's `qualification_passed` field refers only to this public sample check.
It does not qualify an entry for the competition or prove live information coverage.
The printed request counts are reads from saved responses, not network calls.

## Optional: local batch experiments (not the submission entrypoint)

Requires Python 3.11+ and `uv`. This workflow downloads data and makes live requests.
It uses the lower-level batch runner for development experiments. For the reproducible
submission command, use **One-command evaluation candidate** below; do not submit this
multi-step workflow as the evaluator entrypoint.

```bash
uv sync
curl -L 'https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv' -o brreg-enheter.csv
curl -L 'https://builderr.ai/signalpost-company-universe-2025.jsonl.gz' -o signalpost-universe.jsonl.gz

uv run python select_entry_batch.py \
  --universe signalpost-universe.jsonl.gz \
  --count 100 \
  --output entry-companies.jsonl

# Use the 100-company batch as your smoke test.
cp entry-companies.jsonl smoke-companies.jsonl

uv run python scripts/run_competition_batch.py \
  --organisations smoke-companies.jsonl \
  --bulk brreg-enheter.csv \
  --profiles-output out/smoke-profiles.jsonl \
  --output out/smoke-envelopes.jsonl \
  --report out/smoke-report.json \
  --run-id smoke-001 \
  --expected-count 100

# You may test at larger scale locally, but Builderr supplies the official batch for scoring.
uv run python scripts/run_competition_batch.py \
  --organisations entry-companies.jsonl \
  --bulk brreg-enheter.csv \
  --profiles-output out/profiles.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --run-id local-001 \
  --expected-count 1000

uv run --with pytest pytest -q
```

The 100-row smoke is a local operational check only. It does not predict an official
score or qualify an entry.

Increase `--count` and `--expected-count` together for a larger local test. The 100-row smoke test above is practice only; Builderr supplies the companies for every official run.

## Submission entrypoint: one reproducible run command

The submission runner keeps the official-record path and the robots-aware,
registry-linked website crawl in the same reproducible invocation. It writes
one contract envelope per input, a run report, and an inspectable static
evidence browser. It works with the default locked install; the optional
Scrapy crawler is used automatically only when installed.

Install the locked default environment once, then run this single command from the
repository root. Replace the two input paths and expected count with the evaluator's
supplied batch and plain-CSV registry snapshot; keep the runner and output flags intact.

```text
uv sync --locked
uv run python scripts/run_signalpost_submission.py --organisations smoke-companies.jsonl --bulk brreg-enheter.csv --output out/submission-envelopes.jsonl --report out/submission-report.json --viewer out/submission-viewer.html --run-id smoke-submission-001 --expected-count 100 --out-dir out/smoke-submission-run
```

The invocation above is a 100-row smoke example. The evaluator may supply a larger
batch; set `--expected-count` to the actual number of supplied organisations. The runner
processes every supplied organisation and warns if the configured count differs.
The batch path may be JSON, JSONL, plain text, or CSV as documented below.

For a refresh, reuse the same `--out-dir`; before replacing its intermediate
`profiles.jsonl`, the runner loads it as the prior raw snapshot. You can instead
pass `--previous-profiles PATH` to use a snapshot stored elsewhere. Each envelope
then includes previous evidence references, a stable current snapshot ID, and
material changes. Without a prior snapshot, the envelope marks the run as an
initial snapshot.

Each invocation has its own crawler event log and resume directory, so a later
refresh checks sources again instead of treating old crawl events as current.
Use a new `--run-id` for each official batch to keep the run report traceable;
reusing an output directory is supported for prior-profile comparison.

`--bulk` accepts the evaluator's documented **plain CSV** registry snapshot
and local gzip snapshots. The reader detects the file contents, rather than
requiring a `.gz` filename. `--organisations` accepts JSON, JSONL, plain text,
or CSV with an `organisation_number`, `organization_number`,
`organisasjonsnummer`, or `orgnr` header. The runner preserves the supplied
input order and rejects duplicates or malformed organisation numbers.

This runner does not publish third-party platform data until its source-rights
and exact-entity gates are qualified. A local score is a diagnostic only; it
is not an official Builderr score or a qualification claim.

The default submission path uses no language model, no paid API, and no user
credential. Its expected paid third-party API cost is USD 0 per run. If Google
Places is explicitly enabled with evaluator-provided credentials and approved
rights, the report sets `third_party_cost_usd` to `null` and marks the cost as
unknown until provider billing data is supplied; it will not falsely report
zero. Actual network/runtime cost still depends on the supplied batch and
evaluator environment.

An exhausted local `out/nav-feed-snapshot.json.gz` cache is optional. When it
is absent, unreadable, or partial, the runner records NAV as skipped and still
returns the required result for every supplied company. It never publishes a
NAV job claim unless the cache is exhausted and the normalized employer legal
name matches exactly in the same municipality, with duplicate legal names
quarantined using the supplied Brreg snapshot. The NAV feed contains no
organisation number, so the snapshot retrieval time is exposed as the claim's
`as_of` period; near-name and token-reordered matches are not published.

The public Nominatim connector is not part of the official submission runner.
The Nominatim policy treats periodic app requests as bulk geocoding and
restricts regularly repeated scripts to 4 requests per minute; the connector's
1-second pacing is suitable only for a one-time local research run, not the
recurring official batches. If you run that standalone experiment, cache its
results and display required OpenStreetMap attribution and ODbL information.
The official run reports OSM as `not_run` and never depends on it to return an
envelope. See the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
and [OpenStreetMap copyright and licence](https://www.openstreetmap.org/copyright).

Company-site publishing is deliberately stricter than page discovery. The
registry group response is checked for a parent root; if the homepage title or
description identifies that parent, the site and its social links are
quarantined for the child company. A page is emitted as company-site news only
when it is an individual dated post with explicit publication metadata and
substantive article text. `dateModified`, generic “updated” dates, section
labels and undated product/navigation pages do not qualify. See
[`docs/external-connectors.md`](docs/external-connectors.md) for the evidence
rules.

The runner report includes an operational budget check. Defaults are 45 minutes
and 2,000 HTTP requests per 100 supplied companies, matching evaluator guidance
received on 26 October 2026. A local pass is a preflight result, not
an official score or a guarantee for a different batch.

For broader, permitted external recall, it can optionally call **Google Places
API (New)**. This requires an evaluator-provided `GOOGLE_PLACES_API_KEY` and a
deployment-specific confirmation that the applicable Places API licence,
attribution, billing, and retention terms allow the intended use:

```bash
export GOOGLE_PLACES_API_KEY='server-side-key'
export SIGNALPOST_GOOGLE_PLACES_RIGHTS=approved
```

Without both variables, the runner does not contact Google and reports the
connector as `not_run`; it never turns the absence of that source into zero.

## The improvement loop

1. Treat the organisation number as the anchor.
2. Generate site/profile candidates from official data, the company site, lawful search providers and named people.
3. Save every candidate and the evidence for or against it.
4. Publish only exact-entity matches. Parent, brand, franchise and similarly named companies are not exact.
5. Crawl static HTML first. Escalate to a browser only when a deterministic completeness check fails.
6. Measure added supported coverage, wrong-company claims, runtime, requests and cost.
7. Promote a strategy only when it improves coverage without weakening the accuracy gates.
8. Freeze strategies and thresholds before the daily evaluation run.

The strongest differentiator is external evidence that remains exact and auditable: official company pages, company-owned profiles, jobs, dated activity, ratings/reviews and permitted public signals. Do not trade accuracy for volume.

## Important source rule

Open-source code does not grant permission to scrape a platform. Follow each source's terms, robots policy, rate limits and licence. LinkedIn, Meta and Indeed are useful identity/discovery targets, but direct automated collection may be restricted. Use permitted APIs, licensed providers, company-owned outbound links, or return `blocked`/`not_available`.

Read `docs/competition-control-loop.md`, `docs/external-connectors.md` and the public source policy before adding connectors.

## Submission contract

Submit a repository with:

- a 100-company smoke-test result or report;
- one documented command that accepts the evaluator-supplied CSV, JSONL, or
  text batch of organisation numbers;
- exactly one terminal envelope per input;
- pinned dependencies and reproducible setup;
- a previous-snapshot input and material-change output;
- a machine-readable run report with runtime, request count and third-party cost;
- declared models, APIs, licences and source-rights assumptions. The default
  path uses no model or paid API; optional Google Places requires an
  evaluator-provided key and explicit rights approval.
- an agent display name and a contact address for result notifications.

Email the agent name, contact address, public repository URL, exact commit hash,
100-company smoke-test report, one reproducible run command, models/APIs/licences,
source-rights assumptions and expected cost per official batch to `submit@builderr.ai`.
