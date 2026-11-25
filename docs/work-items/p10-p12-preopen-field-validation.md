# P10-DATA-P12-PREOPEN-001 — finite field validation

Issue: [#727](https://github.com/rv198-star/Quanthus/issues/727). P10 owns source
facts and evidence; P12 owns runtime quote consumption. This batch prepares two
real sampling mornings, 2026-11-25 and 2026-11-26, after the missed Nov24 window.

## Current recovery checkpoint

2026-11-25 09:00 node run failed in prepare-choose with
HOLDING_IDENTITY_UNAVAILABLE before POOL/FREEZE. Bounded diagnosis showed the
join produced zero usable CN stock identities because the literal match
identifier_namespace equals CN_STOCK_CODE never matches the warehouse's
actual exchange-suffixed vocabulary (CN_STOCK_CODE:SSE, :SZSE, :BSE). The
selector now matches the CN_STOCK_CODE: prefix, the same convention every
other warehouse pipeline uses; the fixture emits the real namespaced form and
regression tests cover both the suffixed match and rejection of bare or
non-CN namespaces. Verified on the real 2026-11-24 T reference: both
holdings join cleanly and a bounded sample is selected. Owner extended the
finite coverage to the two actual trading days 2026-11-26 and 2026-11-27
(11-24 missed window and 11-25 pre-freeze failure remain sealed history); the
finite vocabulary, per-day T-reference map and plan sample_dates now carry
11-27 with no change to slots, budgets, excluded universe or freeze rules.

Task base is `0faca06923cab6130d7107319c7e21412615515c`, branch
`codex/issue-727-preopen-sampling`. Fixed source
`406cd7adc227f10769ab14df37991eef62f35579`, tree
`045c67e2de25ad361831ad1ef7776f0b72b847bd`, passed CN02 30 focused tests,
Ruff/format, strict mypy and full `make check`: 9,490 passed /35 skipped, exit0,
957.187 seconds. The two finite09:00 timers are installed and read back; actual
freeze/sample receipts are due Nov25/26, followed by field qualification.

Fixed18-file evidence:
`/Users/william/Documents/Codex/2026-11-24/quanthus-p10-preopen-preparation/delivery-v1/`.
`RESULT.json` SHA256 is
`23f0bb6f07ab9e6265e95ee13a223627fe3c255034f2053579bcaa408576e9ca`;
`SHA256SUMS` SHA256 is
`ed50740eab056f5913802ee386f16886e1beb1b23943866351c314d302a9f04e`.
The later metadata-only checkpoint reuses this exact executable/config/test
closure, with affected planning/history checks recorded separately.

## Input and selection

Use the frozen P12 fallback with SHA256
`4131dac0b0462203391826e5fb1c259c42218746668224981ed46fec7216d56c`:
holdings 002517 and 600000; actual buy candidates are empty. P12 E6 is pending.
The plan is `config/pipelines/p10-preopen-samples.v1.json`.

The selector combines pinned Base 2.5.1 listing/identifier/venue facts and the
actual preceding T core manifest and tradability-state projection. It checks
manifest/member SHA and size, active effective/knowledge intervals, code/MIC
identity and T's daily observation interval. Only domain-identical overlapping
listing facts are reused; conflicting identities are excluded, and a missing
required holding blocks preparation. It reads at most 8MiB of selected identity
and state members. It reads no price columns or research results.

Deterministic coverage selections include main boards, ChiNext, STAR, BSE,
main-board ST and source-nontradable observations; total size stays below18.
The #737 universe is excluded. A source-nontradable flag is not a formal halt.
Formal halt/ex-dividend/new-IPO event facts missing from this projection are
reported as coverage gaps. BSE calendar qualification remains unavailable;
its requested raw sample is separately marked. No missing category is inferred
from last price, name prefixes, zero volume or price changes.

## Execution and evidence

After fixed GitHub checkout and verification, invoke the project Python with
that checkout's `src` as `PYTHONPATH`:

```sh
python -m quanthus.p10_preopen_samples prepare-run \
  --pool config/pipelines/p10-preopen-samples.v1.json \
  --output /srv/agentdock/quanthus-ops/p10-preopen-727/2026-11-25
```

`prepare-run` starts between08:55 and09:15 on the named day, requires T's actual
core and the fixed calendar/fallback, and durably writes `POOL.json` and
`FREEZE.json` before waiting. Freeze requires both files before09:15. A completed
freeze may resume with `run --pool <day-root>/POOL.json --output <same-day-root>`;
an orphan pool may only complete its freeze before the cutoff. A late start
without that freeze makes no quote request.

Five slots are09:15:05,09:20:05,09:24:50,09:25:10,09:29:50 BJT. Each uses one
pool request to the existing `https://qt.gtimg.cn/q=` endpoint. An exclusive
request ledger is fsynced before transport; exact retries are rejected, even
after a crash. The dedicated root binds one pool, with a maximum of6 request
reservations. HTTP401/403/429 stops further calls. Redirects are rejected.
Responses are bounded to256KiB and8s. Missed slots are recorded and skipped.

Raw bytes/SHA, every tilde field, raw source clock, request/receipt times and
the layout reference from P12 commit `adda13da6c6d20382164f4c3dd0b8c286994bb78`
are retained. Invalid source clocks remain null; parser failures retain the
wire identity. Late receipts and clock regression have explicit reasons.
Auction, status letters and native limit fields await actual sample review.

The installed runtime is `quanthus-p10-preopen-727-20261125.timer` and
`quanthus-p10-preopen-727-20261126.timer`, at09:00 BJT on their named dates,
ending after the final slot with a1900s maximum lifetime. Each service checks
the fixed406 source/config bytes before startup. Existing daily-core, news and
P12 services/timers are separate. Schedule installation and future execution
have distinct receipts. The Owner-chat heartbeat now reviews receipts at09:35;
it starts no second source collection.

## Verification and retention

Use exact-base `maintenance_checks.py`, focused regressions, Ruff, strict mypy,
planning/exploration and append-only checks; run full `make check` on CN02 with
the existing project runtime. Keep actual results bound to commit/tree and
raw-log SHA; record failed attempts and unavailable checks separately.

The finite preparation question is whether node execution can preserve true
freeze/request/receipt evidence while the Owner chat is busy. Real field
qualification follows the two mornings. After their receipts and P12 review,
remove expired units; retain source, immutable pool/raw evidence and the PR
until its acceptance and dependent references are resolved.

Maintenance method: apply exact-source/runtime and manifest-compatibility
checks from `docs/maintenance/lessons.md`; Q-RECOVERY-COMPAT's production lane
recovery is outside this isolated sampler. The observed Nov24 miss is addressed
by finite node execution and a durable freeze, with synthetic clock/crash
counterexamples. Known preparation effort and remaining costs are in the
external checkpoint; no source requests were made on Nov24 for #727.
