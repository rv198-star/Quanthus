# P10 P12 daily sidecar engineering

| Field | Value |
|---|---|
| Parent | [P10 LLD 12](../design/lld/12-permanent-data-line.md) |
| Work | P10-DATA-P12-DAILY-001 / [#726](https://github.com/rv198-star/Quanthus/issues/726), parent #478 |
| Design status | approved for the bounded metadata scope accepted by P11 |
| Delivery gate | open; isolated candidate, independent review and consumer binding pending |
| Baseline | origin/main b995967655cb7ed2265f6eca1a1e2c27fb84f3e3 |
| Runtime | p10-daily-consumer-sidecar-runtime-v1.2, src/quanthus/p10_daily_sidecar.py |
| Tests | tests/unit/test_p10_daily_sidecar.py |
| Request | [finite engineering request](../../config/pipelines/p10-daily-sidecar-engineering.v1.json) |

## Goal and boundary

Give P12/P11 a reusable metadata validator and immutable event store so a fixed package's
identity, real source/validation/publication/channel clocks and withdrawal history can be
checked before consumer qualification. Existing descriptors, market files, source clocks,
CURRENT pointers, pipelines and schedules are unchanged. No production adapter is enabled.

The spec baseline is p10-daily-consumer-sidecar-v1, SPEC SHA256
b072a68abf79167d052511e79817bb6eca58e0a123be31698c2fe24f3d77716f and contract SHA256
975b49b8c740ecf6ea89ca71872accf6ac79600a188ec34df310e27381cad35e.
These original artifacts remain unchanged in the accepted local spec package.

## API and complete identity

ConsumerConfig / from_dict injects the versioned consumer, purpose, field-spec, universe,
policy, evaluator-code revision and exact required/optional member IDs. Null field/pool
binding stays blocked. The M2 mapping candidate marks every needed hard fact required.
Its physical pool/member mapping is still unresolved; it is not an activated consumer config.

InputVerification carries an existing source verifier's exact input identity, evidence URI/SHA
and explicit validation interval. input_identity covers the entire config, delivery binding,
source descriptor/manifest, all consumed member hashes/sizes/source versions/source clocks
and their evidence. A new pool, policy, version, source clock or member closure invalidates
receipt reuse. The module reads no source files and performs no warehouse rehash. The actual
upstream verifier owns its receipt truth; this module checks identity/coverage and clock
conformance, not signatures or source acquisition. Final consumer admission stays P11/P12.

The isolated library currently returns INPUT_OR_CLOCK_BLOCKED or TEST_ONLY. The
CLOCKS_COMPLETE_FOR_CONSUMER_REVIEW state is reserved for a future qualified-source adapter;
it is not emitted by this version. All SOURCE_OBSERVED records carry
SOURCE_QUALIFICATION_UNBOUND, even if caller-supplied URI/kind/identity strings look valid.
Fixture/synthetic references add NON_OBSERVED_REFERENCE; a source grade with a synthetic
proof kind is rejected. Synthetic fixtures return TEST_ONLY; historical metadata stays blocked.
Missing hard members stop the dependent use; optional member defects remain explicit in the
member result. The module neither changes holdings nor creates sell instructions.

## Two time fields and v1 implementation refinements

binding.observation_end is the T market observation anchor (day1: 15:00+08).
binding.decision_cutoff is the explicitly registered knowledge/consumer deadline
(day1: 22:30+08). Source clocks after the market close are retained and checked against the
registered deadline. This refinement applies to the prospective binding; old historical
T-close profiles remain unchanged. Only T or earlier observations may produce a T signal.
Row-level market scope remains the upstream qualified input/consumer's responsibility.

The accepted v1 spec describes withdrawal visibility but its sample has only per-member
payload visibility. This implementation makes publication.visible_at explicit for the event
itself, including a withdrawal. Member available_at continues to mean visibility of that
payload version. A missing event/member time remains null and blocks the affected selection;
neither field is filled with datetime.now, file mtime, built_at or completed_at.
revision.replaces_descriptor_sha256 is explicit for REVISE/WITHDRAW.

For PUBLISH/REVISE, known source <= validation <= publication <= member visibility, and the
complete event visibility must cover its members. Intervals preserve precision and upper
bounds. Source, publication and visibility evidence must identify the same descriptor and
exact covered members. P10 records no consumer receipt. P11/P12 independently records actual
received/first-read/verified times.

## Append-only and as-of

SidecarStore requires an explicit dedicated metadata root disjoint from protected source
roots. It has no default production path. CONSUMER.json binds the config; immutable numbered
event files bind canonical content hashes. A file lock serializes concurrent appenders.
Write/fsync to a temporary file, exclusive hard-link commit and directory fsync keep torn
staging files outside the committed sequence. Exact retries reuse one event; a new process
can retry after either a pre-commit or post-link crash.

Every stream has one predecessor per market date. REVISE requires a new delivery ID,
descriptor and manifest with the same observation/deadline/consumer scope. WITHDRAW refers
to the previous payload identity. Delivery IDs cannot be rebound. Stored corruption, sequence
gaps and stale predecessors reject explicitly.

select_as_of requires market_date and an explicit cutoff. Later-visible corrections or
withdrawals leave an earlier cutoff snapshot on its old valid version; visibility intervals
crossing cutoff and unknown event clocks return blocked. An eligible withdrawal returns
WITHDRAWN_VERSION. Events for other dates do not contaminate the requested date.
P10 source revisions do not replace a P12 advice package on the same date.

## Field mapping and next adapter

The [M2 v2 candidate map](../../config/pipelines/p10-p12-member-map.m2-v2.json) binds the field
table SHA 205052896a4e96f606c909b13fdc5746a5b29ba4cca51af2ad5540213d56bcb2 and source-code
table/column semantics without reading market rows. F10 remains a P11 algorithm/adapter.
F11 normalized actions/exceptions and factors are not silently interchangeable.
F2 next_session must be covered by an authoritative calendar already known at T's cutoff,
not inferred from a T+1 price row.

A future publisher adapter may pass already-verified core metadata and real evidence into
this API, writing only a separate metadata namespace. It must not rehash all inputs, alter
CURRENT or source files, or derive missing clocks from the call time. Activation, real
SOURCE_OBSERVED member qualification and P12 physical input reading are separate work.
The current PR contains only the library, synthetic probes, mapping and governance records.

## Checks, bounded cost and remaining work

First checkpoint budget: 90 minutes from 2026-11-24 03:31:43+08; no market network, purchase,
new source or deployment. Project Python 3.10.16 is used through its explicit absolute path.
Focused probes cover normal/missing inputs, cutoff/precision/receipt identity, observation vs
deadline, crash retries, parallel writers, revision/withdrawal and source write boundaries.
Changed-file Ruff/strict mypy and exact-base maintenance/exploration/blueprint checks accompany
the candidate. Full make check and exact candidate/log identities are retained in the external
engineering receipt and reported at handoff.

The first focused run found a cross-cutoff withdrawal interval incorrectly falling back to
the old version. Selection now keeps that interval explicitly blocked; the failing probe is
retained. A second probe retains an old cutoff snapshot when a withdrawal is known to have been published only after that cutoff, even if its channel visibility is still unknown. The original failing probe and fix are retained. Routine format/type repairs are included. This reuses the repository's established
exclusive-link/fsync pattern and stays outside existing publisher/runtime modules.

Blueprint DATA is reviewed with no facet change: a metadata engineering candidate does not
qualify new data or change research quality/value/admission. The finite request preflight
found no related DATA conclusions. Old research and #701 remain parked; #727 still waits for
pool/fields/window. Remaining: independent code review, final field/pool binding, real timing
evidence, isolated node acceptance and any separately authorized adapter deployment.

## f32 independent audit repair

P11 returned CHANGES_REQUIRED for f32e2c1c307da30763a78786c28517d1e3ba4afd. Its old Git/tree,
PR evidence and audit SHA256SUMS ebb8d2e67430f1226733a5c95498541d9bdcc0b8a02ae627e1595caafe102ba0
remain retained. The [bounded repair request](../../config/pipelines/p10-daily-sidecar-audit-repair.v1.json)
permits one narrow engineering repair; the separate M2v3/session increment stays on its own
branch/version. Three fail-first probes reproduce fixture promotion, corrupt predecessor
append and payload-changing withdrawal at the exact f32 code before fixes.

The current library has no real M0/source/publisher qualification loader. Caller-declared
proofs are metadata to validate, not source authority. Consequently observed records remain
blocked until a separately qualified adapter exists; changing a fixture URI to a cn02 URI
does not open that gate. Test helpers now default to SYNTHETIC; the original 47 cases remain
in the focused set with sample-grade expectations corrected.

Each committed file now has the explicit sidecar-verified-event-envelope.v1 schema containing
the original wire record and caller-supplied InputVerification. Both are committed atomically
by the same fsync/exclusive-link protocol. Every read and append revalidates stored receipts,
complete schema/evidence/causality and chain semantics before accepting a successor. No prior
receipt is reconstructed from an edited record. Old bare-event store files fail explicitly;
this undeployed engineering version requires a fresh dedicated root. It does not migrate or
rewrite old events, actual descriptors, data or clocks.

WITHDRAW means whole-version withdrawal: descriptor/manifest/binding and the entire original
member inventory, source version/hash/size/state and clocks remain identical. Only withdrawal
event/publication/reason/link fields change. Partial withdrawals need their own versioned
contract; this version rejects member substitutions or implicit partial closure.

ResourceLimits is optional, versioned and included in consumer identity. Supplied member,
evidence, path-byte, record/envelope-byte, event, enumerated-file and committed-store-byte
caps are enforced, including before stored-file reads. An exact retry at an event cap stays
idempotent. Missing caps remain explicit UNCONFIGURED_RESOURCE_BOUND notes; there is no
default 1000-member gate. The real product/risk/cost and a nonactivated day1 example are in
the [consumer resource budget](../../config/pipelines/p10-daily-sidecar-budgets.v1.json).
Its 256KiB sidecar bound belongs only to the named day1 intake; P11 still owns the shared
15-minute/8-request/256KiB total and must budget other descriptor/manifest metadata as well.
The final member/evidence/path counts wait for actual consumer closure. Warehouse payload
sizes and unrelated qualified products are unaffected, following ADR0016.

Independent source/publisher loading, P11/P12 actual receipts and consumer qualification,
node acceptance and any production deployment remain the next scoped integration steps.
Focused repair probes and full exact-closure check receipts are retained externally under
quanthus-p10-sidecar-audit-repair-003; no market-row read or new source endpoint is involved.

## Second narrow binding and retry repair (305 audit)

Exact task base30503b92, prior fixed artifacts and independent CHANGES_REQUIRED are retained.
Ten fail-first probes reproduce the two binding defects and four P2 API/recovery defects.
policy_sha256 is now always a valid required SHA. Nullable field/pool facts stay pending.
M0 is a separately configured **external document content SHA** (`m0_ref=sha256:...`),
independent of the complete config digest and policy identity. Configv1.2 serializes that
explicit M0; v1/v1.1 without it keep their exact wire serialization but remain pending.
An honest null record M0 is assessed as M0_NOT_BOUND/CONSUMER_CLOSURE_PENDING. A configured
M0 must match the record reference; an unconfigured caller reference remains blocked.
This declarative binding never opens SOURCE_QUALIFICATION_UNBOUND or loads a real M0/source.

Exact retries compare every persisted InputVerification field, including original times,
before returning idempotent reuse. Committed file caps count committed event envelopes;
`.pending-*` crash files are retained, skipped for committed caps and never interpreted as
records. Their disk reclamation/retention is outside this library's committed-store budget.
Selection preserves resource boundary_notes on successful and blocked/no-visible outcomes.
Nonmapping config inputs consistently raise SidecarError(INVALID_CONSUMER_CONFIG).

Focused closure83 includes the original66 plus the new pending/external-M0/policy, exact
retry, orphan-file, boundary-note and nonmapping probes. Full check, fixed Git/tree and
CN02 formal command results are recorded separately. The prior regulation/session successor
stays fixed until these repairs are propagated in an independently versioned successor.
