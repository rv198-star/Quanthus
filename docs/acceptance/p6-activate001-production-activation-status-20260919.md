# P6-ACTIVATE-001 Production Activation Status — 2026-09-19

| Field | Value |
| --- | --- |
| Work item | `P6-ACTIVATE-001` |
| Runtime state | production activation enabled |
| Acceptance state | activation engineering complete; final work-item acceptance waits for the first legal post-activation official cycle and its independent reproduction |
| Target candidate | `candidate.p4-trend-risk@1.0.1` |
| Replay Worker runtime | `quanthus-replay:git-2ca5321` |
| Read-only product/health projection | `quanthus-platform-kernel:git-7e0acb2` |
| Official collection progress at closeout | `0 / 66` sessions, `0 / 3` independent blocks |
| First official market date | not yet available |
| Existing final-review issue | `P6-REVIEW-001` / GitHub #120 |
| Authority | paper-only; notification, broker, live capital, arbitration 2022-2024 and permanent blind 2025 remain unauthorized |

## Closeout decision

P6 has crossed the production activation boundary. The production health projection reports
`prospective_activation_enabled` with admission enabled, while official collection remains
`0 / 66`. This is the expected state before the first complete trading session strictly after
the immutable activation market date becomes available as one deeply verifiable READY daily-DWD
release.

This record deliberately does **not** mark `P6-ACTIVATE-001` accepted early. The frozen design
requires the first legal post-activation official cycle and an independent reproduction before the
work item closes. No historical 2026 package may be replayed or relabeled to satisfy that gate.

## Production evidence

The activated production boundary was verified with:

- API `/health` and `/ready`: HTTP 200;
- health projection:
  - `state=prospective_activation_enabled`;
  - `admission_state=enabled`;
  - `official_collection_sessions_completed=0`;
  - `independent_blocks_completed=0`;
  - `first_official_market_date=null`;
  - `last_admitted_dwd_release=null`;
- Replay Worker P6 controller enabled;
- P6 runtime contract compliant;
- read-only mounts present for Base V2.1, correction, frozen research bundle and
  `/data/dwd-daily`;
- latest production disabled rehearsal succeeded with:
  - DWD reads = 0;
  - Shadow writes = 0;
  - Paper writes = 0;
  - settlements = 0;
  - scorecards = 0;
  - notifications = 0;
  - broker actions = 0;
  - live-capital actions = 0;
  - `official_evidence=false`;
- zero official-cycle Jobs existed at closeout, proving no pre-activation history was backfilled.

The activated Replay Worker remains on the exact frozen runtime revision used by the activation
boundary. Read-only API projection changes do not mutate that Worker identity.

## Operating state after closeout

No daily Owner action is required.

When a new READY daily-DWD package arrives, the controller may submit an official cycle only when
its market date is strictly later than the activation market date. The first successful cycle moves
collection from `0 / 66` to `1 / 66`.

After that, the system accumulates the frozen prospective budget automatically:

1. 66 collection trading sessions in three non-overlapping 22-session blocks;
2. no new candidate collection after session 66;
3. up to 20 additional trading sessions for target maturity;
4. no result-driven parameter, threshold, expert-topology or budget changes;
5. infrastructure/data defects may be repaired without rewriting published evidence;
6. poor strategy performance is evidence, not an engineering defect.

## P6-REVIEW-001 / #120

`P6-REVIEW-001` already exists as GitHub #120 and must not be duplicated.

It remains blocked until:

- `P6-ACTIVATE-001` closes with the first legal official-cycle reproduction;
- the declared collection budget is closed;
- the required maturity tail is complete or the frozen stop condition closes it honestly;
- the Paper ledger and eligible settlements reconcile.

At that point the issue becomes the only final-review gate. The human review may continue, narrow
with a new version, remap, retire, or remain inconclusive. No P6 result automatically authorizes P7.

## Claim ceiling

This closeout proves production activation and readiness for prospective collection. It does not
prove strategy quality, positive return, expert value, final Paper performance, P6 review
acceptance, broker readiness, or live-capital authority.
