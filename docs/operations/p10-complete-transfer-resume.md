# Complete Daily mirror resume

The first qualified weekly financial continuation adds approximately 1.4 GB to a
complete Daily package. CN01 observed egress cannot complete this bootstrap in one
20-minute OCI service attempt.

The versioned private `.complete-sync-cache-v1` retains interrupted rsync partials.
Only exact remote pointers and manifest hashes enter the cache. A cached older
trading-date job continues across the following trading date and closed calendar
days before starting its new target. All targets still require the authoritative
T+1 trading-day start checkpoint, including future pending dates.
The two-minute mirror timer runs overnight; each service attempt remains bounded
to 20 minutes. A same-attempt ExecStopPost hook checks systemd SERVICE_RESULT
and records durable failure email before the timer can restart that service.
The earlier external OnFailure hook could observe the next attempt's start time
and mislabel an old target as the execution date; it is removed from this unit.
Successful stops send no failure mail. Failure mail is independently
retried by the existing five-minute mail timer.

An exact previously visible or privately cached local file can seed a new package
only after its actual byte count and SHA256 match the incoming immutable inventory.
Private candidates are limited to 28 package manifests in this job's cache;
their old qualification claims are never reused. rsync uses
checksums and delayed replacements, preserving old package contents. Same-device
files use hard links; unsupported devices use the existing sealed-file copy
helper. Raw files and source truth are not transformed.

The cache has no consumer pointer or visibility receipt. READY, full native
projection, R2 minimum, R3=0, source closure and actual UTC visibility remain
mandatory before publication. Interrupted partials do not imply acceptance.
Capacity checks retain the package staging budget plus 4 GiB reserve.

During deployment, wait for the original service attempt to end. Preserve its
retained staging directory and adopt its contents into the private cache only
after proving the service is idle and its pointer identity is unchanged. Keep
rollback links, units, original pointers and all old sealed packages.
