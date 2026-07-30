# Issue 45 OCI Portable ADS Parallelism Qualification

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | accepted for conditional two-build resource qualification |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |
| Tested revision | `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` |
| Evidence root | `/home/ubuntu/data/quanthus/acceptance/issue-45-ads-parallel-qualification-a9a4a85` |
| Governing runbook | `docs/operations/dual-lane-data-platform-runbook.md` |

## 1. Scope And Verdict

This record supplements, but does not rewrite, the earlier sequential publication evidence in
`docs/acceptance/issue-45-portable-ads-20260730.md`. OCI was given a persistent 12 GiB emergency
swap reserve and the two largest historical scopes, arbitration and the 2025 permanent-blind
migration, were then built concurrently from the accepted source snapshot.

Both bundles completed construction, wrote `READY`, and passed the root-owned deep semantic check.
The batch completed in 38 minutes 25 seconds instead of the 73 minutes 11 seconds represented by
the two observed task durations added together, a 47.5% reduction in wall time. Resource evidence
therefore qualifies at most two concurrent full ADS builds on this OCI node when all runbook
preconditions hold. Serial execution remains the default and three-way publication is prohibited.

This was not a complete publisher success. Both commands exited 1 at the final operating-system
role probe because the deliberately isolated evidence root is below mode-0750 `/home/ubuntu`, which
neither locked scope principal can traverse. Formal role probes on the production-shaped
`/srv/quanthus/portable-ads` bundles had already passed. A real publication is successful only when
the same final probe passes on its actual target path.

## 2. Swap Configuration

| Property | Observed value |
| --- | ---: |
| Swap file | `/swapfile-quanthus` |
| Allocated file size | 12,884,901,888 bytes |
| Active swap size | 12,884,897,792 bytes |
| Ownership and mode | `root:root`, `0600` |
| Persistent entry | `/swapfile-quanthus none swap sw 0 0` in `/etc/fstab` |
| Swappiness | `10`, persisted in `/etc/sysctl.d/90-quanthus-swap.conf` |
| Root-disk space after the run | 23,051,603,968 bytes available |

The swap file is an emergency reserve, not additional working RAM. It must not be resized or
recreated for ordinary runs. The pre-change `/etc/fstab` is retained at
`/etc/fstab.pre-quanthus-swap-20260730`.

## 3. Parallel Task Evidence

| Property | Arbitration | 2025 migration |
| --- | ---: | ---: |
| Elapsed | 34:46.06 | 38:24.50 |
| CPU utilization | 103% | 103% |
| Maximum RSS | 10,766,424 KiB | 11,899,676 KiB |
| User CPU | 2,138.31 s | 2,363.57 s |
| System CPU | 28.97 s | 32.01 s |
| Manifest SHA-256 | `7bea8e26eaccd0906d897502b524372d948f127fcba2432a5e787ed0f18726c6` | `1adae0b9c4895eebf661c5ea4a2f24c112bbc36c96a5cd7fa41dab0f51b14945` |
| READY SHA-256 | `f0e7a5e471621caad446034874476eebf1d15c24bfaa61219078ac82b326bc8b` | `56f6d51ae0aea0f62af4aebdb46d069ae90886c2f071b2e84c5ddbbf76235583` |
| Build / READY / deep check | pass / pass / pass | pass / pass / pass |
| Final process status | 1 at isolated-path role probe | 1 at isolated-path role probe |

Each process used approximately one of OCI's four logical CPUs. This supports the diagnosis that
the previous long elapsed time was primarily serial CPU work, not memory exhaustion. The resource
qualification is based on the peak-RSS and system telemetry below rather than that diagnosis alone.

## 4. System Telemetry

The 461 `vmstat` data rows sampled over the batch recorded:

| Metric | Observed value |
| --- | ---: |
| Peak swap used | 75,964 KiB |
| Minimum free memory | 188,860 KiB |
| Minimum free + buffers + cache | 3,407,460 KiB |
| Peak swap-in / non-zero samples | 174 KiB/s / 36 |
| Peak swap-out / non-zero samples | 5,159 KiB/s / 7 |
| Peak I/O wait | 37% |
| Minimum idle / peak steal | 0% / 2% |

There was brief swap-out and limited later swap-in, so this evidence does not claim zero paging.
Peak swap occupancy was only about 74 MiB, and the samples did not show sustained bidirectional
paging or prolonged I/O wait. The node completed both deep checks without an OOM kill or resource
failure.

The machine-readable summary is
`/home/ubuntu/data/quanthus/acceptance/issue-45-ads-parallel-qualification-a9a4a85/resource-summary.json`.
Raw `time -v`, `vmstat`, free-memory, swap, disk, publisher logs, manifests, `READY` files, and both
completed bundles remain below the same evidence root. No artifact was deleted.

## 5. Accepted Operating Boundary

The active runbook owns the operating procedure. This evidence supports only these bounds:

- default to serial full-bundle publication;
- permit `max_parallel=2` only on the qualified four-CPU OCI shape with active 12 GiB swap,
  `vm.swappiness <= 10`, and at least 20 GiB free on the root filesystem before launch;
- never run all three historical scopes concurrently;
- monitor swap-in, swap-out, I/O wait, available memory, and free disk throughout the overlap;
- stop launching new work and return to serial operation on sustained bidirectional paging,
  sustained I/O wait, material disk-headroom erosion, or memory-pressure termination; and
- require the final locked-principal role probe to pass on the actual `/srv` target path.

The test does not authorize target timers, production cutover, consumer pointer changes, cleanup,
or a broader claim that arbitrary ADS/ML workloads can safely run in parallel.
