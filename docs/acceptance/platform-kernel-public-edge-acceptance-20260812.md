# Platform Kernel Public Traefik Edge Acceptance

| Field | Value |
| --- | --- |
| Evidence status | accepted |
| Evidence date | 2026-08-12 |
| Scope | Public HTTPS carrier for the accepted PK4 Operator Web |
| Public URL | `https://quanthus.oci.061718.xyz/operator` |
| Governing design | `docs/design/lld/11-platform-kernel.md` |
| Operations | `docs/operations/platform-kernel-runbook.md` |

## Decision And Boundary

The operator authorized publication through the existing OCI Traefik edge after the original
tailnet-only address was not reachable from the browser device. This supplements the accepted PK4
Web evidence; it does not reopen the Web implementation or expand Platform Kernel domain authority.

Only the Web container joins `traefik-net`. Traefik terminates TLS and forwards to Web port 3100.
The Web remains the same-origin proxy for admitted API paths. PostgreSQL and API retain no public
port or Traefik route, and Tailscale Serve remains an independent tailnet-only fallback.

## Required Runtime Evidence

- public DNS resolves `quanthus.oci.061718.xyz` to the OCI public address;
- the public `/operator` and `/ready` paths return HTTP 200 with a trusted certificate;
- the response carries the configured HSTS, frame-denial, content-type, referrer, and permissions
  headers;
- the Web container is healthy and is the only Platform Kernel container on `traefik-net`;
- public `/metrics` is not routed while loopback metrics remain available;
- login, MFA challenge, login throttling, and same-origin API behavior remain intact; and
- Tailscale Funnel is disabled.

## Runtime Results

- Public DNS resolved `quanthus.oci.061718.xyz` to the OCI public address `168.107.27.177`.
- Direct public-name checks returned HTTP 200 for `/operator` and `/ready` with TLS verification
  result 0. `/ready` reported PostgreSQL ready through the Web same-origin proxy.
- The active Let's Encrypt certificate covers `*.oci.061718.xyz`; its observed validity window is
  2026-06-14 through 2026-09-12.
- Public `/operator` carried one-year HSTS, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and the restricted camera,
  geolocation, and microphone permissions policy.
- Public `/metrics` returned 404 after the edge exclusion was applied; loopback `/metrics`
  continued to return 200.
- The full public-domain Playwright run passed 8/8 desktop and Pixel 7 tests in 16.7 seconds,
  including login, least-privilege user creation, MFA enrollment/challenge/disable, gate and
  runtime inspection, attribution verification, accessibility, and overflow checks. After the API
  metadata image refresh, the core public-domain suite passed again at 4/4 in 9.3 seconds.
- A dedicated invalid-login probe returned `invalid_credentials` through the fifth attempt and
  `login_rate_limited` after the configured threshold. Anonymous `/auth/me` remained 401.
- PostgreSQL, API, and Web were healthy with `unless-stopped` restart policy. Only Web joined
  `traefik-net`; API retained host-loopback 8080 and PostgreSQL retained no host port.
- Tailscale reported the fallback as `tailnet only`; Funnel was not enabled.
- The refreshed public Gate API reported `platform-kernel-2026-08-12.6`. Focused Platform Kernel
  tests passed at 31, with the host-DSN acceptance variant skipped because its environment variable
  was not set; the separate real Docker PostgreSQL acceptance remains the applicable database
  evidence.

The single-worker API metadata refresh used a sequential replacement and produced one transient
Web proxy fetch failure during that replacement. Requests recovered after API health passed and no
later Quanthus-route 5xx was observed. This evidence accepts the public carrier, not zero-downtime
deployment; PK6 cutover and recovery remains open.
