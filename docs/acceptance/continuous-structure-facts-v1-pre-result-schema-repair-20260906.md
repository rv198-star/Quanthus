# Continuous Structure Facts v1 pre-result schema repair (2026-09-06)

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-003` |
| Repair timing | before any Real-DWD result access |
| Behavior change | none to builder output, scoring, thresholds, run scope, DWD inputs, or episode semantics |
| Qualification revision canceled | `1ea8929c6d111f9deefd067882b9d41af9f05b69` |
| Run A | `job_94e006faf9983f5892b036e20ab2d9b4` — canceled, `result_ref=null` |
| Run B | `job_46dbba44b828e6002df72afaddc546cd` — canceled, `result_ref=null` |

## 1. Defect found before result access

Independent semantic review found one schema-invariant hole in `ContinuousStructureFactRow.__post_init__`.

`attempt_volume_ratio20` is episode-bound evidence: it describes the volume ratio observed at the breakout attempt. The builder already emits it only when an episode exists, but the dataclass validator did not reject a manually constructed row carrying `attempt_volume_ratio20` while `episode_id` was `None`.

This was a contract-hardening defect, not an observed Real-DWD fact error. No result-bearing artifact from the qualification revision was opened before the repair decision.

## 2. Repair

The repaired validator now fails closed when:

```text
episode_id is None
AND attempt_volume_ratio20 is not None
```

An episode may still legitimately carry `attempt_volume_ratio20=None` when volume evidence is unavailable. The repair therefore does not make volume confirmation mandatory and does not alter the approved price-only episode trigger.

The compatibility expert docstring was also corrected to match existing behavior: non-active-episode rows abstain; the expert does not fall back to a rolling-resistance score.

## 3. Qualification disposition

Both `1ea8929` qualification Jobs were canceled before result access and have `result_ref=null`. They are not acceptance evidence.

The frozen outcome-blind review plan remains valid because neither its selection rule nor review criteria depend on this validator hardening.

A new exact Git revision must pass repository checks and run a fresh independent Real-DWD A/B qualification. Only the new revision may support `P3-STRUCT-003` acceptance.

## 4. Claim ceiling

```text
Real-DWD result accessed from 1ea8929: NO
Builder semantics changed: NO
Episode trigger changed: NO
Volume threshold introduced: NO
Economic mapping changed: NO
P3-STRUCT-003 accepted: NO
```
