# P3-EXPERT-004 Candidate Detail Expert Evidence Acceptance

Date: `2026-08-23` UTC

Issue: `#150` / `P3-EXPERT-004`

Implementation commit: `a5846ac`

Public deployment: `https://quanthus.oci.061718.xyz`

## Accepted outcome

The existing candidate detail response and page now project one exact, read-only expert-evidence
package for `candidate.p4-trend-risk@1.0.1`. Other candidate versions return
`expert_evidence.state = not_applicable`. No expert mutation route, lifecycle decision, governance
transition, promotion, notification, Order, Fill, or execution authority was added.

The deployed P4 response contains:

- state `available`;
- 2 formal experts and 2 assessment scorecards;
- 4 economic-effect entries, including the unallocated interaction;
- 2 preregistered disagreement cases;
- `lifecycle_decision = null`;
- candidate approval state `pending-review / governance-not-submitted` with 0 approval events;
- `execution_authorized = false`.

## Immutable evidence chain

| Artifact | Identity |
| --- | --- |
| Product package | `expert-evidence-candidate-p4-trend-risk-1.0.1-undecided-v1` |
| Product package digest | `sha256:aa650f5188ad3929a673cf544c61f3ae97ace4da07a8ef52fd403fa0a5a2d2a8` |
| Strategy card digest | `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c` |
| Topology manifest digest | `sha256:b53e9b424080c4a051419c20a4777d6afcf51bf209eb5705b09ba6ca6d4768a3` |
| Evaluation Run | `expert_run_481e6181930f6e031e479c65d40a7fa04e2f0ebf4d9b6290c3605810d1cc60c0` |
| Formal scorecard digest | `sha256:538978540758343a452c37ee2598b12cb61875890018da741a684fdb75333ac0` |
| Attribution release | `formal-attribution-711220040b90681feb6bd50f` |
| Attribution release digest | `sha256:1754a4a3a185420734e6addab71110ee82ecd71fb4fbd182f9ee1506c49c98ff` |
| Pipeline evaluation digest | `sha256:711220040b90681feb6bd50f82a0c5de8e142086bdace0aa158f7b82cad5d4b6` |

The API verifies exact Run inventories, checksums, Parquet row counts, strategy-card semantics,
evaluation identities, topology reconstruction, release inventory, release digest, and `READY`
receipts before projecting fields. Invalid trusted bindings return a typed HTTP 503 and never mix
partial or older evidence.

## Deployment boundary

The API image contains only the narrow expert read path and `pyarrow` needed for immutable Run
verification. It does not contain `backtest`, `event_library`, `providers`, or `expert_strategy`.

| Runtime | Image | Image digest | Size |
| --- | --- | --- | --- |
| API | `quanthus-platform-kernel:git-a5846ac` | `sha256:57d0290b233b04507475b10f783ef08879393a6c6098d8493e2e1af4b9e23400` | 108,183,697 bytes |
| Web | `quanthus-platform-web:git-a5846ac` | `sha256:26a201f023001f30accacf2fd7597933760647a3ef85ff21795fa464613f8188` | 81,378,312 bytes |

The production release had been published from a mode-`0700` temporary directory. Its content and
digest were valid, but the UID-10001 API correctly failed closed because it could not traverse the
directory. The publisher now seals immutable releases as mode `0555` directories and `0444` files.
The same metadata-only permission repair was applied to this exact verified release; no artifact
bytes or digest changed.

## Verification

- `QUANTHUS_TEST_EXPERT_ARTIFACT_ROOT=/tmp/quanthus-expert-inspect.RnvypC make check
  PYTHON=.venv/bin/python`: `1335 passed, 4 skipped` in 180.14 seconds. The four skips are the
  separately configured PostgreSQL fixture tests; the production expert projection test ran.
- Strict MyPy passed for the product projection, release verifier, and Platform query closure.
- Ruff and `git diff --check` passed.
- Product Web contract suite: 6 passed.
- Product Web Vite production build passed. The existing large-bundle warning remains.
- UID-10001 minimal-image smoke test projected `available / 2 / 2 / 4 / 2` from the production
  read-only expert volume.
- Public login with the accepted admin credential, catalogue read, P4 detail read, baseline detail
  read, OpenAPI route audit, and `/ready` all passed.
- OpenAPI contains no route with an expert mutation surface.
- Browser console/page-error collection was empty and horizontal overflow was absent at both
  viewports.

## Browser evidence

- [Desktop 1440x1000](issue-150-candidate-expert-evidence-20260823/desktop-1440x1000.png),
  `sha256:f6d94e91bf07d15349a5ce07dd412fbeb14c56f561cdad9f005c38c9d2ebcb2d`
- [Pixel 7 mobile](issue-150-candidate-expert-evidence-20260823/mobile-pixel7.png),
  `sha256:db682ff5d99f41b2e46d40fbb56a549836875d631ccbadcefd46dba8b5c7e252`

## Residual risks and rollback

The Web build reports existing dependency audit findings and an existing bundle-size warning. No
dependency major upgrade was introduced into this bounded Issue. The evidence remains development
2020-2021 only; arbitration 2022-2024 and permanent-blind 2025 were not accessed.

Rollback removes the optional response field, Web section, and serving binding while preserving all
immutable Runs, releases, and approval state. Removing the read-only expert volume does not affect
candidate replay, the database, or either Worker.
