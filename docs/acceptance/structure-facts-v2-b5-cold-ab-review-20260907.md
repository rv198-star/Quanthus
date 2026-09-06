# Facts v2 B5: cold A/B, consumer/P2 and outcome-blind review acceptance

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-004-B5` / #222 |
| Acceptance | ACCEPTED / CLOSED |
| Result-bearing revision | `723f22feeb7dfbecb2bffbc83662ced7bb29822d` |
| Run A | `job_5165e1bd99614b6186b58f228c770971` |
| Run B | `job_064e89c9ebbe4ed49badd8e07b78ea88` |
| Execution authority | false |
| Economic claim | none |

## 1. Same-revision independent cold reproduction

Both Jobs independently built the bounded Direct-DWD TMP prerequisite with zero cross-Job cache hits and produced the same semantic outputs.

Stable identities reproduced exactly:

```text
request digest:
sha256:c9a8082f351499ca7b2a2f0469d1c405c6e8b3ad5b428e7f9e48e0935316b92d

input digest:
sha256:2272dc751b1792dfa13584edbedfec4a9fa4373036ed5241d9b9c8cfe9235999

input-readiness digest:
sha256:ff61690b80bae61070440a83ee3683f458135a7555bbfe942b7453bc5c93f4e6

structure digest:
sha256:cd1b79db7b3ab4d6c38a970ad1b7747a03cbac5be93d02dd83948243891208fc

ADM manifest digest:
sha256:45d5e60d8c2e31299e16d92f13f83c4ffac191c520f2c8c0c2f19ca71db77d84

result / summary digest:
sha256:5c641efd2b155e6fd440d9630319f389b637a3bb4f5c5222ae74b6b4d8e6d60d

source-inputs.jsonl SHA-256:
sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac
```

The physical audit/runtime records correctly differ because they contain actual generation/use clocks and elapsed times:

```text
Run A audit.json:   sha256:6a1be91a38fb43c22973ea22edab3124e7f8bd20a950ca62d4e8e83ff48a88b6
Run B audit.json:   sha256:b178b5ae2e80e68fbd26f24d59702dc3a9ca6e4c623a30cc0a6193d5bd7686aa

Run A runtime.json: sha256:d7845f7525a7b8f605e62e48b2490e8cdad445a6087a807694b88177cded8b0b
Run B runtime.json: sha256:bbcb89b29072c2d5f45d3b17d45bb19470fcce9d83168f8bbe31732b0665d80b

Run A physical manifest: sha256:93e3c94967f460285655c68468365eeb10de46b50ef41fec1b817ef9c299f1a4
Run B physical manifest: sha256:09922b6438a25f79d7f247ed286f6bdd389ba2218e4ed47ccf23053424332167
```

This difference is expected and is consistent with independently recorded operation clocks; physical hash differences alone do not authenticate timestamps. Each receipt chain is validated separately, and no wall-clock equality is required for semantic A/B reproduction.

## 2. Process and consumer evidence

Each run reproduced:

```text
episode observations: 168
  started:              3
  held:                93
  lost:                 3
  below:               66
  reclaimed:            3

consumer decisions:   168
audit events:         168
P2 probes:               5
P2 fills:                5
```

The direct compatibility consumer invokes the existing FeatureFact / ExpertContext / Aggregator path once per qualified target. It consumes continuous signed distance; it does not compute from a coarse renderer. Each invocation is bound to one exact observation revision and a separate physical generation/publication/consumption receipt chain.

The five P2 probes reuse the accepted next-open execution contract. Their role is engineering compatibility only; no portfolio/economic acceptance follows.

## 3. Runtime accounting

```text
Run A Direct-DWD build:          51.163567 s
Run B Direct-DWD build:          50.666141 s
Run A source extraction:          9.513207 s
Run B source extraction:          9.391378 s
B2 admission:                    ~0.021 s / run
B3-B5 causal build:              ~2.16-2.18 s / run
consumer/aggregator:             ~0.49 s / run
P2 compatibility:                ~0.79 s / run
provider builds:                  1 / run
cross-Job cache hits:             0
```

Each Job built the expensive prerequisite once and shared it downstream. The bounded derivative repair therefore solved the earlier unnecessary decade-scale physical work without introducing cross-Job reuse.

## 4. Preregistered outcome-blind review

The selection rule was frozen before result access in `docs/design/structure-facts-v2-delivery.md`: fixed security order, earliest start/loss/reclaim by 20/60 window, then deterministic round-robin fill from remaining non-censored episode observations, maximum 12.

Both A and B produced the same 12-row sample. No future return, economic outcome, P2 fill outcome or hidden state participates in selection.

Review result:

```text
12 / 12 PASS
```

Checks performed:

- same `anchor_key` / `anchor_revision` is retained across each start/loss/reclaim lifecycle;
- episode id remains stable across later observations;
- lost observations are below the frozen anchor; reclaimed observations are strictly above it;
- `previous_loss_ref` is absent before loss and retained after loss/reclaim;
- exchange-session age and hold count progress forward without rewriting prior rows;
- warm-up episodes carried into the report are not relabeled as report-period starts;
- missing category counts remain zero rather than manufacturing examples;
- report-scope open processes are right-censored in metadata instead of terminally mutating a last-day business row;
- all 12 `signed_distance` values equal `close / frozen_level - 1` under the frozen Decimal precision 28.

Representative lifecycles:

```text
000001 / scale 20 / anchor 16.95
2020-01-03 start      17.18  +0.013569321533923303834808260
2020-01-08 loss       16.66  -0.0171091445427728613569321534
2020-01-13 reclaim    16.99  +0.002359882005899705014749263

300750 / scale 60 / anchor 114.5
2020-01-09 start      115.00 +0.004366812227074235807860262
2020-01-10 loss       113.31 -0.0103930131004366812227074236
2020-01-13 reclaim    116.90 +0.020960698689956331877729258
```

## 5. Gate disposition

| Gate | Result |
| --- | --- |
| Append-only process observations | PASS |
| No terminal-state backfill | PASS |
| Fact identity independent of generation/use clocks | PASS |
| Direct expert/aggregator compatibility | PASS, 168 decisions |
| Exact physical consumption audit binding | PASS, 168 events |
| Existing P2 compatibility | PASS, 5/5 fills |
| Same-revision independent semantic A/B | PASS |
| Preregistered outcome-blind review | PASS, 12/12 |
| Build-once / no cross-Job cache accounting | PASS |
| Economic value | NOT TESTED / NOT CLAIMED |

B5 acceptance closes only the bounded retrospective engineering vertical. It does not authorize a production Structure Expert, B6/B7 expansion, thirteen-state taxonomy, model training, Shadow/paper/live execution or a profitability claim.
