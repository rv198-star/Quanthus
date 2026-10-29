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
