# P3-EXPERT-005 MVP Acceptance and Shadow Disposition

Date: `2026-08-23` UTC

Issue: `#151` / `P3-EXPERT-005`

Implementation commit: `b6e7dd5`

Public deployment: `https://quanthus.oci.061718.xyz`

## Accepted outcome

The exact `candidate.p4-trend-risk@1.0.1` development package is independently reproduced,
bound to one append-only human lifecycle decision, and projected on the existing candidate-detail
API/Web page. The lifecycle result is `inconclusive`; its Forward Shadow disposition is
`disabled` with reason `forward_shadow_not_explicitly_authorized` and no observation budget.

The approved strategy digest froze the exact strategy/evidence identity and authorized continuous
completion. It did not explicitly authorize the semantic choice `keep` or entry into Forward
Shadow. The implementation therefore took the only authority-conservative compatible result:
`inconclusive + disabled`. This interpretation grants no new authority and can be superseded only
by a later append-only human decision for a new accepted package.

## Immutable evidence chain

| Artifact | Identity |
| --- | --- |
| Strategy card | `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c` |
| Acceptance packet | `sha256:8ba3263be7a1de6b8c5459dc6ca3458832f30ade510e7c047e7af1591c20b609` |
| Lifecycle decision | `sha256:2ebfc70329571cc188de7ea7abd0353c47b54d072b4114f6661dfc195e3df6ee` |
| Decided product package | `sha256:ae685b98a3ae2ebb477783d184dbea83bfa04bbb495909919b2d3ba28e69fa59` |
| Formal evaluation Run | `sha256:481e6181930f6e031e479c65d40a7fa04e2f0ebf4d9b6290c3605810d1cc60c0` |
| Technical assessment Run | `sha256:eff9bef52ddd7eba83c7acf75ec75189fba503a0f6f210983bcc99092b8833a2` |
| Formal scorecard | `sha256:538978540758343a452c37ee2598b12cb61875890018da741a684fdb75333ac0` |
| Attribution release | `sha256:1754a4a3a185420734e6addab71110ee82ecd71fb4fbd182f9ee1506c49c98ff` |
| Pipeline evaluation | `sha256:711220040b90681feb6bd50f82a0c5de8e142086bdace0aa158f7b82cad5d4b6` |
| DWD effective view | `sha256:bfacd36302f791b4f489aa2d8f4d768f87d618b5a9b44326c32d35289b723d97` |

The old undecided package remains immutable in the product configuration. Only the exact P4
candidate binding advanced to the new package. The read audit covers development dates
`2020-01-01` through `2021-12-31`; formal strategy decisions end on `2021-12-30`. Arbitration
`2022-2024` and permanent-blind `2025` were not read.

## Disabled Shadow proof

- The static schedule contains zero enabled bindings and one disable receipt for the exact strategy
  version and lifecycle decision digest.
- Both `forward_observation` and `mature_settlement` require an enabled static binding before the
  PostgreSQL queue insertion point.
- The accepted disabled submission returns `shadow_binding_disabled`; the store remains at zero
  Jobs and no PostgreSQL DSN is read by the node command.
- No promotion, notification, broker, Order, Fill, governance transition, or execution authority
  was added. The public candidate response still reports `execution_authorized = false`.
- Rollback keeps all published packets and decisions, removes or changes only the static schedule
  binding, and may point the read-only product binding back to the preserved undecided package.

## Verification and deployment

- Full check with production expert evidence mounted: `1344 passed, 4 skipped` in `181.11s`.
  The four skips are separately configured PostgreSQL fixture suites; the production packet,
  reproductions, release, decided projection, and zero-Job tests ran.
- Strict MyPy and Ruff passed for the lifecycle and product projection closure.
- Product Web contract suite: 6 passed; Vite production build passed.
- API minimal-image smoke as UID `10001` with the evidence volume read-only returned
  `available / inconclusive / disabled`.
- Public login, readiness, candidate detail, lifecycle digest, zero execution authority, and
  OpenAPI mutation-route audit passed. OpenAPI has no expert mutation route.
- Browser console/page-error collection was empty and horizontal overflow was absent at both
  accepted viewports.

| Runtime | Image | Image digest | Size |
| --- | --- | --- | --- |
| API | `quanthus-platform-kernel:git-b6e7dd5` | `sha256:7741133bd1ffb38640a946989ef9b8541b77e3353e2294c5cedf1cec86982661` | 108,188,873 bytes |
| Web | `quanthus-platform-web:git-b6e7dd5` | `sha256:eea7a733a29d9965df44f7c93a3a205a68cd68e9d515fcc3041dfd849f2e6550` | 81,378,426 bytes |

## Browser evidence

- [Desktop 1440x1000](issue-151-expert-mvp-shadow-handoff-20260823/desktop-1440x1000.png),
  `sha256:ce0f2e42b7316ab05e506d6e15c550d3b3cbaf60afb398c11bc11e0bbe2339e0`
- [Pixel 7 mobile](issue-151-expert-mvp-shadow-handoff-20260823/mobile-pixel7.png),
  `sha256:aa913ce553f55ea3efdb57b589da8307021ce50c4117a4ce4a543c29a2cb3c98`

## Residual limitations

Human review time was not separately metered and is declared `not_metered`; no estimate was
invented. The Web build retains its existing bundle-size warning. The result is development-only
and does not claim forward performance. Enabling Forward Shadow requires a later explicit human
decision with a compatible finite observation budget; this acceptance does not provide one.
