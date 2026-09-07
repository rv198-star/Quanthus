"""Reviewed Chinese reading projection; never a formula, status or lineage authority."""
from __future__ import annotations

import re
from typing import Any

CHINESE = re.compile(r"[\u3400-\u9fff]")
TIME_PROFILES = {
    "market_observation": "市场观察区间；观察时间与版本可知时间分开",
    "effective_interval": "业务有效区间，左闭右开；版本可知区间另存",
    "effective_event": "业务事件与公告时点分别记录",
    "reporting_period": "报告期间与披露时间分别记录",
    "governance_event": "受影响业务时点与发现时间分别记录",
}
UNITS = {"index": "指数值", "ratio": "比例值（无量纲）", "percent": "百分数",
         "decimal": "按精确规格解释", "percentage": "百分数"}
KINDS = {"ods_dataset": "原始来源数据集", "dwd_table": "规范业务表", "dwd_field": "规范业务字段",
         "feature_spec": "已登记指标规格", "label_card": "规则标签", "semantic_family": "概念分类",
         "semantic_concept": "语义概念"}


def _pair(value: object, key: str, fallback: str | None = None) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) not in {1, 2}:
        raise ValueError(f"catalog Chinese metadata missing: {key}")
    name = value[0]
    description = value[1] if len(value) == 2 else fallback
    if any(not isinstance(v, str) or not CHINESE.search(v) for v in (name, description)):
        raise ValueError(f"catalog Chinese name/description missing: {key}")
    return name, description


def apply_presentation(entries: dict[str, dict[str, Any]], metadata: dict[str, Any]) -> None:
    """Attach human-readable summaries to existing exact IDs, rejecting unreviewed additions."""
    if (metadata.get("authority") != "catalog_reading_projection_only"
            or metadata.get("schema_version") != "1.0.0"):
        raise ValueError("catalog reading authority differs")
    for item in entries.values():
        canonical, kind, details = item["canonical_id"], item["kind"], item["details"]
        original = item["description"]
        facts: list[dict[str, str]] = []
        cautions = list(details.get("cautions", []))

        def fact(label: str, value: object, target: list[dict[str, str]] = facts) -> None:
            if value is not None:
                target.append({"label": label, "value": str(value)})

        if kind in {"dwd_field", "dwd_table"}:
            table = canonical if kind == "dwd_table" else details["table"]
            table_name, table_description = _pair(metadata["tables"].get(table), table)
            fact("业务对象", table_name)
            fact("时间口径", TIME_PROFILES[details["time_profile"]])
            if kind == "dwd_table":
                name, description = table_name, table_description
                fact("记录粒度", "以业务键区分对象，以版本键区分历史修订；精确键见技术详情")
            else:
                field = details["field"]
                name, description = _pair(metadata["fields"].get(field), canonical)
                if table == "dwd_index_bar" and field in {"open", "high", "low", "close"}:
                    names = {"open": "开盘指数", "high": "最高指数",
                             "low": "最低指数", "close": "收盘指数"}
                    name = names[field]
                    description = f"该指数行情区间的{name}值，单位为指数点位，不是证券的每股报价。"
                name = f"{name} · {table_name}"
                nullable = "可以为空；空不代表零或否" if details["nullable"] else "字段契约要求非空"
                fact("允许缺失", nullable)
                field_type = details["type"].removesuffix("?")
                type_label = ("精确十进制定点数" if field_type.startswith("decimal") else
                              "带时区时间" if "timestamp" in field_type else
                              {"string": "文本 / 标识", "bool": "是 / 否", "boolean": "是 / 否",
                               "date": "日期", "int32": "整数", "int64": "整数"}.get(
                                   field_type, "结构化类型，见技术详情"))
                fact("数值形式", type_label)
                if field in {"open", "high", "low", "close", "turnover", "volume"}:
                    if table == "dwd_index_bar" and field in {"open", "high", "low", "close"}:
                        fact("计量口径", "指数点位")
                    elif field == "volume":
                        fact("计量口径", "遵循来源量纲；该字段未独立声明股 / 手，不作统一单位推断")
                    elif field == "turnover":
                        fact("计量口径", "成交金额；结合币种及来源金额单位，不是换手率")
                    else:
                        fact("计量口径", "未复权证券报价；结合该行币种及行情区间")
            cautions.append("历史回溯数据不自动证明当时公开或平台当时可知；实际使用记录属于决策审计。")
        else:
            name, description = _pair(metadata["objects"].get(canonical), canonical, original)
            if kind == "feature_spec":
                unit = details.get("unit")
                fact("结果单位", UNITS.get(unit, "按该规格的精确单位解释；见技术详情"))
                fact("回看时段数", details.get("lookback_sessions"))
                fact("最少历史时段", details.get("minimum_history_sessions"))
                missing = ("必要输入或历史不足时不提供有效值，不使用零值顶替"
                           if details.get("null_policy") == "fail_closed"
                           else "遵循该规格缺失规则，见技术详情")
                fact("缺失处理", missing)
                fact("使用范围", "版本化历史研究规格；不直接授予交易权限")
                cautions.append("以上是该指标规格的窗口要求；完整研究管道还会检查共同历史、数据质量和截面就绪性，不能只凭窗口长度判定可用。")
            elif kind == "label_card":
                fact("判定依据", "按已登记指标与冻结规则判断；具体参数和原始取值见技术详情")
                fact("使用范围", "描述规则条件，不是订单或成交")
            elif kind == "ods_dataset":
                fact("数据角色", "保留来源信息；是否进入业务表由声明的转换关系决定")
                cautions.append("本卡展示来源契约，不宣称已经核验完整物理包成员或全部源字段。")
            elif kind == "semantic_concept":
                if item["implementation_status"] == "declared_only":
                    fact("当前能力", "概念说明；暂无与此概念精确绑定的已验收计算规格")
                else:
                    fact("当前能力", "已有明确绑定的有限范围实现；不代表所有参数和应用均已验证")
                if item.get("accepted_scope") == "bounded_retrospective_b1_b5":
                    fact("验证范围", "Facts V2 B1–B5 固定历史回溯切片，非正式投资专家")
                cautions.append("概念相关不等于算法等价；实际计算必须选择精确版本与参数规格。")
            elif kind == "semantic_family":
                fact("用途", "用于概念分类和发现，不是可直接执行的计算规格")
        if not facts:
            fact("对象类型", KINDS[kind])
        item["display_name"], item["description"] = name, description
        item["keywords"] = sorted(set([*item["keywords"], name]))
        details["source_description"] = original
        details["reading"] = {"facts": facts, "cautions": list(dict.fromkeys(cautions)),
                              "metadata_authority": "config/catalog-business-metadata.v1.json"}
