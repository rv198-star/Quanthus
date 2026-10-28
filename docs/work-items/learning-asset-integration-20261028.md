# Minimum learning asset integration and entry integrity

| Field | Value |
| --- | --- |
| Design status | approved — Owner's2026-10-28 instruction covers steps1–3 of the [conditional route](learning-next-route-20261028.md) |
| Delivery gate | accepted-conditional for the bounded entry repairs, clean reader and Git handoff in the [dated delivery](../acceptance/learning-first-tranche-20261028.md); no consumer admission |
| Work items | #545 comparison integrity; #543 unchanged training scope; #546 retention; #540 consumer opportunity |
| Governing parent | [Blueprint mechanism](../design/blueprint-progress.md); [original immutable MVP](learning-system-mvp.md) |
| Batch boundary | Four hours total; no new acquisition, real fit/update, market replay or production/P6 changes |

## Exact reusable entry

`scripts/expert545_held_structure.py` now produces the versioned
`expert545-held-structure-inputs-v2` freeze. Before a build writes its Facts directory,
it checks the exact held bytes and unique date/unit/security/quantity/entry-episode
population, every selected/supplemental source byte identity and physical path, and
the complete project Python/config/dependency declarations plus both entry scripts.
Comparison checks the same held and execution identities before opening its Facts
and binds the materialization, trace proof and launch record to that freeze. It
checks loaded Quanthus module origins as well as disk hashes. Source files are not
reopened by comparison, which consumes the frozen materialization.

The broad code closure intentionally favors refusing reuse after an unrelated
code/config change over silently omitting a transitive dependency. It is not a
semantic cache or a new generic execution platform. Python and installed dependency
versions are recorded by the handoff; this contract does not claim a hermetic Python runtime.

`scripts/expert545_capture_b_trace.py` still requires the exact historical producer
revision, now with clean producer Python/config state. Before producer imports or
invocation, it writes a launch record binding the actual launcher, producer code,
config, dependency declarations and source freeze. It checks the closure again
around execution. A failed run retains that record and cannot emit a completed
receipt. The temporary network hook is restored even if invocation fails.

Old v1 freezes and old capture receipts remain immutable evidence. They cannot be
fed to the repaired runner as newly qualified inputs, and the old missing launch
provenance is not reconstructed or claimed recovered. No historical full replay is
required to fix this engineering boundary. New source freezes still need their own
existing research authority; this interface change grants none.

In new materializations, a row with no support process has count0 and an empty
revision list. Existing single/multiple process aggregation retains its actual
count. The3887 old optional count anomalies and the original8393-row conclusion
remain disclosed in their original sealed evidence; no old file is rewritten.

## Minimum preserved assets

Two production modules are integrated byte-for-byte from local Git
`7f4ecc9ad9d0569b68b27cfac5a8081aae4e5a63`: `stage_learning/local_head.py`
and `head2_consumer.py`. All ten other imported reader dependencies already match
the retained reader's hashes. The former is a small explicit fit/update/checkpoint
primitive; importing or reading it grants no real training authority. The latter
always abstains in the actual expert protocol, with no calibrated probability or
consumer admission. Synthetic tests exercise these properties through the existing
aggregator; they do not import the unrelated old replay-comparison helper.

The original Markdown and JSON MVP contracts are restored at their original paths,
with their original SHA256 identities, from `e79eeb027917de4f6a48abf563569b55f3e7c91b`.
Their old preparation states are not current module results. No thresholds, frozen
research requests or product acceptance criteria change.

The old census, feature-construction and temporal/revision modules stay protected
in the old local worktree/history. They are not required to load the six retained
checkpoints and are deliberately outside this minimal integration. No whole old
branch, unrelated failing tests, model weights, source payload or local run database
is imported into Git. A future reproducible feature rebuild must separately qualify
those remaining modules; a reader smoke is not that rebuild or an independent fit.

The clean-checkout smoke uses only committed ordinary code, an exact eleven-file
reader closure and six original checkpoint files. It validates all original hashes,
byte-identical dump/load, parent/scaler semantics, missing-input UNAVAILABLE and
present-input ABSTAIN without probability. It rejects corrupt bytes, wrong hash and
wrong bindings for each model. No market input, reference label or network is needed.

## Contribution and next gate

Repairing these interfaces does not improve Head2 quality, prove strategy value,
accept a future review queue or activate P6. The third step must inspect the actual
consumer and freeze a use/eligibility decision before any later experiment. Existing
pointwise results may diagnose feasibility only; consumed cases cannot become a new
independent evaluation. Delivery evidence and the generated blueprint carry the
current result rather than this contract claiming a product pass.
