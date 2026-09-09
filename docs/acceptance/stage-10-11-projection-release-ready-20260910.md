# P3-STAGE-002 Stage 10/11 Projection Contract release-ready evidence

| Field | Value |
| --- | --- |
| Decision | READY FOR IMMUTABLE-F9 QUALIFICATION |
| Work item | `P3-STAGE-002-B` complete; `P3-STAGE-002-C` next |
| Projection contract | `config/stage-10-11-projection.v1.json` |
| Contract file SHA256 | `sha256:1c9e6e39f77a3391524b9034946b6caf84aa4abe36d5331a1751b7168302406a` |
| Qualification request | `config/stage-10-11-projection-qualification.v1.json` |
| Qualification file SHA256 | `sha256:a3c756bbca4f97e91d5a6e7f0a2dbad6f24f142ae801a18c629110fafa566894` |
| Source F9 delivery snapshot | `sha256:a1fcb5ae727fc02748909336463144042996eb35914df12c227cec7e29739637` |
| Source runs | `run_237a3e986b49490381f380a7ed5d4d14`; `run_f371d3f2cb0440aea2c49ceaad1c237b` |
| Source support-facts digest | `sha256:6abbd976591739ede4451fd15ad2a11e8dd98519cc8039c2374f575c0035aff8` |
| Market-data access in qualification | forbidden |
| Primary-stage output | forbidden / always null |

## 1. Frozen semantic change

The milestone does not change the retained Stage-10/11 predicates. It changes their **representation**:

- Stage 10 is formalized as a persistent structural-failure context proposition;
- Stage 11 is formalized as a nested current rebound proposition under that context;
- `{10,11}` is an allowed candidate set;
- that candidate set is explicitly not a primary-stage result.

The existing accepted F9 research projection already represented the same evidence with
`stage_10_active` and `stage_11_active` booleans. The new contract makes the nesting and lack of
primary-stage authority explicit and adds a versioned confirmation identity that binds exact source
fact revisions.

## 2. Engineering evidence before result-bearing qualification

Focused implementation suite currently passes:

```text
12 passed
Ruff: PASS
```

It covers:

- strict projection-contract authority and thresholds;
- first qualifying Stage-10 confirmation and immutable reference reuse;
- Stage 11 remaining valid when current depth recovers inside 2%;
- reclaim clearing current candidates without rewriting historical confirmation;
- exact new/legacy Stage-10 and Stage-11 predicate equivalence on synthetic causal facts;
- explicit `{10,11}` nesting with `primary_stage=null`;
- threshold-version changes leaving Support Evolution Fact identity unchanged;
- fixed qualification request, no arbitrary config path;
- two immutable source-artifact replay with zero market-data/provider access;
- deep reconstruction of formal projection output;
- fixed Event Worker dispatch to `quanthus.stage_projection_qualification`.

## 3. Qualification gate

`P3-STAGE-002-C` must execute exactly one governed qualification Job. That Job reads only the two
accepted P3-STRUCT-005 artifact roots and must prove:

1. both source artifacts still pass the original F9 deep verifier;
2. their Support Evolution facts reconstruct to the accepted digest;
3. the new formal projection is byte-equivalent across the two cold source runs;
4. every formal Stage-10 context predicate equals retained `stage_10_active`;
5. every formal Stage-11 rebound predicate equals retained `stage_11_active`;
6. formal counts remain 241 observations / 2 Stage-10 confirmations / 10 Stage-10 contexts /
   6 Stage-11 rebound conditions;
7. every primary stage remains null;
8. market-data reads and DWD-provider opens remain zero.

Any mismatch is a qualification failure. The run must not retune thresholds or reread DWD.

## 4. Claim ceiling

A passing qualification can only promote Stage 10/11 to a **formal development Projection Contract**.
It does not authorize HUMAN_EXPERT_REFERENCE, external validation, official LabelCards, daily inference,
primary-stage compression, strategy consumption or trading. 2022-2024 is not reread and 2025 remains
untouched.
