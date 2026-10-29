# Historical decision review v1

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted-conditional for the exact retained retrospective archive; [acceptance](../acceptance/historical-decision-review-20261029.md) |
| Parent | [Reusable learning assets](learning-assets-v1.md) |
| Work items | META-LEARN-001 / #540; existing #545 held-position interface |
| Implementation | `src/quanthus/expert_strategy/decision_review.py`, `scripts/learning_decision_review.py` |
| Contract | `config/research/decision-review-delivery.v1.json` |
| Tests | `tests/unit/test_decision_review.py`, `tests/contract/test_learning_consumer_example.py` |

## Consumer and scope

The Owner and research callers need to answer why an already-recorded held-position
decision occurred, with its original event, facts and sources. This reader consumes the
exact previously-consumed #545 selection, not an arbitrary warehouse or a new candidate.
Business dates remain 2012–2021. The actual retrospective source cutoff remains
`2026-10-25T08:51:06.934678+00:00`; it is not historical information availability.
No training, model score, Facts recomputation, policy evaluation or economic replay runs.

The frozen selection has 351 members / 104,033,820 bytes and 8,393 reviews over 538
episodes, 343 issuers and 8,380 security dates. A security/date alone is ambiguous.
The key is `(market_date, security_code, unit_id, entry_key)`; entry date is retained.
The original position state is preserved, including `trial`.

## Dependency graph and artifact lifecycle

`exact selection + contract + ordinary code -> authenticate members -> project once ->
immutable review package -> JSON / offline HTML queries`.

Source authentication and projection are shared prerequisites. A routine query reads
only the package. A separately registered independent construction deliberately rebuilds
the projection; it is not a cache hit. The package is a job-local research delivery,
not a published ADM or a new authorization to retain/reuse market data across jobs.
Its retention follows the owning job's explicit policy.

## Contract

- Build verifies every exact source member's SHA256 and size, physical paths, unchanged
  stats, complete event census, source cutoff, feature identity and source linkage before
  creating the output directory. Existing outputs are never overwritten.
- Each row retains baseline action, required action, reason and decision digest; original
  feature Decimal strings/nulls and lineage digest; retained structure input; and the old
  parked shadow suggestion separately. Four source locators identify original member
  digests and row/field positions. Original paths are inert provenance during reading.
- The manifest binds the selection, contract, loaded Quanthus source files, retained
  producer code/config and source-manifest identities, census, payload hash and stage counts.
  The package contains exactly `reviews.jsonl`, `manifest.json` and `READY`, written last.
- Verify/query require a trusted manifest SHA256 supplied by the caller. They validate the
  exact physical set, independent regular files, READY, hashes, unique ordered event keys,
  population, retrospective scope and member references. They never reopen source roots.
- Query filters include security, date, unit, episode and recorded reason. Results report
  matched/returned/truncated counts and are limited to 100 rows. JSON/HTML outputs are
  exclusive new files outside the package and original source directories.
- HTML is escaped, offline, and has a restrictive CSP with no script or network dependency.
  It displays recorded facts and reasons; it does not infer a missing explanation.

Missing, changed, duplicate, aliased, out-of-scope or conflicting input fails closed.
An interrupted build retains its evidence without READY. Corruption or a missing package
file never triggers a fallback. The expected manifest digest is the trust root, not a claim
that a maliciously replaced digest or an arbitrary package is scientifically qualified.

## Meaning and limits

Unknown structure stays unknown and cannot create a forced sale. Existing hard exits remain
recorded independently. The original 3,029 unavailable observations, their separate reasons
and 535 required exits are retained. The old shadow is explanation-only: neither 12 example
cards nor the one changed decision supplies new independent evidence of its usefulness.

The retained held trace does not observe a learned-model call, alternative entry timing,
allocation or fills. These remain `NOT_OBSERVED`, not proof of absence across Quanthus.
Model/purpose identities in the separate calling example are preserved while pending models
remain ABSTAIN/NOT_GRANTED. This engineering delivery changes no model-quality, investment-value,
P6 or trading admission. Operation is described in the
[consumer guide](../operations/learning-assets-consumer.md#review-an-already-recorded-historical-decision).

## Original B decision-chain companion

The Owner-approved `RECORDED-B-DECISION-CHAIN-001` delivery adds a separate reader for
original B entries, original simulated order/intent/status/fill events, held reviews,
corporate actions, exits and recorded ledger context. It does not expand the held-only
reader's claims. Implementation: `src/quanthus/expert_strategy/decision_chain.py` and
`scripts/learning_decision_chain.py`; exact contract:
`config/research/decision-chain-delivery.v1.json`; synthetic regressions:
`tests/unit/test_decision_chain.py`. Its own acceptance is recorded in
[the decision-chain record](../acceptance/recorded-b-decision-chain-20261029.md).

The stage graph is `exact B selection + sealed held projection -> authenticate original
trace/body hashes -> build one immutable chain index -> read-only JSON/offline HTML`.
It reuses #580; it does not rematerialize Facts or execute a policy, model or backtest.
The self-contained job-local research package copies original member bytes into independent
files, includes the original manifests/receipt, exact contract, index and complete file
hashes; READY is last. Its trust root is a caller-supplied manifest SHA256. Cold validation
rechecks retained provenance and independently recomputes the index from local records,
without dereferencing archival paths. An arbitrary replacement trusted digest does not
confer scientific qualification.

The key is the original entry decision key; original coordinates supply security/date.
A signal joins by exact formal decision digest, Shanghai decision date, unit and side,
with a security check. Order/intent/fill/status/ledger/action references use their explicit
ids. Local engine sequence numbers never group the globally exported events. Snapshots
and settlement context join only a unique actual filled unit interval through its exit
session. Initial empty snapshots remain visible in the package census, not falsely
assigned to a position. Multiple units remain separate.

`allocated=true` means the producer considered the opportunity; it does not establish a
reservation or order. Held decision rows were recorded before execution with `filled=false`;
actual exits come from explicit original fill events, not that flag. Unrecorded allocation
or rejection reasons remain unknown; original status reasons are retained. End-of-run unit
ledgers are labelled separately and are not per-episode performance. Day-only decisions
remain day-only; HTML groups by Shanghai date and sorts only timed engine events by instant.
Decimal strings, original time strings, nulls, unknown structure and parked shadow records
are unchanged. No missing value creates an exit.

This companion preserves 22,955 entry decisions, 8,393 held decisions, 17,062 engine events,
538 filled entries and 535 filled exits. The exact original 2012–2021 scope and actual
2026 retrospective cutoff remain; all execution is original simulation. Counterfactuals,
live execution, historical PIT and adoption of any learned model remain unqualified.
Neither this delivery nor PR merge starts a new comparison or P6.

## Entry funnel and optional allocation observation

`ENTRY-ALLOCATION-DELIVERY-001` / #540/#545 reuses the authenticated B package once
to produce a Chinese offline funnel and identity-bearing JSON through
`expert_strategy.entry_funnel` / `scripts/learning_entry_funnel.py`. The full
22,955-opportunity denominator has 14,062 trial-entry actions, 543 buy orders,
538 filled entries and 5 unfilled entries. Of the trial-entry actions, 13,519
have no observed order. The selected manifest members contain no original
allocation result; their reasons remain `NOT_RECORDED`, not inferred capacity
rejections. Strategy abstentions (8,893) and original buy-status reasons are
shown separately. Sell/held statuses never become entry-rejection reasons.
The report binds the trusted package/contract/member identities and original
scope, and includes exact decision/event row locators. It exposes no prices
or performance statistics and runs no allocator or replay.

For a future separately authorized replay, `FormalP2ReplayRuntime.run_variant`
accepts the keyword-only `allocation_audit=True` (default false). Immediately
after the existing allocator call, it serializes that same result once,
including its exact ordering, priorities, disposition, capacity/reason and
reservation references. Explicit opportunity, entry and formal-decision keys
connect every candidate, including those not requesting allocation. The
pre-execution available-unit/active-lifecycle identities, Shanghai business
date, original decision/snapshot cutoff and actual UTC observation time are
retained; observation time is not provider acquisition or historical knowledge.

The additive `entry-allocation-audit.json` has schema
`quanthus.entry-allocation-audit.v1`, its own digest, and complete hashes of
the seven original replay files. Its identity includes the actual run/data/
feature/strategy/topology/industry, execution policy, population and source
metadata, scope, and repository code/config hashes. It is an operational
sidecar: observations may have different timestamps/digests between runs.
It is appended after the original replay digest and manifest are computed;
original decisions, events, equity, returns, ledgers, reconciliation and
manifest stay byte-identical with audit on or off. No allocation rule, legacy
flag, economic identity or activation gate is changed. Default callers receive
exactly the old file set; audit-enabled callers must retain the separate
sidecar under their owning job's budget and may not graft it onto old archives.

Tests: `test_entry_allocation_audit.py` and `test_entry_funnel.py`. Independent
immutable-base behavior/cost and isolated reader evidence are recorded in
[this delivery acceptance](../acceptance/entry-allocation-delivery-20261030.md).
This is not an approval to rerun historical trading to fill missing reasons.
