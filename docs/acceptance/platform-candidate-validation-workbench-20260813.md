# Candidate validation workbench acceptance (2026-08-13)

| Field | Value |
| --- | --- |
| Status | internally accepted and deployed |
| Work item | `PK-WEB-003` |
| Product URL | `https://quanthus.oci.061718.xyz/` |
| Governing design | `docs/design/lld/11-platform-kernel.md` |
| Product decision | `docs/architecture/adr-0011-quantdinger-vue-product-web.md` |

## Correction

The candidate-only Product Web was a read-only catalogue: users could see five summary rows but
could not inspect a candidate or perform a review workflow. This correction turns the retained
QuantDinger-derived Web into a candidate-validation workbench.

The accepted target includes:

- searchable, filterable, sortable candidate queue with current Platform review state;
- candidate detail routes with real replay metrics and 121 monthly equity observations;
- complete closed-trade ledgers for all five candidates, 929 trades in total;
- frozen parameters, run manifest, data/replay lineage, and QuantDinger policy divergence;
- append-only `start-review`, `request-changes`, and `submit-governance-review` actions; and
- admin/operator writes, viewer read-only access, rejected-candidate closure, and explicit false
  governance-admission and execution-authorization fields.

Review submission only queues a candidate for a separately owned governance review. It does not
change the immutable product catalogue, admit a strategy, or authorize execution.

## Evidence projection

`config/product-candidate-evidence.v1.json` is generated from accepted replay artifacts. It contains
five candidates, 121 monthly equity observations per candidate, and all 929 closed trades. The
embedded content digest is
`sha256:ddd1ba88c768042fe38656a9ea6744ec95f68588eb14947ff695f70e0c79fe46`.
The API verifies the schema, catalogue version, exact digest, recomputed content digest, candidate
coverage, and required evidence sections. This bounded projection is not a per-strategy ADS.

## PostgreSQL acceptance

The isolated Docker acceptance passed against PostgreSQL 16.14 and one-worker Gunicorn. A clean
schema applied `identity-0001`, `jobs-0001`, and `candidate-review-0001`; the second migration run
was empty. The exact six-table surface includes `platform_candidate_reviews`.

The runtime test authenticated an operator, loaded the QuantDinger candidate with 121 equity points
and 18 closed trades, appended `start-review`, read the persisted `reviewing` state from both detail
and catalogue APIs, and verified `governance_admitted=false` and
`execution_authorized=false` throughout.

| Artifact | SHA-256 |
| --- | --- |
| PostgreSQL runtime JSON | `f085063f96b453e0c527e25be7a0b9354d7143aa3e79f02a192427a5fbc097c1` |
| PostgreSQL JUnit XML | `9b68cfb995e47ddd913190dab5790e1eeefa6ec9d27c7f5f3bd9ae2408385db0` |

## Verification

- Product Web source contracts and production build: 5 passed; build passed.
- Focused Platform suite: 39 passed.
- Generic operator/attribution Web contracts: 13 passed.
- Ruff, Python compile, and diff checks: passed.
- Public Playwright acceptance: 4 passed on desktop Chromium and Pixel 7, including direct detail
  route reload, nonblank ECharts canvas pixels, and horizontal-overflow checks.
- Authenticated public API returned 121 equity observations, 18 complete QuantDinger trades,
  `pending-review`, empty production review history, and false governance/execution fields.
- API image:
  `sha256:8466cc316fc118ba8838837ee0b8434f1e18395e12aeb0b1c99f68a452717acd`.
- Web image:
  `sha256:5b1f24f0ae88491c22bdfdb34911c3462db41c6693e8b40b36bcaf285f10c824`.
- PostgreSQL container remained
  `9ca0889ec025ff1c731abbb3c510930726e26d723be8c4e4a3a3c82ed8b1cfc3`
  and retained named volume `quanthus-platform_quanthus-platform-postgres` across the API/Web-only
  rollout. The production review table was empty after non-mutating browser acceptance.
- Desktop screenshot: `1280x2561`,
  `sha256:c6ae1c0525c87f5a8b11a9f99e58ce061382da4f1c8a100c525a77bdcd1552d0`.
- Mobile screenshot: `1082x9104`,
  `sha256:43a36414dd0624facd9ce067676651396aa39c338932189b752a3c383be43426`.

Repository-wide `make check` is expected to remain blocked only by the pre-existing unreadable P4
`event_library` files. Their ownership, permissions, and contents are outside this work item and
must not be changed here.
