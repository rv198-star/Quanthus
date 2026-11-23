# P10 forward announcement and news capture

| Field | Contract |
|---|---|
| Parent | [Permanent P10](12-permanent-data-line.md), ADR0016 |
| Design status | approved for Owner's bounded forward collection scope |
| Delivery gate | open; raw trial, source/consumer qualification separate |
| Work | P10-DATA-NEWS-FWD-001 #735, case collection #736; frozen definition #737 |
| Code | `src/quanthus/forward_news_capture.py` |
| Config | `config/pipelines/p10-forward-news-capture.v1.json` |
| Tests | `tests/unit/test_forward_news_capture.py` |

Owner authorized collection from 2026-11-24. The first week is a separately identified
trial; Base2.6 and its 11-30 boundary remain unchanged. Data from 2026-12-01 onward may be
qualified for Base2.7 through that release's own contract. This collector does not publish
to any Base, DWD, CURRENT, recommendation or account interface.

## Scope and sources

Use the existing/free Sina7x24 visible feed and CNINFO disclosure query. Capture source
native IDs, source links, titles/body, raw entity mentions, category/venue hints and raw
response bytes. The producer does not assert supplier relationships, sentiment labels or
security identities from names. Actual identities remain unbound until the qualified
`dwd_disclosure_subject`/`dwd_security_identifier` PIT closure is supplied.

An initial empty-stock CNINFO query scans the current-day provider result by pagination;
incremental overlap runs every5min and a full rescan runs every6h for older-page revisions;
provider pagination completion and all-A-share universe qualification are different fields.
Preserve any extra source instrument types rather than silently dropping unknown identities.
Each disclosure keeps both document ID and subject-specific source record ID.

The two sources run independently every five minutes, including night/preopen. Host calls
are spaced by at least two seconds. The versioned budget permits at most70 requests/240s,
60 announcement pages, four news pages, bounded wire/decompressed bytes and six documents per run (priority first, at most two background). Current provider count1274 makes this cheaper than per-security queries.
401/403 hold the source pending a material access-policy resolution; 429 persists Retry-After.
No credential, access-control, captcha or paid-source workaround is implemented.

## Clocks and immutable source identity

Source-reported publication, source update, actual response completion, original first seen,
version first seen, local commit interval and producer publication observation are separate.
All producer clocks are measured when those operations occur, with explicit UTC offsets.
Date-only/midnight CNINFO epochs stay null publication instants with the original raw value,
date and reason retained. Neither HTTP Date, ingestion time nor current time substitutes for
publisher time. Sina timestamps require the feed's explicit +0800 timezone evidence.

Wire gzip bytes and hashes remain source evidence; decompression is bounded. Normalized
identity covers semantic payload, normalizer code and full config digest. Exact duplicates
reuse their original member; changed source content/policy creates a new immutable member
linked to the previous member. Original first seen and a later version's availability remain
distinct. Source absence does not mean withdrawal; only observed changes are recorded.

Content-addressed objects and per-run receipts live in an explicit dedicated private root.
File fsync/exclusive-link commits preserve original source objects. SQLite is a local index
and body queue, not a warehouse or consumer admission authority. Each run binds code/config,
source response SHA, request/error/coverage counts and measured costs. Raw captures have no
READY qualification, and consumer availability stays unknown until a real channel receipt.

## Priority document sidecars

Title/category-driven priority covers earnings, contracts, holdings, reorganizations,
regulatory action, research records, inquiries and unusual volatility. Owner #735
comment7115182288 authorizes **all newly observed forward PDFs**, in two queues: priority
extracts Markdown; background archives only and waits behind priority. No historical PDF
backfill is part of this line. Metadata keeps an explicit `pdf_archive_pending` until success.

The CN02 existing rclone-a7 channel measured27,306,169,918,253B free on11-24 05:56:54+08.
A dedicated private Drive folder `quanthus-news-announcements-pdf`, ID
`1E9vA71wqRBAD9Ey7V0iHLpM-L_rSfnIu`, is independent of `quanthus-ods`/Base. No new
credential or sharing setting is created. Raw PDF bytes are not retained in permanent wire
or CAS stores: bounded TMP/staging → extraction (priority) → SHA/size → cloud
`sha256/<prefix>/<sha>.pdf` → full readback SHA256+size → durable receipt → local deletion.
Failure keeps the exact staged bytes and original receipt for retry without re-downloading.
A committed receipt survives a crash between local deletion and queue update. Pending jobs
are metadata-version identities; a source revision cannot replace the earlier pending PDF.

Staging is explicitly512MiB/256 files, with1GiB free floor. At80% staging occupancy or source
rate hold, background downloads pause first. At capacity all new PDF downloads pause and
alert, while metadata continues. Source403/access holds and429/Retry-After persist. Archive
calls have a separate64-request bound, one channel refresh via pinned existing rclone image,
and no broad Drive listing, `sync`, deletion or permission change.

Owner #735 comment7115271620 freezes the tiered mechanism through11-30. Baseline local
Poppler pdftotext22.02.0 uses layout extraction, conservative Chinese line joining and
repeated margin removal. Failed/garbled pages alone use local RapidOCR-ONNXRuntime1.4.4;
its three model hashes and isolated dependency versions are pinned. Four OCR pages/PDF,
100 text pages and finite process/output bounds protect the capture task; remaining pages
stay explicit partial/failed. There is no LLM/vision API or implicit fallback. The extractor
version includes normalization/model identity; cache key is (PDF SHA, extractor version).
Markdown is UTF8 and strictly below32KiB. Full PDFs remain available through archive receipts.

Trial reporting selects the first200 distinct priority PDF identities actually observed
11-24..30, plus about20 table checks. Store per-document elapsed time, text/OCR/failure route,
garbled-character rate, examined/recovered/failed pages, table-review disposition and layer
bytes. Measured upload bytes/times/failures and backlog support the week-end receipt; do not
substitute unit tests for those future measurements. No mechanism/threshold change during
trial is implied by an evaluation. Local extractor choice can be reviewed after the receipt.

From Base2.7, eligible12-01-onward news enters an independent ODS+DWD package group in
RELEASE-SET with its own cutoff/acceptance, sharing security/calendar versions with market
packages. News is not merged into market DWD. ADM stays outside Base. PDF bodies remain
in the deduplicated cloud store; ODS/DWD/Base refer to the archive inventory and receipts.
The11-24..30 trial keeps a separate identity and is excluded from Base2.6/2.7 formal scope.

## Frozen case boundary

Case #737 is VALIDATION_ONLY: no training, feature selection or threshold tuning. The
pre-auction definition and body SHA freeze10 main stocks,2 controls and2 index benchmarks;
after09:15 supplements never join that main set. Input source versions must actually have
been visible before2026-11-24T09:15+08. A revision first observed after cutoff is a result
capture even if its original document ID/publication date predates cutoff. Matching only
tags capture placement; PIT identity and case qualification remain separate.

P10 preserves inputs now, and assembles only after the declared window ends. Owner confirmed
11-05T00:00 to11-25T16:00 America/New_York and11-26T06:30+08 packaging. A-share bars reuse
existing daily-core; no new minute collection. Result-side package data is stored separately,
sealed after assembly and read only in a declared validation step with reader/time logging.
No result prices/returns/text are printed by capture logs, and this collector computes none
of the research metrics. P11's research is parked under#739 until P12#724 acceptance; capture
continues independently. The case never enters any Base.

## Activation and recovery

Use an isolated fixed GitHub code revision, explicit code SHA and dedicated run root. A
source-byte change stops the old loop; code/config/runtime updates get a new receipt.
One lock prevents overlap. A STOP file stops only this collector. The new source job does
not reuse or modify daily-core/#726/#727 schedules. Report a stored config, an executed
capture, an active loop and an accepted data product as their actual separate states.

Retain original failures, partial batches and source hashes. Current unresolved source facts
include date-only CNINFO publication, full universe/PIT mapping and consumer channel clocks.
These block dependent qualified uses, not independently captured raw evidence.
