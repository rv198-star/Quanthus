# P10 acquisition prevention v1

Owner authorization: 2026-10-13. Additive nonexecution release.

Target-session membership, IPO/outdate metadata and CNINFO issuer identifiers are retained with original responses. Current metadata is never historical PIT proof. Snapshot projections have unknown freshness, no current no-change claim and no watermark advancement. Their affected uses stay blocked; qualified daily data proceeds under the explicit versioned policy.

Base and dated products remain immutable. New listing identities sit in new Daily reference files. Unknown issuer identity or ticker reuse fails construction. Disclosure corrections retain current knowledge versions; body revisions remain unknown without body hashes. Weekly financial continuation reuses all three statement envelopes and matching receipts through the existing adapter, without F10/shareholder or canonical unit/PIT admission.

Disclosure comparison binds both earlier DWD and original ODS observations. A source change that reverts to an older DWD value is still retained. Financial concept projection preserves all original observations, checks their semantics, and passes equivalent per-concept/time witnesses to the existing revision projection, avoiding a full observation sort. Weekly DWD waits for an in-flight weekly ODS producer to finish.

Prevention-enabled ODS selection and new minute references reject old releases without target-day universe and original-response evidence. For a same-date universe migration, build an isolated new daily-core root from the verified previous-date pointer, then promote its immutable package and exact catalog into the unchanged production root after that fresh daily product passes. Save the prior dated catalog/pointer separately and retain the old package; never relabel its old population as a refresh.

Complete 1d/1m/5m products carry verified domain supplements. Minimum R2 and R3=0 remain mandatory for joint release. OCI reads supplements under complete-product admission and actual visibility time. Daily pointer watcher/retry, closed-date announcements and weekly continuation run automatically. Outer failure hooks and queued-mail replay cover timeouts and transport failures.

## Deployment and rollback

Distribute code through GitHub into isolated exact-SHA checkouts. Run Linux focused tests, make check and actual-file shadow construction. Record old failures separately. Switch code only while producer jobs are idle; first save symlinks, environment, units and enabled timer state.

Pin collector SHA, prevention policy, ODS runner and domain continuation root. Enable the daily path/timer, natural-day/weekly timers and extended mail replay. Protect original response evidence from legacy pruning. Retain existing Base, old Daily and original complete identities.

Stop for input identity/FK failure, unavailable authoritative universe, R3, unresolved listing identity, an in-flight producer or insufficient space. Roll back saved links/config/units and stop the new continuation jobs. Keep truthful new immutable versions as separate evidence. Verify original core/complete pointers after rollback.

Acceptance requires the actual 60 late and 4 metadata correction keys, reused financial receipt/row closure, refreshed universe, automatic scheduling and transport-confirmed failure mail. A configured timer alone is not tomorrow's observed run.

## Proven suspended responses

A target-date response with explicit tradestatus=0 and valid isST may have missing volume/amount. Retain its status and original tokens; omit the price bar instead of fabricating values. An open security with missing required numeric tokens remains blocked. For an already failed status-only attempt, use the published `quanthus.pipelines.p10_suspended_status_recovery` CLI with a new attempt path and full code SHA. It verifies original dataset/raw hashes, exact target-day universe, venue and closed status, preserves prior failed evidence and original observation clocks, and makes zero supplier calls. It cannot repair open or unknown securities.

Observed full refreshed daily collection exceeded one hour; the CN01 daily cycle receives a 10,800 second budget with a 10,900 second systemd bound. This changes runtime scheduling only, not admission or resource gates. Production storage may use group-readable persistent NAS bind mounts; writing services require the exact mounts. Archive outbox/notification state remains local. Drain all writing triggers before cutover; code/pointer rollback retains NAS and new receipts unless a separately verified storage rollback is feasible.

## T0 operating dependencies and bounded recovery

Natural-day announcement scheduling uses the same current-date authoritative operating calendar as the daily/T+1 controllers. It validates the saved provider, observation time, row digest and date flags; sealed Base calendar rows are not a current operating calendar. An existing same-date calendar is reused, otherwise the existing calendar provider path obtains one. Trading days are explicitly handled by daily-core. A closed date requires continuous preceding calendar evidence before selecting the last open date's universe; missing dates fail closed rather than infer weekdays.

Each continuation invocation records its mode, actual target date, code revision, start time and systemd InvocationID. Its service stop hook may bind a business date only to that originating invocation. CN01 systemd 249 uses the stop context directly; a delayed OnFailure compatibility hook requires originating MONITOR identity and never queries a newer current invocation. Internal failure notification and the outer hook then reuse the existing business-date notification key. Unknown or stale invocation evidence retains an unresolved runtime-date alert; unrelated failures are not suppressed.

Under acquisition prevention v1, a daily-core event lane that fails its first universe-provider query may retry once after the parallel market lane is READY and its exact current-date universe verifies. Record the original attempt and verified universe manifest identity. Reuse the same lane job and original deadline; do not rerun prices, use a stale snapshot, reset the budget or relax admission. Reverification, process-start and cleanup failures remain terminal evidence. No third attempt is authorized by this recovery.

Deploy the daily-core code root and its continuation stop hook together after producers drain, preserving the previous code link and hook configuration. The independently pinned T+1 controller and archive code need not change. Focused synthetic tests and same-date cached-calendar execution are repair checks, not advance acceptance of tomorrow's collection, complete DWD or two-end delivery.
