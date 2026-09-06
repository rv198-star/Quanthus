# Continuous Structure Facts v1 implementation checkpoint (2026-09-06)

| Field | Value |
| --- | --- |
| Status | implementation checkpoint; not acceptance |
| Work item | `P3-STRUCT-003` |
| Design | `docs/design/continuous-structure-facts-v1.md` |
| Owner decision | construction approved; price-only episode creation; volume independent; coarse renderer explanation-only |
| Predecessor | `P3-STRUCT-002` final same-revision reproduction still running at checkpoint time |
| Real-DWD qualification | NOT RUN |
| Independent reproduction | NOT RUN |
| Accepted | NO |

## Implemented boundary

The isolated implementation now contains:

- `src/quanthus/structure_facts.py` — immutable Continuous Structure Facts v1 row/package contracts,
  prior-20 rolling anchors, signed margins, deterministic breakout episode identity, frozen episode
  anchors, post-attempt hold progress, explicit termination, gap/reset behavior, feature projection,
  and an explanation-only coarse renderer;
- `config/structure-continuous-facts.research.v1.json` — frozen development-only fact/episode policy;
- `src/quanthus/structure_facts_run.py` and
  `config/structure-facts-run.development.v1.json` — bounded development Real-DWD runner, direct
  compatibility expert consumption, existing Aggregator path, and P2 execution probes;
- Event Worker support for exactly that third fixed Structure run config under the already-admitted
  `quanthus.structure-mvp.v1` Job family; and
- stdlib-only Worker deep verification that rejects coarse/state authority inside fact rows even if
  the attacker recomputes otherwise-consistent file and semantic hashes.

No generic DWS, ADS, state engine, arbitrary command path, model, new Worker family, or execution
authority is introduced.

## Frozen semantics

1. Rolling resistance is the maximum high of the preceding 20 valid sessions, excluding T.
2. Rolling support is the minimum low of the preceding 20 valid sessions, excluding T.
3. An episode starts when T close is strictly above the prior rolling resistance. Volume never gates
   episode existence and is published independently.
4. The episode freezes breakout level, attempt support level, attempt snapshot/time, attempt volume,
   and anchor-window identity.
5. Later closes at or above the breakout level increment hold count and episode age.
6. The first later close below the frozen breakout level ends that episode. It cannot move the
   breakout anchor and cannot create another episode on the same row.
7. Missing/invalid continuity resets history and active episode memory; it never implies bearish
   structure, reduction, or sale.
8. Fact rows contain no authoritative coarse state. The coarse renderer is a derived read-only
   explanation projection and does not contribute to fact-row or ADM content identity.

## Current evidence

Focused synthetic/adversarial tests cover:

- prior-20 exclusion of T;
- price-only breakout creation with low volume;
- frozen anchor and row-local explanation;
- hold progress;
- failed hold / episode termination / later new episode identity;
- gap reset;
- deterministic package identity;
- direct `FeatureFact -> Expert -> Aggregator` consumption;
- P2 compatibility fills through a fake accepted replay provider;
- immutable artifact verification and tamper failure; and
- Worker rejection of hash-consistent rows carrying forbidden coarse authority.

At checkpoint time the focused Structure Facts + runner + Worker suite is green. This is
implementation-readiness evidence only. It does not prove real DWD behavior, independent rebuild,
operator acceptance, economic value, or production suitability.

## Required remaining gates

`P3-STRUCT-003` remains open until, in order:

1. `P3-STRUCT-002` predecessor closes;
2. repository-wide `make check` passes on the release candidate;
3. the exact release is published through GitHub and the Event Worker is deployed at that revision;
4. fixed Real-DWD Run A succeeds under read-only Base/correction mounts;
5. fixed Real-DWD Run B independently rebuilds and reproduces fact content, episode identities,
   result/manifest digests, consumer counts, and P2 probe results;
6. a bounded outcome-blind review checks representative anchor/episode rows for T-close semantic
   consistency; and
7. design/work-item/seed state is closed out with all qualification seeders returned to `0`.

## Claim ceiling

Until those gates pass, the strongest claim is:

> Continuous Structure Facts v1 is implementation-ready in isolation and passes synthetic/contract
> verification. It is not yet a real-DWD accepted fact product.

This checkpoint does not authorize thirteen-state expansion, a production Structure Expert, model
training, Shadow, paper, live execution, or sealed-partition access.
