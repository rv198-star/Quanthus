# P3/P5 continued input recovery — 2026-10-03

Status: active input qualification, not research acceptance or another terminal closeout.
The Owner explicitly instructed continued attempts after approving PR #419. Current Owner routing
permits the existing OCI data node through NexusAgent; no #424 SSH exception or legacy OCI control
path was used. PR #469 merged the reviewed continuation; this record preserves its actual result.

## Formal retained B: successful execution, partial qualification

Job `job_5352d6c61af94a1aac4162a9a6a5d357` / Run
`run_5352d6c61af94a1aac4162a9a6a5d357` completed at `2026-10-03T04:07:13.870879Z`,
attempt/claim generation 1/1, started `04:01:16.912367Z`. Exact runtime
`92a70a7bc11b2df2da16cd00dbacdbd4877d6b8b` was released through the existing production interface,
EventWorker only, after normal PR #469 merge at `1857cc5182db3173f3569e8fd4d2019bd03ca6ec`.
Effective image: `sha256:3477f9201848cf5ff26ade4ddd1971296bf2def5aba99886667b89595c4efa3d`.
P6/replay image and full environment digest were unchanged; health/ready returned 200.

Output is immutable under `/data/event-runs/structure-runs/run_5352d6c61af94a1aac4162a9a6a5d357`.
Manifest SHA `4a6557aad4416d9f86c184c2f57552d444093fbb2e76f4db942e02906887fcce`, result digest
`15a42673fb01e41e33cc34bf6a1fddaeb787019470c89cce4e5b9fb9e6fe9105`, candidate digest
`d126a7576f7e1f31c8cdd3b3afb155bce59a39cae56b0c324a16667ace08f19e`.
Independent read-only verifier `session-470b3c43e95f9ef1c1647055` passed full frozen-input bytes,
READY, result recomputation and 57 output files / 313,766,273 bytes. This is independent verification,
not independent cold construction. The single attempt marker remains spent and unchanged.

All 27 retained producer fields matched over 2,455,175 rows with zero differences. Actual decision
denominator is 2,143,553, excluding 306,630 warmup rows and the final 4,992 execution-day rows.
All 5,251 securities have temporary static industry coverage. The authoritative calendars match:
60 warmup opens (2024-10-09..2024-12-31), 416 decision-to-next-open pairs. The unchanged scanner
found 11,741 triggers, rejected 1,482 by same-security isolation, leaving 10,259 before industry
concentration; 3,852 concentration rejections leave 6,407 candidates. No industry membership is
missing. Application elapsed: 340.284 seconds; shared audit TMP was built once/reused/removed.
No strategy results, targets, P2 fills, D, P6 mutation or formal DWD repair were produced.

The independent acceptance review (`session-d62dcf7cb6fec4668fcad657`, corroborated by root
`session-566767c04fd0451e3fd7d84f`) found two material qualifications still missing:

- 35 opens from 2026-08-03..09-18 have 4,992/4,992 invalid bars each, totaling 174,720.
  Frozen Daily source writes `price_basis='none'`; the actual retained producer filters
  `UNADJUSTED` and preserves empty LEFT JOIN rows. Missing_bar=0 therefore does not mean actual
  coverage. Zero projection differences faithfully preserve this defect. It is not established
  raw DWD loss, legitimate suspension or a reason to shrink the fixed window.
- The frozen candidate card requires T-close availability for each consumed family. 2025 has
  1,250,718 late raw bars among 1,251,467 pairs; listing/security/state are all late. These counts
  prove warehouse timing, not leakage or valid public availability. The recent visibility policy
  checks effective time but lacks independent per-family economic availability evidence. The
  development-only retrospective approval cannot silently qualify this recent window.

Conclusion: **`PARTIAL_INPUT_QUALIFICATION`, B remains NO_PASS; D remains inactive.** Job success,
artifact integrity, retained_input_requalified=true and candidate non-emptiness do not override that
decision. Sealed outputs and A/C/C-R1/old negative receipts are preserved. Per Owner instruction,
[#473](https://github.com/rv198-star/Quanthus/issues/473) owns unified adapter governance and
[#474](https://github.com/rv198-star/Quanthus/issues/474) owns PIT/source qualification. Temporary
research handling cannot close either issue or modify the original DWD/P6 path.

### Bounded source adapter diagnostic

The new input-only request `daily-adapter-probe.v1.json`, SHA
`396b823a16ee28b2636b90a8b915f26ab6897cf2e0edd6b54ed5186a2ce78f3c`, freezes exact package/READY,
bar and calendar identities from recovered source metadata: 35 packages / 70 payload files /
846,352,085 bytes. All dates stay inside the deficient Daily suffix. OCI owns 33 packages; CN01
owns exact Aug04/Aug05 packages unavailable on OCI. No broad filesystem search or package rebuild.

It verifies the complete selected-node closure before any Parquet read, builds each package's 1d
TMP once and reuses it for identity/calendar, basis, price validity and raw knowledge-time counts.
Unknown basis remains fail closed. Raw times and values are never rewritten; publication is not
inferred. Only aggregate receipts are emitted, no corrected bundle/candidate/economics/labels or
formal B acceptance. One external attempt marker per node, wall 600 seconds, DuckDB 2 threads /
2 GB, TMP 512 MiB, output 64 KiB. It is a new diagnostic, not a repeat of the spent formal request.
At the initial preregistration it had not executed. Original-data repair and any later B gate remain separate.

CN01 has now completed its exact Aug04/Aug05 subset at `7b501fe`, Nexus
`session-d25081a34a794c559536cf03`: 9,984 raw 1d rows, all `none`, legacy-selected zero,
invalid-price zero, zero-activity eight. All 9,984 raw times are after T-close; none was inferred or
changed. Four files / 48,277,010 bytes verified; application elapsed 0.384 seconds, TMP removed.
Receipt SHA `b3a2b3bb18caa39aa2a79fadb0862e2b146e4bd00379e28020a7c4421db73831`.
The exact Linux/Python 3.10/duckdb 1.5.6 environment passes all 16 synthetic cases. Its isolated
test venv needed pytz for fetching synthetic timestamps; no global dependency or formal data changed.

OCI's preflight passed 15 pure cases in its actual Python 3.11/duckdb 1.5.6 image; the Git-host
fixture was separately covered on CN01. Its first actual attempt stopped at source-byte verification,
before DuckDB import/Parquet row access: the Aug27 original package is not readable by the
unprivileged diagnostic. Original source permissions and the spent attempt are preserved.
Failure log SHA `732e205cf37df226d86a3929092681058b75d280cd401e90aeb7fa65f4aa9b31`;
marker SHA `d8ce949c367e00e46be5a8d80e210020605b971f52ea5f05706b6e9fe35dca42`.
It is an access failure, not evidence of missing DWD or a completed row probe.

The separate `daily-adapter-pre-read-recovery.v1.json` binds that exact failure and original request.
It authorizes one new OCI attempt with the same per-node budget and all original package/member
bytes, changing only the Aug27 storage location to private exact copies through the existing
read-only source interface. It does not rename/reset the old marker, alter original permissions,
repeat the successful CN01 subset, open candidate/economic data or admit another automatic
recovery. No root diagnostic container is used.

The recovery completed at `5cf2fb1cf95f3219232b50988de890e398ad80ba`, Nexus
`session-d8cb204d5265b323c33cb6ed`: 33 exact packages / 66 files / 798,075,075 bytes,
164,736 raw 1d rows. Every row has `none` basis and valid prices; legacy-selected zero,
unknown-basis/time zero, zero-activity 165. All raw knowledge times remain after T-close.
Application elapsed 2.246 seconds (byte verification 0.636; shared TMP scan 1.610); TMP removed.
Receipt SHA `c9b50c3dccc6489f3f695018f5888fa5e3b2cec4ba5af98c1353622efe5ea070`, result digest
`sha256:af12c027f2fabe007a1f570498668b4008d83f4966d6ecf30c987c94f43c9db1`.
The source copying and container orchestration are not included in application elapsed time.
The isolated production-image preflight passes 16 cases; its Git-host fixture is covered separately.

Independent local receipt verification recomputed both result digests and physical receipt hashes,
matched each node's exact frozen date set, and proved the union has 35 unique dates, each with
4,992 rows. Combined: 174,720 raw/valid-price/`none` rows, zero legacy matches or unknown basis/time,
173 zero-activity rows, 70 files / 846,352,085 bytes. All raw times are late; none was replaced.
Combined verification digest: `sha256:69cb5e1626990f044ef2fc163ad016c81a2213b2d1d4a5ce6cc568a335eb9cb8`.
This establishes the diagnosed suffix's consumer mismatch, not loss of the original DWD bars.
Zero activity remains subject to tradability rules and is not silently treated as an eligible entry.
Both successful attempts are spent. No corrected bundle, candidate scan, economics or labels ran.
PIT/economic availability remains unproven, B remains NO_PASS, D inactive and data issues open.

Seventeen new synthetic cases and all 30 retained-input cases pass (47 total). Full `make check`:
3,100 passed / 7 skipped / 41 existing failures in 112.59 seconds; failed IDs exactly equal the
prior reviewed retained-entry check, with zero new IDs. Focused Ruff, planning and diff checks pass.
CI at `5cf2fb1` records Python 3.10: 3,100 passed / 20 skipped / 28 prior failures (27 retired
dual-lane runtime bindings and one BaoStock version gate); Python 3.12 was fail-fast cancelled.
Web retains nine existing Node TypeScript-extension failures. Neither check is reported as green.
The source diagnostic and its read-only implementation review do not qualify formal B.

### Temporary Daily basis adaptation — executed

The Owner's continuing delegation and temporary-only boundary permit the new, distinct
`temporary-daily-adapter.v1.json`. It binds the completed diagnostic receipts and exactly the same
35 original package identities. The spent diagnostic and retained B attempts are not reopened.
This performs one private materialization per node: retain every raw column/value/type, rename
the original `price_basis` to `source_price_basis`, and add canonical `UNADJUSTED` plus the adapter
policy version. Source prices, quantities, timestamps and provenance remain byte-semantically
unchanged, verified by exact typed `EXCEPT ALL` after Parquet serialization. Unknown basis,
invalid prices, duplicate identities or source drift stop publication.

All adapted rows explicitly carry `decision_admissible=false` and
`P3_ECONOMIC_AVAILABILITY_UNQUALIFIED`. This source artifact cannot feed candidate/replay/strategy
decisions until a separate fully qualified B binds every input family's economic availability.
It is not a corrected retained bundle, candidate scan, D result or formal DWD repair.

Dependency graph: complete source/code/config/diagnostic verification -> one calendar and 1d TMP
per package -> identity/basis/price validation -> canonical serialization -> exact raw-column
comparison -> immutable receipt. All consumers share that one 1d TMP; staging is removed. Outputs
are private content-addressed caches, reuse disabled, 30-day retention with research-operator
cleanup; preserve receipts after payload expiry. The key covers the complete source closure,
config/policy, code tree/revision, output schema, scope and security subset. No ADM promotion.
Budget per node: 600 wall seconds, two DuckDB threads / 2 GB, separate 512 MiB TMP and 64 MiB
published-output ceilings, one attempt with no automatic recovery. No P6 or original permissions
change. Unified repair and temporary exit still belong to #473/#474.

Sixteen adapter synthetic cases pass; with diagnostic and retained cases, 63 tests pass.
Independent read-only review found and verified repairs to COPY parameter ordering and separate
TMP/output accounting. Full `make check`: 3,116 passed / 7 skipped / 41 failed in 116.64 seconds,
with exactly the prior 41 failure IDs and zero new IDs. Focused Ruff/planning/diff checks pass.
The full repository check is not green.

Both new single materializations completed at `9d825e973f1a2b4e0a4171041813c3bc9c310762`
through Nexus, using GitHub-distributed detached source worktrees and the unchanged production
image for the isolated OCI runtime. CN01's existing isolated Python 3.10 preflight and the OCI
Python 3.11 image each pass all 16 synthetic cases. No production worker was released.

| Node | Actual session | Rows | Parquet bytes | Application seconds | Receipt SHA |
| --- | --- | ---: | ---: | ---: | --- |
| CN01 | `session-c380cce769e4d232a52e614f` | 9,984 | 564,767 | 0.639160 | `9420349db6cb4c0948118303befaa148116b478cb111329be96105e2f04417ae` |
| OCI | `session-20e0552eb06b7325f1f5e0c6` | 164,736 | 9,478,265 | 4.976391 | `3e2fcdd1ca86223f5cd9d5ae144549cb59f4c5988fb83f9a9077afc823e1447d` |

Combined verification proves all 35 unique original dates, 174,720 rows and 21 original columns
with zero typed source differences after serialization. All raw timestamps remain late; 173
zero-activity rows remain recorded. All 174,720 output rows are decision-unavailable. The private
cache payloads total 10,043,032 bytes; the original selected closure remains 846,352,085 bytes.
Fresh read-only SHA/size verification passed every one of the 35 published Parquet files. Local
verification recomputed both receipt/result digests and complete cache identities, matched the
exact date union and confirmed identical code-receipt digests on both nodes.
Combined verification digest: `sha256:62c2b95ecd440a82f99d0cec428b5415ea9a06c2e82452cb429ed3e8a84aae1f`.
Narrow TMP was built once per package, shared by all checks, and removed on both nodes.

OCI first encountered a synthetic launcher error because the image's default entrypoint treated
`sh` as a Quanthus subcommand. No market attempt marker/code receipt existed. The original
`synthetic-tests.log` was retained; the corrected launcher explicitly overrides only its isolated
container entrypoint and writes `synthetic-tests-verified.log`. Production configuration, source
bytes, code and budgets were unchanged. This was preflight correction, not a market retry.
Both actual materialization markers are now spent; no automatic repeat is authorized. #473/#474
remain open; the temporary basis defect is handled, while source availability still prevents B/D.

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

The Owner separately approved `single-security-industry-proposal.v1.json` on 2026-10-03.
The activation is sealed in `config/research/p3-p5-temporary-source-activation.v1.json`, together
with the Owner's subsequent instruction that formal data gaps belong to the separate data workstream
and current research handling is temporary only. Formal industry repair is tracked by
[#470](https://github.com/rv198-star/Quanthus/issues/470), not closed by temporary success.

The exact one-security/date query ran once at `3d2ac91`, through OCI Nexus
`session-b8e60ae67d4f9030de5fff08`, with isolated BaoStock 0.9.3. It returned
`QUALIFIED_TEMPORARY_STATIC_SOURCE` in 1.439 application seconds, zero retries; identity/date,
classification and hierarchy checks passed. Receipt SHA
`b26177362b1da0f15a4f4612ffb4325e8bded039b1035443fed2c429176a428b`; response SHA
`6883ab4e46add9770c79f49cdaec258bd5a80912612ddfadbcb40edbc3a2abfd`. Actual retrieval time is
`2026-10-02T17:55:56.775201+00:00`, not a backdated historical knowledge time. The fixed private
attempt is durable and cannot be automatically repeated; source files and DWD remain untouched.
No server request-echo attributes were captured; those extra audit fields were not an approved
qualification gate and cannot justify another query. B remains unaccepted and D remains NOT_RUN.
A/C/C-R1 and #319 NO_PASS remain unchanged.

The next isolated composition at `782b37e` completed the temporary static mapping in 5.028
application seconds: 5,201 DWD + 49 sealed P4 + 1 approved residual = all 5,251 securities,
zero missing/duplicate/conflicting identities. The original 5,249 decision-window securities and
dated aliases remain unchanged. No candidate/replay/strategy calculation ran. Nexus session
`session-e2238406ba64130c4fb872b7`; receipt SHA
`2981ae78e2afffe638c610ce0c6545f84b4c489350e8b03e982939c7a7dcda3a`, mapping SHA
`36dac5f6f941ddad05640a0f12d0f77555af8457d07a528e7b43d9a5af4b0280`, provenance SHA
`b27484490a201e54a4d44b84fccd7eedb41396170c3b81faff67d510b40534f9`. The fixed private root is
`p3-p5-20261002/p3-temporary-industry-closure/attempt`; three outputs total 3,573,178 bytes.
This resolves the temporary mapping subproblem only. Formal B source/availability/scan acceptance
remains open. The implementation passed 12 synthetic contracts and an independent read-only review.

Exact source-lineage recovery also found the required Aug05/Aug04/Aug03 Base V2.1 versions on CN01.
With the OCI suffix and the accepted Aug27 bridge, all 35 selected packages (870,036,020 declared
bytes) passed physical SHA checks; no package was rebuilt. Target identity and sync time match the
retained bundle. External source-lineage receipt SHA
`f270f66bacedc15de744a590841740247b394a3fd4164b8bf316f57c1435e9f7`. The old Base-v2 branch in
the inspection inventory is excluded. Initial CN01 access failures were resolved through existing
Nexus/passwordless-sudo read permission without changing permissions; they are not data-loss evidence.
This is recovered provenance, not independent reproduction or formal B qualification. Formal B
still requires a versioned retained-input entry and the existing event-worker-only release gate;
the current Worker does not load new source merely because a Job names a new revision.

### Retained-input B engineering checkpoint

The new narrow EventWorker entry binds `retained-input.v1.json`, physical SHA
`b642509bd80bde69a5e765c5e8b7860e01b3643e33e576029e6788d343294ff4`: 52 files and 304,584,475
bytes, with the original request, temporary activation, source-lineage receipt and exact calendar
prerequisites. Source calendars were selected and byte-verified from package manifests without
deserializing market rows. The Base prefix ends at 2026-07-31; all 35 selected daily members and
the unique accepted Aug27 bridge remain explicit. The history/decision/execution window is unchanged.

The Job verifies every admitted byte before row access, compares all 27 retained producer fields
exactly, checks historical identity/calendar/next-open pairs, and reuses one job-local intermediate
for coverage ledgers and the existing replay audit report. Only after these gates does it scan the
unchanged outcome-blind candidate rule. Resource limits are 1,800 wall seconds, two DuckDB threads,
6 GB DuckDB memory, 8 GiB TMP and 4 GiB output. An fsynced external attempt marker prevents an
automatic second scan; the same Job may publish already sealed staging after interruption. The
parent Worker also enforces the hard wall deadline.

Thirty synthetic tests pass, including complete artifact construction and recovery, exact Decimal
and identity drift, missing calendar days, reason overlap, resealed-map tampering and parent timeout.
Two independent read-only implementation reviews passed, including comparison with the original
reader's synthetic audit. The new scan TMP budget parameter retains existing defaults for other
consumers. Its two catalog code-evidence bindings and derived catalog snapshot were refreshed from
the actual source bytes; catalog entry/gap counts are unchanged.

No formal B Job or actual retained market-row scan has run at this engineering checkpoint. B
acceptance, D activation and governed DWD repair remain false; #470/#471/#472 remain open.

Full `make check` at the reviewed retained-entry tree with `TMPDIR=/private/tmp`: 3,083 passed,
7 skipped, 41 failed and zero errors (118.433 seconds). All 41 failed IDs are in the recorded
56-failure baseline; no new failure remains. The catalog registration test binds the refreshed
derived digest while the sealed E9 registration receipt remains unchanged. Full checks are still
not green; existing frozen-runtime/macOS failures remain explicit. Focused Ruff and catalog
projection verification pass. Logs/XML remain external under `retained-b-reviewed-check.*`.

## P5: verified three-table supply

The later full per-session identity join succeeded at `2d73967`, Nexus
`session-0d8cd9c6c5e9cc65bd026fbd`: all 7,722,194 bars match one admitted calendar/listing/security
version at the Shanghai session date; zero missing or ambiguous identities. Receipt SHA
`31f3670bb872f1373a94ed40d4144d87e3b92aa5ecd642c4863185fbc2a7138e` (14,740 bytes).
It consumed the same frozen 250 census members and two identity dimensions, built each narrow TMP
once and reused it. The 1,364,297,412-byte TMP was removed; application time 518.736 seconds under
the unchanged 600-second cap. No OHLCV values, action rows, labels or protected-year payloads were
read. This is retrospective identity coverage only; original normalization scope and action prefix
still block coordinate/window qualification. It does not establish ACTIVE or accept B.

The first attempt at `28eccbf` failed at date serialization, so it produced no accepted receipt.
The corrected code adds explicit timestamp serialization and an end-to-end synthetic receipt test;
the retry kept all source and resource boundaries. Full check with canonical `TMPDIR=/private/tmp`
at `782b37e`: 3,038 passed / 7 skipped / 56 failed, exactly the baseline failure IDs. The preceding
macOS symlink-TMP check had 31 extra path-alias failures; both logs and the comparison are retained.

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
| State-only/no matched bar; cause unclassified | 18,991 |
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

The separately approved `input-closure-proposal.v1.json` binds two Base identity files (541,560 bytes)
and the accepted v3 normalized-action/exception members (4,687,197 bytes), from checksum-verified
manifest metadata only. The exact correction manifest/READY is available on OCI and matches
`4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde` (Nexus
`session-50642482f5b8e473ace0fcdc`). This removes the need to depend on an RX6800 copy for these
two source members. It does not imply any other artifact or reference ledger is available on OCI.
The source extension requires full member verification, a fixed historical projection from mixed-year
members and coordinate-contract equivalence before Facts materialization. It does not read raw
action/term data or correction tradability, or manufacture ACTIVE/reference truth.

The identity-only subset executed at `d115ea0`: 4,679 historical listing rows and 4,679 linked
security-version rows, zero listing rows completely lacking an overlapping security version, and
two security IDs with multiple retained versions. This establishes dimension availability, not
per-session uniqueness or complete coverage. Both knowledge_from extrema are
`2026-08-03T23:59:00Z`. Receipt SHA
`69e90f8912226ab84fbf916c551fa3a498c4bd2771ba930ffd018a3996c820f7`; application elapsed 0.725
seconds; 3,339,491 bytes of shared job-local TMP removed. No action or market rows were consumed.

[#471](https://github.com/rv198-star/Quanthus/issues/471) tracks original normalization manifest
availability, and [#472](https://github.com/rv198-star/Quanthus/issues/472) tracks the 18,991 state-only
slots for classification. Neither is a claim that all DWD data is missing. Original normalization
scope, coordinate/120-session windows, ACTIVE and B remain unqualified. The 119-session F9 sidecar
is a research-package limitation, not evidence of DWD history loss. Formal repair stays with the
data workstream; private research sources must exit new runs only after a governed replacement is
bound and compared. Existing audit artifacts are preserved, never silently overwritten.

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

After the Worker recovery fixes, the full `make check` reported **2,992 passed / 7 skipped /
56 failed** in 94.32 seconds. Failed IDs still exactly match the same baseline; zero new failures.
The planning check (after the test target's baseline failure) and focused Ruff checks pass separately.
Raw receipts: `orphan-final-check.log`, `orphan-final-check.xml`, and
`orphan-final-check-comparison.json` in the external artifact root.

After temporary-source implementation, full `make check` at `d115ea0` reports **3,014 passed /
7 skipped / 56 failed** in 96.96 seconds, with exactly the same 56 failed IDs. Fifteen P3 collector
boundary tests and seven P5 identity boundary tests pass; both implementations received independent
read-only review. The precise 0.9.3 provider source was inspected on OCI; the reviewer's local 0.9.4
source observation is not substituted for that version evidence. Planning and focused Ruff checks
pass separately. Those check receipts use the `temporary-identity-check` prefix.

Latest S2/identity checks at `782b37e` use `temporary-closure-canonicaltmp-check`: **3,038 passed /
7 skipped / 56 failed**, zero new failure IDs, 110.44 seconds. The first noncanonical macOS TMP run
and its 31 path-alias failures remain available under `temporary-closure-check`; the canonical-path
rerun resolves all 31 without changing production code. Planning and focused Ruff pass separately.

Remote source/output roots are private under `/home/ubuntu/quanthus-research/p3-p5-20261002`.
Raw tool and run receipts are external local artifacts under
`artifacts/p3-p5-input-recovery`, including the exact P3 manifest, all-version residual query,
P5 census receipt, test XML and execution-call envelopes. No market payloads are committed.
