# #545 / #546 independent review — 2026-10-28

Work items: P3-EXPERT-007 / #545 and META-LEARN-002 / #546, under #540.
Authority: [held-path contract](../design/lld/04-experts-aggregator/expert-strategy-layer/held-structure-shadow.md),
[blueprint mechanism](../design/blueprint-progress.md), live issues and the immutable
original MVP plan at `e79eeb027917de4f6a48abf563569b55f3e7c91b`.
This document is dated audit evidence. It grants no new research, data or deployment authority.

## Verdict

**Retain both scientific dispositions, but fix two comparison-entry checks before
reusing or merging PR574's runner.** The tested structural-reduction mapping remains
parked; first-round model assets are readable, but product MVP and candidate admission
remain unmet. PR575 accurately distinguishes its completed bounded audit from the
still-open #546 product contract. No new acceptance-blocking claim defect was found
in that distinction.

Reviewed exact heads:

- PR574: `8a1565c2f8ca81ae10cdae9a1961835be2d132e6` against
  `0e07ed8391a7d1b465b5f84722ab003efc43a6be`.
- PR575: `8afa62bf4f13203d7242b9a646b0ae7f49fb3589` against PR574.
- Live main: `a332e79f3f548f6c0ea558092f745b584045de80` at review.
  Both reviewed PRs and their preceding PR570/572/573 stack remain open drafts.

## Findings requiring engineering work

### P2-A — comparison does not enforce the frozen holding-file identity

`scripts/expert545_held_structure.py:494–495` reads `held_file` and derives
`entry_key` membership without comparing it to `held_sha256`. Subsequent checks
validate date/unit, security and quantity, but not the frozen episode identity.
The final decision depends on the number of changed entry episodes.

A disposable synthetic reproduction first freezes one episode, then changes only
the held file's episode keys. With the original frozen hash retained, `compare()`
still succeeds: the changed-episode count moves from one to two and its disposition
changes from PARK to permitting a proposal for a later capital comparison. No
real source or sealed output was edited. The synthetic expert result was computed
once by the real evaluator, then reused to isolate CLI admission/denominator logic;
it is not a market experiment or a full strategy reproduction.

Required correction: check the held bytes before interpretation; retain the exact
date/unit/security/quantity/episode set; reject drift before writing comparison
output. Include control and changed-episode regressions. A self-consistent new
receipt must not erase a mismatch with the previously frozen authority.

### P2-B — comparison omits execution code/configuration identity checks

`compare()` checks the materialization's reference to the input-plan hash, but
does not validate the plan's `code_config_sha256` entries. `build()` contains
that check; the later comparison stage does not. In the disposable reproduction,
an expected script hash of all zeroes is accepted and a successful comparison
receipt is produced. The baseline-digest check does not cover a changed optional
mapping when the original baseline behaviour remains unchanged.

Required correction: enforce the relevant frozen code/config/import closure at
each stage boundary before execution. Freeze the capture helper's launch bytes
before any future B capture. Before any new raw-reading build, revalidate the
selected source/held identities; avoid treating the existence of an old freeze as
current byte verification. Use a small shared stage-entry helper, not a new
resource/control framework. Negative tests must cover code and policy drift
while the baseline still agrees.

These are reuse/reproducibility defects, not evidence that the recorded real run
was corrupted. This audit rehashed its eleven executed code/config pins and all
343 Facts files, and reconciled every retained decision with its structural input.
The current original held-file and captured-trace bytes also match their frozen hashes.
The original run also retains a full source/code unchanged receipt. Its missing
capture-launch hash is explicitly disclosed and cannot now be reconstructed as a
contemporaneous fact; matching old B outputs supports the narrower reconstruction
claim, not a general launch-provenance guarantee.

### Existing P3 caveat — no-process placeholders have a misleading count

Exactly 3,887 unique security/date rows have no process identity but retain
`process_count=1`. `process_revisions` is empty there. This was already disclosed
by the producer and does not enter `evaluate_shadow`, so it does not change the
current result. Correct the default/placeholder count in a new code version before
any cardinality consumer; preserve the sealed files and their original identities.
Do not publish the old auxiliary histogram as an actual process census.

## Independently checked conclusions

### #545 — current mapping has insufficient incremental influence

The 43-member PR574 export and 78-member PR575 export both retain exact file sets
and matching SHA256 lists. Recounting the 8,393 decision records independently
confirms 343 issuers, 538 entry episodes, 8,380 distinct security/date inputs and
the following complete denominator:

| Item | Count | Interpretation |
| --- | ---: | --- |
| Available structure | 5,364 / 8,393 | 63.9% of held reviews under this contract |
| Unavailable structure | 3,029 / 8,393 | 36.1%; retained, not counted as negative evidence |
| Confirmed stage10 | 46 | 30 fail the existing holding-age requirement |
| Reduction proposals | 16 | 15 already required a baseline full exit |
| Additional final action | 1 | One issuer/date/entry episode, no_action to reduce |
| New sells from missing structure | 0 | Missingness does not become damage |

Unavailable reviews comprise 796 insufficient-history cases, 2,080 outside the
declared support horizon, 140 zero-activity observations and 13 discontinuities.
The dominant horizon category is a representation scope limit, not proof of missing
warehouse data. It does not authorize lengthening the horizon to rescue this result.

The two equivalent representations preserve confirmation memory and use the same
policy. Their equality is an implementation check, not two independent strategy
successes. The synthetic causal-prefix, multi-process, unknown-input and guardian
precedence regressions also pass. The recorded full B decision digests agree;
this audit did not rerun B, source-to-Facts materialization or a capital portfolio.

The frozen one-episode stop is satisfied. Park this policy; do not claim that all
structural signals are useless, that unavailable cases have no opportunity, or
that returns/risk/costs improved. No such outcome was measured.

### #546 — retained engineering assets, no usable learning MVP

Twelve prior safe receipts, six checkpoints (43,171 bytes), eleven transitive
reader sources and the original three artifact pairs matched their pinned hashes.
A new process again loaded all six models, preserved their exact encoding and
parent/scaler identity, and rejected all eighteen corruption/hash/source-binding
cases. Missing inputs remain UNAVAILABLE; unqualified present inputs remain ABSTAIN
with no calibrated probability. This audit's OS tracing records no Internet connect
or Parquet read. The prior audit's two failed startup Unix nscd probes remain
historical startup observations, not Internet source access; none recurred here.
This is a reader smoke, not a fresh independent fit.

The first actual fit used six training rows and its update used nine development
rows. Its previously recorded independent construction remains valid at that
engineering scope. It is not convincing recognition-quality evidence. Later
seven/nine models do not inherit that independent refit. Seven remains an
unqualified research asset; rejecting nine does not accept seven.

The old learning reader and original planning objects remain local to retained
histories; main and the later consumer stack lack those interfaces. GitHub's
commit API again does not resolve `e79eeb0...` or `7f4ecc9...`. This is a delivery
risk already correctly identified by #546, not a new assertion of data loss.
Protect those local objects/worktrees until a reviewed durable handoff exists.
Do not import the whole old branch merely to recover a small reader.

The product lacks a state candidate with the required reference/use qualification
and a demonstrated incremental consumer benefit. This is the main remaining
problem, not a need to fill all twelve vocabulary slots or run more tests.

## Verification and limits

Job: `/srv/agentdock/.codex/learning545-546-review-20261028T115537Z-8afa62b`.
Evidence: `asset-audit.v2.safe.json`, `compare-binding-probe.safe.json`,
`reader-launch.frozen.json`, `reader-smoke.json`, `reader-trace-audit.safe.json`,
`live-github.safe.json`, and `checks.json` in the safe handoff.

Fresh targeted held-structure regressions: **30 passed**. An initial invocation
without the intended PYTHONPATH failed collection against the installed older
package; its log is retained, then the exact reviewed source passed. New reader
smoke: six models / eighteen rejected negatives. The synthetic binding probe
reproduces the two findings; it is deliberately not called a passing rejection
test. Required whole-repository verification is recorded in the final handoff.
Remote Actions have zero executed job steps because of the recorded account
payment/spending condition; no remote-green or production claim follows.

No new acquisition, real training, parameter search, economic replay, P6 activation,
source mutation, service change or merge was performed. This review creates new
audit records only; the previous exports and scientific dispositions remain sealed.
The proposed multi-stage next route is separate from evidence:
[conditional continuation plan](../work-items/learning-next-route-20261028.md).
