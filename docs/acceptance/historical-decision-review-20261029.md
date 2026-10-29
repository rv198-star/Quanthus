# Historical decision review delivery — 2026-10-29

Work item: META-LEARN-001 / #540, reusing the #545 held-position archive.
Governing design: [Historical decision review v1](../design/historical-decision-review-v1.md).
Caller instructions: [Consumer guide](../operations/learning-assets-consumer.md#review-an-already-recorded-historical-decision).
Base: `6446b556ab4a62763c2028bfa825af4bc1b93b84`, the actual PR #579 merge.

## Delivered behavior

The pending-model example now retains model and purpose digests. A subprocess regression
first reproduced their omission; changing only those identities now changes the output
identities while preserving decisions and ABSTAIN/NOT_GRANTED behavior.

The new CLI authenticates and projects the exact already-consumed historical archive,
then queries recorded decisions and facts as JSON or offline HTML. It does not create
new historical decisions, recompute Facts, score a model or run an economic comparison.
Source selection SHA256:
`0420217b68e31e650e3816d03969a5ecf68fe480087d5f7966bd3607dd80600b`.
All 351 selected members / 104,033,820 bytes were fully hashed, and were unchanged in
the independent field audit. No new source acquisition or archive search ran.

## Real output and independent evidence

The job root on CN02 is
`/srv/quanthus-base22-data/jobs/decision-review-delivery-20261029T130059Z`.
Data-bearing artifacts stay outside Git and remain under the existing retrospective-use
boundary. Its usable outputs are:

- `outputs/review-package-final/`: the immutable three-file review package;
- `outputs/review-cases.html`: 12 predetermined categories for direct offline inspection;
- `outputs/review-cases.json`: the same cases and source locators.

The final manifest SHA256 is
`f6b72f331edf4973d5ff3a31dedfa136d9df129b63f8267223a5b3c6d381a727`.
The package is 21,855,862 bytes. Projection took 10.072 seconds; an independent build
took 10.009 seconds (10.86 seconds including process startup, peak RSS 287,908 KiB).
They have byte-identical manifest/payload/READY and different file inodes.

An independent checker that does not import the production projector compared all
8,393 exact event keys and the 538-episode / 343-issuer / 8,380-security-date census
against the original selection. For the 12 categories fixed before value inspection,
it directly resolved original held/trace/shadow/structure locators and compared recorded
fields, feature string types, lineage digests, decisions and actual cutoff.
This is full key coverage plus 12 field-level cases, not an independent audit of every
projected value. The 12 cases are explanation checks, not new scientific references.

The independently built package was moved and read in a new `python3 -I -S -B`
process with an empty environment, only ordinary code/runtime files allowed, and native
`connect` calls forced to fail. Python hooks and strace show no original-source opens
or internet connection attempts. Two local NSS socket attempts were denied; they are
not hidden as zero syscall activity. The reader opened exactly the package's three files.
The first harness attempt rejected the ordinary timezone database; that failed harness
log is retained, and the accepted allowlist includes `/usr/share/zoneinfo`.

After the positive read, the same explicitly disposable independent package was used
for corruption and missing-payload negatives. They failed with `REVIEW_PAYLOAD_CHANGED`
and `REVIEW_PHYSICAL_FILE_SET`; no original-source fallback occurred. The primary
package and original inputs were not modified.

An earlier valid prototype was superseded by the final code identity. Its payload was
byte-identical to the final package and was retired only after a retained restoration
proof; original prototype metadata/receipts remain. Failed pre-output attempts and the
independent-build cost remain recorded rather than counted as one total invocation.

## Observed use cost and meaning

One package load took 0.319 seconds and 12 in-memory key lookups took 0.000026 seconds.
An independent resolution of the same questions through four original locator roles
opened 12 distinct source members and took 0.943 seconds with decoded files shared.
These are local script observations, not a throughput benchmark or measured human-time
saving. Reopening and verifying the package for the delivered view took 0.583 seconds.

All 3,029 unavailable structure observations and 535 required baseline exits remain
recorded. The original position state is `trial`. Business dates remain 2012–2021;
the actual retrospective source cutoff is `2026-10-25T08:51:06.934678+00:00`, not an
inferred historical availability time. The old shadow proposal remains parked.
Unobserved model use and entry/allocation/fill alternatives remain NOT_OBSERVED.

## Local checks and disposition

Environment: Python 3.10.12, pytest 8.4.2, Ruff 0.16.10, mypy 1.20.2 on CN02.
`PYTHONPATH=src` selected the exact new checkout, rather than another editable install.
The original data mount is `noexec`; the same checkout was temporarily transferred,
per-file byte/mode verified, to executable `/dev/shm`, then returned to its original
path with all tracked files and staged changes unchanged. No second source tree was
distributed to another node, and no source data or production mount was changed.

| Check | Actual result |
| --- | --- |
| New reader and identity regressions | 16 passed; included in the focused and full runs |
| Reader + identity + blueprint/exploration/document focused set | 108 passed, 4.11 seconds |
| Full `make check` | Doctor/compile passed; full pytest finished with **5,698 passed, 20 skipped, 1 failed**, 559.55 seconds; original command exit 2 retained |
| Exact failing case, original code/assertions, data-disk TMP | `make check` with an explicitly recorded `pytest --lf` selection: **1 passed**, 1.95 seconds; doctor/compile/planning passed, command exit 0 |
| Changed Python files Ruff / focused mypy | Passed; mypy checked the new production module with `--follow-imports=silent` |
| Full Ruff | 216 diagnostics; all diagnostic files byte-identical to exact base; no new diagnostics in changed files; overall lint remains failed |
| Blueprint/exploration exact-base history and live issue audit | Passed / reconciled; existing records preserved |
| Real HTML | 12 cards parsed; escaped/inactive content, CSP and no external resources checked; no browser visual test |

The one full-suite failure was the unchanged
`test_verification_cli_accepts_successor_and_keeps_legacy_options_explicit`:
the monthly ODS verifier correctly rejected tmpfs free space below its original 8 GiB
floor. The same case passed on the data mount without changing or waiving that floor.
The full run and one-case environment recheck are separate evidence; they are **not**
reported as a single 5,699-pass invocation. The remaining planning prerequisites ran
in that incremental `make check`. No product or test code changed between the runs.

Two earlier attempts stopped at the allocated-byte safety threshold; another was
interrupted by the job-only successful-TMP cleanup hook encountering a read-only test
directory. Their logs, budget receipts and losslessly verified synthetic failure TMP
are retained. The hook was corrected for owned test TMP only; neither test assertions
nor production permissions were changed. Reproducible compilation caches and successful
function-scoped TMP were retired, while failed evidence remained. This is test retention,
not a new production resource framework or a hidden relaxed gate.

The 20 skips cover unmounted production/PostgreSQL fixtures and unavailable optional
Torch/sklearn dependencies. Python 3.12, whole-repository mypy, browser visual testing and
Web application tests were not run; the latter application was not changed. Hosted CI is
unavailable by Owner policy and is not claimed as executed. These are new local results,
not the earlier 5,683-test record. The previously reported 193-lint record remains
historical; the actual wider Ruff invocation here reports 216 as above.

The local engineering delivery supports retrospective read-only use of this exact
archive. Model quality, investment value, strict historical PIT, P6 and trading admission
do not increase. #540/#545/#546 are not closed by this tool. The blueprint corrects the
stale claim that the earlier readers were not integrated, while preserving product
admission as not granted. No new comparison is automatically selected: a later task
needs a concrete observed decision gap and its own qualified scope and finite budget.
