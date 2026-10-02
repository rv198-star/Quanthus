# P3/P5 continued input recovery — 2026-10-03

Status: active input qualification, not research acceptance or another terminal closeout.
The Owner explicitly instructed continued attempts after approving PR #419. Current Owner routing
permits the existing OCI data node through NexusAgent; no #424 SSH exception or legacy OCI control
path was used. PR #469 contains the reviewable continuation.

## P3: actual retained-input diagnosis

The exact failed candidate survived in the original event volume. SHA:
`7a7ffc385152c67ca8389cf3bde6b0dbff3f9bcfb659f63d28b7ce962d368699`.
Only its identity/date columns were queried. The code at `835dea9` preserves sequential listing
changes under a stable security/company identity and rejects overlapping listing histories.
Its 18 synthetic tests and an independent read-only review passed.

The isolated industry inspection completed in 38.25 seconds. Manifest
`0afe3c451292e94bffa35c064585720ece936f2f858a897aa4dfbd1e8ef11123`, result
`3a1d4b28b64498f9a89af9d6247878a091f528ee83a04fc0c002a3ab829d9965`.

| Identity denominator | DWD source | Sealed P4 supplement | Unresolved |
| --- | ---: | ---: | ---: |
| All 5,251 securities | 5,201 | 49 | 1 |
| Decision-window 5,249 securities | 5,201 | 47 | 1 |

Zero DWD/P4 classification conflicts. These are securities, not the historical 10,259 missing
candidate-event occurrences. The remaining `688287/SSE` has one stable company/security/listing
binding. A checksum-verified all-version query of the same frozen membership file returns zero
rows for that company (Nexus `session-1e732f4e527c963237e5ebff`). The sealed 259-member supplement
also has zero matches. This excludes the admitted sources; it does not prove an external provider
lacks the classification.

The original files remain unchanged. A permission-expansion/root-container proposal was rejected
by automatic approval review and was not executed. The successful alternative copied only exact
industry members into private task directories, verified every source hash, and ran unprivileged
with read-only inputs and no network. This is an actual isolated diagnostic, not a formal B Job.

`single-security-industry-proposal.v1.json` is a concrete **unactivated** proposal for one source
query on the first observed identity date. It cannot inherit network authority from #412's
existing-source inspection. B remains unaccepted and D remains NOT_RUN. A/C/C-R1 and #319 NO_PASS
remain unchanged.

## P5: verified three-table supply

The frozen `input-census.v1.json` selects exactly 250 existing 2012–2021 members. All payload
checksums matched Base manifest `541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`
before any Parquet schema or row read. Selected size: 824,617,539 bytes; file-list digest:
`84edfba056bf1cf1378dce3b4eaf087e1d80d4309e2c24d9356fd2ddff83c668`.

The independently reviewed code at `71864bcddfadfdcf5b2fa47ff56ead0fb7adb4ba` executed through
Nexus in an isolated unprivileged container, no network, 2 CPUs/2 GiB container limit, DuckDB
1 GiB, aggregate TMP ceiling 4 GiB and external 600-second timeout. Application elapsed time was
97.32 seconds; manifest `119af99681a7ce2b2ee9620564b970fc5db7c1226f42c8f2fc6ca6dfd3e534de`.

| Source or join | Rows / slots |
| --- | ---: |
| Daily bars | 7,722,194 |
| Tradability | 7,741,185 |
| Calendars | 7,829 |
| Unique date-aligned bar/state supply | 7,722,194 |
| State-only, missing bar | 18,991 |
| Bar-only | 0 |
| Orphan or ambiguous calendar slots | 0 |

No OHLCV values, labels or strategy outcomes were projected. Each narrow source table was built
once, then reused for accounting. The 1,193,029,632-byte TMP database was removed at completion.
Each source's knowledge_from is `2026-08-03T23:59:00Z`; none covers the historical T-close as a
warehouse-version interval. Historical research remains retrospective; public availability is not
inferred from warehouse dates. Security/ACTIVE population and independent reference capacity remain
null. Tradability flags cannot supply those missing semantics.

The subsequent bounded sidecar verified the two accepted F9 manifests and their identical source-input
member under `facts-sidecar.v1.json` at `6c254d2`. The 357 members cover three securities, each with
119 consecutive source sessions; source-contained 120-member identity windows are therefore zero.
This does not establish a global input shortage. Identity/epoch scope was not extrapolated, the
120-session gate was not lowered, and other F9 projection/result members were not read.
Sidecar manifest: `5a2a1cc24d962fb8fd5dd2b603fb7c15a20479d4b473447880b6707fd5542089`.
The following registered publication contracts were checked for a sufficient closure:

| Reference | Manifest SHA256 | Retained input limit |
| --- | --- | --- |
| P014 | `5926d73a04e37b74f7025c2f38314898ee7c5414aa45fe0686de20357ce3c388` | 61-session evidence windows, no complete source-input members |
| P015 | `8219bb18b1c5c3236fa5e3dd1ec0ea5112dfc101e286d4dbdab1c72c3d3c8d59` | 61-session source window, 21-close reference history |
| P020 | `e89833f5714d0bdead981bd3895483e5f2196a48dc846f429ca9ff04245f7e3d` | endpoint Trend facts and identity map |
| Second-group | `16056a637d4456ed3d9dcab4e4a1f9feb9b7f5e361e76bcc66c16a12acd14cb9` | summary and candidate only; job-local Facts not published |
| Early D pilot | `8656ccf634bfce1d1a4343c61084d2b744bd2cedcddc7cb162d4aadf887cdf64` | at most 61 history rows; missing full daily identity/coordinate members |

These packages cannot supply the missing 120-session closure. This is a statement about these
registered members only. An old `coordinate_epoch` string is not a digest of security/listing identity.
No settlement/outcome member was opened to search for input evidence.

The new unactivated `input-closure-proposal.v1.json` binds two Base identity files (541,560 bytes)
and the accepted v3 normalized-action/exception members (4,687,197 bytes), from checksum-verified
manifest metadata only. The exact correction manifest/READY is available on OCI and matches
`4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde` (Nexus
`session-50642482f5b8e473ace0fcdc`). This removes the need to depend on an RX6800 copy for these
two source members. It does not imply any other artifact or reference ledger is available on OCI.
The source extension requires full member verification, a fixed historical projection from mixed-year
members and coordinate-contract equivalence before Facts materialization. It does not read raw
action/term data or correction tradability, or manufacture ACTIVE/reference truth.

## P5: bounded synthetic execution recovery

D / #404 was reopened within its original synthetic engineering boundary under the Owner's
continuation instruction. The existing worker now uses a P5 lease inherited by execution and
verification children. A replacement supervisor cannot start new work while such a child is alive.
Never-started claims retain their exact job identity; completed artifacts receive verification only;
incomplete artifacts fail closed without rerunning a fit. The first pre-recovery state is retained as
an immutable snapshot, not advertised as a complete attempt history. No exit code is invented for an
orphan whose process result was not observed. Terminal status and cancellation use a short shared
decision lock. Legacy claims without verifiable ownership block new work and remain untouched.

Fault injection includes a real supervisor SIGKILL with a surviving child, child-exit uncertainty,
identity drift, terminal-plus-queued state, cancellation during verification and immediately before
terminal commit, Git preparation with a surviving child, and persistent terminal-write failure.
The focused recovery/adapter/Worker run passed 135 tests and independent final read-only review
found no BLOCKER/MATERIAL. This establishes bounded
synthetic recovery, not full D acceptance, a GPU result, T1 admission or deployment authorization.

## Engineering checks and evidence locations

P5 census: 15 synthetic tests and independent read-only review PASS. Tests include protected
trade-date rejection, source-family/hash/symlink restrictions, duplicate and orphan accounting,
half-open knowledge intervals, Shanghai dates, empty-count semantics, and resource enforcement.
The full check at `71864bc` initially found three stale routing assertions, subsequently corrected.
The final full check at `6c254d2` reports **2,968 passed / 7 skipped / 56 failed** in 95.41 seconds.
Every failed test ID matches the prior 56-failure baseline: zero new failures, not a green full check.
The sidecar's nine synthetic tests and separate independent read-only review also pass.

After the Worker recovery fixes, final full `make check` reports **2,992 passed / 7 skipped /
56 failed** in 94.32 seconds. Failed IDs still exactly match the same baseline; zero new failures.
The planning check (after the test target's baseline failure) and focused Ruff checks pass separately.
Raw receipts: `orphan-final-check.log`, `orphan-final-check.xml`, and
`orphan-final-check-comparison.json` in the external artifact root.

Remote source/output roots are private under `/home/ubuntu/quanthus-research/p3-p5-20261002`.
Raw tool and run receipts are external local artifacts under
`artifacts/p3-p5-input-recovery`, including the exact P3 manifest, all-version residual query,
P5 census receipt, test XML and execution-call envelopes. No market payloads are committed.
