# P10 finite supplementary-source watch

This separate image serves P10 source qualification under #583–#587. Its only
writable bind is a new P10 supplementary-observation root. It does not mount
warehouse inputs, collector configuration, Docker, credentials or consumer data.

Build from an exact **published GitHub revision**, using the existing
`container_code.bundle` exporter for tracked `src`, `config` and `pyproject.toml`.
The build context contains `code/`, `wheels/` and `requirements.lock`. Include the
policy's exact astock release wheel plus pinned requests/certifi/charset-normalizer/
idna/urllib3 wheels; lock every wheel with its verified SHA256. Use this Dockerfile
from that same Git revision. Build with `--network none --pull=false` and
`--build-arg QUANTHUS_REVISION=<full revision>`; bundle contents and installed
astock modules are checked during the build. Never copy an untracked source tree.

Run as UID/GID 998 with `--read-only --cap-drop ALL --security-opt
no-new-privileges --pids-limit 64 --memory 256m --cpus 0.5`, a small `/tmp` tmpfs,
and just the empty, UID-998-owned root bound at `/work`. Use a size-limited Docker
JSON log (`max-size=5m`, `max-file=2`) and `--restart on-failure:3`:

```text
watch --root /work \
  --policy /opt/quanthus/config/pipelines/p10-supplement-sources.v1.json \
  --revision <full revision>
```

Each daily Job makes one bounded request per capability, with no retry, source
substitution or TLS downgrade. Each source has an outer process
deadline that includes DNS. A raw response is fetched, parsed and normalized once;
quality and the candidate ledger share it. Published raw/normalized evidence is an
immutable P10 qualification audit product; unpublished staging is TMP. The
manifest records complete input/code/policy identities and stage metrics.

`watch.json` persists the original seven-day end time and next attempt. Restarting
cannot extend the watch or repeat an already published daily job. A changed
contract requires a separately named root. At expiry the process writes
`STOPPED_REVIEW_DUE` and exits successfully, so the failure-restart policy does not
renew it. `candidates.json` records per-source UTC verification times, source
dates/clocks, quality, failures, distinct-day evidence and review disposition.
The minimum three distinct successful days and recent three consecutive successful
days permit review; they never grant business or historical PIT admission.

Inspect with `status` using the same root/policy/revision. Verify immutable
`runs/*/manifest.json` and use `read_observation` with the policy SHA and actual
knowledge cutoff; a later snapshot cannot fill an earlier failure. The policy SHA
is the canonical JSON digest from `p10_source_contracts.digest(policy(path))`,
also recorded in every run manifest; the source-file SHA is a separate identity.

Rollback: stop this watch container. Every source then becomes unavailable for
new observation work and independently admitted daily products continue on their
own contracts. For selective rollback, use a newly named watch root with repeatable
`--disabled provider:capability` flags. The disabled source records
`SOURCE_DISABLED_ROLLBACK`; other sources still run. Keep the original root/image
as audit evidence. No old success is silently republished, and missing optional
data does not imply a sale. There are no production credentials in this image.

The current source inventory and online dispositions live in
[`p10-source-supply-20261029.md`](../../docs/work-items/p10-source-supply-20261029.md).
## Additional three-capability qualification

The original fifteen-source watch remains pinned to its original image, library,
policy, root and deadline. For the additional announcement, single-ETF trading-flow
and anonymous guest-bond contracts use a separate runtime with astock0.1.2,
`requirements-v2.lock` and `config/pipelines/p10-supplement-sources.v2.json`.
Use the versioned Dockerfile with build argument
`P10_POLICY=config/pipelines/p10-supplement-sources.v2.json`; stage the v2 lock as
the build context's `requirements.lock`. The normal bundle and published-revision
verification still apply. Do not install both optional source extras together.

Give this watch its own empty absolute bind and invoke `watch --root /work --policy
config/pipelines/p10-supplement-sources.v2.json --revision FULL_PUBLISHED_REVISION`.
Keep the same non-root/read-only/resource/log bounds described below. Its explicit
three-key subset must not request the existing fifteen providers again. First cold
container qualification is a separately frozen integration check, not an identical
failed-request retry and not a second successful day. Persist start/end/next times;
restart must not renew the period or re-fetch the current Job.

Announcement coverage is archival only. ETF flows are vendor-classified secondary
trading, not fund subscriptions/redemptions. Jisilu is an anonymous subset capped
at30 rows; C-type convertible prices have per100-face units, and E-type exchangeables
remain metadata-only. The source's context date and optional time text are never
combined into a quote timestamp. Keep these restrictions in every review.

On storage-constrained CN02, validate with pytest's successful-fixture cleanup:

```sh
PYTEST_ADDOPTS='-o tmp_path_retention_policy=failed -o tmp_path_retention_count=1' \
  make check PYTHON=/path/to/isolated-check-venv/bin/python
```

With pytest 8.4.2 this reclaims successful synthetic fixture TMP after teardown and
keeps failed fixtures. Test selection and the existing 8 GiB free-space gate stay
unchanged. Retain the complete check log and command receipt outside TMP; use a
separate qualification environment for the 0.1.2 wheel. This does not change the
P10 data cleaner's mounts, schedule or evidence retention.

### Named PVC futures diagnostic tranche

Policy `config/pipelines/p10-supplement-sources.v3.json` selects only Sina V2701
with astock `0.1.3`. Build with `P10_POLICY` set to that file and stage
`requirements-v3.lock` as `requirements.lock`; use a separate environment, image
and empty evidence root. Do not upgrade either existing watch in place.

The request is capped at 4096 bytes/20 seconds and a single dated contract.
Price is CNY/tonne; contract size is separate. Native date/time labels are never
joined or converted to a trading day. Volume, open interest and tick-size units
are excluded. The conservative scope stops before 2027-01-01 in Shanghai; expiry
blocks new requests and same-day cache reuse. The snapshot remains audit evidence.
A seven-day watch is finite and does not grant real-time, historical PIT or
business admission, or delay continued qualification of remaining sources.

## A-share v4 qualification window

`config/pipelines/p10-supplement-sources.v4.json` selects two astock `0.1.4`
contracts: one SH600519 margin window and one A-share block-trade date, each
page 1/size 2. Use `P10_POLICY` for that file and stage `requirements-v4.lock`
as `requirements.lock`. Use a new image, empty writable root and container;
the existing v1/v2/v3 watches keep their versions and persisted deadlines.

The policy is explicitly a fixed-session vendor-reference sample. Distinct
success days mean access-observation days, not newly covered native sessions.
A seven-day watch never upgrades freshness, full-market coverage, exact amount
reconciliation, historical PIT or business admission. The block-trade amount
discrepancy remains visible in every applicable normalized observation.

Priority is A shares first. HK/US source requests require new access or contract
evidence and remain low priority; do not add unchanged failed requests to v4.

The installed local v4 watch is `quanthus-p10-a-share-watch`, source
`2a630b0ed3df81ee80089d7767c0100b037a1d1c`, root
`/srv/quanthus-source-watch/a-share-2a630b0ed3df`. Its finite window ends
2026-11-06 06:15:57 Asia/Shanghai; restart preserves that deadline and does not
repeat the current Job. The actual first cycle used two requests and returned
four rows. Offline rollback after success rejected current reads while retaining
prior evidence. Exact image/manifest identities, timing and the distinct-day
limit are recorded in the supply work item above. This reference-qualification
watch is separate from production collection and from the three earlier watches.
