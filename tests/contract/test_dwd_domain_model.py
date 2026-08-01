from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "config" / "pipelines" / "dwd-domain-model.v1.json"
ODS_POLICY_PATH = (
    ROOT / "config" / "pipelines" / "ods-base-final-through-20260731.v2.json"
)
ODS_ACCEPTANCE_PATH = (
    ROOT
    / "docs"
    / "acceptance"
    / "meta-data-base-001-ods-base-202607"
    / "rollup-v1.2-acceptance.json"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _resolved_columns(model: dict[str, Any], table: dict[str, Any]) -> dict[str, str]:
    columns: dict[str, str] = {}
    for inherited in table["inherits"]:
        columns.update(model["column_sets"][inherited])
    columns.update(model["time_profiles"][table["time_profile"]])
    columns.update(table["columns"])
    return columns


class DwdDwsDomainModelContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = _load(MODEL_PATH)
        cls.ods_policy = _load(ODS_POLICY_PATH)
        cls.ods_acceptance = _load(ODS_ACCEPTANCE_PATH)
        cls.tables = {table["name"]: table for table in cls.model["tables"]}
        cls.dimensions = {
            dimension["name"]: dimension
            for dimension in cls.model["dws_model"]["dimensions"]
        }
        cls.facts = {fact["name"]: fact for fact in cls.model["dws_model"]["facts"]}

    def test_product_scope_owns_exactly_seven_business_domains(self) -> None:
        self.assertEqual(
            [domain["id"] for domain in self.model["domain_catalog"]],
            [
                "listed_company",
                "security_market_observation",
                "tradability_state",
                "disclosure_company_event",
                "financial_reporting",
                "industry_classification",
                "ownership_capital",
            ],
        )
        self.assertEqual(
            self.model["scope"]["domain_scope_owner"],
            "quanthus_product_objective",
        )
        self.assertEqual(
            self.model["scope"]["source_availability_owner"],
            "governed_ods_inventory_and_semantics",
        )
        self.assertTrue(self.model["scope"]["physical_build_started"])
        self.assertEqual(
            self.model["status"],
            "approved_for_scripted_build_implementation",
        )
        self.assertEqual(
            self.model["scope"]["new_base_input"],
            "exact_immutable_self_contained_ods_base_rollup_only",
        )

        for reference in self.model["industry_references"]:
            self.assertFalse(reference["scope_authority"])

    def test_core_objects_and_supporting_structures_are_separate(self) -> None:
        domain_ids = {domain["id"] for domain in self.model["domain_catalog"]}
        declared_core = {
            table_name
            for domain in self.model["domain_catalog"]
            for table_name in domain["core_objects"]
        }
        actual_core = {
            name
            for name, table in self.tables.items()
            if table["object_role"] == "core_object"
        }
        self.assertEqual(declared_core, actual_core)

        for name, table in self.tables.items():
            with self.subTest(table=name):
                if table["object_role"] == "governance_sidecar":
                    self.assertEqual(table["domain"], "governance_sidecar")
                else:
                    self.assertIn(table["domain"], domain_ids)
                self.assertIn(table["object_role"], self.model["object_roles"])

        self.assertEqual(self.tables["dwd_listed_company"]["object_role"], "core_object")
        for support in (
            "dwd_security",
            "dwd_security_identifier",
            "dwd_listing",
            "dwd_trading_venue",
            "dwd_trading_calendar",
            "dwd_financial_concept",
        ):
            self.assertEqual(
                self.tables[support]["object_role"],
                "supporting_structure",
            )

    def test_dwd_tables_have_valid_keys_time_foreign_keys_and_roles(self) -> None:
        self.assertEqual(len(self.model["tables"]), len(self.tables), "duplicate table name")
        self.assertEqual(len(self.tables), 33)
        self.assertEqual(
            {
                role: sum(table["object_role"] == role for table in self.tables.values())
                for role in self.model["object_roles"]
            },
            {
                "core_object": 14,
                "supporting_structure": 18,
                "governance_sidecar": 1,
            },
        )
        delivery_states = set(self.model["delivery_states"])
        self.assertNotIn("blocked", delivery_states)

        for name, table in self.tables.items():
            with self.subTest(table=name):
                self.assertIn(table["delivery_state"], delivery_states)
                self.assertIn(table["time_profile"], self.model["time_profiles"])
                self.assertEqual(table["inherits"], ["lineage"])
                columns = _resolved_columns(self.model, table)
                self.assertTrue(table["business_key"])
                self.assertTrue(table["row_key"])
                self.assertEqual(set(table["business_key"]).difference(columns), set())
                self.assertEqual(set(table["row_key"]).difference(columns), set())
                self.assertTrue(set(table["business_key"]).issubset(table["row_key"]))
                self.assertEqual(len(table["row_key"]), len(set(table["row_key"])))
                self.assertIn("knowledge_from", table["row_key"])
                for key_column in table["row_key"]:
                    self.assertFalse(
                        columns[key_column].endswith("?"),
                        f"{name} nullable row-key column: {key_column}",
                    )

                for foreign_key in table["foreign_keys"]:
                    self.assertIn(foreign_key["column"], columns)
                    target_name = foreign_key["table"]
                    self.assertIn(target_name, self.tables)
                    target_columns = _resolved_columns(self.model, self.tables[target_name])
                    self.assertIn(foreign_key["reference_column"], target_columns)
                    source_type = columns[foreign_key["column"]]
                    self.assertEqual(source_type.endswith("?"), foreign_key["nullable"])

    def test_minimum_identity_bridge_replaces_issuance_spine(self) -> None:
        security_fks = {
            (item["column"], item["table"])
            for item in self.tables["dwd_security"]["foreign_keys"]
        }
        listing_fks = {
            (item["column"], item["table"])
            for item in self.tables["dwd_listing"]["foreign_keys"]
        }
        self.assertIn(("company_id", "dwd_listed_company"), security_fks)
        self.assertIn(("security_id", "dwd_security"), listing_fks)
        self.assertIn(("venue_id", "dwd_trading_venue"), listing_fks)

        absent_tables = {
            "dwd_legal_entity",
            "dwd_security_issuance",
            "dwd_trade_event",
            "dwd_index_constituent_membership",
        }
        self.assertEqual(absent_tables.intersection(self.tables), set())
        deferred_ids = {item["id"] for item in self.model["deferred_objects"]}
        self.assertIn("security_issuance_and_underwriting", deferred_ids)
        self.assertIn("legal_registry_entity", deferred_ids)
        self.assertIn("order_and_trade_event", deferred_ids)

    def test_disclosure_subject_is_the_single_report_action_association(self) -> None:
        subject = self.tables["dwd_disclosure_subject"]
        self.assertIn("exactly_one_subject_identifier_is_non_null", subject["constraints"])
        self.assertNotIn("disclosure_id", self.tables["dwd_corporate_action"]["columns"])
        self.assertNotIn("disclosure_id", self.tables["dwd_financial_report"]["columns"])
        self.assertIn("corporate_action_id", subject["columns"])
        self.assertIn("financial_report_id", subject["columns"])

    def test_dws_fact_constellation_has_conformed_dimensions_and_declared_grains(self) -> None:
        self.assertEqual(len(self.dimensions), 10)
        self.assertEqual(len(self.facts), 13)
        self.assertEqual(
            self.model["model_patterns"]["dws"],
            "kimball_fact_constellation_with_conformed_dimensions",
        )
        self.assertEqual(
            self.model["dws_model"]["snapshot_binding"],
            "every_fact_row_references_one_immutable_snapshot_key",
        )

        for name, dimension in self.dimensions.items():
            with self.subTest(dimension=name):
                self.assertTrue(dimension["grain"])
                self.assertTrue(dimension["key"])
                self.assertIn(dimension["delivery_state"], self.model["delivery_states"])
                self.assertEqual(set(dimension["source_tables"]).difference(self.tables), set())

        for name, fact in self.facts.items():
            with self.subTest(fact=name):
                self.assertTrue(fact["grain"])
                self.assertIn("dws_dim_snapshot", fact["dimensions"])
                all_dimensions = set(fact["dimensions"] + fact["optional_dimensions"])
                self.assertEqual(all_dimensions.difference(self.dimensions), set())
                self.assertEqual(set(fact["source_tables"]).difference(self.tables), set())
                self.assertIn(fact["delivery_state"], self.model["delivery_states"])
                if name != "dws_fact_company_industry_membership":
                    self.assertTrue(fact["measures"])
                for semantics in fact["measures"].values():
                    self.assertTrue(semantics)
        self.assertEqual(
            self.facts["dws_fact_security_market_interval"]["grain"],
            "one snapshot listing interval-start interval-size and price-basis",
        )
        self.assertEqual(
            self.facts["dws_fact_adjustment_factor"]["source_tables"],
            ["dwd_adjustment_factor", "dwd_adjustment_factor_input"],
        )
        self.assertEqual(
            self.facts["dws_fact_financial_metric"]["delivery_state"],
            "source_available",
        )

    def test_financial_and_detailed_ownership_are_required_in_the_initial_build(self) -> None:
        for table_name in (
            "dwd_financial_concept",
            "dwd_financial_fact",
            "dwd_share_capital_observation",
            "dwd_shareholding_observation",
        ):
            table = self.tables[table_name]
            self.assertEqual(table["delivery_state"], "source_available")
            self.assertNotIn("blocker", table)

        for fact_name in (
            "dws_fact_financial_metric",
            "dws_fact_share_capital",
            "dws_fact_shareholding_position",
        ):
            self.assertEqual(
                self.facts[fact_name]["delivery_state"],
                "source_available",
            )

        transforms = self.model["transform_contracts"]
        self.assertEqual(
            transforms["financial_reporting"]["initial_semantics"],
            "provider_native_concepts_in_versioned_source_namespace",
        )
        self.assertIn(
            "retain_shareholder_source_name_as_the_required_identity_and_allow_canonical_shareholder_id_to_remain_null",
            transforms["shareholding_position"]["requirements"],
        )

    def test_final_ods_base_static_sources_have_exactly_one_disposition(self) -> None:
        source = self.ods_policy["source"]
        expected = {
            item["dataset_id"]
            for group in ("files", "trees", "archive_trees")
            for item in source[group]
        }
        mappings = self.model["ods_source_contract"]["static_sources"]
        mapped_ids = [item["source_id"] for item in mappings]

        self.assertEqual(len(expected), 53)
        self.assertEqual(self.model["ods_source_contract"]["static_source_count"], 53)
        self.assertEqual(len(mapped_ids), len(set(mapped_ids)), "duplicate source disposition")
        self.assertEqual(set(mapped_ids), expected)
        self._assert_source_mappings_are_valid(mappings)

    def test_daily_and_intraday_artifacts_have_exactly_one_disposition(self) -> None:
        source = self.ods_policy["source"]
        daily = source["daily"]
        expected = set(daily["market_required_artifacts"])
        expected.update(daily["event_required_artifacts"])
        for values in daily["market_date_required_artifacts"].values():
            expected.update(values)
        for release in source["intraday_releases"]:
            expected.update(release["required_artifacts"])

        mappings = self.model["ods_source_contract"]["release_artifacts"]
        mapped_ids = [item["artifact"] for item in mappings]
        self.assertEqual(len(expected), 17)
        self.assertEqual(self.model["ods_source_contract"]["release_artifact_count"], 17)
        self.assertEqual(len(mapped_ids), len(set(mapped_ids)), "duplicate artifact disposition")
        self.assertEqual(set(mapped_ids), expected)
        self._assert_source_mappings_are_valid(mappings)

    def test_weekly_payloads_expose_business_content_for_the_initial_build(self) -> None:
        mappings = self.model["ods_source_contract"]["weekly_artifacts"]
        by_name = {item["artifact"]: item for item in mappings}
        self.assertEqual(len(by_name), len(mappings))
        self.assertEqual(by_name["fundamentals_receipts"]["disposition"], "ods_only")
        self.assertEqual(by_name["raw_f10"]["disposition"], "modeled")
        self.assertEqual(by_name["financial_statements"]["disposition"], "modeled")
        self.assertEqual(by_name["financial_quarterly"]["disposition"], "modeled")
        self.assertEqual(
            by_name["industry_membership"]["target_tables"],
            [
                "dwd_industry_scheme",
                "dwd_industry_node",
                "dwd_company_industry_membership",
            ],
        )
        self._assert_source_mappings_are_valid(mappings)

    def test_container_members_and_release_auxiliary_artifacts_are_explicit(self) -> None:
        contract = self.model["ods_source_contract"]
        members = contract["container_members"]
        self.assertEqual(contract["container_member_count"], 10)
        self.assertEqual(
            {item["member"] for item in members},
            {
                "announcements",
                "crawl_progress",
                "fin_abstract",
                "fin_progress",
                "holder_changes",
                "holder_progress",
                "info_meta",
                "shareholder_count",
                "shareholder_count_progress",
                "shareholder_count_revision",
            },
        )
        self.assertEqual(
            next(item for item in members if item["member"] == "fin_abstract")[
                "disposition"
            ],
            "modeled",
        )
        self.assertEqual(
            next(item for item in members if item["member"] == "holder_changes")[
                "target_tables"
            ],
            ["dwd_shareholding_change"],
        )
        auxiliary = contract["release_auxiliary_artifacts"]
        self.assertEqual(
            {item["artifact"] for item in auxiliary},
            {
                "acquisition_log",
                "after_hours_fixed_price_receipts",
                "after_hours_fixed_price_trades",
                "lane_status",
                "release_metadata",
            },
        )
        self._assert_source_mappings_are_valid(auxiliary)

    def test_rollup_is_self_contained_and_chained_hotfix_lineage_is_explicit(self) -> None:
        build_input = self.model["build_input_contract"]
        self.assertEqual(
            build_input["kind"], "immutable_self_contained_ods_base_rollup"
        )
        self.assertEqual(build_input["bundle_id"], "ods-base-202607-v1.2")
        self.assertTrue(build_input["self_contained"])
        self.assertFalse(build_input["source_packages_required_after_build"])
        lineage = build_input["rollup_lineage"]
        self.assertEqual(len(lineage["hotfixes"]), 1)
        hotfix = lineage["hotfixes"][0]
        self.assertEqual(
            hotfix["hotfix_id"],
            "ods-base-202607-v1.1-calendar-history-hotfix-001",
        )
        self.assertEqual(
            hotfix["parent_manifest_sha256"],
            lineage["parent"]["manifest_sha256"],
        )
        self.assertEqual(hotfix["scope_count"], 7318)
        self.assertEqual(
            hotfix["application_mode"], "add_absent_business_keys_only"
        )
        inherited = lineage["parent"]["inherited_rollup"]
        self.assertEqual(inherited["parent"]["bundle_id"], "ods-base-202607-v1")
        self.assertEqual(len(inherited["hotfixes"]), 1)
        self.assertEqual(inherited["hotfixes"][0]["scope_count"], 209)
        self.assertEqual(
            inherited["hotfixes"][0]["application_mode"],
            "fill_absent_keys_only",
        )

        precedence = build_input["overlay_precedence"]
        self.assertIn("parent_base_wins", precedence["default"])
        self.assertEqual(precedence["overlap_mismatch_policy"], "fail_build")
        self.assertEqual(precedence["expected_sse_szse_listed_scope_after_overlay"], 5201)
        self.assertEqual(precedence["bse_policy"], "BSE_PARTIAL_NONBLOCKING")
        self.assertEqual(
            precedence["business_keys"],
            {
                "daily_ohlcv_none_hotfix": ["stock_code", "date"],
                "daily_status_observation_hotfix": ["stock_code", "date"],
                "minute_1m_hotfix": ["stock_code", "ts"],
                "minute_5m_hotfix": ["stock_code", "ts"],
                "valuation_history_hotfix": ["stock_code", "date"],
                "fundamentals_financial_statements_parquet": [
                    "stock_code",
                    "statement_type",
                ],
                "fundamentals_financial_quarterly_parquet": [
                    "stock_code",
                    "report_date",
                ],
                "fundamentals_raw_f10_parquet": ["stock_code", "category"],
                "fundamentals_industry_membership_parquet": [
                    "stock_code",
                    "market_date",
                ],
            },
        )
        calendar_precedence = build_input["calendar_history_precedence"]
        self.assertEqual(
            calendar_precedence["business_keys"],
            {
                "trading_calendar_history_hotfix": [
                    "exchange",
                    "trade_date",
                    "session",
                ]
            },
        )
        self.assertEqual(calendar_precedence["overlap_mismatch_policy"], "fail_build")
        self.assertEqual(calendar_precedence["venues"], ["SSE", "SZSE"])
        self.assertEqual(calendar_precedence["bse_policy"], "not_fabricated")

        mappings = self.model["ods_source_contract"]["hotfix_artifacts"]
        mapped_ids = [item["artifact"] for item in mappings]
        self.assertEqual(self.model["ods_source_contract"]["hotfix_artifact_count"], 34)
        self.assertEqual(len(mapped_ids), 34)
        self.assertEqual(len(mapped_ids), len(set(mapped_ids)))
        self._assert_source_mappings_are_valid(mappings)

        by_name = {item["artifact"]: item for item in mappings}
        self.assertEqual(
            by_name["trading_calendar_history_hotfix"]["target_tables"],
            ["dwd_trading_venue", "dwd_trading_calendar", "dwd_tradability_state"],
        )
        self.assertEqual(by_name["daily_ohlcv_none_hotfix"]["disposition"], "modeled")
        self.assertEqual(by_name["minute_1m_hotfix"]["target_tables"], ["dwd_security_bar"])
        self.assertEqual(by_name["minute_5m_hotfix"]["target_tables"], ["dwd_security_bar"])
        self.assertEqual(
            by_name["fundamentals_financial_statements_parquet"]["disposition"],
            "modeled",
        )
        self.assertEqual(by_name["security_hotfix_stock_list"]["disposition"], "superseded")

    def test_every_dwd_object_has_an_explicit_ods_source_disposition(self) -> None:
        contract = self.model["ods_source_contract"]
        mapped_tables = {
            table
            for group in (
                "static_sources",
                "release_artifacts",
                "weekly_artifacts",
                "hotfix_artifacts",
            )
            for mapping in contract[group]
            for table in mapping["target_tables"]
        }
        self.assertEqual(mapped_tables, set(self.tables))

    def test_existing_dwd_cannot_influence_the_model_contract(self) -> None:
        self.assertFalse(self.model["scope"]["existing_dwd_used_as_model_input"])
        self.assertFalse(self.model["scope"]["existing_ads_used_as_model_input"])
        self.assertEqual(
            self.model["scope"]["existing_dwd_role"],
            "retained_outside_new_build_not_read_or_reconciled",
        )
        self.assertFalse(
            self.model["build_input_contract"]["existing_dwd_or_ads_read_allowed"]
        )
        self.assertFalse(
            self.model["build_input_contract"][
                "historical_or_current_warehouse_package_read_allowed"
            ]
        )
        self.assertTrue(
            self.model["build_input_contract"]["same_run_upstream_candidate_read_allowed"]
        )
        self.assertNotIn("legacy_compatibility", self.model)
        self.assertIn("ods_to_dwd_source_and_row_coverage_report", self.model["build_gates"])
        self.assertIn("dwd_to_dws_grain_and_reconciliation_report", self.model["build_gates"])

    def test_script_dag_breaks_market_tradability_cycle(self) -> None:
        execution = self.model["build_execution_contract"]
        ownership = execution["dwd_script_ownership"]
        self.assertEqual(execution["script_count"], 17)
        self.assertEqual(execution["calendar_backbone_issue"], 72)
        self.assertEqual(ownership["72"], ["dwd_trading_calendar"])
        self.assertNotIn("dwd_trading_calendar", ownership["59"])
        self.assertEqual(len(ownership["59"]), 5)
        self.assertEqual(
            ownership["58"],
            [
                "dwd_security_bar",
                "dwd_security_valuation",
                "dwd_market_index",
                "dwd_index_bar",
            ],
        )
        self.assertEqual(
            ownership["60"],
            [
                "dwd_disclosure",
                "dwd_disclosure_subject",
                "dwd_corporate_action",
                "dwd_corporate_action_term",
                "dwd_adjustment_factor",
                "dwd_adjustment_factor_input",
            ],
        )
        self.assertEqual(
            execution["required_dependency_edges"][:4],
            [[56, 57], [57, 72], [72, 58], [58, 59]],
        )
        owned = [table for tables in ownership.values() for table in tables]
        self.assertEqual(len(owned), 33)
        self.assertEqual(set(owned), set(self.tables))

    def test_machine_dag_covers_every_cross_owner_foreign_key(self) -> None:
        execution = self.model["build_execution_contract"]
        ownership = execution["dwd_script_ownership"]
        owner_by_table = {
            table: int(issue)
            for issue, tables in ownership.items()
            for table in tables
        }
        derived_edges = {
            (owner_by_table[foreign_key["table"]], owner_by_table[table_name])
            for table_name, table in self.tables.items()
            for foreign_key in table["foreign_keys"]
            if owner_by_table[foreign_key["table"]] != owner_by_table[table_name]
        }
        declared_edges = {
            tuple(edge) for edge in execution["cross_owner_foreign_key_edges"]
        }

        self.assertEqual(declared_edges, derived_edges)
        self.assertTrue(
            declared_edges.issubset(
                {tuple(edge) for edge in execution["required_dependency_edges"]}
            )
        )
        self.assertNotIn((60, 58), declared_edges)

    def test_price_limit_regime_is_a_prioritized_rule_scope(self) -> None:
        regime = self.tables["dwd_price_limit_regime"]
        columns = _resolved_columns(self.model, regime)
        self.assertEqual(
            regime["business_key"], ["price_limit_regime_id", "effective_from"]
        )
        self.assertEqual(columns["venue_id"], "string")
        self.assertEqual(columns["listing_id"], "string?")
        self.assertEqual(columns["board_code"], "string?")
        self.assertEqual(columns["security_status"], "string?")
        self.assertEqual(columns["priority"], "int32")
        self.assertIn("scope_must_resolve_by_priority", regime["constraints"])

    def test_new_build_is_bound_to_the_exact_final_ods_base(self) -> None:
        build_input = self.model["build_input_contract"]
        package = self.ods_acceptance["package"]

        for field in (
            "bundle_id",
            "through_date",
            "manifest_sha256",
            "content_digest",
        ):
            self.assertEqual(build_input[field], package[field])
        self.assertEqual(self.ods_acceptance["source_inventory"]["scope_gaps"], [])
        self.assertFalse(build_input["parser_mapping_or_normalization_work_is_a_source_gap"])

        dispositions = {
            mapping["disposition"]
            for group in (
                "static_sources",
                "release_artifacts",
                "weekly_artifacts",
                "hotfix_artifacts",
            )
            for mapping in self.model["ods_source_contract"][group]
        }
        self.assertNotIn("blocked", dispositions)

    def test_docs_expose_implementation_authority_but_keep_publication_gated(self) -> None:
        adr = (
            ROOT / "docs" / "architecture" / "adr-0006-industry-aligned-dwd-domain-model.md"
        ).read_text(encoding="utf-8")
        lld = (
            ROOT
            / "docs"
            / "design"
            / "lld"
            / "01-data-foundation"
            / "dwd-domain-model.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Seven first-class business domains", adr)
        self.assertIn("Status: Accepted", adr)
        self.assertIn("Design status | implementation-approved", lld)
        self.assertIn("DWS fact constellation", lld)
        self.assertIn("53 static source ids", lld)
        self.assertIn("historical DWD/ADS tables", lld)
        self.assertIn("#72 trading-calendar backbone", lld)
        self.assertIn("Exact immutable self-contained `ods-base-202607-v1.1`", lld)
        self.assertIn("Issue #69", adr)
        self.assertIn("publication", adr)

    def _assert_source_mappings_are_valid(self, mappings: list[dict[str, Any]]) -> None:
        allowed = set(self.model["ods_source_contract"]["allowed_dispositions"])
        for mapping in mappings:
            identity = mapping.get("source_id") or mapping.get("artifact")
            with self.subTest(source=identity):
                self.assertIn(mapping["disposition"], allowed)
                targets = mapping["target_tables"]
                self.assertEqual(set(targets).difference(self.tables), set())
                if mapping["disposition"] in {"ods_only", "deferred"}:
                    self.assertEqual(targets, [])
                else:
                    self.assertTrue(targets)
                if mapping["disposition"] == "source_gap":
                    self.assertTrue(mapping.get("gap_reason"))


if __name__ == "__main__":
    unittest.main()
