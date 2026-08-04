# ODS / DWD 数据字典

| 字段 | 值 |
| --- | --- |
| 设计状态 | approved |
| 交付状态 | Base accepted；日更部分覆盖；DWD 周更 open |
| 上级设计 | [`../01-data-foundation.md`](../01-data-foundation.md) |
| 实例身份 | `config/releases/v2-build202607.json` |
| DWD schema 权威 | `config/pipelines/dwd-domain-model.v1.json` + `dwd-domain-model.v1.1-extension.json` |
| 适用截止日 | 2026-07-31 |

## 1. 用途和权威边界

本文回答四个问题：ODS 中保存了什么，DWD 中有哪些业务对象，每张 DWD 表的粒度和键
是什么，以及字段应如何解释。ODS 是源忠实层，同一业务可能同时保存 Parquet、SQLite、
JSON、原始二进制、回执和修复证据，因此不存在一张统一的 ODS 逻辑大表。DWD 是规范化
业务层，33 张表必须遵守固定 schema、键、时间和血缘契约。

精确类型、键和外键以机器契约为准；本文是其中文阅读投影。Base manifest 证明某个实例
实际包含哪些数据和行数，但不能修改 schema 定义。日更和周更只增加不可变增量或修正
分区，不修改 Base。

表级和字段级来源、全量构建 DAG、当前日更/周更覆盖见
[`ods-to-dwd-lineage.md`](ods-to-dwd-lineage.md)。

## 2. Base V2 实例

| 层 | 包 | Manifest SHA-256 | Content digest | 规模 |
| --- | --- | --- | --- | --- |
| ODS | `ods-base-202607-v2` | `sha256:7f74a7655f3f86982b4e662f55b12aff4fa2f28ee23cf9b5db530f9f2aa0955b` | `sha256:c95adef6624d5d904a53fc93b27d1e2d78ed4a908a653661d89c47147380ac01` | 112 datasets；21,396 files；13,643,600,642 bytes |
| DWD | `dwd-base-202607-v2` | `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f` | `sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9` | 33 tables；468,268,180 rows；14,842,590,066 payload bytes |

## 3. ODS 字典

### 3.1 ODS 记录层次

| 层次 | 标识 | 含义 |
| --- | --- | --- |
| Base | `bundle_id` | 一个不可变 ODS 集合，本版为 `ods-base-202607-v2` |
| Dataset | `dataset_id` | 逻辑数据集；是查询目录和血缘映射的稳定入口 |
| Part | `path` | Dataset 下的物理文件；记录 format、role、SHA-256、bytes 和来源路径 |
| Release | `daily_releases[]` | 被 Base 收录的不可变日发布及其 READY/manifest 证据 |
| Inventory | `source_inventory[]` | 原源路径的 included/excluded 分类及原因 |

`role` 描述文件在 ODS 中承担的职责，不代表下游一定生成业务行。`increment` 是日发布成员；
`source_*` 是源业务数据或源证据；`*_lineage`、`receipt`、`status` 和 `quality_evidence`
通常只参与审计。是否进入 DWD 必须以血缘文档中的 `modeled / ods_only / superseded /
deferred` 映射为准。

### 3.2 主要 ODS 业务 schema 家族

下表记录 Build202607 中实际 Parquet/SQLite 元数据。字段为源忠实字段，允许可空、字符串
日期或提供方单位；DWD transform 负责类型、单位、身份和 PIT 规范化。

| 家族 / 代表 dataset | 主要字段 | 说明 |
| --- | --- | --- |
| 证券身份 `security_master` | `security_id, stock_code, exchange, name, list_date, delist_date, status, bs_code, as_of, source, built_at` | `stock_code` 不是仓库主键；`security_id` 仍需经 DWD 身份桥规范化 |
| 采集股票表 `stock_list` | `stock_code, stock_name, market` | 采集范围，不是身份权威；被 `security_master` 取代 |
| 日行情 `daily_ohlcv_unadjusted` | `stock_code, date, open, high, low, close, volume, amount, turnover_rate, pct_change, adj_mode, adj_version, source, built_at` | DWD 只接收未复权原子行情；复权行情为下游投影 |
| 分钟行情 `minute_1m_research_archive`, `minute_5m_formal_archive` | `stock_code, ts, open, high, low, close, volume, amount` | 1m/5m 共享业务形状；时间标签由 transform 规范为区间起点 |
| 估值 `valuation_daily` | `stock_code, date, pe_ttm, pb, ps_ttm, pcf_ttm, total_share, total_mv` | 拆成 `measure_type/measure_value/methodology` 长表 |
| 指数行情 `index_daily` | `index_code, date, open, high, low, close, volume, amount` | 指数身份与指数区间事实分离 |
| 交易日历 `trading_calendar` | `exchange, trade_date, is_open, session, source, built_at` | 历史缺口由 `trading_calendar_history_hotfix` 只补父 Base 不存在的键 |
| ST 状态 `st_status_pit` | `interval_id, stock_code, exchange, status_label, is_st, effective_start, effective_end, announcement_date, knowledge_cutoff, source, source_revision, event_kind, confidence, notes, built_at` | 有效时间和可知时间分开保留 |
| 停复牌 `suspensions` | `suspension_id, stock_code, suspend_start, suspend_end, resume_expected, term_text, reason, source, as_of_query_date, knowledge_cutoff, built_at` | 进入 listing status 并参与可交易状态计算 |
| 涨跌停规则 `price_limit_regime` | `interval_id, stock_code, exchange, board, effective_start, effective_end, upper_limit_pct, lower_limit_pct, listing_window_rule, no_limit_days, price_tick, rounding_rule, rule_basis, priority, announcement_date, knowledge_cutoff, source_revision` | DWD 使用有优先级的有效期规则，不复制为每证券一行 |
| 公司行动 `corporate_actions` | `action_id, stock_code, ex_date, announce_date, action_type, cash_per_share, bonus_shares, transfer_shares, progress, source, source_row_key, built_at` | 行动主体、日期和条款进入不同 DWD 字段/表 |
| 分红历史 `dividend_history` | `stock_code, announce_date, dividend_per_share, bonus_shares, transfer_shares, ex_dividend_date, progress` | 与公司行动对账，不直接覆盖行动权威 |
| 复权因子 `adjustment_factors_pit` | `stock_code, effective_date, fore_adjust_factor, back_adjust_factor, adjust_factor, adj_version, knowledge_cutoff, source, built_at, visibility_origin, source_revision` | 因子与其行动/证据输入分表保存 |
| 财务摘要 `financial_quarterly` | `stock_code, report_date, publish_deadline, roe, revenue, net_profit, revenue_growth_yoy, profit_growth_yoy` | 摘要/对账输入，不替代完整三表 |
| 完整财务封装 `fundamentals_financial_statements_parquet` | `security_id, stock_code, statement_type, provider_source, requested_periods, period_count, earliest_report_date, latest_report_date, latest_publish_date, latest_update_time, raw_payload_gzip, raw_payload_sha256, raw_payload_bytes, market_date, source_revision, acquired_at` | transform 无损解压并展开 provider-native 科目和事实 |
| 行业快照 `fundamentals_industry_membership_parquet` | `security_id, stock_code, stock_name, industry_code, industry_name, classification, provider_update_date, market_date, source_revision, acquired_at` | 只从首个可辩护快照起生效，不向过去伪造历史 |
| F10 原文 `fundamentals_raw_f10_parquet` | `security_id, stock_code, category, source_filename, source_start, source_length, content, content_sha256, provider_updated_date, market_date, source_revision, acquired_at` | `股本结构` 和 `股东研究` 由版本化解析器展开；原值/单位继续保留 |
| 股东户数 `shareholder_count` | `code, name, end_date, shareholder_count, pre_shareholder_count, change_num, change_ratio, avg_hold_num, avg_market_cap, total_a_shares, total_market_cap, hold_notice_date, report_label, source, knowledge_cutoff, fetched_at` | DWD 明确公司/证券观察范围，不静默合并 |

`announcement_archive` 是 SQLite 容器。业务表为：`announcements(code, name, title,
publish_time, ann_id, url, category, impact)`、`fin_abstract(code, report_date, ...财务指标)`、
`holder_changes(exchange, code, person, actor, relation, shares_before, change_shares, avg_price,
shares_after, change_ratio, reason, change_date, disclosure_date, row_key)` 和
`shareholder_count(...)`。`crawl_progress`、`fin_progress`、`holder_progress`、`info_meta`、
`shareholder_count_progress/revision` 是采集进度或修订证据，不是独立业务事实。

### 3.3 完整 ODS dataset 目录

以下 112 个 id 来自 ODS Base V2 manifest；括号内为 manifest role 和数量。

- `accepted_source_lineage` (7): `source_evidence_adjustment_factor_pit_v1`, `source_evidence_delist_placeholder_decisions`, `source_evidence_delisted_history_decisions`, `source_evidence_p1_formal_pack`, `source_evidence_shareholder_export`, `source_evidence_st_pit_accepted`, `source_evidence_volume_amount_null_decision`
- `canonical_reference` (1): `trading_calendar_history_hotfix`
- `evidence` (1): `security_gap_audit`
- `increment` (24): `daily_events_announcements_delta`, `daily_events_corporate_actions_delta`, `daily_events_dividends_delta`, `daily_events_factor_evidence_delta`, `daily_events_financial_disclosures_delta`, `daily_events_suspensions_delta`, `daily_intraday_1m_after_hours_fixed_price_receipts`, `daily_intraday_1m_after_hours_fixed_price_trades`, `daily_intraday_1m_intraday_bars`, `daily_intraday_1m_intraday_coverage`, `daily_intraday_1m_intraday_meta`, `daily_intraday_1m_trading_calendar`, `daily_intraday_5m_intraday_bars`, `daily_intraday_5m_intraday_coverage`, `daily_intraday_5m_intraday_meta`, `daily_intraday_5m_trading_calendar`, `daily_market_adjustment_evidence_delta`, `daily_market_daily_ohlcv_none_delta`, `daily_market_daily_status_observation_delta`, `daily_market_index_daily_delta`, `daily_market_security_master`, `daily_market_stock_list`, `daily_market_trading_calendar`, `daily_market_valuation_daily_delta`
- `legacy_source_lineage` (2): `legacy_dataset_manifest`, `legacy_dataset_manifest_metadata`
- `lineage` (1): `daily_release_metadata`
- `provider_receipt` (5): `daily_provider_receipts`, `fundamentals_financial_statements_parquet_receipts_parquet`, `fundamentals_industry_membership_parquet_receipts_parquet`, `fundamentals_raw_f10_parquet_receipts_parquet`, `valuation_history_receipts`
- `provider_request_scope` (1): `security_hotfix_stock_list`
- `provider_source_evidence` (9): `daily_provider_status`, `daily_provider_window`, `fundamentals_financial_quarterly_parquet`, `fundamentals_financial_statements_parquet`, `fundamentals_financial_statements_parquet_meta_json`, `fundamentals_industry_membership_parquet`, `fundamentals_industry_membership_parquet_meta_json`, `fundamentals_raw_f10_parquet`, `fundamentals_raw_f10_parquet_meta_json`
- `quality_evidence` (2): `coverage_report`, `daily_assembly_summary`
- `retained_immutable_source_gap_fill` (1): `source_evidence_weekly_full_fundamentals_20260731`
- `retained_immutable_source_lineage` (1): `source_evidence_weekly_corrections_20260724`
- `source_acquisition_lineage` (2): `source_evidence_valuation_history_july_repair_checkpoints`, `valuation_history_july_repair_metadata`
- `source_acquisition_receipts` (1): `valuation_history_july_repair_receipts`
- `source_contract` (3): `adjustment_contract_qfq`, `adjustment_contract_quanthus`, `source_precedence`
- `source_evidence` (6): `adjustment_factors_pit`, `announcement_archive`, `corporate_actions`, `dividend_history`, `financial_quarterly`, `shareholder_count`
- `source_exception_evidence` (2): `delist_boundary_exceptions`, `factor_pit_exceptions`
- `source_identity` (3): `security_hotfix_scope`, `security_master`, `stock_list`
- `source_lineage` (9): `daily_provider_metadata`, `index_daily_metadata`, `minute_1m_checkpoint`, `minute_1m_status`, `minute_5m_checkpoint`, `minute_5m_status`, `parent_repair_lineage`, `reference_build_metadata`, `valuation_history_metadata`
- `source_market_archive` (2): `minute_1m_research_archive`, `minute_5m_formal_archive`
- `source_market_data` (9): `daily_ohlcv_adjusted`, `daily_ohlcv_none_hotfix`, `daily_ohlcv_unadjusted`, `index_daily`, `minute_1m_hotfix`, `minute_5m_hotfix`, `valuation_daily`, `valuation_history_hotfix`, `valuation_history_july_repair`
- `source_reconciliation` (1): `daily_source_reconciliation`
- `source_reference` (3): `announcement_orgid_map`, `price_limit_regime`, `trading_calendar`
- `source_status_evidence` (3): `daily_status_observation_hotfix`, `st_status_pit`, `suspensions`
- `supplementary_source_evidence` (13): `source_evidence_adjustment_factor_exceptions`, `source_evidence_adjustment_factor_pit`, `source_evidence_baostock_none_reference`, `source_evidence_delist_boundaries`, `source_evidence_delisted_coverage`, `source_evidence_invalid_ohlc`, `source_evidence_missing_none_history`, `source_evidence_none_tail_repairs`, `source_evidence_price_limit_regime`, `source_evidence_st_baostock_raw`, `source_evidence_st_semantics_audit`, `source_evidence_suspension_repairs`, `source_evidence_tradability_c2_s1`

## 4. DWD 字段约定

### 4.1 所有表继承的血缘字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `record_source` | string | 产生该业务记录的 ODS dataset/成员身份 |
| `source_record_id` | string | 源记录稳定 id 或规范化源键 |
| `source_revision` | string | 源修订/发布身份 |
| `source_artifact_sha256` | string | 实际读取源 artifact 的 SHA-256 |
| `knowledge_from` | UTC timestamp | 该版本最早可被系统知道的时刻 |
| `knowledge_to` | nullable UTC timestamp | 该知识版本失效时刻；空表示仍开放 |

### 4.2 时间 profile

| Profile | 字段 | 适用对象 |
| --- | --- | --- |
| `effective_interval` | `effective_from`, `effective_to?` | 身份、代码、上市、状态、行业和规则有效期 |
| `effective_event` | `effective_at`, `announced_at?` | 公告、公司行动、持仓变化等事件 |
| `market_observation` | `observation_start`, `observation_end?` | 行情、估值、可交易状态和资本观察 |
| `reporting_period` | `period_start?`, `period_end`, `published_at?` | 财报和股东户数 |
| `governance_event` | `affected_effective_at`, `detected_at` | 缺口和质量例外 |

所有有效区间和知识区间使用半开区间 `[from, to)`。字段类型后的 `?` 表示 nullable。
`decimal_P_S` 表示精确十进制定点数，不允许把金额、股数或比例无依据地混加。

## 5. DWD 33 表字典

“表特有字段”之外，每张表还包含第 4 节的血缘字段和其 time profile 字段。`row_key`
是物理版本行唯一键；业务键和完整外键以机器契约为准。英文粒度来自机器契约，避免中文
转述改变精确含义。

| 表 | 域 / 角色 / owner | 精确粒度 | Row key | 分区 | Build202607 行数 | 表特有字段 |
| --- | --- | --- | --- | --- | ---: | --- |
| `dwd_listed_company` | 上市公司 / 核心 / #57 | one recognized listed-company identity version | `company_id + effective_from + knowledge_from` | - | 5,868 | `company_id:string`, `display_name:string`, `company_type:string`, `resolution_scope:string` |
| `dwd_company_identifier` | 上市公司 / 支撑 / #57 | one namespace-qualified company identifier interval | `identifier_namespace + identifier_value + effective_from + knowledge_from` | - | 5,868 | `company_id:string`, `identifier_namespace:string`, `identifier_value:string`, `identifier_authority:string` |
| `dwd_security` | 上市公司 / 支撑 / #57 | one security definition and listed-company association version | `security_id + effective_from + knowledge_from` | - | 5,868 | `security_id:string`, `company_id:string`, `instrument_type:string`, `instrument_currency:string` |
| `dwd_security_identifier` | 上市公司 / 支撑 / #57 | one namespace-qualified security identifier interval | `identifier_namespace + identifier_value + effective_from + knowledge_from` | - | 17,274 | `security_id:string`, `identifier_namespace:string`, `identifier_value:string`, `venue_id:string?` |
| `dwd_listing` | 上市公司 / 支撑 / #57 | one interval admitting a security to a venue under a listing code | `listing_id + effective_from + knowledge_from` | - | 5,868 | `listing_id:string`, `security_id:string`, `venue_id:string`, `listing_code:string`, `board_code:string`, `listing_currency:string`, `lot_size:int64?` |
| `dwd_trading_venue` | 上市公司 / 支撑 / #57 | one effective trading-venue reference version | `venue_id + effective_from + knowledge_from` | - | 3 | `venue_id:string`, `mic_code:string`, `venue_name:string`, `country_code:string`, `timezone:string` |
| `dwd_security_bar` | 证券市场观察 / 核心 / #58 | one listing interval and unadjusted price basis | `listing_id + observation_start + interval_size + price_basis + knowledge_from` | interval/year/month | 264,385,668 | `listing_id:string`, `calendar_session_id:string`, `interval_size:string`, `price_basis:string`, `currency:string`, `open/high/low/close:decimal_38_10`, `volume/turnover:decimal_38_10?`, `trade_count:int64?` |
| `dwd_security_valuation` | 证券市场观察 / 核心 / #58 | one security valuation measure and methodology at one observation time | `security_id + observation_start + measure_type + methodology + knowledge_from` | year/month | 72,829,458 | `security_id:string`, `listing_id:string?`, `measure_type:string`, `measure_value:decimal_38_10?`, `currency:string?`, `methodology:string`, `fiscal_basis:string?` |
| `dwd_adjustment_factor` | 证券市场观察 / 支撑 / #60 | one security effective-date adjustment method and revision | `adjustment_factor_id + effective_from + knowledge_from` | - | 139,947 | `adjustment_factor_id:string`, `security_id:string`, `adjustment_method:string`, `adjustment_direction:string`, `factor_value:decimal_38_18`, `anchor_date:date32`, `evidence_set_digest:string` |
| `dwd_adjustment_factor_input` | 证券市场观察 / 支撑 / #60 | one accepted corporate-action or source-evidence input to one adjustment-factor revision | `adjustment_factor_id + input_type + input_evidence_digest + effective_from + knowledge_from` | - | 139,947 | `adjustment_factor_id:string`, `input_type:string`, `corporate_action_id:string?`, `input_evidence_digest:string`, `input_revision:string` |
| `dwd_market_index` | 证券市场观察 / 支撑 / #58 | one effective benchmark-index definition version | `index_id + effective_from + knowledge_from` | - | 3 | `index_id:string`, `index_code:string`, `index_name:string`, `base_currency:string`, `methodology_version:string?` |
| `dwd_index_bar` | 证券市场观察 / 核心 / #58 | one market-index interval | `index_id + observation_start + interval_size + knowledge_from` | interval/year/month | 23,257 | `index_id:string`, `calendar_session_id:string?`, `interval_size:string`, `currency:string`, `open/high/low/close:decimal_38_10`, `volume/turnover:decimal_38_10?` |
| `dwd_trading_calendar` | 可交易状态 / 支撑 / #72 | one venue trade date and session type | `venue_id + trade_date + session_type + knowledge_from` | year | 28,214 | `calendar_session_id:string`, `venue_id:string`, `trade_date:date32`, `session_type:string`, `is_open:bool`, `open_at/close_at:timestamp_us_utc?`, `calendar_reason:string?` |
| `dwd_security_status_interval` | 可交易状态 / 支撑 / #59 | one typed status interval for one security | `security_id + status_type + effective_from + knowledge_from` | - | 16,479 | `security_status_id:string`, `security_id:string`, `status_type:string`, `status_value:string`, `reason_code:string?` |
| `dwd_listing_status_interval` | 可交易状态 / 支撑 / #59 | one typed status interval for one listing | `listing_id + status_type + effective_from + knowledge_from` | - | 8,581 | `listing_status_id:string`, `listing_id:string`, `status_type:string`, `status_value:string`, `reason_code:string?` |
| `dwd_price_limit_regime` | 可交易状态 / 支撑 / #59 | one effective prioritized price-limit rule scope with optional listing override | `price_limit_regime_id + effective_from + knowledge_from` | - | 2,750 | `price_limit_regime_id:string`, `venue_id:string`, `board_code/security_status/ipo_phase:string?`, `priority:int32`, `listing_id:string?`, `regime_type:string`, `upper/lower_limit_ratio:decimal_18_12?`, `reference_price_method:string` |
| `dwd_tradability_state` | 可交易状态 / 核心 / #59 | one listing and trading-session decision-state observation | `listing_id + calendar_session_id + observation_start + knowledge_from` | year/month | 18,557,414 | `listing_id:string`, `calendar_session_id:string`, `is_tradable/can_buy/can_sell:bool`, `reason_code:string`, `upper/lower_price:decimal_38_10?`, `input_revision_digest:string` |
| `dwd_observation_gap` | 可交易状态 / 支撑 / #59 | one expected listing interval without a market observation | `listing_id + calendar_session_id + interval_size + affected_effective_at + knowledge_from` | year/month | 3,799,794 | `gap_id:string`, `listing_id:string`, `calendar_session_id:string`, `interval_size:string`, `gap_reason:string`, `is_expected:bool`, `evidence_digest:string` |
| `dwd_disclosure` | 披露与公司事件 / 核心 / #60 | one company disclosure document or event identity and revision | `disclosure_id + effective_at + knowledge_from` | year/month | 6,489,940 | `disclosure_id:string`, `company_id:string`, `disclosure_type:string`, `source_document_id:string`, `title:string`, `document_uri:string?`, `language:string` |
| `dwd_disclosure_subject` | 披露与公司事件 / 支撑 / #60 | one typed subject relationship from one disclosure | `disclosure_id + disclosure_subject_id + effective_at + knowledge_from` | year/month | 8,920,349 | `disclosure_subject_id:string`, `disclosure_id:string`, `subject_type:string`, `security_id/corporate_action_id/financial_report_id:string?` |
| `dwd_corporate_action` | 披露与公司事件 / 核心 / #60 | one declared corporate-action identity and revision | `corporate_action_id + effective_at + knowledge_from` | year/month | 94,797 | `corporate_action_id:string`, `company_id:string`, `security_id:string`, `action_type:string`, `action_status:string`, `record_date/ex_date/payment_date:date32?` |
| `dwd_corporate_action_term` | 披露与公司事件 / 支撑 / #60 | one typed term or option within one corporate action | `corporate_action_id + corporate_action_term_id + effective_at + knowledge_from` | year | 209,493 | `corporate_action_term_id:string`, `corporate_action_id:string`, `term_type:string`, `option_id/currency:string?`, `amount_per_share/ratio_numerator/ratio_denominator/subscription_price:decimal_38_10?` |
| `dwd_financial_report` | 财务报告 / 核心 / #61 | one company reporting period statement scope and revision | `financial_report_id + period_end + knowledge_from` | year | 1,749,480 | `financial_report_id:string`, `company_id:string`, `report_type:string`, `statement_scope:string`, `accounting_standard:string`, `audit_status:string?`, `restatement_flag:bool` |
| `dwd_financial_concept` | 财务报告 / 支撑 / #61 | one taxonomy or declared source concept version | `financial_concept_id + taxonomy_version + effective_from + knowledge_from` | - | 765 | `financial_concept_id:string`, `taxonomy_namespace/version:string`, `concept_code/name:string`, `statement_type/data_type/period_type:string`, `balance_type:string?` |
| `dwd_financial_fact` | 财务报告 / 核心 / #61 | one report context concept unit dimensional coordinate and revision | `financial_report_id + financial_concept_id + unit_code + context_hash + period_end + knowledge_from` | year | 89,622,402 | `financial_fact_id:string`, `financial_report_id:string`, `financial_concept_id:string`, `unit_code:string`, `value_decimal:decimal_38_10?`, `value_text:string?`, `dimensions_json/context_hash:string`, `decimals:int32?`, `is_nil:bool` |
| `dwd_industry_scheme` | 行业分类 / 支撑 / #62 | one effective industry-classification scheme version | `industry_scheme_id + scheme_version + effective_from + knowledge_from` | - | 1 | `industry_scheme_id:string`, `scheme_version/name/owner:string` |
| `dwd_industry_node` | 行业分类 / 支撑 / #62 | one effective node version in an industry scheme | `industry_node_id + effective_from + knowledge_from` | - | 102 | `industry_node_id:string`, `industry_scheme_id:string`, `parent_node_id:string?`, `node_code/name:string`, `node_level:int32` |
| `dwd_company_industry_membership` | 行业分类 / 核心 / #62 | one listed-company membership interval in one industry node | `company_id + industry_node_id + effective_from + knowledge_from` | - | 5,201 | `company_id:string`, `industry_node_id:string`, `membership_type:string`, `confidence:decimal_9_6?`, `source_declared_as_of:date32` |
| `dwd_share_capital_observation` | 所有权与股本 / 核心 / #63 | one security capital type observation date and revision | `security_id + capital_type + observation_start + knowledge_from` | year | 207,064 | `security_id:string`, `capital_type:string`, `share_quantity:decimal_38_10`, `unit_code:string`, `raw_value/raw_unit/parser_version:string` |
| `dwd_shareholder_count_observation` | 所有权与股本 / 核心 / #63 | one company and optional security holder-count period-end revision | `shareholder_count_observation_id + period_end + knowledge_from` | year | 411,318 | `shareholder_count_observation_id:string`, `company_id:string`, `security_id:string?`, `observation_scope:string`, `holder_count:int64`, `average_holding:decimal_38_10?`, `raw_holder_count/raw_unit/parser_version:string`, `raw_average_holding:string?` |
| `dwd_shareholding_change` | 所有权与股本 / 核心 / #63 | one disclosed holder-change event and revision | `shareholding_change_id + effective_at + knowledge_from` | year/month | 192,773 | `shareholding_change_id:string`, `company_id:string`, `security_id:string?`, `shareholder_source_name:string`, `shareholder_id:string?`, `change_type:string`, normalized/raw quantity and percentage fields, `disclosure_id:string?`, `holder_match_key/parser_version:string` |
| `dwd_shareholding_observation` | 所有权与股本 / 核心 / #63 | one holder and security position observation date and revision | `company_id + security_id + shareholder_source_name + holding_type + observation_start + knowledge_from` | year | 390,627 | `company_id/security_id:string`, `shareholder_source_name:string`, `shareholder_id:string?`, normalized/raw holding quantity and percentage, `holding_type/table_kind:string`, `holder_rank:int32`, `holder_category/share_nature:string?`, `holder_match_key/parser_version:string` |
| `dwd_quality_exception` | 质量治理 / 旁表 / #64 | one accepted quality-rule result for one affected business key and revision | `affected_table + affected_business_key_json + rule_id + affected_effective_at + knowledge_from` | year | 1,607 | `quality_exception_id:string`, `affected_table/domain/business_key_json:string`, `rule_id/version:string`, `classification/severity/authority/disposition/resolution_state:string`, `evidence_digest:string` |

## 6. 关键字段解释

| 字段族 | 解释 |
| --- | --- |
| `company_id` | 上市公司内部身份；不是工商法律实体 id |
| `security_id` | 证券内部身份；同一公司可关联多证券 |
| `listing_id` | 某证券在某场所、代码和有效期下的上市关系 |
| `calendar_session_id` | 交易场所、交易日和 session 的稳定引用 |
| `record_source/source_record_id/source_revision` | 从 DWD 行追到 ODS 逻辑 dataset、源行和修订的最小闭环 |
| `effective_*` | 业务事实何时生效；不等于系统何时知道 |
| `knowledge_*` | 系统何时可知该版本，是 PIT 回放的过滤条件 |
| `observation_*` | 行情、估值或状态所描述的观测区间 |
| `published_at/announced_at` | 对外披露时间；不得用后续修订时间替代历史可知时间 |
| `raw_*` | 解析前的源值/单位证据；规范化值不能覆盖它 |
| `*_digest`, `*_sha256` | 内容或输入集合身份，不是业务自然键 |

## 7. 维护规则

1. 新增或修改 DWD 字段，先更新机器模型与物理 schema，再同步本文和契约测试。
2. ODS Base 增加 dataset 时，必须在完整目录和血缘 disposition 中登记。
3. Base 行数只描述 `V2 Build202607`；日增量行数不得回写为 Base 规模。
4. 数据消费者按 manifest、table/dataset id 和 PIT 字段读取，不依赖目录名称猜测语义。
5. 字典不授予删除、重写 Base、发布 DWS/ADS 或改变 consumer pointer 的权限。
