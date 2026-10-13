# P10 complete Daily incremental GDrive transport

Owner scope: daily acquisition prevention and qualified complete Daily delivery.
Base 2.3 is immutable and is never packed into this archive. Consumer purpose,
minute R2 floor / R3 exclusion, actual knowledge times and source seals stay unchanged.

## Transfer and recovery

- v3 first archives the repaired 2026-10-12 complete Daily, including newly attached
  captured weekly finance sources. Wait for its actual checksum comparison and
  CLOUD_READY readback; local packing or an upload process is not confirmation.
- v4 prefers its last cloud-confirmed archive, otherwise that exact v3 bootstrap.
  An unchanged inventory item (path, bytes and SHA256 all equal) is inherited;
  changed/new files plus the new manifest and READY are packed. No retagging.
- Each delta names the exact prior manifest/content/archive SHA256, MD5, size,
  policy and remote location. Before copying a delta, all dependencies' CLOUD_READY,
  actual archive size/MD5 and required metadata SHA256 must match within the bounded
  chain and route/job budget. Missing ancestors or sidecars fail delivery.
- A failed frozen archive keeps its original exact dependency during later retries,
  even if a newer day succeeds first. Its cloud closure is checked again before upload.
- Maximum depth is 27 deltas (28 explicit bundles for recovery). At that bound a
  new full complete-Daily snapshot is required; it still excludes Base. This bounds
  recovery work and is explicit in the versioned policy, not a normal daily resend.
- Download the named dependency closure and current bundle into separate directories.
  Run `python -m quanthus.pipelines.p10_gdrive_incremental_archive --bundle ...`
  with oldest first, exact target `--expected-manifest-sha256`, and a new `--output`.
  It checks compressed bytes, bounded safe extraction, source evidence, every
  inherited file and the final sealed census. It never publishes a CURRENT pointer.
  Before any recovery publication, run the producer semantic verifier and the
  existing purpose admission. Physical restoration alone grants no consumer rights.
- v3/v4 do not delete cloud archives. All referenced bundles must remain available.
  A genuinely changed weekly source is uploaded again as changed data.

## Rollout and rollback

1. Prepare clean exact GitHub checkout; run focused tests, scoped lint/type and
   make check. Compare known full-suite failures against the exact parent.
2. Keep the existing v3 upload running to natural completion. Do not change its
   code, policy, state or immutable cloud objects while it is running.
3. After confirmation, preserve the old release link, units and receipt. Pause
   only archive path/timer activation while no archive job is active.
4. Install the independent `quanthus-gdrive-de-proxy.service` and its root-only
   local configuration, then validate DE using a real synthetic upload/download.
   Preserve old unit snapshots. With archive activation paused and the old job idle,
   point `/opt/quanthus/gdrive-complete-current` at the reviewed v4 exact checkout,
   install the archive/OAuth unit dependencies from that checkout, and restart OAuth
   so future token refresh also prefers DE. Restore path/timer activation last.
   v4 state is `/var/lib/quanthus/gdrive-complete-v4`; mail continues to use the global
   v1 target-day/stage ledger. Never restart the US proxy in an active upload.
5. Check first thin archive CLOUD_READY readback and repeat-run reuse, then next
   T+1 actual delivery. Do not pre-accept tomorrow's supplier run.
6. If dependencies, restored identity or cloud confirmation differ, stop archive
   activation, retain all evidence and restore the old exact release link. Do not
   change ODS/DWD or OCI pointers. Independent daily and OCI delivery continue.

## Resource and acceptance boundary

The existing 4 GiB scratch reserve and 4 GiB package/archive ceilings remain.
Owner changed the preferred route on 2026-10-14: DE.Stlaure is primary on loopback
19084, US.WePC fallback on 19083. The old US.Stlaure entry on 19082 remains available
for its in-flight upload. DE has a separate Xray process/configuration; neither
credentials nor local private configuration enter Git. Existing finite transfer/job budgets,
three attempts per UTC day, one package per run and durable failure mail remain.
Synthetic Linux native restoration proves engineering closure. First actual
cloud confirmation and next trading-day execution are separate runtime receipts.
