# Facts v2: pre-result runtime diagnostic checkpoint

| Field | Value |
| --- | --- |
| Work item | P3-STRUCT-004 / #216; B2 #219 is not accepted |
| Original runtime revision | `e5b9b03b9283d4251b8fcfc7210a76df82a31389` |
| A | `job_a8900f44f8404ca2abf2c30203a7da50` |
| B | `job_d1b2d3c9101648dc81967b062606d000` |
| Observed terminal disposition | Both failed, attempt/claim 1/1, no result_ref or sealed Run manifest |
| Root cause | Not yet established from exported error summary |

## Observed failure, not an economic or semantic result

A ran from 2026-09-06 17:42:34.563306 to 17:44:42.779550 UTC.
B ran from 17:44:42.796210 to 17:46:50.254940 UTC.
Both exported only the final DuckDB error-message help line:

```text
structure_mvp_child_exit_1:See also https://duckdb.org/docs/stable/guides/performance/how_to_tune_workloads
```

This is insufficient to distinguish a memory limit, temporary disk pressure or another DuckDB
resource failure. No source-admission counts, facts, scores, samples or P2 results were published.
The two tasks were already terminal when inspected; cancellation was neither needed nor attempted.
A bounded readability check of the exact A staging stderr file in the known Event Run locations
found no readable log in the development workspace. No permissions, Docker access or mounts were
changed to bypass that boundary.

## Diagnostic change

Add stage-bounded exception wrapping in the v2 runner only. Provider opening and source extraction
retain their original exception chain; the final exported exception now includes the stage, exception
class and first nonempty message line, bounded to 300 characters. This addresses diagnostic loss;
it is not claimed to repair the underlying resource failure. The existing Worker/DWD provider and
all source, window, anchor, process, score, P2 and sample rules are unchanged.

The request and calculation-spec digests remain those preregistered in
`structure-facts-v2-runtime-start-20260907.md`. A focused test covers multiline exceptions ending in
a help URL. One new controlled attempt may diagnose the unchanged workload on the explicit diagnostic
revision. Do not enqueue a second attempt until its outcome/root cause is inspected. If it reaches
publication it is the first successful result-bearing Run, not a retry that may be hidden.

## Budget and issue state

The first two failed attempts are retained and counted as actual compute expenditure. Zero successful
result-bearing outputs exist so far; there is no seen-result tuning. The target remains one pair of
same-revision successful cold Runs, no third successful result-bearing Job and no open-ended search.
No new source view, ticker, date, threshold or seeder is authorized by this diagnostic change.
B2-B5 and the parent remain open. Subsequent root-cause repair must be specifically evidenced before
any acceptance, and all attempts must remain visible in the final record.
