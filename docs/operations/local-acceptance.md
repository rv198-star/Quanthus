# Run and verify local engineering acceptance

Parent: [Local acceptance contract](../design/local-acceptance-v1.md).
Work: META-GATE-001 / #3; configuration: `config/local-acceptance.v1.json`.

Use a clean committed checkout and a Python 3.10+ environment containing the dependencies
for your declared local profile. Hosted CI is unavailable by default. Run applicable focused
regressions and changed-file lint/type checks during development; this entry performs the
existing complete `make check` once the change is stable.

```bash
make local-acceptance PYTHON=/absolute/venv/bin/python \
  ACCEPTANCE_JOB=/absolute/private-parent/new-job
```

The parent must exist and the job must be new, absolute, physically separate from the checkout
and runtime. The profile reserves 8 GiB filesystem free space plus the unspent portion of
its 192 MiB combined job/worktree budget (existing checkout allocation is already on disk).
The default check time limit is 30 minutes. If this
profile does not fit the task, review a new versioned profile rather than disabling its checks.

Read the short terminal summary, then `receipt.json` for command status and counts,
`pytest.json` for individual skip/failure reasons, and `checks.log` for full output.
`SHA256SUMS` binds the four completed evidence files. `PASS_WITH_SKIPS` explicitly means
the optional/skipped capabilities were not exercised. `FAILED` and `INCOMPLETE` retain evidence.

To review the **same checkout, commit and environment** without rerunning:

```bash
make local-acceptance-verify PYTHON=/absolute/venv/bin/python \
  ACCEPTANCE_JOB=/absolute/private-parent/existing-job
```

Only verify receipts from an executor you already trust. Success says `REUSED_NOT_RERUN`;
it must not be described as a fresh independent run. Verification only reads its inputs.
Any new commit (including a merge or documentation-only follow-up), changed dependency code,
missing log or changed test profile rejects reuse. Use a fresh job for the new run.

This environment intentionally omits inherited integration credentials, external mounts and
user plugin/test-selection settings. Run explicitly required integration/Web/other-runtime
acceptance separately and report those results alongside this local profile. Independent
rebuilds, cold checks, data/strategy value and production approval retain their own gates.

After a job is used, retain its small evidence files for the PR handoff. Reclaim its remaining
TMP only under the job's explicit retention authority; the command never deletes old jobs,
formal outputs, data sources or another task's files. No timer or cleanup daemon is installed.
