# Structure Intelligence MVP real-DWD acceptance (2026-09-06)

| Field | Value |
| --- | --- |
| Status | **ACCEPTED — technical MVP only** |
| Governing work | `P3-STRUCT-001` |
| Design | `docs/design/structure-intelligence-mvp.md` |
| Qualification Event Worker revision | `269e73304090ba8296a0413ed8b0a0c01430b2b3` |
| Outcome-blind review projection revision | `2957faadb2459000f250d0bc424b608e4f40fbe6` |
| Run A Job | `job_f0f9d19fbcf5c51f27893bcd28972eee` |
| Run B Job | `job_9ee09b038746aadd790779bd2b1eee2e` |
| Real-DWD qualification | PASS |
| Independent reproduction | PASS |
| Outcome-blind semantic review | PASS, 10/10 bounded cases |
| Economic-value acceptance | **NOT RUN / NOT CLAIMED** |
| Draft 13-state activation | **NO** |
| Shadow / paper / notification / live authority | **NONE** |

## 1. Acceptance decision and claim ceiling

`P3-STRUCT-001` is accepted as a bounded engineering MVP proving that Quanthus can carry real
read-only DWD evidence through one thin application projection and the existing decision/execution
contracts:

```text
real DWD Base + correction (read-only)
  -> ReplayDataProvider T-close decision view
  -> thin immutable Structure Research ADM
  -> coarse causal structure facts
  -> accepted trend-stability semantics through a new security-day research adapter
  -> baseline vs structure-assisted expert comparison
  -> existing aggregator
  -> existing close-to-next-open P2 execution probes
```

This acceptance means the full technical chain is real-data executable, deterministic, auditable,
and capable of changing at least one expert decision on the preregistered development slice. It
does **not** mean that the coarse thresholds are trading parameters, the draft thirteen-state model
is accepted, the structure-assisted decision improves returns, or the structure adapter may enter
Shadow/paper/live operation.

The current Direct-DWD provider still declares a retrospective DWD record view and does not prove
per-decision historical-publication visibility. Therefore this acceptance proves engineering
compatibility and T-close causal replay under the provider's declared semantics; it does not upgrade
the provider to a stronger `AS_KNOWN` historical-publication claim.

## 2. Frozen qualification scope

The result-bearing scope was frozen before real-data access in
`config/structure-mvp-run.development.v1.json`:

- warm-up begins `2019-10-08`;
- report window is `2020-01-02` through `2020-03-31`;
- securities are exactly `000001`, `300750`, and `600519`;
- at most five independent P2 execution-compatibility probes are admitted;
- each probe uses 100 shares;
- execution profile remains `close-to-next-open-v1`;
- no model training, parameter search, arbitration partition, permanent-blind partition, Shadow,
  portfolio-performance claim, or result-driven security/date substitution is admitted.

The Event Worker retained the exact DWD Base and correction mounts read-only throughout the
qualification.

## 3. Real-DWD Run A

Run A succeeded and its Worker-side deep verifier accepted the sealed immutable Structure package.
The accepted result contains:

| Evidence | Run A |
| --- | ---: |
| Report-scope rows | 174 |
| `range_intact` | 132 |
| `breakout_attempt` | 8 |
| `breakout_accepted` | 4 |
| `support_threatened` | 13 |
| `support_breach` | 17 |
| Trend-unavailable observations | 0 |
| Baseline trial entries | 41 |
| Structure-assisted trial entries | 42 |
| Decisions changed by structure | 1 |
| Assisted new entries | 1 |
| Assisted removed entries | 0 |
| P2 execution probes | 5 |
| P2 probes producing a Fill | 5 |

The one observed decision change is sufficient to disprove the null plumbing case in which
structure evidence reaches the expert layer but can never affect an action. It is **not** evidence
that the change is economically beneficial.

Run A semantic identities:

```text
ADM manifest digest:
sha256:e175d8c63298a0ef7bd8bafd25ec4b51564f966257b6e6f634b13f7e2e91610e

Result digest:
sha256:d24e6402e87358d4c7a32bbd1612bc1724f02bc16aa74a545d5aaea466310d36
```

## 4. Independent Run B reproduction

Run B executed as a separate Durable Job on the same exact Event Worker revision and frozen
semantic request. It independently reached `succeeded` and reproduced Run A exactly for the
acceptance identities and bounded reconciliation fields:

- ADM manifest digest: identical;
- semantic result digest: identical;
- row count: `174` in both Runs;
- all five state counts: identical;
- baseline/assisted/changed decision counts: identical;
- P2 probe count: `5` in both Runs;
- P2 Fill count: `5` in both Runs.

The production qualification projection therefore reported:

```text
reproduced = true
```

This closes the deterministic real-DWD reproduction requirement for the MVP.

## 5. Outcome-blind bounded semantic review

After both immutable Runs completed, the API projected a bounded review sample from Run A's sealed
`structure-adm.jsonl`. Sampling is deterministic: at most the first two rows of each actually
observed coarse state are selected. The review projection exposes only T/prior-window evidence:
`market_date`, `security_code`, `state`, `close`, `prior_high20`, `prior_low20`,
`volume_ratio20`, `reason_code`, and `knowledge_cutoff`. It exposes no future return, future bar,
future outcome label, P2 result, or later-state path.

The audit session manually reviewed all 10 projected rows. This is a bounded manual semantic review,
not independent human-label validation and not a substitute for a future formal review corpus.

| State / case | T-close evidence | Review |
| --- | --- | --- |
| `breakout_attempt`, `000001`, 2020-01-03 | close `17.18` > prior high `16.95`; volume ratio `1.4726` >= `1.20` | PASS |
| `breakout_attempt`, `000001`, 2020-01-06 | close `17.07` remains above the earlier frozen attempt level `16.95`; reason is confirmation pending | PASS |
| `breakout_accepted`, `000001`, 2020-01-07 | the 01-03 attempt at `16.95` is followed by 01-06 and 01-07 closes above that frozen level; acceptance occurs only after the two post-attempt holding closes | PASS |
| `breakout_accepted`, `300750`, 2020-01-22 | close `133.82` > current prior high `125.08`; volume ratio `1.7611`; no visible T-close contradiction with accepted-breakout semantics | PASS |
| `range_intact`, `000001`, 2020-01-02 | close `16.87` is inside prior range `[15.21, 16.93]`; high volume alone does not create a breakout | PASS |
| `range_intact`, `000001`, 2020-01-08 | close `16.66` is inside prior range `[15.25, 17.34]`; the earlier breakout episode had already resolved | PASS |
| `support_breach`, `000001`, 2020-01-21 | close `16.00` < prior low `16.10` | PASS |
| `support_breach`, `000001`, 2020-01-23 | close `15.54` < prior low `15.71` | PASS |
| `support_threatened`, `000001`, 2020-01-16 | prior low `16.10`; 2% threat ceiling is `16.422`; close `16.33` is above support but inside threat band | PASS |
| `support_threatened`, `000001`, 2020-01-17 | prior low `16.10`; close `16.39` is above support but below `16.422` | PASS |

Review result: **10/10 PASS; no obvious T-close semantic contradiction found.**

One non-blocking auditability follow-up remains: a `breakout_accepted` ADM row does not currently
materialize the frozen attempt level as its own field. The level is reproducible from the preceding
ADM sequence, as the `000001` chain demonstrates, but directly publishing `active_attempt_level`
or an episode/anchor identity would make row-local explanation stronger before any broader state
system is promoted.

## 6. Infrastructure and governance observations

The external production MCP submit/status bridge had not yet added `quanthus.structure-mvp.v1` to
its fixed whitelist. Qualification therefore used the repository-governed, default-off Event Worker
qualification seeder already described in `lld/11-platform-kernel.md`. It submitted exactly two
fixed Structure Jobs through the normal PostgreSQL `DurableJobQueue`; no unrelated Job kind or
arbitrary command was overloaded. Jobs retained the normal claim-generation lease, child process,
read-only DWD mounts, immutable artifact publication, and Worker deep verification.

A temporary read-only health projection was used to observe the two otherwise host-hidden
qualification Jobs and the bounded outcome-blind review sample. Both qualification seed and health
projection are returned to disabled defaults in the acceptance closeout revision; the capability
remains available only behind explicit configuration for a future governed qualification need.

## 7. Closeout verification

After the acceptance/status edits and the restoration of both qualification controls to disabled
configuration, verification passed:

- focused Structure MVP / traceability / Platform contract set: `61 passed`;
- repository-wide `make check`: `1400 passed, 7 skipped`;
- the seven skips are retained environment-bound PostgreSQL or mounted-production-artifact checks,
  not Structure MVP failures;
- `git diff --check`: PASS.

The closeout revision must be deployed to both API and Event Worker so production returns to normal
health output and no startup qualification Jobs are self-submitted.

## 8. Disposition

The evidence justifies the following lifecycle decision:

```text
P3-STRUCT-001: CLOSED
Structure Intelligence technical MVP: ACCEPTED
Real-DWD chain: PASS
Independent reproduction: PASS
Outcome-blind bounded semantic review: PASS
Economic value: NOT TESTED
13-state expansion: NOT AUTHORIZED BY THIS ACCEPTANCE
Model training: NOT AUTHORIZED BY THIS ACCEPTANCE
Shadow / paper / live: NOT AUTHORIZED
```

The next evidence step, if pursued, is a separately preregistered **complete-P2 economic
comparison** using the same information/history budget and frozen baseline versus
structure-assisted mapping. That experiment should decide whether coarse structure facts are worth
retaining/refining. This technical MVP does not require, and does not authorize by implication,
full thirteen-state implementation before that value test.
