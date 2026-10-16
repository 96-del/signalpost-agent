# Evidence-grounded review and next strategy

Checked 2026-10-16. This is a decision memo, not a score prediction. The challenge page and source-owner
documentation are authoritative; local runs establish only what happened in this checkout.

## Verified constraints

- Builderr currently asks for a repository, exact commit, one reproducible run command, a 100-company smoke result,
  declared models/APIs/licences/cost/contact, and one complete terminal result for every supplied company.
  The qualification bar is 65/100 on an official run. Scoring is 50 recall/coverage, 30 precision/evidence,
  12 synthesis, and 8 UX. Sources: [challenge page](https://builderr.ai/challenges/signalpost) and its
  linked [evaluation contract](https://builderr.ai/docs/signalpost-evaluation-harness.md). Its live page
  states the entry should handle 1,000+ profiles, that Builderr supplies the official batch, and that
  local smoke tests are operational only. The public board was last marked reviewed 5 October 2026; its
  displayed standings may lag new runs.
- Brønnøysundregistrene documents exact-organisation-number lookups, roles, group structure, subunits,
  updates, and bulk CSV. Its open-data API documentation identifies NLOD as the licence. Source:
  [official Enhetsregisteret API documentation](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html).
- NAV permits use of its job-ad API for display and analysis, but consumers must promptly remove/update
  inactive or changed ads and handle personal data lawfully with retention limits. Source:
  [NAV job-ad API terms](https://arbeidsplassen.nav.no/vilkar-api).
- Public Nominatim is not a bulk lookup service: its policy sets an absolute maximum of one request per
  second, recommends caching, and discourages periodic bulk geocoding. Source:
  [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).
- Similar registry/data systems reinforce a useful architecture distinction: entity lookup by a stable
  identifier is materially safer than loose name search. OpenCorporates documents deliberately loose
  name search; GLEIF offers LEI lookup and parent/child relationships but only for entities covered by
  LEI records. These are design comparators, **not approved Signalpost sources**: [OpenCorporates API
  reference](https://api.opencorporates.com/documentation/API-Reference), [GLEIF API](https://www.gleif.org/en/lei-data/gleif-api).

## What comparable agent benchmarks actually show

- WebArena is a reproducible, self-hosted web-agent benchmark. Its paper reported 14.41% end-to-end task
  success for its best GPT-4 agent versus 78.24% for humans on that benchmark. It measures interactive
  website task completion, not company-fact recall, so the percentages must not be transferred to
  Signalpost. It supports testing end-to-end outcomes and failure cases rather than assuming an agent
  works because individual tools do. Source: [ICLR 2024 WebArena paper](https://proceedings.iclr.cc/paper_files/paper/2024/hash/4410c0711e9154a7a2d26f9b3816d1ef-Abstract-Conference.html).
- OpenAI's BrowseComp has 1,266 hard web-research questions. Its reported accuracy ranged from 1.9% for
  GPT-4o with browsing to 51.5% for a system trained specifically for BrowseComp. These are not
  company-profile metrics. The authors explicitly report that adding browsing alone was insufficient;
  search strategy and synthesis mattered. Source: [BrowseComp](https://openai.com/index/browsecomp/).

Practical implication: don't replace the deterministic identity/provenance pipeline with a generic
browser agent based on benchmark headlines. If an LLM or search planner is added later, keep it on the
candidate-discovery side; require deterministic exact-identity checks, source-span evidence, abstention,
and held-out evaluation before publishing a fact. Measure with the evaluator's actual batch and score.

## What this repository/run proves

- Commit `c0a25eea9ce26802a9c1187442866e41619e273c` passed `uv sync --locked` and the documented test
  command: 117 tests and 5 subtests.
- The 2026-10-16 local 100-company run emitted 100/100 envelopes and passed its validation checks. The
  output contains 902 evidence records; in that output, each evidence record had a source URL and
  retrieval date, and each available claim referenced an emitted evidence ID. One website lookup is
  explicitly `failed` because `tnbbl.no` did not resolve. This is a captured source failure, not proof of
  zero failures on another batch.
- The run report says the NAV connector was skipped. The saved NAV metadata says `feed_exhausted: false`
  after 3,163 pages / 3,163,000 scanned items; a 2026-10-04 NAV report found zero matches in its 100
  profiles. This cache is neither an exhausted snapshot nor evidence of NAV recall. Do not enable it or
  describe it as complete.
- The local site-activity extractor recorded 2 observations across 100 profiles and the company-site
  news/activity extractor recorded 0. That is the observed local result, not a coverage estimate for the
  official batch.
- Local validation is not an official score. The current public board reports no qualified entry; its
  top listed score is 60.80/100, with 13.82/50 recall and coverage. That makes verified recall the likely
  strategic bottleneck for the competition, but it does not establish this entry's own score.
- `scripts/score_competition_v3.py` encodes an earlier external-first 55/15/10/12/8 rubric. That rubric
  is obsolete and the script cannot estimate the current official score. Do not use its
  `qualification_passed` or `awardable_score` output to make submission decisions.

## Decision

Keep legal identity anchored on BRREG organisation numbers. Preserve all claims as source-linked,
time-stamped observations and abstain when identity or rights are uncertain. The current default source
set is reproducible, but local observations demonstrate sparse external activity, so a score above 65
cannot be inferred from its smoke test.

Do not turn on the partial NAV cache or public Nominatim to chase volume. A defensible recall expansion
must first name a permitted source, show exact-company linkage, define update/deletion and retention
behavior, quantify requests/runtime/cost, and prove coverage on a held-out batch. Candidate discovery
services or global registries must not be treated as evidence until their licence, storage, cost, coverage
of Norwegian SMEs, and exact-identifier match are confirmed for this evaluator.

## Next actions

1. Publish the exact tested commit and send Builderr the smoke report and runner command; request the
   evaluator's score breakdown and confirm which revision hash was recorded. Do not wait for a reply
   before sending the candidate.
2. Ask for the actual run budget and official input/output contract if those have not been supplied.
   Don't guess concurrency, source quotas, or the meaning of any internal environment variables.
3. On evaluator feedback, spend a remaining revision on the largest measured loss. If recall is the gap,
   research a lawful source with exact Norwegian company IDs first; if delivery/timeout is the gap,
   optimize bounded concurrency/checkpointing before adding sources; if precision is the gap, tighten
   attribution and abstention.
4. Use the official challenge page—not this memo—for deadlines, revision allowance, and final terms.
