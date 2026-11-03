# Observed closing-auction supplement

Use only for a source-held Daily whose complete BaoStock 48-bar grid omits an independently observed closing auction. Never edit the sealed predecessor or infer a residual from daily bars.

Inputs: held attempt receipt, original captures, source-held package, and captured Tencent responses with URL, SHA256 and timezone-aware request/response clocks in CAPTURE.json. SSE main-board detail units are lots of 100 shares. KCB detail lots are rounded, so use independent 14:59/15:00 cumulative exact-share observations. Exclude post-close trades. Amount preserves supplier-reported aggregate precision; do not label it exchange-exact.

`python -m quanthus.pipelines.securities_warehouse.closing_auction_repair --attempt <held-attempt> --evidence <capture-root> --output <new-source-package> --revision <Git-commit>`

Replay verifies both archived source streams, former R3 identities, content hashes, capture index, exact daily share volume and terminal close. Only the terminal bar changes; phase is continuous_and_closing_auction. Every bar referencing a compound raw envelope names the compound source in DWD. Knowledge time advances to new evidence/seal time. Rebinding this new representation through the legacy volume-only path explicitly refuses it rather than dropping evidence. Rebind-derived predecessors are not supported by this bounded builder.

Run native source verification and full Daily build/verify before publishing a new attempt. The old attempt remains intact. Pin the accepted processing commit in the source manifest and run receipt.

For a code-only runtime refresh, build a tracked source bundle with container_code.bundle from a GitHub-fetched commit, then use ops/data/Dockerfile.code-refresh with an existing immutable RUNTIME_IMAGE digest and the same PIPELINE_REVISION. It changes no dependencies or collector code. verify_bundle rejects obsolete/extra/missing members. Preserve the prior deployment JSON and image for rollback; do not edit an existing release.
