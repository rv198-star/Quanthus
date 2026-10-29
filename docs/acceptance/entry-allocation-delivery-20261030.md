# Original-B entry funnel and optional allocation observation — 2026-10-30

| Field | Value |
| --- | --- |
| Parent | [Historical decision review](../design/historical-decision-review-v1.md#entry-funnel-and-optional-allocation-observation) |
| Work | META-LEARN-001 / #540; existing #545 decision interface |
| Authority | Owner approved the PR #588 Astra review's bounded steps 1–3; maximum three hours |
| Local code parent | `d436329f66f712863fc26f277cec064d90535525`; its tree equals merged main `937edee15c3fd0533766f38716e9d79d09ef0174` |
| Delivery gate | Engineering evidence below; no new scientific, production or P6 acceptance |
| Proposal | `config/planning/entry-allocation-request.v1.json`; executed v2 proposal and authority frozen externally |

## 1. Exact historical boundary

The existing self-contained original-B package is reused, not rebuilt:
`cn02:/srv/quanthus-base22-data/jobs/decision-chain-delivery-20261029T145734Z/outputs/chain-package`.
Trusted manifest SHA256:
`aa9b7449c552be317d61dd032b47a847771a96797220b4cec54907cbc8917738`.

The selected original manifest/completion/receipt members contain no saved allocation
batch. No broader archive hunt or new economic replay was performed. The earlier
audited census is reused and the delivered report agrees with an independent join
from original formal-decision digest/date/unit through explicit signal/intent/order ids:

| Recorded stage | Opportunities |
| --- | ---: |
| Complete entry denominator | 22,955 |
| Scoring abstention / no action | 8,893 |
| Trial-entry action | 14,062 |
| Trial-entry action without observed buy order | 13,519 |
| Actual buy order | 543 |
| Filled buy | 538 |
| Buy order without fill | 5 |

The 13,519 missing allocation explanations remain `NOT_RECORDED`. An action is not
a reservation, and absence of an order is not evidence of an erroneous decision or
lost profit. Original buy-status reasons are available separately. The exact 2012–2021
scope and actual retrospective cutoff `2026-10-25T08:51:06.934678+00:00` stay unchanged.

## 2. Actual delivery and unchanged behavior

Job root: `cn02:/srv/agentdock/.codex/entry-allocation-delivery-20261029T163322Z`.

- `outputs/entry-funnel.html`: Chinese offline report, complete denominator and original
  row examples. `outputs/entry-funnel.json`: package/contract/member/purpose identity,
  precise counts, unknown coverage and explicit original row references. Together
  these occupy 10,226 bytes; no prices, model arrays or economic metrics are exported.
- `evidence/funnel-delivery.safe.json`: authenticated source hashes, unchanged physical
  stats, independent event join and prior-census comparison. Verification/projection is
  shared by both outputs; original package/source files are never changed.
- Optional `allocation_audit=True` adds one versioned operational sidecar to an already
  authorized `FormalP2ReplayRuntime` call. It serializes the existing allocation batch
  once, before execution mutations, with original candidate/decision/priority/capacity/
  reservation references. Default callers retain their original interface/file set.
- `evidence/synthetic-behavior-cost.safe.json`: methods from the immutable original
  commit, new audit-off and new audit-on produce byte-identical original decisions,
  engine events, equity, returns, final ledgers, reconciliation and manifest. Only the
  two intended runtime methods differ structurally. The optional audit remains outside
  the original replay digest. The actual allocator still runs exactly once per frame.

Three synthetic off/on measurements were approximately 2.6–3.2 ms versus 76–82 ms,
with 121,708 additional bytes for the one-frame sidecar including the once-per-run
full code/config identity. These are tiny synthetic measurements, not a claim about
complete historical replay overhead. New operational observation times are real UTC;
they do not recreate old provider acquisition or historical knowledge times.

## 3. Checks and independent verification

Focused regressions: **68 passed**, including 12 new tests. They exercise no empty
unit, industry/daily-cap rejection, equal-score ordering, reservation, unfilled buy,
strategy abstention, genuine historical unknowns, direct allocator-result equality,
old-output equality, no additional allocator calls, source-output overlap, exclusive
outputs, bad trusted digest and escaped offline HTML. One initial synthetic fixture
omitted an existing required position field; its failure is retained and the fixture
was corrected without changing production rules.

The new report was independently recreated in a fresh `bwrap` mount/network namespace
with cleared environment, only ordinary code/runtime and the exact read-only package.
Native `strace` confirms no original source roots opened and no network connects.
JSON/HTML hashes match the delivered outputs; a wrong trusted digest is rejected.
Original #588 independent construction/two-location/corruption evidence is reused by
identity; it is not claimed to have been reconstructed again in this reader-only batch.

`make check` passes on Python 3.10.12: **5,734 passed, 20 skipped, 8 warnings**, pytest
653.84 seconds, complete command 659.95 seconds, exit 0. The 20 skips are five missing
disposable PostgreSQL configurations, two absent production-artifact mounts, three
optional sklearn cases and ten optional torch cases. No assertions or test selection
were weakened. The TMP parent is executable; only this job's passed function TMP is
reclaimed, with failures/shared TMP retained. All 1,484 source/config/test/script files
remain byte-identical before and after the complete check.

Changed-file Ruff and the new reader's strict mypy pass. Whole Ruff retains exactly
216 old diagnostics, all in files byte-identical to the merged base. The changed
legacy runtime retains three pre-existing mypy diagnostics, independently compared
to the exact base with local import resolution; whole-repository mypy is not newly
qualified. Exact-base blueprint/conclusion history checks and live issue reconciliation
pass. Hosted CI remains unavailable by Owner instruction; Web and Python 3.12 are
not newly tested.

The check's sampled peak RSS is 504,025,088 bytes and job allocation 49,213,440 bytes;
minimum system free space is 9,015,410,688 bytes, above the frozen 8 GiB floor. The
54,128,640-byte isolated worktree is separately accounted for; together with job
materials this stays within the 192 MiB envelope. No source/old-job cleanup or full
market-library copy is needed. Failed proposal/fixture/import-resolution attempts
and bounded Git fetch failures remain in the handoff, not counted as passes.

## Disposition

Steps 1–3 are closed at the declared engineering boundary with the final local-check
receipt. Blueprint EXPERT/REPRO/CLOSEOUT engineering coverage
is updated, with a separate unchanged TRACE review. Research quality, model value,
prior negatives, exposure/cumulative cost, missing historical PIT and P6/production
admission remain unchanged. The historical recovery branch stops at missing records;
no new comparison, collection, fit, threshold/allocation-policy change or real replay
is activated. A concrete unmet consumer decision is required before another research
batch, not merely the ability to generate more audit records.
