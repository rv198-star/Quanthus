# P4-LABEL-001 Production A/B Acceptance

| Field | Value |
| --- | --- |
| Work item | `P4-LABEL-001` |
| Status | accepted |
| Production revision | `62a8329ae344792a218d0451880cc1a7d30402fa` |
| Input digest | `sha256:7b92a5d570c641a92d8ec587dd27f4bf9a2a57524dfb1f07aacf1d3bb8212fea` |
| Outcome card | `p4-event.outcome-first-passage@1.0.0` / `sha256:b623a647116a69a0d6559bd265b51bdd3ac0654826cf54ba38b87d819ea165ca` |
| Structural-break card | `p4-event.structural-break-trend@1.0.0` / `sha256:1294e0550361bafe9ecfbcfddb32a7bb1ff378332c4fb173db662f95debad9fc` |

## Production runs

Run A:

- Job: `job_83cfcce4d9284046a2f1f63157e76ca0`
- Run: `run_83cfcce4d9284046a2f1f63157e76ca0`
- Started: `2026-08-21T08:07:23.441298+00:00`
- Completed: `2026-08-21T10:10:53.082553+00:00`

Run B:

- Job: `job_15e7632e51bb479e9a4cc41001060814`
- Run: `run_15e7632e51bb479e9a4cc41001060814`
- Started: `2026-08-21T10:59:37.260803+00:00`
- Completed: `2026-08-21T13:06:18.151334+00:00`

Both runs used the same production revision and governed input digest, with different idempotency keys.

## Deterministic reconciliation

A and B reproduced exactly:

- candidate events: `36,820`
- executable events: `36,022`
- not-executable events: `798`
- positive events: `4,519`
- neutral events: `4,159`
- failure events: `25,963`
- censored events: `0`
- unavailable events: `1,381`
- settled three-class events: `34,641`

The full accounting reconciles without unexplained loss:

`4,519 + 4,159 + 25,963 + 798 + 1,381 + 0 = 36,820`.

## Immutable identities

A and B reproduced the same Event Library and Label Corpus identities:

- Event content digest: `sha256:80dc078199a94dc4c5009de1b4a211ee400561a97993a71f61a0dc0e06b745ac`
- Event manifest SHA-256: `sha256:117a0be96c61fdf27536d56097ef0312a306f2367edad7127ca9482564353acd`
- Label content digest: `sha256:02fa0057936d9b59d0058325448b7ef638cda3ef28803cd8306e43b0675c1580`
- Label manifest SHA-256: `sha256:31ed3e95771390c4df3cb4cff21ba2b3415458456cf6b6f02621c7c81bdc5b12`

The Event Library content digest remains the same as the accepted #74 corpus, proving that labeling did not mutate candidate-event content.

## Acceptance conclusion

`P4-LABEL-001` passes its reproducible development-corpus gate. The exact approved label cards and the production labeling path deterministically reproduce the same labels and package identities across independent A/B runs. Arbitration and permanent-blind data remain outside this acceptance and retain their existing access gates.
