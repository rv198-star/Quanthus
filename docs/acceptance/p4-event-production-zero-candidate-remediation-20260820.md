# P4 Event Production Zero-Candidate Remediation Checkpoint

| Field | Value |
| --- | --- |
| Evidence status | production defect confirmed; remediation implemented; rerun pending |
| Evidence date | 2026-08-20 |
| Work item | `P4-EVENT-002` / GitHub Issue #74 |
| Negative production Run | `run_4609925c665c44ddaba56538565f7e08` |
| Negative Run code revision | `2484766a12ff07ae4d8fed6d70ed427b2dfd2ca9` |
| Candidate rule | `p4-event.price-volume-state-transition@1.0.0` |
| Candidate digest | `sha256:b04464c838705e71331515332ceb9e796ce26698c784d4615cff00dfdcf41c50` |
| Source policy after remediation | `p4-event-source.direct-dwd-v2.1-retrospective@2.1.0` |
| Source-policy digest after remediation | `sha256:000bd6898903e87f55f3c5ca70f37afaa060029b464e379eead0884ca52a2d53` |

## 1. Production finding

The first successful full-corpus production scan after the temporal-security join repair completed
without a process or verifier error but sealed an empty Event Library. Bounded read-only diagnostics
of the immutable `summary.json` showed that the trigger rule itself was not empty:

| Counter | Production value |
| --- | ---: |
| evaluated rows | 7,626,395 |
| raw triggers | 53,527 |
| isolated candidates | 46,522 |
| concentration retained | 0 |
| candidate events | 0 |
| same-security isolation rejects | 7,005 |
| static-industry-missing rejects | 46,522 |

The complete isolated set was therefore lost at the post-trigger static-industry concentration
boundary. This excludes a too-strict price/volume trigger as the explanation for the zero result.

The production-bound legacy mapping contained 1,687 memberships and every mapping `security_id`
was a plain six-digit code. The P4 physical projection looked up those memberships with governed DWD
`security_id` values. The two namespaces were not the same contract, so every isolated trigger
received `static_industry_missing`.

The empty Run remains immutable negative evidence. It is not a valid #74 acceptance Run, no Run B
was submitted from it, and no arbitration, blind, simulation, governance, or execution authority
was opened.

## 2. Remediation decision

P4 no longer treats the frozen P3 1,687-security replay mapping as a full-market P4 identity
authority. The approved candidate rule and threshold digest are unchanged.

Source policy 2.1 keeps static level-one industry strictly concentration-only and derives the
physical mapping from the immutable DWD V2.1 Base:

- `dwd_company_industry_membership` supplies the current static company classification;
- `dwd_industry_node` resolves the membership to its level-one parent;
- the candidate projection supplies governed `dwd_company_id`, `dwd_security_id`, listing code, and
  exchange;
- the materialized static mapping is keyed by the same governed `dwd_security_id` used by Event
  identity; and
- the mapping artifact is deterministic, checksum-bound, and included in the immutable Event
  Library package inventory.

The DWD classification is used as a static non-PIT concentration grouping only. It is not promoted
to historical industry membership and cannot participate in trigger creation.

## 3. New fail-closed coverage gate

The pure scanner still records `static_industry_missing` after raw-trigger formation so the
reconciliation remains observable. The physical P4 path now refuses to publish a Run if any
isolated trigger lacks static-industry membership. A partial or empty Event Library can therefore
no longer be accepted merely because its manifest is internally consistent.

This is conformance to the already frozen rule-card requirement that static industry have complete
candidate-security coverage. It does not change the quiet/active thresholds, feature window,
isolation, concentration caps, split policy, or event identity semantics.

## 4. Verification before production rerun

Focused verification after the remediation:

- event source, physical scan, and pure scan: 16 passed;
- Direct-DWD projection, P4 source/scan, Event Worker, and platform Job regressions: 30 passed;
- changed-file Ruff and `git diff --check`: passed.

Repository-wide `make check`, merge, exact production Event Worker deployment, and replacement
production A/B runs remain required before #74 can close.
