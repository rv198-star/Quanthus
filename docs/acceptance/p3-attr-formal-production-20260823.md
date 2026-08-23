# P3-ATTR-001 Formal Attribution Production Acceptance (2026-08-23)

| Field | Value |
| --- | --- |
| Status | accepted for bounded development attribution |
| Work item | `P3-ATTR-001` / Issue #54 |
| Code revision | `259a649` |
| Production Worker Job | `job_774907d37fc844dda41397e2c2afb5ca` |
| Expert Run | `expert_run_481e6181930f6e031e479c65d40a7fa04e2f0ebf4d9b6290c3605810d1cc60c0` |
| Run manifest digest | `sha256:481e6181930f6e031e479c65d40a7fa04e2f0ebf4d9b6290c3605810d1cc60c0` |
| Input digest | `sha256:66b4936af993de2d2c5b0fa37a9ded505207bea8c8d7e41dc7a3d5e279e3bc54` |
| Formal release | `formal-attribution-711220040b90681feb6bd50f` |
| Release digest | `sha256:1754a4a3a185420734e6addab71110ee82ecd71fb4fbd182f9ee1506c49c98ff` |

## Accepted bindings

The Run binds `candidate.p4-trend-risk@1.0.1` at semantic digest
`sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c`,
preregistration digest
`sha256:a9057b028a52a4794c333fe9ca7ae3e8d936df96912375f148f11fb419bb19bb`,
and attribution plan digest
`sha256:6b2f2e2144b761bec5b9ef948b9e80bd8b11694b44b19fa86e45d0b15404394a`.
It uses DWD Base `dwd-base-202607-v2.1`, correction
`dwd-base-202607-v2.1-correction-20260813-001`, and parent Event Run
`sha256:8b9d184c2947b82d1a98440e7e6313e3659735fa352ea5dd72ec4b90fc80050c`.
Only development dates 2020-01-01 through 2021-12-31 are present. Arbitration 2022-2024 and
permanent-blind 2025 remained sealed.

The January compatibility Sentinel physically reproduced 267 decisions over 2020-01-01 through
2020-01-31 at decision-set digest
`sha256:431c9c9376daf327cd1385e837c62772b72631e11b128806be98084bc7c071d8` before
the full attribution Pipeline ran.

## Bounded results

The Pipeline executed four unique complete capital-constrained P2 variants and three deterministic
paired 22-session block-bootstrap checks. All three preregistered claims are structurally valid and
all three uncertainty checks completed and passed.

| Variant | Net total return | Maximum drawdown | Action change rate |
| --- | ---: | ---: | ---: |
| Native strategy | `+6.704618%` | `7.102827%` | `0%` |
| Trend expert fallback | `0%` | `0%` | `26.381203%` |
| Position-risk expert fallback | `-2.921056%` | `11.012667%` | `15.585594%` |
| Joint fallback | `0%` | `0%` | `26.381203%` |

The trend fallback blocks all entries, so the joint fallback equals the trend fallback in this
bounded population. Removing the position-risk expert changes net return by `-9.625674` percentage
points and maximum drawdown by `+3.909840` percentage points relative to native behavior. The
preregistered joint interaction is therefore `+9.625674%` for net return and `-3.909840%` for
maximum drawdown under the declared formula. It is reported beside the single-expert effects and
is not allocated to either expert.

These are development counterfactuals, not forward-performance evidence or causal profit
ownership. Every claim routes to `human_lifecycle_review_required`; the release records
`execution_authorized=false`.

## Publication and determinism

The PostgreSQL Replay Worker completed in 1,376 seconds under one Worker, the 14 GiB working-set
stop, and the 16 GiB memory-plus-swap hard limit. The immutable formal release contains 102
declared payload files and 22,263,432 bytes; including `release.json` and `READY`, its physical
inventory contains 104 files. No partial Expert Run was published by failed predecessors.

An isolated container reproduced the same frozen input into a distinct release and Run root with
the same 16 GiB hard limit. Deep verification passed independently for both roots. All 104 release
files and all 10 Expert Run files have identical relative paths and bytes; there are no missing,
extra, or differing files.

The bootstrap seed now receives the approved strategy-card digest explicitly. This removes the
deployment-path dependency that previously attempted to resolve repository configuration relative
to an installed `site-packages` module. The regression suite proves deterministic output and a
different seed for a different strategy digest.

## Verification and boundary

Focused formal-attribution, adapter, and generic-Pipeline tests passed (45 tests), along with Ruff,
changed-file integrity, installed-image import/signature smoke, production deep verification,
PostgreSQL publication checks, and full byte comparison. Repository `make check` passed 1,329 tests
with four optional external-PostgreSQL-DSN tests skipped; planning synchronization also passed.

This acceptance closes only `P3-ATTR-001`. It authorizes `P3-EXPERT-004` to project these trusted
digests read-only into candidate detail. It does not approve the strategy, open sealed partitions,
enable Shadow, grant notification or execution authority, or authorize automatic lifecycle
mutation.
