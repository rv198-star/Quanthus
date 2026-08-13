# Five-candidate shared replay acceptance (2026-08-13)

> **Superseded for the QuantDinger row only.** The shared-provider batch remains evidence that all
> five candidates can complete the common workflow. Its QuantDinger +69.630054%, 30-fill row came
> from the older shared replay bundle, not directly from DWD v2.1. The governing external
> conformance and current Product Web metric are in
> `dwd-v2-1-quantdinger-conformance-20260813.md`: 38 fills, 19 closed trades, and +60.279056%
> under complete Quanthus P2.

## Outcome

The five registered candidate versions completed the full development replay through one
configured `ReplayDataProvider` session. The batch succeeded with `5/5` candidates and no
governance or execution transition.

Batch identity:

- batch id: `candidate-batch-e8a022725bee32e6`
- batch digest: `sha256:2c18101c67d3dcd9267cdf6d1fe0aedeea3dc756f716855c850ff405792f3523`
- scope: decision dates `2012-01-04` through `2021-12-30`; final T+1 execution `2021-12-31`
- completed at: `2026-08-13T03:42:26.097551+00:00`
- observed wall time: approximately 99 minutes on the OCI ARM host

## Data identity

The provider opened the accepted shared development projection once:

- bundle: `strategy-replay-development-1f1ea4d-v1`
- content: `sha256:ed799c7825c10ae1a1e6b7a722c0df82e35703a77d822a77b3d1cc2a5970c284`
- manifest: `sha256:9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628`
- source snapshot: `p1-historical-replay-20260729-v1`

The provider also verified the currently installed DWD authority baseline:

- package: `dwd-base-202607-v2.1`, through `2026-07-31`
- content: `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9`
- relationship: authority baseline, not represented as the direct parent of the older replay
  snapshot

The run built one temporary 572,813,487-byte feature panel in container tmpfs and reused it for
the baseline and governed runners. It did not create or retain a per-strategy ADS.

## Candidate results

| Candidate | Runner | Operations | Closed trades | Total return | Max drawdown |
| --- | --- | ---: | ---: | ---: | ---: |
| `baseline.ma-trend@2.0.0` | baseline suite | 481 | 240 | 25.865810% | 14.321173% |
| `baseline.breakout20@2.0.0` | baseline suite | 369 | 184 | 35.409620% | 15.314306% |
| `baseline.oversold-rebound@2.0.0` | baseline suite | 515 | 257 | -34.419370% | 40.825524% |
| `candidate.market-breadth-trend-breakout@1.0.0` | governed portfolio | 461 | 230 | 31.909470% | 4.746455% |
| `quantdinger.strategy-v2-bullish-three-lines@11.0.0-quanthus.1` | external adapter | 30 | 15 | 69.630054% | 10.377494% |

Every batch item reported `status=succeeded`, `execution_authorized=false`, and retained the
candidate workflow boundary. The batch reported `governance_transition_applied=false`.

## Reproduction

Build the bounded replay image and persist output on a host mount rather than container tmpfs:

```bash
./ops/bin/build-replay-image
mkdir -p artifacts/candidate-replay
docker run --rm --read-only --tmpfs /tmp:rw,size=12g \
  -v /srv/quanthus:/srv/quanthus:ro \
  -v "$PWD/artifacts/candidate-replay:/output" \
  quanthus-replay:local run-candidate-batch \
  --output /output/five-candidate-development.json
```

The command now emits stage events for data verification, shared feature construction, and each
runner family. Its runtime profile makes it a background `JobRunner` workload, not a synchronous
HTTP request.

The repository wrapper persists the same manifest contract by default:

```bash
make replay-image
make candidate-batch
```

## Post-run verification and deployment

- focused Python regression: `33 passed`
- replay-provider/product contract subset: `9 passed`
- Platform suite: `37 passed`, `1 skipped` only because the host test DSN was not configured
- product Web contracts: `5 passed`
- mypy: three changed modules passed
- ruff: changed Python files passed
- production product build: passed (existing large-chunk warning retained)
- public Playwright: `4 passed` across desktop Chromium and Pixel 7
- public root: HTTP 200 with valid TLS; external `/metrics`: HTTP 404
- API image: `sha256:ca7e94f2a432f7d9734e5880bc54f8323af248dfba3fa6df0f0628f9289eddb3`
- Web image: `sha256:e368535cb9ac74309731b1316a39ee70ee80cae36829303145b9b19d49d439bf`
- public URL: `https://quanthus.oci.061718.xyz/`
