# P10-DATA-BASE24-MAIN-001: Integrate the Base 2.4 construction code

| Field | Value |
| --- | --- |
| Parent | [Work-item index](index.md); [Base 2.4 / #527](https://github.com/rv198-star/Quanthus/issues/527) |
| Governing design | [Data foundation](../design/lld/01-data-foundation.md); [permanent P10](../design/lld/12-permanent-data-line.md) |
| Design status | approved: Owner requested the completed Base 2.4 construction code be integrated on 2026-10-23 |
| Delivery gate | accepted-conditional for local source verification; hosted CI unavailable because of account billing |

## Exact source and integration scope

Starting main is `6a788f8faab35036e58a5497d75f6a5ee574f417`. Retain the clean
source `codex/base22-standalone` at
`ab261d0a02ea161ca4c7790b376be25134f7e9d2`. Main already contains the independent
image engine, Base 2.4 DWD composition, readers and P6 interfaces through
[#550](https://github.com/rv198-star/Quanthus/pull/550) and
[#552](https://github.com/rv198-star/Quanthus/pull/552). This successor brings
the remaining construction dependencies into that baseline.

The shard merger also preserves the separately retained fixes from
`codex/base24-merge-repair` at `f37dafc2f2105c8eef5aaf93be635eb9cd7e0b6c`:
verified retries can supersede a prior hold, repeated holds retain their evidence,
and the optional exact source index binds its evaluator version. Merge only its
shard module and corresponding synthetic tests with the current source; do not
replace other modules with that older branch. The salvage reader has annotation
and third-party stub declarations for strict typing; data behavior is unchanged.

The integration review adds two bounded safeguards to the retained shard index:
validate source labels as single safe components before any output creation, and
reject directory traversal errors rather than recording a partial file inventory.
Twelve disposable regressions reproduce the original gaps before the fix and
must pass afterward, with no input bytes changed or output directory created on
rejection. These are engineering findings, not evidence of damage to real data.

| Construction boundary | Versioned production modules |
| --- | --- |
| Frequency/source selection and historical denominator | `base24_scope.py`, `base24_coverage.py` |
| Bounded native 5m capture and source validation | `base24_native5m_acquire.py`, `base24_native5m_worker.py`, `native_5m_exact.py`, `tushare_native_5m.py` |
| Partition merge, retained failures and coverage quality | `base24_native5m_shards.py`, `base24_native5m_salvage.py`, `base24_native5m_ledger.py`, `base24_native5m_quality.py`, `base24_native5m_bounded.py` |
| Exact projection and monthly DWD ownership transfer | `base24_native5m_projection.py`, `base24_native5m_dwd.py`, `base24_downstream_job.py` |
| Independent ODS materialization, verification and transport proof | `base24_ods_image.py`, `base24_ods_transport.py` |
| Domain dependencies and additive observation packages | `base24_late_disclosure.py`, `base24_weekly_financial.py`, `base24_weekly_materialize.py`, `base24_weekly_package.py`, `base24_weekly_industry.py` and their selected disclosure/financial dependencies |
| Paired acceptance status reconciliation | `base24_release_status.py` with `base24-acceptance-bindings.v1.json` |

The `base24_*.py` construction modules above live under
`src/quanthus/pipelines/securities_warehouse/`; provider adapters live under
`src/quanthus/providers/`. Exact selected files and their source hashes are
recorded in the integration handoff. Tests use disposable synthetic sources.

## Shared stage graph and resource ownership

Freeze exact policy, source files and historical listing/session denominator;
then capture/qualify disjoint windows, retaining original wire bytes and failures.
The canonical acquisition and coverage ledger are immutable prerequisites shared
by the ODS constructor and the monthly native-5m projection. Project each month
once, validate its original clocks and Decimal values, and transfer its accepted
physical output to the DWD bar package. The existing `base24_image.py` composes
that package with the verified remaining 32 native tables into the full image.
ODS retains its selected source closure in independent regular files. Seal each
image only after its offline audit; READY is the last write.

Monthly projection TMP may be retired only after the exact output transfer and
hash proof under its explicit lifecycle contract. Source-retirement helpers
require a frozen plan, exact seal identity and separate cleanup authority. No
cleanup or materialization executes merely because this code is merged.

The optional shard source index is TMP with exact plan, source/member and evaluator
identity; unreadable or changed members reject reuse. It does not replace formal
independent cold verification. Separate immutable image construction, two physical
moves/new-process source-denied and network-denied reads, and disposable damage
negatives remain acceptance modes. No real image is rebuilt for this code merge.

## Interfaces and qualification boundaries

Default Base 2.4 scope is `1d` plus native `5m`, with business observations through
2026-09-30 in Asia/Shanghai. Historical native 5m begins at the applicable session
on or after 2020-01-01. Actual acquisition and knowledge clocks remain separate;
earlier daily/domain history is retained. Optional 1m is excluded from this new
profile without changing the frozen legacy Daily or four-lane interfaces.

The existing build functions are explicit opt-in APIs: `freeze_ods24_plan`,
`materialize_ods24`, `audit_ods24`, `seal_ods24`, `build_composition_plan`,
`materialize_base24_image`, `audit_base24_image` and `seal_base24_image`. Their
plans must bind actual source/config/code identities and an approved resource
budget. Capture entry points require a frozen selection and explicit invocation;
`scripts/run_base24_tushare_native5m.py` accepts an ephemeral key through stdin or
an interactive prompt and never supplies a stored credential. This merge does not
authorize any capture or network request.

`base24_release_status` reconciles exact locally supplied acceptance receipts and
reports whether current payload verification was rerun. Recorded acceptance is
not a new scan, universal consumer admission, historical PIT proof or execution
authority. The 4,173 visibility exceptions, purpose restrictions and sealed
financial/price concepts retain their existing semantics.

## Preserved main and excluded work

Preserve main's newer `base24_p6_purpose.py`, frozen `daily_domain.py`,
`daily_ods_catalog.py`, current quality policy and market-observation interfaces.
The new monthly path generates native rows through `base24_native5m_dwd.py`; it
does not need the source branch's optional extension to the legacy market builder.
Keep P6 #538's effective-view and knowledge-window repairs intact.

This is not the entire long-lived P10 branch. Docker, timers, mail, cloud-sync
deployment, Daily/recovery controllers, historical 1m experiments and unrelated
research work are outside this PR. Production services, CURRENT, data files and
credentials remain unchanged. Future physical incorporation of #538 remains
[#539](https://github.com/rv198-star/Quanthus/issues/539).

## Validation and handoff

Run all selected synthetic tests, the existing image/reader/#538 and frozen legacy
regressions, changed-file Ruff, new-module strict mypy and repository `make check`
on the actual integrated tree. Record commands, runtime versions, pass/skip/failure
counts, source selection and preserved-file hashes in plain-text logs and a
checksummed read-only handoff. Earlier image receipts and long-branch test reports
are not tests of this merge. Hosted CI availability and unrun runtimes must be
reported separately. This source integration grants no new data qualification or
production activation.

## Completed local verification checkpoint

The exact integrated construction tree recorded by
`a3b68a51babf551a6b46bd306c45709f82fd503c` passed `make check` on Python 3.10.12:
4,831 passed, 20 skipped and eight deprecation warnings. Doctor, compilation and
planning checks also passed. The full run started on the exact staged contents
before their metadata-only commit; its frozen tree
`09a2ec89af3b2136e5a2a06dc78cdff76ea5a690` matches that commit, and no source,
configuration or test file changed during the run.

The selected 405-case synthetic set passed before the added boundary tests; the
final shard/salvage subset passed 30 cases. These overlap with the full run and
must not be summed. All 12 new path/scan-error cases first failed against the
unfixed implementation, then passed. Changed-file Ruff and strict mypy for all
27 new production modules pass. A full Ruff scan retains 216 existing findings
in 25 files, all byte-identical to starting main, with zero changed-file findings.

[#553](https://github.com/rv198-star/Quanthus/pull/553) carries this integration.
GitHub's jobs did not start because account billing/spending admission failed;
this is not hosted CI success. Python 3.12, a new Web run and whole-repository mypy
were not run locally. The 20 skips require production expert artifacts, a test
PostgreSQL DSN or optional sklearn/torch dependencies. Eight warnings concern the
existing Arrow batch API. This final checkpoint changes documentation only and
does not claim a new real-data build, data acceptance or production activation.
