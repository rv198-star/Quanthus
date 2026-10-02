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

The next bounded step verifies only two accepted F9 manifests and their identical source-input
member under `facts-sidecar.v1.json`. It may count identity/coordinate coverage for those exact
members, without widening their three-security date scope or reading F9 model/projection results.

## Engineering checks and evidence locations

P5 census: 15 synthetic tests and independent read-only review PASS. Tests include protected
trade-date rejection, source-family/hash/symlink restrictions, duplicate and orphan accounting,
half-open knowledge intervals, Shanghai dates, empty-count semantics, and resource enforcement.
The full check at `71864bc` reported 2,956 passed / 7 skipped / 59 failed. Fifty-six failures match
the prior baseline; the three newly failing routing assertions were corrected and their 53-test
contract suite passes. A final full check follows the remaining sidecar changes; no green full-check
claim is made here.

Remote source/output roots are private under `/home/ubuntu/quanthus-research/p3-p5-20261002`.
Raw tool and run receipts are external local artifacts under
`artifacts/p3-p5-input-recovery`, including the exact P3 manifest, all-version residual query,
P5 census receipt, test XML and execution-call envelopes. No market payloads are committed.
