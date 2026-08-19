# Platform Kernel PK6 cutover and recovery acceptance (2026-08-13)

| Field | Value |
| --- | --- |
| Status | accepted |
| Work items | `PK-RECOVERY-001`, `PK-CUTOVER-001` |
| Product URL | `https://quanthus.oci.061718.xyz/` |
| Production source | GitHub `main@b9d459a129bcf5e30d275f97010f67869b0e1226` |
| Recovery package | node-local mode-0700 `pk6-b9d459a-20260813` |

## Accepted evidence

- Remote `main` contains the complete Platform Kernel, Replay Worker, QuantDinger-derived Product
  Web, DWD v2.1 correction, contracts, and operations baseline. The merge preserved the promoted
  DWD Base authority of 455,817,962 rows and 14,447,400,032 payload bytes.
- The baseline passed 1,135 Python tests with three PostgreSQL-marked tests skipped in that run;
  the repository Docker PostgreSQL acceptance separately passed from clean schema. Incremental
  Ruff, doctor, compile, planning, Product Web tests/build, operator Web tests/build/lint, and all
  three production image builds passed. Existing full-repository Ruff/mypy debt remains outside
  this milestone; it was not hidden or converted into a false pass.
- The self-verifying recovery package contains a complete Git bundle, exact API/Web/Replay and
  PostgreSQL images, PostgreSQL custom dump, sealed candidate Runs, attribution releases,
  Compose/config/license inventory, expected state, restore tooling, and SHA-256 checksums. It
  excludes secrets, failed `.staging` attempts, and the external read-only DWD authority.
- Isolated project `quanthus-pk6-restore-drill2` restored from the package into separate volumes
  and a private network. API and Web became healthy without a Worker or Traefik attachment. The
  restored database contained one candidate Run, five active pointers, and 24 users; every Run
  `result.json` matched its PostgreSQL `result_sha256`. The isolated project and volumes were then
  removed; its receipt remains beside the package.
- The deferred `redis-response-cache` capability was restored from QuantDinger `v5.0.15` /
  `e64e1c227bf3174e441a42143620179b286387e1` in a disposable worktree. Three cache-port, TTL,
  namespace, and default-exclusion contracts plus Ruff passed. The experiment was discarded and
  Redis remains absent because no measured activation trigger exists.
- Production was cut from the damaged repository path to clean checkout
  `/home/ubuntu/services/quanthus-platform`. API, Web, Replay Worker, and PostgreSQL Compose
  metadata all point to that checkout. Node-local environment references immutable commit tags.
- A real rollback restored the pre-PK6 API, Web, and Replay image digests; health, public edge,
  PostgreSQL counts, and Run hash passed. Production was then moved forward again to API
  `sha256:ce1eebc9...`, Web `sha256:8f6cc9d...`, and Replay `sha256:570d2c2f...`.
- Final production retained job counts of 19 succeeded, four failed, and one canceled, plus one
  sealed Run, five active pointers, 24 users, and Run digest
  `6acc41583875759e362dba6480e802b222aacbcc9b2fe5f74596a9efa753a89b`.
- Public `/`, `/operator`, candidate detail, and `/ready` returned 200; public `/metrics` returned
  404. Admin login, five-candidate catalogue, QuantDinger detail, replay state, sealed Run history,
  and logout passed. Only Web joins `traefik-net`; API, Worker, and PostgreSQL remain private.
- Traefik remains the sole product edge. The former tailnet-only Tailscale `:8443` Serve route was
  reset and reports no Serve configuration. No QuantDinger container or application service remains.
  Existing node-local warehouse schedules remain intentionally outside Platform Kernel ownership.

## Residual risks

- The retained QuantDinger Vue 2 dependency is end-of-life and its locked dependency audit reports
  one high-severity issue; the operator Web dependency tree reports two high-severity issues. This
  accepted migration preserves the reviewed pinned source and does not claim those risks are fixed.
- The recovery package is node-local. Loss of the entire OCI disk remains outside this drill; a
  later off-node encrypted copy requires a named backup destination and credential authority.
- Pre-PK6 rollback image tags are retained locally for incident recovery. They are not the active
  deployment and do not depend on the old repository path.
