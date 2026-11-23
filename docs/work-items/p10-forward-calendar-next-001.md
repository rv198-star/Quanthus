# P10-DATA-CALENDAR-NEXT-001 — independent forward calendar

| Field | Value |
| --- | --- |
| Work item | [#743](https://github.com/rv198-star/Quanthus/issues/743), native Task under permanent #478 |
| Parent | [P10 LLD 12](../design/lld/12-permanent-data-line.md) |
| Design status | approved for this bounded P12 request |
| Delivery gate | open; P12 #719/#723 owns reader integration and receipt |
| Code / tests | `src/quanthus/p10_forward_calendar.py`, `tests/unit/test_p10_forward_calendar.py` |
| Evidence | `/Users/william/Documents/Codex/2026-11-24/quanthus-p10-forward-calendar/` |

## Purpose and boundary

The observed-only daily calendar cannot answer the next exchange session. Supply an independent
date-only product for P12, without changing retained Base/daily manifests or their timers.
This first version covers 2026-11-24 through 2026-12-31. The current official weekly rules are
effective from July 6; the builder does not extend that authority to earlier rules or 2027.

Source qualification is pinned to four exact official originals: SSE/SZSE 2026 annual notices
and their current weekly trading rules. The source SHA identities live in the narrow module.
SSE clause 2.4.1 and SZSE clause 2.3.1 establish Monday-Friday subject to announced closures.
Annual closures are parsed from each venue's own complete notice. Civil makeup weekends remain
closed. Source review and all date construction operate without market rows.

BSE's original notice/rule URLs returned redirect loops from local and CN02 checks. Its venue
entry stays `unavailable`; a qualified SSE/SZSE calendar is delivered independently. A later BSE
recovery or new year uses a separately verified immutable version, without changing this one.

## Consumer contract

Discovery is `CURRENT_FORWARD_CALENDAR.json` below the delivered root. Its relative `descriptor`
and `publication` paths point into one immutable version and each has a SHA256. Consumers bind the
version and verify these byte digests before reading. The descriptor also has `content_sha256`,
calculated over its canonical JSON with that field omitted (`encoded` defines canonical bytes).

`coverage.start/end` are inclusive calendar dates in `Asia/Shanghai`. Consumers select their
named entry in `venues`: an available entry contains all `sessions` and a `next_session` map
for every covered calendar day. A value is the first strictly later covered session, or null
with `next_session_unavailable[date]`. Unsupported venues and dates outside coverage remain
unavailable; never substitute another venue or calculate a weekday fallback. In particular,
December 31 has no covered successor and returns `NEXT_SESSION_OUTSIDE_COVERAGE`.

The product reports `PARTIAL` while BSE is unavailable. P12 can admit the available venues for
its declared purpose and isolates a missing venue to affected securities.

## Clocks and publication

Each source retains URL, publication **date**, raw SHA/size, revision/clause and actual HTTP
receipt time. Exact upstream `source_published_at` and `source_available_at` stay null with a
date-only reason. `source_retrieved_at` is the actual response completion observed by P10;
old official publication dates do not become historical internal acquisition clocks.

`built_at` records construction. It is not product visibility. After staging is renamed into
the version directory, the publisher records `publication_commit_at`, verifies the descriptor
and source readback, then records `channel_visible_at` in `PUBLICATION.json`. The pointer exposes
these as `published_at` and `available_at`. P12 separately records its actual discovery/receipt.
As-of use requires the delivered availability at or before the consumer's knowledge cutoff.

Publication writes only a new version and this new product's pointer. Existing versions are
immutable. Source receipt, payload and publisher clocks are separate; unknown source clocks
are never replaced with `now`. The CLI only constructs from previously captured originals:

```bash
PYTHONPATH=src python -m quanthus.p10_forward_calendar \
  --sources /absolute/source-root --receipts /absolute/SOURCE-RECEIPTS.json \
  --output /absolute/forward-calendar-root --version p10-calendar-20261124-v1 \
  --code-revision FULL_GIT_SHA
```

## Finite verification and remaining work

ADR 0016 applies to venue-local availability: BSE failure does not stop SSE/SZSE delivery.
The maintenance lesson Q-RECOVERY-COMPAT is not a same-date lane rebuild here: old manifests,
deadlines and source timestamps stay intact. Focused probes cover holiday/weekend boundaries,
year-end, unknown venue, before-visibility cutoff, original SHA mismatch, semantic tampering and
immutable publication. Applicable local lint/type, exact-base maintenance/planning checks and
`make check` precede handoff; existing baseline failures are reported separately.

P12's new reader and actual receipt/acceptance remain with P12. This task adds no market fetch,
rule algorithm, scheduler, strategy activation or change to Base 2.6. New calendar authority is
a new versioned input; current use of source-observed historical calendars remains unchanged.
