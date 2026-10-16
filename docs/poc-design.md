# 1,000-company POC design

## Problem

Build a reusable agent that can create useful, source-attributed profiles for all registered
Norwegian entities. The first proof is a 1,000-entity run, not a nationwide production claim.
Annual accounts apply to accounting-obliged entities, not every registry entity: AS/ASA generally
file, while many sole proprietorships do not. `not_required` and `not_filed` must not be scored as
crawler failures.

## Baseline

The baseline is the Brønnøysund bulk entity record only. Every enrichment module must improve a
measured field or evidence category over that baseline without reducing identity accuracy or
provenance quality.

## Modules

| Module | Output | Ground truth / evaluation |
|---|---|---|
| Registry | identity, status, form, NACE, employees, address, registered website | Exact bulk/API equality |
| Financials | latest official annual-account fields and reporting period | Official Regnskapsregisteret response |
| Financial history | available filing years and official PDF-copy links | Official annual-account copy endpoint; rate limited |
| Roles | named role holders and role types | Official public role endpoint |
| Group | official group relationships | Official group endpoint; 404 is an explicit state |
| Locations | registered subunits/establishments | Official subunit endpoint |
| Website | title, description, structured organisation/location data | Registry-linked site; held-out human precision audit |
| Social links | declared LinkedIn/Facebook/Instagram/X/YouTube URLs | Links present on the company-controlled site |
| Signals | dated company news/press items | Company-reported evidence only; no sentiment claim without a labelled audit set |

The evidence agent routes overview, financial, leader, location, and social questions to these
records before composing an answer. It returns source URLs with every fact. Sentiment deliberately
abstains until a labelled Norwegian news/social evaluation is available; company-owned marketing
text is not treated as independent sentiment.

## Representative sample

Use a frozen 1,000-entity corpus with two reported slices:

- 700 population-weighted entities that estimate real registry performance;
- 300 stress cases covering ambiguity, adverse/deleted state, sparse filings, groups/subunits,
  diacritics, missing/redirecting websites, uncommon forms, and rare industries.

Strata combine:

- legal form: AS, ASA, ENK, public/municipal forms, associations/foundations, and other;
- employees: missing/zero, 1-4, 5-19, 20-99, 100+;
- state: active, bankrupt/liquidating/deleted;
- geography: Oslo, other large municipalities, and the rest of Norway;
- registered website: present/absent.

Split the frozen corpus by a stable hash before development: 600 development, 200 validation, and
200 final held-out. Do not tune repeatedly on the final 200. Report population and stress results
separately; a stress-oversampled aggregate is not nationally representative. The sampler records
the snapshot ETag/hash and seed.

## Score and control loop

The [official challenge page](https://builderr.ai/challenges/signalpost), checked 2026-10-16, is the
source of truth: qualification requires at least 65/100 on an official run, scored across recall/coverage
(50), precision/evidence (30), synthesis (12), and UX (8). Builderr supplies the evaluation batch; local
proxy scores do not qualify an entry or predict its official score. Within recall/coverage, the page says
70% of each information type's coverage score is based on company reach and 30% on the number of checked
facts found.

The following are proposed **internal engineering targets**, not Builderr thresholds or guarantees:

- Registry identity mismatches: 0/1,000.
- Evidence-schema validity: 100%.
- Official endpoint outcomes classified: 100%, including explicit 404/blocked states.
- Financial parsing success: at least 95% of HTTP-200 responses.
- Website fetch success: at least 75% of valid registry website URLs.
- Social-link precision: at least 95% on a 100-company held-out manual audit.
- Resumed run duplicates: 0.
- P95 official request latency: reported, not hidden behind an average.

Each module records baseline, current score, target, error buckets, latency, requests, and bytes.
The next iteration must address the largest recoverable error bucket. Stop a module after two
iterations without a material score improvement, and report the blocker rather than widening claims.
See [`external-footprint-loop.md`](external-footprint-loop.md) for internal connector metrics and gates;
they are research controls, not a substitute for Builderr's official scoring.

## Official qualification constraints

There are no project-invented qualification thresholds. The published qualification requirements include
one result for every supplied company; distinct terminal states for available, not_available, blocked,
not_applicable, ambiguous, and failed; source, retrieval date, and relevant reporting period for claims;
safe and reproducible execution; and no material wrong-company match. Consult the official page and linked
evaluation contract for the current full terms.

## Evidence that changes the plan

- If official financial coverage is too sparse by entity form, financials become an availability
  layer rather than a universal profile requirement.
- If website URLs are missing for most companies, lawful search-provider access becomes a separate
  paid connector; result-page scraping is not the fallback.
- If social or sentiment precision cannot be measured on held-out labels, those fields remain
  company-reported context and do not enter scoring.

## External-footprint qualification

External information can improve recall/coverage, but unqualified connectors remain quarantined.
Search output can nominate a URL but cannot prove company identity. A sentiment item needs exact-entity
and source-span checks; a company rollup needs multiple independent sources. No external module earns
trust before the held-out evidence and rights gates in [`external-footprint-loop.md`](external-footprint-loop.md).
