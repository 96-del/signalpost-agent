# Signalpost revision release-readiness — 14 November 2026

This is a local operational record, not an official Builderr score prediction.
The qualification gates remain an official run, at least 65/100 overall, and
at least 95% correct checked published claims.

## Exact local release check

From the repository root, the locked default environment was checked with:

```text
uv sync --locked
uv run --locked --with pytest pytest -q
uv run --locked python scripts/run_signalpost_submission.py --organisations smoke-companies.jsonl --bulk brreg-enheter.csv --output out/release-final-20261114/envelopes.jsonl --report out/release-final-20261114/report.json --viewer out/release-final-20261114/viewer.html --run-id release-final-20261114-100 --out-dir out/release-final-20261114/intermediate
```

Observed result:

- 155 tests and 8 subtests passed.
- 100 input organisations produced 100 envelopes in input order.
- Contract validation, terminal-state validation, and viewer generation all passed.
- The run took 244.550 seconds and measured 642 HTTP requests, inside the
  45-minute and 2,000-requests-per-100 guidance supplied by Builderr.
- No connector errors or contract errors were reported.

## Deliberate publication boundaries

- The submission path publishes no social-profile URL. A social URL found in
  page HTML, structured data, a refresh snapshot, or a prior change record is
  withheld. This specifically prevents a platform-owned footer URL from being
  attributed to the company.
- A registry-linked website whose exact legal-entity identity cannot be
  established is emitted as `ambiguous`, not as a company website claim.
- Company news requires a verified company site, an individual direct article
  page, a non-generic title, substantive text, an explicit publication date,
  source URL, retrieval time, and content hash.
- Company jobs require that same site gate plus a direct detail URL, structured
  `JobPosting`, substantive text, direct application signal or non-expired
  deadline, and no conflicting named hiring organisation.
- A bounded conventional URL-set sitemap fallback can find direct site pages
  absent from navigation. It makes one sitemap request at most, never recurses
  through a sitemap index, and keeps the robots, public-network, same-domain,
  byte, timeout, and page-count rules.

## What this does and does not establish

The local release check establishes reproducible execution for the checked
inputs. It cannot establish the score, precision percentage, recall, or
qualification outcome on Builderr's hidden batch. The official evaluator is
the authority for those results.
