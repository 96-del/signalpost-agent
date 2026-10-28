# Signalpost optimization and release control

The [published Signalpost challenge page](https://builderr.ai/challenges/signalpost) is the scoring and
submission source of truth. As checked on 2026-10-16, qualification requires at least 65/100 on an
official run. The four axes are recall and coverage (50), precision and evidence (30), synthesis (12),
and UX (8). Builderr supplies fresh company batches; local scores and precomputed profiles are not the
official result.

An evaluator clarification received on 26 October 2026 confirmed that the
evaluator passes an organisation-number file and, when required, the frozen
registry file; the documented one-command runner must emit `OUTPUT_CONTRACT.md`
envelopes. The participant-received limits are 45 minutes and 2,000 requests per
100 companies, with public internet and no credentials. This clarification
supplements the published contract; the original message remains the authority.

The operational objective is therefore two-part: (1) finish the evaluator-supplied batch, returning one
terminal envelope per input within the supplied resource budget; and (2) improve verified recall without
publishing unsupported or wrong-company facts. Do not optimize against obsolete local rubrics or call a
local smoke result a qualification score.

## Evidence-led loop

1. Record the exact commit, locked install command, runner command, input batch digest, outputs, and run
   report before changing the candidate.
2. Use official evaluator feedback as the score authority. Use local tests only to diagnose specific
   failure modes, not to predict a score.
3. Choose one change tied to a measured loss or a reproducible operational defect. State the source,
   rights, identity evidence, freshness rule, expected runtime/cost, and failure behavior first.
4. Add a regression test for the highest-risk failure, then run the locked default install, test suite,
   and bounded end-to-end batch.
5. Keep a connector only if it adds independently verifiable facts on unseen companies without weakening
   entity precision, source rights, or run completion. Otherwise leave it disabled.
6. Freeze the exact commit before evaluation. Never change the code after sending its hash and call it the
   same version.

## Stop conditions

- Stop/roll back on a material wrong-company publication, fabricated or unsupported claim, missing input
  envelope, prohibited request, unaccounted credential, or reproducible clean-install failure.
- Do not publish a source snapshot unless its pagination is exhausted, its freshness is suitable for the
  claim, and its reuse terms are satisfied.
- Do not enable a connector solely because a local score suggests more coverage. Require exact-entity
  evidence, rights review, bounded runtime, and test cases for collisions and missing data.
- Treat `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, and `failed` as distinct
  outcomes. Missing information is not zero.
