# Platform Kernel PK4 Operator Web Acceptance

| Field | Value |
| --- | --- |
| Evidence status | accepted |
| Evidence date | 2026-08-12 |
| Accepted gate | PK4 Operator Web |
| Next gate | PK6 Cutover and Recovery open |
| PK5 decision | not applicable to first cutover; later draft-only activation remains possible |
| Feature Manifest | `config/platform-features.v1.json` |
| Gate source | `config/platform-kernel-gates.v1.json` |
| Governing design | `docs/design/lld/11-platform-kernel.md` |
| Operations | `docs/operations/platform-kernel-runbook.md` |

## Result

PK4 is accepted. The Quanthus-owned, source-built operator Web completes the retained login,
logout, MFA, user administration, runtime/gate inspection, and attribution-verification workflows
without importing QuantDinger-Vue or calling a removed capability. It runs as an independently
removable Web container over the accepted API/PostgreSQL kernel. Quanthus remains the authority for
gate and attribution facts.

PK5 Optional Authoring is explicitly not applicable to the first cutover. This is not a permanent
removal: `strategy-indicator-authoring` remains deferred in the recovery inventory and requires a
later, separate draft-only activation decision before implementation.

## Browser Acceptance

The final serial Playwright run passed all eight tests in 16.6 seconds against the production
Docker Web on `127.0.0.1:3100`:

- desktop and Pixel 7 profiles each created a real least-privilege user;
- each profile used its own real operator account to enroll TOTP MFA, complete challenged login,
  and disable MFA;
- each profile logged in and inspected health, readiness, PK gates, users, security, and a real
  attribution verification result that reached `succeeded`;
- each anonymous login surface had zero axe violations and no horizontal overflow; and
- bundle and rendered-route contracts contained no MCP, Fast Analysis, AI Copilot, billing,
  broker, OAuth, or other removed/deferred API reference.

The first browser pass found a login supporting-text contrast defect; its color was corrected from
`#6d776f` to `#59635c` before final acceptance. A release-directory permission defect also caused
early attribution jobs to fail. Directory modes were normalized to readable traversal, and later
jobs verified the same synthetic fixture successfully. Failed historical job rows remain visible
in PostgreSQL; they were not deleted to improve the evidence.

## Runtime And Regression Evidence

- Web production build and Node contracts: `13 passed`; ESLint passed.
- Platform and planning contracts: `30 passed, 1 skipped`. The skip was only the host-DSN variant
  of the PostgreSQL acceptance test.
- Isolated Docker PostgreSQL 16.14 and Gunicorn acceptance: `1 passed`, no skip. Generated evidence
  digests are `bfc029f2529576930abc4e02c0adfed54e288bb65f1cf3a068e278d1a1d79600`
  for JSON and `5c0c0354d26ddfdf2db8c61e81db287984ff7feaba74ce7c6da21caef92ce83c`
  for JUnit.
- Platform plus existing attribution-publication regression: `151 passed, 1 skipped`; the Docker
  run above executes the skipped host-DSN behavior against a real disposable PostgreSQL service.
- Final API image: `sha256:c624ef3051afd02410a1000ab79d478e3fb4d9c4960a952f30a3f2bfa2e9af86`,
  running as `quanthus` with one Gunicorn worker and no control-socket write error.
- Final Web image: `sha256:dda321cf7daade40bb42187479799b1da68622f9c18ca46d003390c50211d3b8`,
  running as the unprivileged `node` user with no production `node_modules` tree.
- PostgreSQL, API, and Web Compose services all reported healthy.

## Network And Visual Evidence

Tailscale Serve exposes `https://quanthus-oci.tail3912a.ts.net:8443` to the tailnet only and proxies
to Web loopback port 3100. Forced-resolution HTTPS checks returned PostgreSQL readiness and the
`平台控制台 · Quanthus` operator title through that exact edge.

Final desktop and Pixel 7 screenshots cover anonymous login and authenticated runtime status.
Direct inspection found no incoherent overlap, clipping, or horizontal overflow. These generated
screenshots are local acceptance artifacts and remain outside Git.

## Dependency And Scope Risk

The compatible Next, React, Playwright, Vite, Wrangler, and Cloudflare build dependencies were
updated before final acceptance. `npm audit --omit=dev` reports zero production vulnerabilities.
The full source/build tree still reports two high findings in Vinext's `image-size` build-time
dependency; no non-breaking upstream fix exists. The production Web image contains compiled output
and the repository-owned Node server, not `node_modules` or the vulnerable build parser. This is a
bounded build-input risk, not a claim of zero supply-chain risk, and should be removed when Vinext
publishes a compatible fixed dependency.

## Acceptance Limits

The mounted attribution release is synthetic plumbing evidence, not formal strategy evidence or a
strategy conclusion. The Web does not grant authoring, provider, broker, execution, notification,
MCP, or AI authority. Repository-wide `make check` still cannot traverse pre-existing P4
event-library files and tests owned by another lane; those permissions were not changed, and that
unrelated gap is not counted as PK4 evidence.
