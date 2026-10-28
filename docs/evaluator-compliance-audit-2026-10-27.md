# Builderr Signalpost compliance audit — 27 October 2026

This is an evidence log for the published [Signalpost challenge](https://builderr.ai/challenges/signalpost),
[evaluation contract](https://builderr.ai/docs/signalpost-evaluation-harness.md), and
[source policy](https://builderr.ai/starter-briefs/signalpost-sources.md). It is not a score prediction
or a claim that the agent is bug-free.

## What Builderr requires

- At least 65/100 on an official run. The published weights are recall/coverage 50, precision/evidence 30,
  synthesis 12, and UX 8. The current public board is a dated 17 October snapshot, not this entry's score.
- One terminal result for every supplied organisation number, in input order, including missing, blocked,
  ambiguous and failed source outcomes. Missing values must not be represented as zero.
- Claim-level evidence with source, retrieval time, relevant reporting period, content hash and extraction method;
  prior evidence is preserved and refresh is idempotent.
- No fabricated financial claims and no material wrong-company publication. Parent/group facts and company-owned
  content must not be attributed to a child without exact-entity support.
- A clean install, pinned dependencies, one reproducible evaluator command, disclosed source rights and safe URL
  handling. The official set currently has 1,000 companies and may grow to 1,100.
- A participant-received evaluator clarification on 26 October confirmed a 45-minute and 2,000-request-per-100-company
  limit, public internet, and no credentials. It also says the evaluator supplies an organisation-number file and
  a frozen registry snapshot when the documented command takes `--bulk`. This private communication supplements,
  but does not replace, the published contract.

## Repository audit status

| Requirement | Evidence in this repository | Status / remaining limit |
|---|---|---|
| Batch input and frozen registry | `--organisations` accepts JSON, JSONL, CSV or text; `--bulk` detects plain CSV or gzip content, including BOM-aware decoding; requested order is preserved. | Current working-tree end-to-end run used the 100-company JSONL smoke batch and an actual 842,270,122-byte decompressed plain CSV. It selected all 100/100 registry rows with none missing. |
| Exactly one terminal envelope | Submission runner compares output count, uniqueness, order, terminal state, claim/evidence references, timestamps, source URLs and strict JSON. | Current working-tree 100-row plain-input run emitted 100/100 and passed contract/resource checks (631 requests; 3m51s). A clean current-working-tree 1,000-row run on 27 October emitted 1,000/1,000, selected all 1,000 with no missing registry matches, and passed all final checks. The saved report records 2,150.269 seconds (35m50s) and 6,505/20,000 requests. This is a local sample, not the official score or exact future batch. |
| Evidence and periods | Evidence now includes source class, extraction method, retrieval time, hash and source span; financial claim periods are emitted and validated; dated company-site posts retain their publication date. | Website terms and each site's identity cannot be proven universally by tests. Conservative abstention is intentional. |
| Group/site attribution | Registry group root is checked against homepage title, description, visible footer/legal identity text and structured organisation names; unverified sites remain ambiguous and their social/activity claims are withheld. | This is a deterministic gate, not manual verification of every company domain. Residual matching risk remains and must not be described as zero. |
| News validity | Only a dated, substantive, non-generic page under a news-like individual-article route is emitted; generic/updated dates do not qualify. | An exact official-site gate does not prove every news story is about the company; evaluator feedback should be used to review any remaining example. |
| Hiring and public-footprint recall | The official runner can consume an exhausted NAV cache and exact-company company-site news; restricted third-party scrapers are not in the default run. | Current local NAV cache is dated 3 October and explicitly `feed_exhausted: false` (1,308,895 active entries after 3,163 pages; fetch log ends after repeated TLS/DNS failures). The runner correctly skips it. Current plain-input smoke found 1 site-surface observation and 0 news observations out of 100; the 1,000-row run found 24 site-surface observations and 1 dated company-site article. Scrapy breadth crawling was skipped because the optional runtime is not installed. Hiring and independent external-footprint recall remain substantial gaps, not completed modules. |
| Refresh | Prior raw profiles are loaded before the output snapshot is replaced. Crawler job state and event logs are isolated by invocation; transient fetch failures do not create false removals. | Covered by repeat-run tests; same-source live behavior remains environment-dependent. |
| Source rights and secrets | Default run uses no personal credentials or paid APIs. Google Places is disabled without evaluator-provided key and approved rights. Public Nominatim is excluded from the recurring official runner after checking its current rate/use policy. Robots, redirect and private-network checks are applied to company-site fetching. | Robots permission is not a complete legal/terms review. DNS validation has a DNS-rebinding time-of-check/time-of-use residual; use evaluator egress controls if available. |
| Runtime/request budget | Runner reports elapsed time and HTTP request use against the email-confirmed caps; redirect/retry attempts are counted for the primary HTTP clients. | Only an official run establishes compliance on the actual batch. Optional connector failures mark request accounting incomplete rather than claiming zero. |
| Install and evaluator command | `uv.lock`, `uv sync --locked`, and the README's one-command CLI are present. | `uv sync --locked` succeeds; latest full suite on Python 3.14.3: 150 passed, 8 subtests passed. 11 upstream `mf2py` deprecation warnings remain; no test failures. Both current-tree 100- and 1,000-row runs used actual plain CSV registry input. |
| Public contract's local npm checks | The published evaluation contract lists `npm run check:signalpost`, `npm run check:signalpost-showcase`, and `npm run check:signalpost-challenge`. Neither this repository nor the downloaded official Python starter archive contains `package.json`; running the first check in this repository exits with npm's missing-package error (`-4058`). | Unresolved contract/starter mismatch. Do not invent placeholder npm scripts; ask Builderr whether these checks apply to a Python submission and what equivalent they expect. |
| Cutoff handling | The participant brief says Builderr supplies a cutoff, but the public `OUTPUT_CONTRACT.md` and current CLI do not define a cutoff argument/environment variable or its semantics. | Unresolved evaluator-interface question. Do not guess whether it means “as-of” time, batch-selection cutoff, or something else. |
| Qualification score | No local script can establish an official score; prior local diagnostic scores are not official. | Unknown until Builderr runs the frozen commit. |

## Release rule

The plain-CSV smoke and current-tree 1,000-company end-to-end run now pass, so the earlier locally
reproducible CSV/scale uncertainty is cleared. Before sending a revision, review and commit the current
working-tree diff, then send that exact hash and attach the saved 100-company smoke report (the existing
committed smoke artifacts predate this run). Ask Builderr whether the three `npm run check:*` commands in
the technical contract apply to this Python-only submission; do not add no-op scripts. The public brief's
"cutoff" language does not define an extra CLI argument, while Soham's email says they pass the company
file and frozen registry to the documented command, so do not invent a cutoff flag. A local run proves
only those inputs worked in this environment; it cannot guarantee 65/100, the future batch result, or
zero defects.
