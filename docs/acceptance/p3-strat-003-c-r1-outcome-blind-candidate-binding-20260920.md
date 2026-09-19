# P3-STRAT-003-C-R1 Outcome-Blind Candidate Binding Acceptance — 2026-09-20

| Field | Value |
| --- | --- |
| Work item | `P3-STRAT-003-C-R1` / GitHub #385 |
| Decision | `PASS_OUTCOME_BLIND_CANDIDATE_BINDING_REPAIR` |
| Parent | `P3-STRAT-003` / GitHub #317 |
| Parent engineering acceptance | `P3-STRAT-003-C` / GitHub #320 |
| Implementation commit | `e24319c272842354ba3b7caaa8e1768b8ed0c569` |
| Base revision | `05bc108a40b7c1e21bca22f264227a96390d121c` |
| Runtime contract | `config/research/p3-strat-003/runtime.v1.1.json` |
| Runtime digest | `sha256:bd9422c5dcc2dbb0cb989fdea3075a76eafcbb75f4e6527b0c1d762efae49238` |
| Recent-history outcome / strategy-result read | **none** |
| P6 mutation / deployment | **none** |

## Finding and repair

B input design review found that the first C implementation inherited the legacy
`P3-ATTR-001` population resolver. That resolver accepts only a complete outer P4 Run containing
both an Event Library and an Outcome Label Corpus.

That requirement is correct for the already accepted 2020–2021 formal-attribution path, but is
incorrect for P3-STRAT-003 before D: creating or verifying the 2025/2026 Outcome Label Corpus in B
would expose result-bearing future outcomes before the frozen D gate.

C-R1 repairs only that integration seam.

The legacy `FormalP2ReplayRuntime` default path still requires one exact verified P4 outer Run.
The recent-history `P3Strat003ReplayRuntime` may instead bind one B-produced
`quanthus.p3-strat-003-candidate-input.v1` manifest. Exactly one population source is required;
supplying neither or both fails closed.

## Candidate-input contract

The bounded candidate manifest is outcome-blind and binds:

- the A request digest;
- exact 2025-01-01..2026-09-17 decision scope and 2026-09-18 execution end;
- one immutable candidate-events JSONL file and SHA-256;
- one exact static-industry concentration mapping and SHA-256;
- one replay-bundle digest;
- event count and a semantic manifest digest.

The loader verifies every EventRecord identity, the frozen scope, candidate count, unique event ids,
listing code, static-industry membership and physical file hashes before replay construction.
A candidate outside the frozen scope, changed file, changed industry mapping, request mismatch or
bundle mismatch fails before result-bearing replay.

The recent-history batch identity now binds this candidate-manifest digest instead of pretending it
is a legacy outer Event Run digest.

## Runtime contract

`runtime.v1.1.json` supersedes the C v1 runtime only for the recent-history population boundary:

- D uses `--candidate-input-manifest`;
- `--event-run-digest` remains the legacy compatibility argument;
- the two arguments are mutually exclusive;
- outcome-label binding is explicitly forbidden before D;
- provider partition remains `p3-strat-003-recent-history`;
- B/T/R/TR, resource limits, cold-reproduction rules and P6 non-mutation remain unchanged.

No expert, threshold, candidate rule, aggregator precedence, P2 matcher, position rule or cost rule
changed.

## Verification

Explicit focused verification after the repair:

- candidate-input / P3-STRAT-003 runner / formal strategy / formal attribution adapter:
  **31 passed / 0 failed**;
- broader replay-provider / expert-strategy / formal-attribution / planning regression:
  **78 passed / 0 failed**;
- P3-STRAT-003 planning plus current-phase traceability assertions:
  **PASS**;
- `quanthus doctor`: **PASS**;
- compileall: **PASS**;
- planning-check: **PASS**;
- Ruff on all changed Python/test files: **PASS**;
- `git diff --check`: **PASS**.

A full repository `make check` was also executed as one real process. The MCP foreground response
timed out while pytest was still actively using CPU/IO, so no invented monolithic pass count is
reported. The original process was allowed to finish; `.pytest_cache/v/cache/lastfailed` was empty
after completion and no test process remained. Duplicate tool-replay processes were terminated and
were not used as evidence. The explicit focused and broader suites above carry the durable numeric
evidence for this repair.

## Conformance review

The repair was reviewed against #385 and the frozen A request, not only against green tests.

Accepted properties:

- B remains outcome-blind and does not need to produce target labels;
- D remains the first gate allowed to settle or inspect recent-history outcomes;
- legacy 2020–2021 outer-P4-Run binding remains the default formal-attribution path;
- candidate input cannot silently cross the frozen scope or swap industry/replay identities;
- batch resume identity continues to fail closed on semantic input drift;
- P6-v1 Worker, controller, paper ledger, budget, run roots and mutable pointers are untouched.

## Next gate

C-R1 closes the candidate-population integration defect but does **not** open D by itself.

B / #319 must still publish and accept the exact immutable recent-history replay input, candidate
manifest, PIT/coverage/exception evidence and isolated run roots. D / #321 remains blocked until B
closes.

## Claim ceiling

This acceptance proves only that the recent-history runner can consume an outcome-blind candidate
population without weakening the legacy formal strategy path.

It does not prove that B's physical data are qualified, that any 2025/2026 candidate set has been
built, that Trend or Position Risk adds value, that D has run, or that any strategy should be
promoted.
