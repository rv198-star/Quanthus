# Data & Indicator Catalog post-acceptance audit production closeout — 2026-09-07

| Field | Value |
| --- | --- |
| Scope | Independent post-acceptance audit of `P3-DICT-001` Data & Indicator Catalog |
| Audit baseline | `e9d1feba5277faa0a4eb2651ea0fa9a46e023458` |
| Semantic/operations repair | `8d780735eb1a18a33357a0f6e28814a0666c856e` |
| Final Web presentation repair | `d47abf8544e9612b75dd77ab2c166a9c75abfda2` |
| Audit disposition | PASS |
| Trading / formula authority change | NONE |

## 1. Findings closed

The audit did not simply re-run the original tests. It reviewed source authority, semantic
projection, runtime-status ownership, search/filter behavior, Platform API/RBAC, Web presentation,
release packaging and production Web-image retention.

Five findings were closed:

1. **Non-equivalent legacy links were incorrectly suppressing executable-spec gaps.**
   `related_not_equivalent` SMA/RSI links are now discovery-only; only an explicit
   `exact_equivalent` executable binding may suppress `executable_spec_missing`.
2. **Accepted Structure runtime coverage was not projected.** The Structure blueprint now owns an
   explicit seven-concept runtime-binding list backed only by B3-B5
   `accepted_bounded_v2 / real_dwd_ab` evidence. No code-name or prefix inference is allowed.
3. **Structure blueprint B8 was stale.** It now records the accepted Data & Indicator Catalog while
   retaining the explicit claim that many reserved indicator concepts still lack executable specs.
4. **The Web status selector and status presentation were incomplete.** The UI now exposes the full
   supported lifecycle/implementation/verification vocabulary and labels the four independent axes:
   registration/lifecycle, implementation, verification and accepted scope.
5. **Production Web images lacked bounded retention.** Governed Web builds now retain the target
   `quanthus-platform-web:git-*` release and every image reference still owned by any running or
   stopped container, while removing only older unreferenced Web release tags. The script uses no
   `--force`, global `docker image prune`, or volume cleanup.

The detailed pre-production repair record remains in
`data-indicator-catalog-post-acceptance-audit-20260907.md`.

## 2. Current deterministic Catalog identity

The schema remains stable while the projection algorithm is versioned explicitly:

```text
schema: quanthus.data-indicator-catalog.v1
builder: data-indicator-catalog-builder-v1.1
entries: 664
gaps: 10
snapshot digest:
sha256:a21f3595d551378221054a7291652454e61f8281a91d58f766b66776b2692f6b
```

The two additional gaps are deliberate corrections for generic SMA and RSI concepts. Existing
legacy `price.sma20`, `price.sma60` and `price.rsi14` Feature specs remain unchanged and discoverable,
but their explicit non-equivalence no longer masquerades as a generic executable specification.

Seven Structure concepts have bounded accepted runtime projections through exact blueprint bindings:

- `structure.anchor.rolling-high`
- `structure.anchor.rolling-low`
- `structure.geometry.signed-distance`
- `structure.interaction.close-cross-above`
- `structure.interaction.close-cross-below`
- `structure.interaction.close-reclaim`
- `structure.episode.reclaim-sequence`

Other reserved Structure concepts remain `declared_only / contract_only` unless a future governing
blueprint explicitly accepts them.

## 3. Repository verification

Focused Catalog/Structure/Platform/document audit gate:

```text
95 passed
Catalog snapshot --check: PASS
Ruff on changed Python: PASS
bash -n governed Web build / Web image-GC scripts: PASS
git diff --check: PASS
```

Web gate after the final status-axis presentation repair:

```text
npm run lint: PASS
vinext build: PASS
routes: /, /catalog, /operator
npm test: 17 passed / 0 failed
```

Full repository gate:

```text
make check PYTHON=/var/lib/devspace/worktrees/repo-d9c9fcbe/.venv/bin/python
1555 passed
7 skipped
```

The host system `python3` currently lacks pytest, so the repository's existing test virtualenv was
supplied through the Makefile's supported `PYTHON=` override. The seven skips remain the existing
unmounted production expert artifacts and unconfigured PostgreSQL integration fixtures; they are not
counted as passes.

## 4. Production deployment and Web-image cleanup

The semantic/operations repair was deployed through the governed production tool:

```text
revision: 8d780735eb1a18a33357a0f6e28814a0666c856e
API image: quanthus-platform-kernel:git-8d78073
API image digest: sha256:8733a8a7ab803bc6586df12da7b0064548d10fb5b33e7dfb25657ae20f9fb0b2
Web image: quanthus-platform-web:git-8d78073
Web image digest: sha256:a98116de2341b45e460258086c3f9b9efd9f3931b10d3450713b7cc5597dff62
```

The final UI presentation repair was then deployed Web-only:

```text
production checkout revision: d47abf8544e9612b75dd77ab2c166a9c75abfda2
active API image: quanthus-platform-kernel:git-8d78073
active Web image: quanthus-platform-web:git-d47abf8
active Web digest: sha256:6ff8cadaf845278bebe8125762b25b1757c2f2ca2025969f54301b5f598ffb1d
```

Both governed Web builds completed the new scoped image-retention script successfully. Therefore
obsolete unreferenced `quanthus-platform-web:git-*` release tags were removed at build time while the
then-active rollback image and the target release were retained. The admitted production-status API
does not enumerate the rootful daemon's entire image inventory, so this evidence intentionally does
not claim an exact remaining tag count.

No Docker volume, DWD mount, unrelated image repository, running container, or stopped-container
rollback reference is in the GC deletion scope.

## 5. Production smoke

Final production status:

```text
production_dirty: false
health: 200 / healthy
ready: 200 / postgres=true
web_ready: 200
API: healthy / running
Web: healthy / running
```

Public edge checks:

```text
GET /catalog                                      -> 200
GET /api/platform/v1/catalog/summary (no token)  -> 401
GET /openapi.json                                 -> four Catalog GET routes present
```

The current repository projection independently revalidated at the same closeout point:

```text
catalog snapshot exact: entries=664 gaps=10
digest=sha256:a21f3595d551378221054a7291652454e61f8281a91d58f766b66776b2692f6b
```

## 6. Claim ceiling

PASS means the Catalog is a truthful, deterministic, read-only discovery surface for its explicitly
registered sources and accepted runtime bindings. It does not imply that all possible market data or
technical indicators are catalogued, that reserved SMA/RSI/MACD/CCI/KDJ/ATR concepts have accepted
executable formulas, or that a Catalog relation creates strategy or execution authority.

This audit changes no DWD/ODS data values, accepted Feature formulas, Label rules, Facts v2 numerical
semantics, expert policy, P2 behavior, Shadow/paper/live authority, or historical accepted Run.
