# Local engineering acceptance v1

| Field | Value |
| --- | --- |
| Design status | approved — Owner approved the bounded implementation on 2026-10-30 |
| Delivery gate | local engineering accepted with explicit skips; actual run/reuse evidence is linked in the blueprint ledger and task handoff |
| Parent | [Delivery roadmap](lld/09-roadmap.md#4-release-mechanics) |
| Work item | META-GATE-001 / #3; META-TRACE-001 / #2 for blueprint bookkeeping |
| Contract/config | `config/local-acceptance.v1.json` |
| Implementation | `src/quanthus/local_acceptance.py`, `src/quanthus/local_acceptance_pytest.py` |
| Conformance | `tests/unit/test_local_acceptance.py` |
| Operation | [Local acceptance commands](../operations/local-acceptance.md) |

## Consumer and boundary

Quanthus developers and PR reviewers need repeatable local evidence while hosted CI is
unavailable. This is a thin wrapper around the existing `make check`, not a second test
definition or a change to release, scientific, PIT, cold-build or production authority.
Optional integrations are reported as skips; a successful local profile does not qualify
absent PostgreSQL, provider packages, production mounts or other Python/runtime profiles.

## Identity and execution contract

The v1 profile requires a clean committed worktree, with no untracked files recognized by
Git. Tracked regular files are all hashed, including docs, configuration, tests, Makefile,
dependency declarations and the wrapper/plugin. Submodules and repository symlinks fail
closed. Ignored non-cache files in `src/tests/config/scripts` are also rejected. `.tplan/`
is ignored execution bookkeeping; it is not a semantic input to this profile.

Identity binds the full commit, tree and physical checkout path. A new commit, even with
identical files or only documentation changes, requires a new run in v1. This conservative
rule deliberately omits documentation-only reuse exceptions.

The runtime fingerprint hashes the interpreter, standard-library/site-package files and
metadata (excluding bytecode caches), OS release identity, and the make/git/shell executables.
Package versions alone are insufficient. Only local dependency identities are exported;
credentials and direct-install URLs are not emitted. The profile assumes a trusted local OS
and executor; it is not a hermetic system-library image or a signed third-party attestation.

The child environment is constructed from fixed nonsecret values: standard tool PATH,
UTF-8/UTC, hash seed, current source path, job-local HOME/TMP/bytecode path and the fixed
pytest plugin. External environment variables, optional database/mount credentials, user
pytest selection and automatic plugins are not inherited. The wrapper selects the existing
`check` target. It cannot claim arbitrary external inputs have been checked.

Input and runtime fingerprints are rechecked after execution. Changes or an unreadable
fingerprint fail qualification. The run has explicit elapsed-time/RSS/disk/TMP/free-space
limits in the versioned profile; no limit is silently increased. Measurements are sampled,
so the limits are stop thresholds, not kernel-enforced hard quotas. Only this job's own
successful function-scoped TMP is reclaimed after all test phases pass. Failed/shared TMP
and interruption evidence remain. An existing output directory is never overwritten.

## Receipt and reuse contract

A new private job contains `attempt.json`, `inputs.json`, `checks.log`, `pytest.json`,
`receipt.json`, `SHA256SUMS`, plus job-local `home/` and `tmp/`. READY/publication or CI-success
statuses are not created. `attempt.json` remains when the process is killed before completion.
SIGTERM/KeyboardInterrupt stop the owned make process group and retain an incomplete receipt;
SIGKILL/power loss may leave only the attempt and logs. Such output cannot qualify for reuse.

The pytest plugin records passed/failed/error/skip counts, skip reasons, failed node identities
and TMP retention. Counts describe fully passed tests; a teardown error prevents that test
from being counted as fully passed. The raw pytest report remains in `checks.log`.
The final make exit code matters, including failures after pytest (for example planning checks).
Missing telemetry, zero passed tests, failed command, stopped process or changed inputs cannot PASS.

`PASS_WITH_SKIPS` is distinct from `PASS`. A verify call checks the exact member set, hashes,
receipt schema, counts, command outcome, local ownership/write permissions and current identity.
It emits `REUSED_NOT_RERUN`, without creating a new run or modifying old evidence. Missing,
damaged, linked, incomplete, failed or mismatched evidence is `NOT_QUALIFIED` and returns nonzero.

The caller must knowingly trust the local executor. SHA256 protects integrity against accidental
change; an owner who rewrites every file and checksum can forge local evidence. This command
does not authenticate third-party receipts, turn remote CI red into green, automatically merge,
or replace an explicitly independent construction/cold acceptance.
