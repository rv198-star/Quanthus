# Facts v2 B3/B4: real-DWD reference and same-anchor interaction acceptance

| Field | Value |
| --- | --- |
| Work items | `P3-STRUCT-004-B3` / #220; `P3-STRUCT-004-B4` / #221 |
| Acceptance | ACCEPTED / CLOSED |
| Result-bearing revision | `723f22feeb7dfbecb2bffbc83662ced7bb29822d` |
| Runs | `job_5165e1bd99614b6186b58f228c770971`, `job_064e89c9ebbe4ed49badd8e07b78ea88` |
| Structure digest | `sha256:cd1b79db7b3ab4d6c38a970ad1b7747a03cbac5be93d02dd83948243891208fc` |

## 1. Real object evidence

Both independent Jobs reproduced the exact same structure payload:

```text
structure.json SHA-256 / structure digest:
sha256:cd1b79db7b3ab4d6c38a970ad1b7747a03cbac5be93d02dd83948243891208fc

measurements: 861
interactions: 9
scale_unavailable: 0
```

The frozen calculation remains prior-window 20/60 on the exact raw-CNY/action-epoch coordinate, with T excluded and uninterrupted admitted history required. Synthetic gates retain the stationary-price/moving-reference, same-valued-distinct-anchor, correction-as-revision, T-exclusion and gap/censoring counterexamples.

## 2. Qualified target identity

Real review rows show that interaction/process observations bind an exact `anchor_key` and `anchor_revision`; the target remains stable while later closes and process observations change.

Example 1, `000001`, scale 20:

```text
anchor_key:
sha256:1db7b9f3e030a87065a92cb272e69f3f80324cc0f4a5d8368a70df5854766796
anchor_revision:
sha256:562d76af239b928ce46d7be9e1ada88bb80d6b70af8a4eaf26c5ac64048f678e
frozen level: 16.95
```

That exact target is referenced by the start, later loss, later reclaim and intervening hold observations. The target is not regenerated from each day's moving rolling-high value.

Example 2, `300750`, scale 60:

```text
anchor_key:
sha256:ccfe977315e1b9bf5f830e6207b45d049bf817990d502f65f5e5b7a25383c550
anchor_revision:
sha256:bb4aea1c6efe306ebf27e24d4b0dd13210c442ffe4bb4fe424d438f962ea732e
frozen level: 114.5
```

Again the same target survives start -> loss -> reclaim without moving-anchor substitution.

## 3. Same-anchor interaction evidence

The accepted equality/crossing contract is unchanged:

- cross above: previous close <= frozen anchor and current close > anchor;
- cross below: previous close >= frozen anchor and current close < anchor;
- reclaim: strict cross above the SAME frozen anchor after recorded below-anchor evidence;
- no daily-bar claim about intrabar first-touch ordering.

The real result contains exactly nine qualifying interactions, consistent with three starts, three losses and three reclaims in the bounded slice.

### `000001` scale 20 lifecycle

```text
2020-01-03 started:   close 17.18 vs 16.95, distance +0.013569321533923303834808260
2020-01-08 lost:      close 16.66 vs 16.95, distance -0.0171091445427728613569321534
2020-01-13 reclaimed: close 16.99 vs 16.95, distance +0.002359882005899705014749263
```

### `300750` scale 60 lifecycle

```text
2020-01-09 started:   close 115.00 vs 114.50, distance +0.004366812227074235807860262
2020-01-10 lost:      close 113.31 vs 114.50, distance -0.0103930131004366812227074236
2020-01-13 reclaimed: close 116.90 vs 114.50, distance +0.020960698689956331877729258
```

The signed-distance arithmetic was independently recomputed at the frozen Decimal precision 28 and matched all 12 selected review rows exactly.

## 4. Warm-up and report-boundary behavior

The report may contain process observations for an episode that began in warm-up history. This is not relabeled as a report-period start. For example, `600519` carries an episode started on 2019-11-13 into 2020-01-02/03 as below-anchor observations; the report category counts correctly show zero new start/loss/reclaim for that security/window inside the report slice.

At scope end two processes remain open and are recorded only in report metadata as right-censored/open:

```text
sha256:98199a44e2a6a5218ccedb406ac071a56651bde97c222fe9a4819a398fc64146
sha256:ea915fcfa9a93d038ad543077c4e450d495d0a41a7f50ac6cb1f705c8e32bec3
```

No synthetic final-day business mutation is injected.

## 5. Gate disposition

| Gate | Result |
| --- | --- |
| 20/60 prior-window references | PASS |
| T exclusion and full history contract | PASS by tests; real output available at both scales |
| Qualified target identity | PASS |
| Same-valued/multiple target collision defense | PASS by adversarial tests |
| Correction remains revision, not new target slot | PASS by contract tests |
| Same-anchor cross/loss/reclaim | PASS, 9 interactions |
| Moving-reference false reclaim | REJECTED by contract; real lifecycle uses frozen targets |
| Unsupported intrabar ordering | NOT CLAIMED |
| Independent real A/B structure reproduction | PASS |

B3/B4 acceptance proves bounded object and relation semantics only. It does not establish that these references are economically strong support/resistance or predictive signals.
