# P10 P12 daily sidecar engineering

| Field | Value |
|---|---|
| Parent | [P10 LLD 12](../design/lld/12-permanent-data-line.md) |
| Work | P10-DATA-P12-DAILY-001 / [#726](https://github.com/rv198-star/Quanthus/issues/726), parent #478 |
| Design status | approved for the bounded metadata scope accepted by P11 |
| Delivery gate | open; isolated candidate, independent review and consumer binding pending |
| Baseline | origin/main b995967655cb7ed2265f6eca1a1e2c27fb84f3e3 |
| Runtime | p10-daily-consumer-sidecar-runtime-v2 (v1 compatibility), src/quanthus/p10_daily_sidecar.py |
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

The retained [M2 v2 candidate map](../../config/pipelines/p10-p12-member-map.m2-v2.json) binds the field
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

## Independent session metadata increment (M2 v3)

The first fixed engineering candidate is f32e2c1c307da30763a78786c28517d1e3ba4afd,
tree 80cb5211ec9b2818b19b3459cdc1a34c062db700, in draft PR #729. P11 is reviewing that
exact candidate. The session increment uses a separate successor branch; the first candidate,
accepted SPEC/contract, old M2 map and original evidence remain available unchanged.

The [finite incremental request](../../config/pipelines/p10-daily-sidecar-engineering.v2.json)
and [M2 v3 mapping](../../config/pipelines/p10-p12-member-map.m2-v3.json) bind M2 SHA256
e50e6396aeca56e574da379ada96ab0a69bb939377fedbcd7025eef074d7c892 and the P11 rule addendum
SHA256 10a87a3f86259869d70f272ffac9b84f03c6c3ed14e8c4dab0020f7bf82057fe.
The coordinator's review correction governs the v3 proposal's ambiguous None/default wording:
None is UNKNOWN; only named applicability evidence permits NOT_APPLICABLE. No fixed source
latency, buffer or tolerance is invented.

The opt-in v2 config adds MeasureRequirement for each consumed market member. It freezes the
actual measures and requested volume basis, so a sender cannot remove volume from a FULL_DAY
consumer and claim price-only use. V1 config serialization/record fields and behavior remain
compatible. A v2 record cannot be passed to a v1 consumer or downgraded to satisfy a v2 one.
Each v2 member has session metadata or null for an unconsumed reference. Session metadata,
vendor evidence, effective scope and calendar/rule/security digests participate in the inventory
and complete verified-input identity. Old receipts fail after any such change.

Per-market-member metadata includes session_scope, volume_basis, official_price_close_anchor,
full_day_volume_amount_observation_end, vendor_scope_evidence and applicability. Applicability
names venue, board, security-scope SHA, effective range, authoritative calendar/rule SHAs,
APPLIES/NOT_APPLICABLE/UNKNOWN and evidence URI/SHA. The upstream verifier owns actual
source applicability and coverage; publishers must split heterogeneous scopes or supply a
separately qualified homogeneous closure, not assign one board's rule to a whole mixed pool.
Final physical members/pool are still unbound.

For FULL_DAY volume/turnover, UNKNOWN, REGULAR_ONLY and independent after-hours products fail
the requested basis. Known applicable full-day quantities require an explicit observation end
at least 15:30 Shanghai time, and real source/publication/member-visible clocks no earlier
than that end. The price anchor stays at the registered 15:00; calendar/identity references
keep their real clocks. A separately configured price-only use does not depend on unknown
quantity metadata. The library performs no quantity summation or strategy-window evaluation.

The [source inventory](../../config/pipelines/p10-session-source-map.rules-20260706.v1.json)
records vendor/venue/board/effective-range versions and minimal recovery conditions:

| Source | Existing evidence | Remaining source fact |
| --- | --- | --- |
| BaoStock/provider and inherited local-sot daily | raw volume/amount pass through to core volume/turnover; no after-hours merge in projection | inclusion of official after-hours totals is UNKNOWN; vendor definition/version and exact pool coverage needed |
| after_hours_tdx v1 | sparse 15:05–15:30 trades and per-security receipts; lots x100; amount=price x quantity is derived | actual member/coverage/available_at unbound; existing vendor-total inclusion UNKNOWN; no additive merge |
| native limits/status | core tradability flags exist; price-limit.v2 already has main ST5→10 from 2026-07-06 | native upper/lower prices remain NULL in current core; pool/day/status/exception and clocks unbound |

Official exchange statistical rules describe the target; vendor implementation remains a
separate source fact. Acquisition-status text in trading-session.v1 is policy intent.
No source rows, new endpoints, procurement or recovery were used in this inventory.
Tests cover main-board ST before/after the effective date, unchanged STAR/ChiNext, unknown
status, FULL_DAY vs other products, missing/early quantity clocks, early reference clocks,
price-only independence, receipt invalidation, schema downgrade and v2 store retries/as-of.
P11 owns affected security/window comparability and its INPUT_UNAVAILABLE mapping; P12 owns
held actions/physical reads. This task does not implement those domain algorithms.

#727 stays blocked on pool/fields/window. Native preopen limits, formal suspension, auction
and last-quote identities/availability stay separate; zero volume is not formal suspension.
The minimal next engineering step is independent review of this metadata increment and,
after exact consumer/source binding, a separately scoped publisher seam proposal. No adapter
is activated here.
