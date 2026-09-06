# Continuous Structure Facts v1 release-ready checkpoint (2026-09-06)

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-003` |
| Design status | approved |
| Delivery gate | open; Real-DWD qualification pending |
| Base revision | `045e6f92e0dfedebd9bc632729479797ecd19711` |
| Origin/main at freeze | `045e6f92e0dfedebd9bc632729479797ecd19711` |
| Repository-wide check | `1424 passed, 7 skipped` |
| Focused Structure/Worker/docs check | `69 passed` |
| `git diff --check` | PASS |
| Real-DWD Run A/B | NOT RUN |
| Accepted | NO |

## 1. Frozen release purpose

This release candidate closes `P3-STRUCT-002` and opens the already owner-approved
`P3-STRUCT-003 / Continuous Structure Facts v1` production qualification. It changes no DWD data,
does not open a new Worker family, and does not authorize economic, Shadow, paper, live, model, or
thirteen-state behavior.

The exact fact-product design is `docs/design/continuous-structure-facts-v1.md`. Its three approved
architecture decisions are frozen:

1. Continuous Structure Facts v1 is the primary Structure Intelligence computational layer.
2. Breakout episode creation is price-only (`T close > prior 20-session resistance`); volume is an
   independent fact and never gates episode existence.
3. Coarse states are deterministic explanation-only projections and do not belong to fact-row or
   ADM identity.

## 2. Frozen implementation set

Primary new files:

- `config/structure-continuous-facts.research.v1.json`
- `config/structure-facts-run.development.v1.json`
- `src/quanthus/structure_facts.py`
- `src/quanthus/structure_facts_run.py`
- `tests/unit/test_structure_facts.py`
- `tests/unit/test_structure_facts_run.py`
- `docs/acceptance/continuous-structure-facts-v1-implementation-checkpoint-20260906.md`
- `docs/acceptance/structure-intelligence-economic-final-acceptance-20260906.md`

Updated integration/governance files include:

- `src/quanthus/platform/event_worker.py`
- `ops/platform/compose.yaml`
- `tests/unit/test_structure_mvp_worker.py`
- `tests/contract/test_platform_kernel_planning.py`
- `tests/contract/test_document_traceability.py`
- `docs/README.md`
- `docs/design/lld/03-label-feature-system.md`
- `docs/design/lld/11-platform-kernel.md`
- `docs/design/structure-intelligence-economic-value.md`
- `docs/work-items/index.md`

## 3. Key frozen SHA-256 identities

At release freeze:

```text
cb439d70825811a0f642142f39c75da8bb9ac89e3b4d79dc78c6a1c006bd062d  config/structure-continuous-facts.research.v1.json
25e003f13d3ad005c7b44579b9192e7c3f2b3ceb371fc7a2dde54d57d6b1ecde  config/structure-facts-run.development.v1.json
0cc912a8e83ba7b83b59e0b26172e1fff54e502cb8c2cf4f5ae8590b0cc3de4e  src/quanthus/structure_facts.py
ade1d5886fc4ff854ba2fdbd471dc14707fd31ed6c1c18b68e702045534dc287  src/quanthus/structure_facts_run.py
c1989ddf597d3075f470e6938ef711a8ed37869964273a7f9a7b6bf6cb2b9f98  src/quanthus/platform/event_worker.py
86dc127ade795a1866e97099a70bb9d9bdc2b3f6b4f7ecba2cde26eac9146735  docs/design/continuous-structure-facts-v1.md
381e4e29499bbc6b38326e4a9f01e1fd997d18cff457f1f8425ba10ef7302cde  docs/acceptance/structure-intelligence-economic-final-acceptance-20260906.md
eaf4c5867c360e5c178443854a87e9f1127fc22e547ce06d17af7975a3e9310a  docs/acceptance/continuous-structure-facts-v1-implementation-checkpoint-20260906.md
f227ddd506f1afaa5823ccf4bdf3fc54240093d23db7ed6251f2413add12145b  tests/unit/test_structure_facts.py
3ebdeca478f51de336145dbd2b291ce5f5a995137575c95b237004b3cc2028b8  tests/unit/test_structure_facts_run.py
cfcd985996e70423e0ce3cd59b199f7a95b6e59646cc5803b62c85890c172983  tests/unit/test_structure_mvp_worker.py
58d19e4c44e88a45256164ebd1186cd95265e95144f73523c9a7330901cf792b  ops/platform/compose.yaml
```

These hashes are the final pre-publication identities after the exact fact-row whitelist and
`P3-STRUCT-002` closeout edits. The release-ready document itself is naturally outside this hash
list and must be included in the eventual Git commit.

## 4. Engineering evidence already passed

The release candidate proves synthetically/contractually:

- T is excluded from prior-20 support/resistance anchors;
- price-only crossing creates an episode even at low volume;
- breakout/support anchors, attempt snapshot/time, and episode identity are frozen;
- later holds advance count/age without rewriting prior rows;
- failed hold ends the old episode and a later crossing receives a new identity;
- gap/reset clears causal history and never means structural damage or forced sale;
- fact rows contain no coarse-state authority;
- direct `Continuous Facts -> FeatureFact -> Expert -> Aggregator` consumption works without coarse
  mediation;
- bounded P2 compatibility fills pass on the fake accepted replay-provider fixture;
- artifact tampering fails closed; and
- the Event Worker rejects a hash-consistent package containing any extra/non-whitelisted fact-row
  field.

## 5. Exact remaining publication / qualification sequence

Once a Git-write-capable session publishes this frozen release candidate:

1. create a dedicated `p3-struct-003-continuous-facts` branch from the exact current base;
2. commit the full frozen release set and push it to GitHub;
3. fast-forward `origin/main` only if it has not advanced independently;
4. deploy the exact new revision's Event Worker with all qualification seeders `0`;
5. verify production health, clean checkout, read-only DWD Base/correction mounts, and typed Structure
   admission;
6. create one qualification-only revision changing **only**
   `QUANTHUS_STRUCTURE_FACTS_QUALIFICATION_SEED: "0" -> "1"` plus its contract expectation;
7. deploy that exact revision; startup must create exactly two fixed facts A/B Jobs using only
   `config/structure-facts-run.development.v1.json`;
8. require both Runs to succeed and reproduce exact fact content/ADM/result identities, episode
   identities, fact/renderer/consumer counts, and P2 probes/fills;
9. perform the preregistered outcome-blind sample review using T-close facts only; and
10. close `P3-STRUCT-003`, restore the facts seeder to `0`, deploy the closeout revision, and verify
    production health.

## 6. Claim ceiling

At this checkpoint:

```text
P3-STRUCT-002: CLOSED
P3-STRUCT-003 implementation: RELEASE-READY
Real-DWD qualification: NOT RUN
Independent reproduction: NOT RUN
Outcome-blind real-DWD review: NOT RUN
Continuous Structure Facts v1 accepted: NO
Production Structure Expert: NOT AUTHORIZED
13-state: NOT AUTHORIZED
ML: NOT AUTHORIZED
Shadow / paper / live: NOT AUTHORIZED
```

No additional semantic design or threshold change is required before the fixed Real-DWD
qualification. Any post-publication behavior change invalidates the frozen release identity and
requires a new explicit revision/evidence record.
