# P4 Event Entry Snapshot Binding Remediation Checkpoint

| Field | Value |
| --- | --- |
| Status | implementation checkpoint; #74 production A/B acceptance still open |
| Work item | `P4-EVENT-002` |
| Production finding | Run A v9 failed after the full forward scan at T+1 entry partition |
| Failed Job | `job_26d5e6f679aa4852948af716c0d245a1` |
| Failed revision | `c454037a2604ecbf212c5c60a99820e69a229eb7` |
| Failure | `CandidateScanError: entry evidence snapshot differs from candidate snapshot` |

## Production finding

Run A v9 completed the expensive ADM materialization and forward candidate scan without hitting the
previous historical-industry coverage, BaoStock-version, or source-digest gates. It then failed in
`partition_executable_entries()` while binding the accepted P1/P2 T+1 execution evidence.

The failure exposed two valid but different identity scopes:

- the candidate event binds the immutable P4 Research ADM snapshot for the complete application
  input package; and
- `P1DailyExecutionEvidence` preserves the historical reader's source snapshot for the specific
  T+1 execution date.

The old partition contract required those two `snapshot_id` strings to be identical. That was only
true in synthetic tests because the fake reader was manually assigned the ADM snapshot id. The
real `HistoricalReplayFrameReader` deliberately creates its own execution-date source snapshot and
must not have that provenance rewritten.

## Remediation

`CandidateEntryEvidence` now carries an explicit `candidate_snapshot_id` in addition to the
unchanged `P1DailyExecutionEvidence`. The entry partition requires this explicit application
binding to equal the candidate event's ADM snapshot identity, while the original P1 source
snapshot remains intact and is recorded as `entry_source_snapshot_id` in the partition output.

The P1 evidence digest continues to include the original source snapshot, source revision, market
date, knowledge cutoff, execution state, and prices. No source evidence is relabelled as ADM data.

The resulting identity model is:

```text
P4 candidate event
  -> candidate_snapshot_id = exact ADM snapshot

CandidateEntryEvidence
  -> candidate_snapshot_id = exact ADM snapshot binding
  -> evidence.snapshot.snapshot_id = original P1/P2 source snapshot
  -> evidence_digest = digest of the original P1 evidence payload
```

A mismatched `candidate_snapshot_id` still fails closed. A distinct source snapshot is expected and
preserved rather than rejected.

## Non-changes

This remediation does not change:

- the frozen P4 candidate rule, thresholds, cooldown, concentration policy, or split policy;
- the 259-security historical-industry supplement or its checksum-bound source content;
- DWD Base V2.1, correction-001, or any protected data;
- P1/P2 execution-state semantics, rejection mapping, price selection, or T+1 timing; or
- Event Worker queue/concurrency behavior.

Run B remains prohibited until a replacement Run A succeeds and its immutable manifest is readable.
