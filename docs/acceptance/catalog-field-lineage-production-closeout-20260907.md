# Catalog field-lineage repair production closeout — 2026-09-07

| Field | Value |
| --- | --- |
| Work items | `P3-DICT-002-C` / #230; `P3-DICT-002-D` / #231; parent #232 |
| Implementation revision | `c9bbd0721ec45e3437c64d029c4cf5efbd37c64d` |
| Previous production revision | `10301cdad7fed730072442da7921d24d0de65018` |
| Production disposition | PASS |
| DWD / database / Worker change | NONE |

## 1. Production deployment

The repair was committed and fast-forward pushed to `origin/main`, then deployed with the governed
Quanthus production tool for API and Web only. Dry-run validation passed before the real deployment.

Final admitted images:

```text
API: quanthus-platform-kernel:git-c9bbd07
     sha256:37ccfb5f253ab46575964b641fd1bd03644f16d6413d06dbca677ae5ebc5bcef
Web: quanthus-platform-web:git-c9bbd07
     sha256:a2ac191f9b4dc0c61d32a20411bfdd3eb88d812a668e3a2dc629fae2cc264fce
```

Post-deploy production status reported:

```text
production_dirty: false
production_revision: c9bbd0721ec45e3437c64d029c4cf5efbd37c64d
health: 200 / healthy
ready: 200 / postgres=true
web_ready: 200
API: healthy / running
Web: healthy / running
```

Replay Worker and Event Worker were not rebuilt or restarted by this release. No DWD mount or
PostgreSQL migration was changed.

## 2. Accepted product behavior

The deterministic Catalog projection remains 664 entries / 10 internal maintenance gaps. Its
processing graph now contains 326 declared edges. DWD field focus coverage is explicitly reported as:

```text
DWD fields:              524
field_proven:             10
table_context_only:      514
field_unregistered:        0
```

`dwd_adjustment_factor.effective_from` is `field_proven` and exposes the reviewed chain from source
`adjustment_factors_pit.effective_date`, through interval closing, to the P4 Event Library factor
boundary projection. A field such as `dwd_adjustment_factor.factor_value`, for which this release has
no exact field map, is `table_context_only`: its own main graph contains no invented field edge and
the UI separately labels the parent-table processing context as non-field evidence.

## 3. Verification inherited by this release

Implementation requalification is recorded in
`catalog-field-lineage-repair-20260907.md`:

```text
focused Catalog tests: 49 passed
document / traceability checks: 38 passed
Web unit/render tests: 18 passed
Chromium E2E: 3 passed (1440px, 390px, anonymous boundary)
Catalog-scoped strict TypeScript: PASS
full repository gate: 1588 passed / 7 existing environment skips
```

The E2E test explicitly exercises both the exact `effective_from` proof and the table-context-only
fallback. The full DWD-field test iterates all 524 fields and rejects an unexplained isolated field.

## 4. Smoke and limitation

The governed deployment performed production API/Web health and readiness checks on the new images.
This session did not use or retrieve a production user credential solely to inspect an authenticated
Catalog payload after deployment. Authenticated route semantics are covered by the Platform WSGI/API
tests against the exact committed implementation, while image identity and service health are
proven by the governed deployment/status tools. This limitation is not represented as an
authenticated production payload smoke.

## 5. Claim ceiling

PASS means the field-focus UI no longer implies that an isolated node is a complete lineage result,
and the mandatory adjustment-factor example has exact evidence-bound upstream/downstream lineage.
It does **not** mean all 524 DWD fields have exact field-level provenance. 514 fields deliberately
remain `table_context_only` until reviewed field mappings are available. Static declared lineage is
also not evidence that a particular historical Job actually consumed a field; actual use remains an
operation/decision audit concern.

No formula, Facts value, data row, expert policy, P2 behavior, Shadow/paper/live authority or
execution state changed in this repair.
