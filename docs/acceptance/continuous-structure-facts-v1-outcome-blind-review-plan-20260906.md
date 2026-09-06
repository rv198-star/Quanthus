# Continuous Structure Facts v1 outcome-blind review plan (2026-09-06)

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-003` |
| Status | preregistered before Real-DWD Run A result access |
| Scope | development-only technical semantic review |
| Result use | acceptance evidence only; no tuning or threshold changes |

## 1. Purpose

This review asks only whether published Continuous Structure Facts are causally and locally coherent at T close. It does not inspect future returns, outcome labels, economic performance, later portfolio behavior, or sealed partitions.

## 2. Deterministic sample selection

Target sample size is 12 fact rows. Selection uses only fact-row lifecycle fields, `security_code`, and `market_date`.

For each configured security in run-config order:

1. select its earliest `episode_status=active` row with `episode_age_sessions=0`;
2. select its earliest `episode_status=active` row with `episode_age_sessions>=1`;
3. select its earliest `episode_status=ended` row.

Remove duplicates while preserving this order. If fewer than 12 rows are selected, fill the remainder by round-robin over securities using the earliest `available=true` rows not already selected, ordered by market date.

If a lifecycle category does not exist for a security, do not substitute a later category-specific rule; use the deterministic fill rule only.

This selection rule must not be changed after Run A result access.

## 3. Review fields

For each selected row inspect only T-close-visible structure evidence:

- security/date/snapshot/knowledge cutoff;
- `rolling_resistance20` and `rolling_support20`;
- signed resistance/support margins;
- `volume_ratio20`;
- episode identity/status/age;
- frozen breakout/support anchors when an episode exists;
- breakout/support margins relative to frozen anchors;
- attempt timestamp/snapshot and attempt volume ratio;
- history reset / availability reason;
- row digest and source-window identity.

## 4. Pass criteria

A row passes when all applicable statements hold:

- the row is self-explanatory without replaying hidden prior state;
- active/ended episode fields refer to one stable frozen episode identity;
- episode age/hold progression is directionally consistent with the lifecycle row;
- signed margins agree with the published close and anchor direction;
- volume is descriptive evidence only and does not determine whether the episode exists;
- an ended episode does not silently move its breakout anchor;
- missing/reset semantics never imply bearish damage or forced action;
- no coarse state is present as authoritative fact identity;
- no future/outcome information is required to explain the row.

## 5. Failure handling

Any semantic contradiction is recorded as a defect. No result-dependent threshold, anchor rule, episode trigger, sample substitution, or state expansion is permitted inside this qualification. A behavior-changing repair requires a new revision and fresh Real-DWD A/B qualification.

## 6. Claim ceiling

A passing review supports only the engineering claim that Continuous Structure Facts v1 is locally auditable and causally coherent on the frozen development slice. It does not prove economic value, production expert quality, thirteen-state validity, model value, or Shadow/paper/live readiness.
