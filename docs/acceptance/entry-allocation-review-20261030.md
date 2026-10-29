# PR #589 engineering review — 2026-10-30

| Field | Value |
| --- | --- |
| Parent | [Historical review and allocation observation](../design/historical-decision-review-v1.md#entry-funnel-and-optional-allocation-observation) |
| Work | META-LEARN-001 / #540; existing #545 consumer boundary |
| Reviewed implementation | `a5f956570bdd9493b13680029be052a4b67e6a4f` |
| Current PR base | `510f278d50bca976dea08a81beff8e07c8698c2a`; changes after `937edee15c3fd0533766f38716e9d79d09ef0174` are four unrelated P10 documentation files |
| Authority | Owner requested review, acceptance, ordinary merge, then a next-stage plan |
| Evidence | `cn02:/srv/agentdock/.codex/entry-allocation-review-20261029T174254Z` |

## Review disposition

No blocking implementation or evidence finding was identified in the declared scope.
The historical report is accepted as a read-only explanation of recorded original B
events. The optional sidecar is accepted as synthetic-tested observation of an already
computed allocation, with default-off behavior. This does not qualify a learned
candidate, historical PIT, production use, P6, or full-history audit resource cost.

The implementation's old acceptance record is retained. This review separately reruns
its checks; it is not a second author of the implementation or an external human audit.
The final check and publication receipts below determine handoff status.

## Evidence checked

- All 68 listed prior export members match their SHA256 values. The exact 1,484-file
  source/config/test/script closure at the reviewed head matches the prior full test.
- Fresh focused regressions: **68 passed**. The actual allocator runs once per frame.
  Empty capacity, industry/daily caps, stable ordering, real reservation/fill distinction,
  no-action candidates, corrupt trust roots and protected output paths are covered.
- The authenticated original package manifest remains
  `aa9b7449c552be317d61dd032b47a847771a96797220b4cec54907cbc8917738`.
  A separate raw decision-digest/Shanghai-date/unit → signal → intent → order/fill
  join accounts for every buy order exactly once and agrees with every report count:
  **22,955 opportunities; 14,062 trial-entry actions; 8,893 scoring abstentions;
  13,519 trial actions without orders; 543 buy orders; 538 fills; 5 unfilled orders**.
  Original source file identities/stats are unchanged. The new JSON/HTML are byte-equal
  to the delivered report. Old allocation reasons remain `NOT_RECORDED`.
- Methods compiled from immutable base `937edee15c3fd0533766f38716e9d79d09ef0174`,
  current audit-off and current audit-on produce equal original seven files and
  evidence. A separate comparison checks complete unit runtime objects, ledgers,
  lifecycle transitions, decisions and events. Only `run_variant` and `_execute_frame`
  differ structurally in the runtime class. All execution in these comparisons is synthetic.
- A fresh process reads only ordinary code and the read-only retained package under
  `bwrap --unshare-all --clearenv`. Original source paths are absent. Native syscall
  traces show no old-source open and no connect; output hashes match. A bad trusted
  manifest is rejected. Prior independent package construction/relocation/corruption
  evidence is reused by identity, not claimed as a newly repeated construction.
- One initial review-script invocation lacked the test-package import path and failed
  before data access. Correcting only `PYTHONPATH=src:.` succeeded; the failed log is retained.

## Final checks and publication

The review adds this evidence document, its governing-design link and an append-only
no-module-change checkpoint. A fresh `make check` on Python 3.10.12 passes against
that ledger state: **5,734 passed, 20 skipped, 8 warnings**, pytest 648.98 seconds,
whole command 652.63 seconds, exit zero. Doctor, compile and planning checks pass.
The 20 skips retain the five PostgreSQL, two production-artifact, three sklearn
and ten torch environment limitations; no assertions or test selection were weakened.
The exact 1,484-file executable/config/test/script closure is rechecked after the run.
Later changes are documentation only, with affected checks rerun before publication.

Changed-file Ruff and the new reader's strict mypy pass. Exact-base blueprint and
conclusion append-only checks pass; all registered live issue observations reconcile.
The previously measured full Ruff baseline still has 216 diagnostics and the changed
legacy runtime has three old mypy diagnostics. Their code/base identities are unchanged;
these baseline comparisons are reused, not claimed newly clean. Whole-repository mypy,
Python 3.12 and Web checks are not newly run. Hosted failures were checked directly:
the jobs did not start because of billing/spending limits. No manual rerun, bypass or
workflow/protection change was performed.

The check's sampled peak RSS is 583,237,632 bytes; peak new-job allocation is
51,380,224 bytes and minimum system free space 9,081,798,656 bytes. This satisfies
the new job's 128 MiB budget and 8 GiB free-space floor. Only owned passed-test TMP
is reclaimed; sources, old jobs and failure evidence remain. Complete command/log
hashes, final commit and ordinary merge identity are in the final handoff.

The checkpoint reviews EXPERT/REPRO/CLOSEOUT without changing any maturity facet.
No issue is closed and no prior negative, exposure record, frozen request or policy
is changed. No source hunt, market replay, fit, threshold change or consumer activation
was performed.

## Next-stage recommendation — proposal, not activation

Stop adding historical-report variants or trying to infer the 13,519 missing reasons.
Head2, the current 3/4 reference route, volume-gate retries and P6 activation retain
their existing dispositions. A further tool must have a named caller and an actual
decision it supports; merge completion does not automatically select another experiment.

The smallest plausible follow-up is to retain allocation evidence through a normal
authorized replay caller, rather than run an old economic path solely for logging:

Code inspection identifies `FormalP2ReplayBackend.execute_variant` (existing artifact
persistence) and `P3Strat003ReplayRuntime.run_research_variant` (research wrapper) as
possible callers. Neither currently forwards the optional flag. That is the declared
default-off boundary, not a defect in this PR. No upcoming qualified job was established
by this review. Old cached results without a sidecar must remain `NOT_RECORDED`; an
adapter must not automatically replay or reconstruct them.

1. Inspect one current caller and persistence boundary, with a **30-minute cap**.
   Confirm whether an upcoming authorized job needs the optional record. If no such
   caller/job exists, stop with the existing API and guide; do not create a service.
2. If needed, add only opt-in forwarding, immutable sidecar persistence and exact
   identity/readback checks at that caller, **1–1.5 hours**. Default output and existing
   manifests remain unchanged. The consumer must distinguish allocator rejection,
   reservation and later execution rejection, with unknowns explicit.
3. Validate the complete caller → persisted record → readback path with bounded,
   multi-frame synthetic cases and measure retained bytes/RSS, **0.5–1 hour**.
   Piggyback a real measurement only
   on a separately authorized, qualified normal job. No new historical replay is
   activated by this proposal. Only a concrete unresolved decision with admissible
   alternatives justifies a later comparison.

Total proposed engineering envelope is **2–3 hours**, ending early if step 1 has no
consumer. This improves future explanation/diagnosis; it is not an investment-value
claim. Existing P10 source-supply work (#581–#587) proceeds independently and is not
duplicated by this research branch. No broad source repair or whole-12-state campaign
is proposed.
