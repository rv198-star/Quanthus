# Catalog Lineage V2 生产验收与收尾 — 2026-09-07

## 结论

`P3-DICT-003-D` 通过，`P3-DICT-003` 可关闭。

Catalog Lineage V2 已以结果版本 `3aa48377126e28e5e8151c68ff146d2e45894e78` 发布 API + Web。生产健康、ready、公开 `/catalog` 与匿名鉴权边界均通过。本次未修改 PostgreSQL schema、ODS/DWD 数据、Research ADM 物理产物、Replay/Event Worker、Feature/Fact 公式、Expert/Strategy 或执行权限。

## 生产版本

- production checkout: `3aa48377126e28e5e8151c68ff146d2e45894e78`
- API image: `quanthus-platform-kernel:git-3aa4837`
- API image digest: `sha256:a6dcb3473506c690d03f90bff576c822a3d75690fd2c1fa3fbafaa25bfcb45b6`
- Web image: `quanthus-platform-web:git-3aa4837`
- Web image digest: `sha256:76b57715ffa27cd029036fcb312005128fbd673b710ed27469a95f32090cd629`
- previous production revision: `d4e71585a58a229fa8c7ddbb9515e4038dcb2aeb`

Production status reports API/Web healthy, checkout clean, `/health` 200, `/ready` 200 with PostgreSQL=true, and Web ready 200. Replay/Event Worker images and data mounts remain unchanged.

Public smoke:

- `GET https://quanthus.oci.061718.xyz/catalog` -> 200
- `GET https://quanthus.oci.061718.xyz/ready` -> 200 / PostgreSQL true
- anonymous `GET /api/platform/v1/catalog/summary` -> 401 `authentication_required`

No production credential was used in this closeout to fetch an authenticated Catalog payload. Exact authenticated response semantics are covered by Platform API tests and browser E2E against the result code; this document does not overclaim a credentialed production payload smoke.

## Data / Catalog state

Deterministic Catalog v1.3:

- 748 entries / 10 internal maintenance gaps
- 57 ODS datasets
- 33 DWD tables / 524 DWD fields
- 4 governed Research ADM datasets / 76 ADM fields
- 9 Feature specs / 4 Label cards / 21 semantic concepts / 20 semantic families
- snapshot digest `sha256:d253b6baf4a2c53aaad8eeb52709804071bcce2c7431cbedf1383a7f2e8aa037`

Formal Data Lineage V2:

- 94 registered ODS/DWD/ADM asset nodes
- 70 participating assets
- 80 asset edges
- 57 ODS registered / 33 participating
- 33/33 DWD participating
- 4/4 Research ADM participating
- 65 ODS->DWD, 14 DWD->ADM, 1 governed ADM->ADM edge

Main Data Lineage contains no `process:*`, `projection:*`, `source-field:*`, Feature, Label, semantic or consumer nodes. Fields are nested coordinates on formal assets and transformations/evidence live on edges.

Definition Dependency remains separate at 30 nodes / 65 edges. Usage Dependency remains separate at 11 nodes / 6 edges. Static use is not actual Job-use evidence.

## Required counterexamples

`dwd_adjustment_factor.effective_from` resolves as the exact formal field chain:

```text
ODS adjustment_factors_pit.effective_date
  -> DWD dwd_adjustment_factor.effective_from
  -> ADM p4-event-research-adm.candidate_daily.factor_effective_from / adjustment_effective
```

`dwd_security_bar.close` resolves from formal ODS daily OHLCV through DWD into the registered P4, Structure MVP and Continuous Structure Facts ADM fields without engineering projection/process nodes.

`dwd_adjustment_factor.factor_value` has no fabricated exact downstream field mapping: it returns the complete registered asset context with `asset_context_only` semantics.

`price.sma20@1.0.0` is absent from Data Lineage and appears in Definition Dependency. P4 Event Library / Structure consumers appear only in Usage Dependency.

## Verification

Result code and final lean-image evidence boundary were verified with:

- focused Lineage V2 / Catalog backend suites: PASS
- deterministic Catalog snapshot check: PASS, 748 entries / 10 gaps
- Web lint: PASS
- Web unit/contract: 18/18 PASS
- Catalog-scoped TypeScript strict check: PASS
- Chromium E2E: 3/3 PASS (1440px, 390px, anonymous boundary)
- local lean Platform image build: PASS; in-image `DataIndicatorCatalog()` returned 748 entries / 80 asset edges
- Platform image boundary regression: PASS; restricted backtest/event-library modules remain outside the serving image
- `git diff --check`: PASS
- full repository `make check`: 1602 passed / 7 existing environment skips

The seven skips are the existing production-expert-artifact and PostgreSQL-test-DSN environment constraints; they are not counted as passes.

## Failed first deployment and rollback evidence

The first production attempt used result commit `17d53296daaa543283ae9b60035d765d60541b55`. The API container became unhealthy because the new static evidence loader attempted to read implementation source files that the intentionally lean Platform image does not ship. The governed deployment tool automatically rolled checkout/images back to `d4e7158`; post-rollback health/ready were verified healthy.

The repair did **not** weaken the Platform image boundary by shipping backtest/event-library runtime modules. Instead, reviewed source SHA-256 values are frozen in snapshot-bound Catalog authority configs. A development/review checkout verifies those hashes against source when the files exist; the lean serving image consumes only the frozen evidence identity. Result commit `3aa4837` then passed a real lean-image build/runtime probe and the second production deployment succeeded.

## Claim ceiling

“全链” means the full **registered formal-asset ancestor/descendant closure** for the selected asset or exact field mappings. It is not a claim that every possible physical ODS publication, temporary file, every field, or every historical Job instance has been exhaustively registered. Physical delta/hotfix/member identities remain edge evidence rather than nodes. Actual Job consumption, generation/use clocks and runtime versions remain operation/run audit authority, not static Catalog lineage.
