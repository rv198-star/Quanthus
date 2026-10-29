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
