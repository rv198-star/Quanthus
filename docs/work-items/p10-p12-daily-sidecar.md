# P10 P12 daily sidecar engineering

| Field | Value |
|---|---|
| Parent | [P10 LLD 12](../design/lld/12-permanent-data-line.md) |
| Work | P10-DATA-P12-DAILY-001 / [#726](https://github.com/rv198-star/Quanthus/issues/726), parent #478 |
| Design status | approved for the bounded metadata scope accepted by P11 |
| Delivery gate | open; isolated candidate, independent review and consumer binding pending |
| Baseline | origin/main b995967655cb7ed2265f6eca1a1e2c27fb84f3e3 |
| Runtime | p10-daily-consumer-sidecar-runtime-v1, src/quanthus/p10_daily_sidecar.py |
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

assess returns CLOCKS_COMPLETE_FOR_CONSUMER_REVIEW, INPUT_OR_CLOCK_BLOCKED, or TEST_ONLY.
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
retained. Routine format/type repairs are included. This reuses the repository's established
exclusive-link/fsync pattern and stays outside existing publisher/runtime modules.

Blueprint DATA is reviewed with no facet change: a metadata engineering candidate does not
qualify new data or change research quality/value/admission. The finite request preflight
found no related DATA conclusions. Old research and #701 remain parked; #727 still waits for
pool/fields/window. Remaining: independent code review, final field/pool binding, real timing
evidence, isolated node acceptance and any separately authorized adapter deployment.

