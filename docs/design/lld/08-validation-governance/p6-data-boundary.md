# P6-DATA-001 Prospective DWD and Access Boundary

| Field | Value |
| --- | --- |
| Parent | `p6-forward-validation.md` |
| Design status | approved |
| Delivery gate | implementation under P6-DATA-001 |
| Owner value decision | `config/forward-validation/entry/p6-data-001-owner-value-decision.v1.json` |
| Machine policy | `config/forward-validation/data/p6-prospective-data-boundary.v1.json` |
| Runtime contract | `src/quanthus/forward_validation/data_boundary.py` |
| Execution authority | none; data-boundary implementation only |

## 1. Purpose

P6 needs one authoritative answer to four questions before scorecards or a Forward Worker may exist:
which daily DWD release was consumed, when it was demonstrably visible to the P6 consumer, whether
that release may count as official prospective evidence, and what happens if later data corrects it.

This child does not analyze strategy results. It binds and audits data-time authority.

## 2. Daily DWD identity

The only first-program input is an immutable daily DWD domain increment accepted by
`verify_daily_domain_increment`. The P6 binding records:

- package id and market date;
- manifest SHA-256 and content digest;
- exact code revision;
- verified READY/inventory state; and
- row-count/payload diagnostics.

The daily DWD semantic identity does **not** contain wall-clock visibility time. A build that is
semantically identical does not become a different market fact merely because another consumer saw
it later.

## 3. Operational visibility receipt

P6 separately records a `quanthus.p6-dwd-visibility-receipt.v1` with the exact package identity,
`ready_observed_at`, `consumer_visible_at`, source node and deterministic receipt digest.

`ready_observed_at <= consumer_visible_at` is mandatory. The receipt is operational evidence, not a
DWD business fact. A receipt whose package/content identity does not equal the deeply verified
package fails closed.

This separation prevents either of two invalid shortcuts:

- treating market date as proof that Quanthus knew the package at T; or
- embedding consumer wall-clock time into immutable business-fact identity.

## 4. Prospective admission

`P6-GOV-001` and `P6-DATA-001` never activate the program. Without an enabled
`P6-ACTIVATE-001` decision every release is `engineering_rehearsal_only_not_budget`.

After activation, an official candidate release must satisfy all of these conditions:

1. the activation decision is enabled and digest-bound;
2. `market_date` is strictly later than the activation decision's Asia/Shanghai market date;
3. the DWD package passes deep verification and the visibility receipt binds it exactly; and
4. consumer visibility is not earlier than the activation decision.

An already-known 2026 release, the activation-date release, or any pre-activation release can be
used only for rehearsal and never backfilled into the official 66-session budget.

## 5. Access ledger

Every P6 data read produces one deterministic append-only
`quanthus.p6-forward-access-ledger-entry.v1` containing:

- purpose;
- release/package identity;
- decision knowledge cutoff;
- actual consumption time;
- entry-proposal and activation-decision digests;
- official/rehearsal disposition; and
- partition id.

The first program admits only partition `prospective_daily_dwd`. Arbitration 2022-2024 and permanent
blind 2025 are explicit typed failures rather than hidden filesystem conventions.

`consumed_at` may occur after T close/READY; it is an audit clock and never changes the declared
T-close knowledge cutoff. A read before the recorded consumer visibility time fails closed.

## 6. Correction disposition

A later correction never overwrites a prior observation, access record or Run. It creates a new
immutable release identity and one append-only `quanthus.p6-correction-disposition.v1`:

- `preserve`: correction does not change the admitted evidence/decision meaning;
- `degrade`: evidence remains visible but its quality/claim ceiling is reduced; or
- `censor`: the affected evidence is excluded from quality/economic claims under the frozen rule.

Every disposition names the prior and correction package/manifests, affected evidence ids,
rationale and recorded time. `rewrite_prior_evidence` is permanently false.

## 7. Failure semantics

Fail closed on package/READY verification failure, visibility-binding drift, reversed visibility
times, invalid code revision, activation/market-date violation, pre-visibility reads, cutoff/date
mismatch, sealed-partition access, correction identity reuse, duplicate affected-evidence ids, or
attempted downstream authority expansion.

Missing or invalid data blocks new future entries later in P6; this child does not turn missing data
into bearish structure or a forced sale.

## 8. Authority and successors

The post-entry owner value decision opens only P6-DATA-001. This child may not enable scorecards,
Forward Worker, paper runtime, Web, official prospective cycles, Stage action semantics, P5,
notification, broker, live capital, arbitration or permanent blind.

Only accepted P6-DATA-001 evidence may make `P6-SCORE-001` implementation-ready. It does not open the
scorecard child automatically.
