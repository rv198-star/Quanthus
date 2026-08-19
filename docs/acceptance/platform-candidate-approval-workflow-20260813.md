# Platform Candidate Approval Workflow Acceptance

## Status

Accepted on 2026-08-13 for work item `PK-WEB-004`.

## Scope

The candidate validation module was migrated from one combined review status to four independent
projections: replay run, evidence review, governance, and execution authority. All five catalogue
candidates are replay-succeeded, pending review, governance-not-submitted, and not-authorized.
Negative replay performance remains evidence and does not manufacture a governance rejection.

## Evidence

- `tests/platform/test_candidate_approval.py` covers evidence-digest isolation, fixed-clock event
  ordering, changes-requested and review-failed closure, governance return/newer-pass enforcement,
  withdrawal/resubmission, terminal rejection/admission, and permanent execution non-authorization.
- `tests/platform/test_http_vertical_slice.py` covers all initial states, review and governance
  APIs, return/review/resubmit/reject, terminal enforcement, and admin/operator/viewer RBAC.
- `tests/platform/test_postgres_acceptance.py` passed in Docker against PostgreSQL 16.14 and real
  one-worker Gunicorn. It wrote four active approval events, retained zero new legacy-review rows,
  bound both governance events to one package, and persisted governance-rejected with execution
  still unauthorized.
- `artifacts/platform-kernel/platform-kernel-postgres-runtime.json` records the disposable-schema
  runtime evidence. The acceptance project and volume were removed after the run.
- Product Web contract tests and the production Vue build pass with separate review/governance
  controls, four-axis list/detail states, role-specific actions, terminal-state explanations, and
  an explicit boundary that rerun is a separate unavailable job.
- Public HTTPS browser acceptance passed four read-only tests on desktop Chromium and Pixel 7,
  including candidate deep-route reload, real trade evidence, nonblank canvas pixels, and overflow
  checks. Production approval event count remained zero after the run.
- Deployed image digests are API
  `sha256:00f9278eeb14800941aae93cb3e0c86320ad9a8ed05715459f32bfd881f98a8c` and Web
  `sha256:a36f0e0d5232c3ba4c64c57064a8666d2abcf3e59291df4a3ee5c3ce78dd5fc1`.
- Desktop and mobile screenshot SHA-256 values are respectively
  `6dfba12d4febe4e5140b3c0ab2d55c593b7d5e79e2eb45133343a47517823326` and
  `da3f3d6f28edbfcec3f4b1b4a631dbe6c30526b83ae5a244cf1040832e6e07e4`.
- Production PostgreSQL container
  `9ca0889ec025ff1c731abbb3c510930726e26d723be8c4e4a3a3c82ed8b1cfc3` and volume
  `quanthus-platform_quanthus-platform-postgres` were preserved across API/Web-only deployment.

## Verification

```text
45 platform tests passed; 1 PostgreSQL test skipped outside its Docker carrier
1 Docker PostgreSQL/Gunicorn acceptance passed
5 Product Web contract tests passed
13 operator Web tests passed
4 public Product Web browser tests passed across desktop and mobile
Python compileall and Ruff passed
```

Repository-wide `make check` remains blocked by the pre-existing unreadable P4 event-library files
listed in the Platform Kernel Mission evidence. No ownership, permissions, or contents of those
files were changed.

## Boundaries

This acceptance does not add replay-job submission, strategy editing, execution authorization, or
live trading. A data/policy rerun must create a new Run and evidence package. Strategy semantic or
parameter changes require a new candidate version. Public browser acceptance must not mutate
production approval state.
