# Architecture Map

This directory contains accepted architectural decisions and stable topic contracts. The current
whole-system view lives in `docs/design/hld.md`; ADRs explain why that view changed and do not form
a parallel current-state architecture.

## Load order

1. Read `docs/design/hld.md` for system boundaries and cross-module contracts.
2. Read only the ADR or topic contract linked by the active design node.
3. Load an operations runbook only when deployment, monitoring, migration, or rollback is in scope.

## Decisions

| Node | Scope | Load when |
| --- | --- | --- |
| `adr-0001-local-data-stack.md` | File-first analytical baseline | Changing canonical storage or local execution |
| `adr-0002-hermes-oci-data-platform.md` | Runtime ownership and publication topology | Changing node, warehouse, PG, or disaster-recovery boundaries |
| `adr-0003-t-close-plus-5h-pipelines.md` | Daily immutable ODS-to-DWD/DWS path | Changing post-close acquisition, cleaning, gates, publication, or alerts |
| `adr-0004-hermes-ods-scheduler-v1.md` | Repository-owned ODS lane schedule | Changing collectors, timers, cutover, or release states |
| `adr-0005-dual-lane-canonical-and-ads-publication.md` | Dual-lane candidates, OCI canonical authority, atomic release sets, and portable ADS | Changing daily candidate ownership, arbitration, canonical catalogs, ADS portability, or target cutover |

## Topic contracts

| Node | Scope | Governing design |
| --- | --- | --- |
| `security-code-validity-normalization.md` | Identity and code-validity correction | `docs/design/lld/01-data-foundation.md` |
| `st-status-pit.md` | Point-in-time ST state | `docs/design/lld/01-data-foundation.md` |
| `price-limit-regime.md` | Effective-dated A-share limit rules | `docs/design/lld/01-data-foundation.md` |

An accepted ADR must update affected HLD/LLD current-state text in the same change. A topic
contract may refine one module but cannot override the HLD boundary, frozen policy, or contract
tests.
