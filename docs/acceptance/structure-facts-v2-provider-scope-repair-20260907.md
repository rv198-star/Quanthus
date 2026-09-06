# Facts v2: pre-result provider materialization-scope repair

| Field | Value |
| --- | --- |
| Work item | P3-STRUCT-004 / #216; B2 #219 |
| Diagnostic revision | `7b12da115724987e3e9fe22569f3f5d33fbbb22c` |
| Diagnostic Job | `job_e4dc29deede24ad1a0080761caba0b30` |
| Terminal state | failed, no result_ref; zero successful v2 results to date |
| Discovered stage | provider-open, before v2 admission/build/consumer/P2 |
| Change class | Bounded physical materialization; no result-driven rule or population change |

## Root evidence

The diagnostic Job exported:

```text
structure_v2_failure:provider-open:OutOfMemoryException:Out of Memory Error:
failed to offload data block of size 256.0 KiB (2.2 GiB/2.2 GiB used).
```

The local filesystem inspection showed 146 GiB total, 143 GiB used, approximately 2.5 GiB free
and 99 percent usage. DuckDB documents `max_temp_directory_size` as normally bounded by available
disk space and explains that spilling to disk does not allow every workload to complete under any
resource limit. References: https://duckdb.org/docs/stable/configuration/overview and
https://duckdb.org/docs/stable/guides/performance/how_to_tune_workloads .

The precise implementation mismatch is visible in `open_direct_dwd_session`: caller scope is
authorized, but `_materialize` uses `config.history_start` and `config.scope.execution_end`.
The reused general provider was configured for 2011-01-01 through 2021-12-31, far beyond the already
frozen v2 scan/report/execution interval. The source reader's later date restriction did not narrow
that prerequisite materialization. Thus a small research run was building an unnecessary decade of
full-market temporary projections while the disk was nearly full.

## Smallest bounded repair

Add `config/replay-data-provider.structure-v2.development.v1.json` and bind only the v2 request to it:

- materialization history begins 2019-09-30, retaining predecessor-session context;
- decision scope is the original scan start 2019-10-08 through report end 2020-03-31;
- execution scope includes 2020-04-01 for the last report day's existing next-open contract;
- all market records inside that materialization window are retained; there is no new security
  filter in the shared provider;
- DWD Base, correction, exact manifest/content identities, reference data, retrospective record
  view, read-only mounts, provider implementation and legacy provider configuration are unchanged;
- the frozen three report securities, scan/warm-up, report dates, 20/60 windows, geometry, crossing,
  censoring, scoring, P2 quantities/costs, sample selection and two-result budget are unchanged.

No disk content, another project's files, production volume or historical Run is deleted. No RAM,
spill limit, permissions or automatic qualification seeder is increased. Nearly-full disk remains
an operational limitation; this change is not a claim that host capacity has been remediated.

## Identity and verification

The request/provider identities change explicitly because a new materialization config is used:

```text
request_digest:
sha256:c9a8082f351499ca7b2a2f0469d1c405c6e8b3ad5b428e7f9e48e0935316b92d
provider_config_digest:
sha256:ec88015c24c78aeb09522e140fc8f649183b3393d88675b4cbc18feeadac0896
calculation_spec_digest (UNCHANGED):
sha256:e405f68fd1efed10c3e02cc643a35c2b479cace074c9df7bfca75ef0e0c46cc1
```

Two focused configuration tests compare source authorities/references and unchanged materializer
properties against the retained general provider, prove exact required bounds, and assert unchanged
question/algorithm identity. The affected source, run, artifact and Worker tests passed 17/17;
Ruff passed. This proves the configuration contract, not completed real-data equivalence or runtime
acceptance. The first real retry must validate its actual prerequisite extent and complete evidence.

A/B at e5b9b03 and the diagnostic Job remain visible failed attempts; no published values have been
selected or inspected for tuning. Start only corrected A first. If it succeeds, perform exactly one
same-revision independent B and inspect both. If resource or semantic gates remain unsatisfied, stop
with that precise failure rather than launching repeated speculative experiments.
