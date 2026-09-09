# P3-STAGE-002 Stage 10/11 Projection Contract qualification

| Field | Value |
| --- | --- |
| Decision | `PASS_FORMAL_PROJECTION_CONTRACT` |
| Work item | `P3-STAGE-002-C`; closes D and parent after repository closeout |
| Result-bearing revision | `1cccd0ac230bcb39d89b4514fd08c8dfed639215` |
| Event Worker image | `quanthus-event:git-1cccd0a` |
| Event Worker image digest | `sha256:62de30a980b9a503ee6d60584369634c1e77b4d438fbc5726b356ddbe0ce441b` |
| Job | `job_5e2d82adb2a8492488725fcda379424f` |
| Run | `run_5e2d82adb2a8492488725fcda379424f` |
| Input digest | `sha256:fe32ef536d4494f269b6e829b2dbaead797528181f61b275639c2c2f39ce2db3` |
| Manifest | `sha256:ce9418eed4861bcdf17d38eea9dcee297a4921ac33fd34e0714b40c3a6d85b94` |
| Projection contract digest | `sha256:4452283774f1c0a30faa6f95b03076f3191b5fd0036835f71046ba047490876d` |
| Formal projection digest | `sha256:47797766b987d7886de2be86587c7cbd4d7af8d9551d4dc38cd96eb3c6f8a221` |
| Source Support Evolution Facts | `sha256:6abbd976591739ede4451fd15ad2a11e8dd98519cc8039c2374f575c0035aff8` |
| Result digest | `sha256:2e1ae9ffceb500948dc08401d3b8672c0f267b659ea2e71f2837e43138f22dea` |

## 1. Qualification path

The qualification did not open DWD or any market-data provider. It consumed exactly the two already
accepted immutable `P3-STRUCT-005` Support Evolution/F9 cold artifacts and, for each source run:

1. invoked the original F9 deep verifier;
2. reconstructed the admitted source rows, base Structure v2 and Support Evolution Facts;
3. applied `config/stage-10-11-projection.v1.json`;
4. compared every formal Stage-10 context predicate with retained `stage_10_active`;
5. compared every formal Stage-11 rebound predicate with retained `stage_11_active`;
6. required every `primary_stage` to remain null.

The deep verifier also enforces `market_data_reads=0` and `dwd_provider_opens=0`; a nonzero value
would have failed the Job before publication.

## 2. Exact result

The Job succeeded with:

```text
source_run_count                    = 2
row_count                           = 241
stage_10_confirmation_count         = 2
stage_10_context_count              = 10
stage_11_rebound_condition_count    = 6
nested_10_11_candidate_count        = 6
primary_stage_non_null_count        = 0
legacy_predicate_compatibility      = true
p2_probe_count                      = 0
p2_fill_count                       = 0
execution_authorized                = false
official_label_card_authorized      = false
```

`projection-a.json` and `projection-b.json` are byte-identical:

```text
sha256:35e7e98bc865f69d02f140849b2afe363f4e50a289f2766c543313ee8d424ad0
```

This proves the formal projection is deterministic across the two independent accepted F9 source
runs and does not alter the retained Stage-10/11 predicates.

## 3. Accepted semantic boundary

The following is now accepted as the **formal development Projection Contract**:

- Stage 10 is a persistent structural-failure **context proposition**;
- Stage 11 is a nested current rebound **condition proposition** inside an already confirmed
  Stage-10 context;
- candidate set `{10,11}` is intentional and legal at the projection/evidence layer;
- candidate overlap is not publication of two primary stages;
- `primary_stage` remains null;
- Stage 11 consumes the prior immutable Stage-10 confirmation and does not re-prove the current
  2% failure-depth condition;
- reclaim/censor clears current propositions without rewriting earlier valid confirmation evidence;
- a Stage-11 current condition becoming false does not, by itself, define a `11 -> 10` primary-stage
  transition.

The 5-session, 2% and 5% values remain projection policy. They are not part of the identity of the
underlying **演化支撑事实族 / Support Evolution Fact Family**.

## 4. Claim ceiling

This PASS does **not** authorize or prove:

- `primary_stage` arbitration/compression;
- HUMAN_EXPERT_REFERENCE agreement;
- external/forward validation of Stage 10/11 thresholds;
- SHADOW/QUALIFIED/ACTIVE LabelCard publication;
- daily/as-known production inference;
- strategy, portfolio, notification, paper or live trading consumption;
- economic value;
- Stage 5/6/7 reactivation.

The 2022-2024 consumed arbitration partition was not reread. 2025 permanent blind remains untouched.
Stage 5/6/7 remain `INCONCLUSIVE_PARKED`.
