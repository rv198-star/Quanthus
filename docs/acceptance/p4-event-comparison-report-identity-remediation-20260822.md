# P4-REPORT-001 Feature-Panel Identity Remediation

| Field | Value |
| --- | --- |
| Work item | `P4-REPORT-001` |
| Status | implementation remediation complete; production rerun pending |
| Governing report policy | `p4-event.daily-group-comparison@1.0.0` |
| Report policy digest | `sha256:4b7e6f931b06615940a9a72a5bd05f800be40f825716093e04637ab5fd8134a0` |
| Failed production job | `job_15a9f9c2d3934b96a1349b0f3ae2a288` |
| Failed production revision | `6286539da0c673707fd2b1c31d98194749a62457` |

## Failure evidence

The first production comparison-report Run A completed the Event Library and Label Corpus portions,
then failed before publishing a report with:

`configuration error: not every three-class event aligns to its T feature row`

The same run reported complete static-industry coverage, so the failure was not an ADM industry
coverage regression. The already-accepted label corpus remained the source of 34,641 settled
three-class events.

## Root cause

The report adapter crossed two intentionally different identity namespaces as though they were the
same value:

- `EventRecord.identity.security_id` is the governed DWD security identity emitted by the P4 Event
  Library;
- the accepted direct-DWD P3 replay adapter deliberately keys `BaselineFeaturePanel` rows by
  `listing_code` as its replay `security_id`.

`comparison_report._event_label_inputs()` had copied the governed DWD `security_id` into the field
used to join the P3 feature panel. As a result, valid events could not find their T feature row even
though both artifacts were individually correct.

This is the same identity boundary already respected by the accepted P4 label-corpus path and by
the production expert-evaluation adapter. It is an implementation conformance defect, not a data
repair requirement and not a report-policy change.

## Remediation

`src/quanthus/event_library/comparison_report.py` now models the downstream join identity explicitly
as `_ReportEventInput.panel_security_id`. That value is sourced from the Event Library row's
`listing_code`; the governed DWD event identity remains untouched and continues to define the event
namespace.

The aggregation SQL joins `panel_indexed.security_id` only to `panel_security_id`. No report
threshold, feature formula, label, split, event identity, policy digest, or source snapshot is
changed.

A regression fixture now uses deliberately different values for DWD `security_id` and replay-panel
identity while keeping the same `listing_code`, and verifies that the report binds to the replay
panel identity. Existing group-matrix and report-policy tests continue to pass.

## Required acceptance

Before #76 can close:

1. the repository-required full Gate must pass;
2. the corrected exact revision must be deployed to the Event Worker;
3. production Report Run A must seal a readable immutable v3 Event + Label + Report Run;
4. only then may Report Run B be submitted with identical governed input and a different
   idempotency key;
5. A/B report content digest, report manifest SHA, leading-feature identities, accounting, and
   acceptance flag must be identical.
