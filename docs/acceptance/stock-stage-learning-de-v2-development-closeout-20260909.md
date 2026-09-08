# P3-STAGE-001 D/E development v2 closeout — 2026-09-09

| Field | Value |
| --- | --- |
| Work items | `P3-STAGE-001-D` / #241; `P3-STAGE-001-E` / #242 |
| Evidence scope | development-only; not confirmatory qualification |
| RX6800 Job | `stage_f3262f626e98490cb3cd7eec9dea866d` |
| Code revision | `ec93424f36e74fff25aa1519fb198f7389cd1a99` |
| D pilot manifest | `sha256:39a37a6d7fcd34401668df67f08e1d3ec8d7f872b73436b20ba4446d1cab5cb2` |
| Synthetic review manifest | `sha256:270fe98bb6fc45924a6a6da6daf287e835c1a4b88484cc00b57c09e2cea2aa5f` |
| Resolution manifest | `sha256:ae6dc75377a5b461f795cdf02b520e04c294fb2cd59851d093b23676e3edf1e5` |
| Learning manifest | `sha256:bbdf87212d33fdc2010a89662925d1e65147d1fef7dc14de06afa4ca337bb8ff` |
| Development candidate | `config/stage-learning/development-candidate.v1.json` |
| Disposition | **D CLOSED / E CLOSED; R1 development candidate frozen for F** |

## 1. Governed execution

The RX6800 Stage Worker accepted exact revision `ec93424`, completed the governed Job with
`state=succeeded`, `exit_code=0`, and no error, then verified the immutable D artifact before
publishing terminal status. The run started at `2026-09-08T15:54:28.535057Z` and completed at
`2026-09-08T15:55:26.101734Z`, about 57.6 seconds wall time from Worker start to terminal status.
The later OCI-to-RX6800 restricted MCP read path independently returned the same Job identity and
artifact bindings; no rerun was needed for closeout.

The v2 reference supplement contained exactly 120 new development contexts under the frozen
stratified selection contract. Synthetic Panel v2 produced 120/120 complete resolutions at
`SYNTHETIC_EXPERT_CONSENSUS`: 87 `definite`, 21 `ambiguous_set`, 12 `coexistence`, zero
`unjudgeable`; the two primaries disagreed on 14 contexts and the frozen third persona adjudicated
exactly those 14. The quantitative evidence auditor issued zero vetoes. Future settlements were not
visible to semantic review or to the learner.

This is still synthetic role-isolated policy evidence authored within one model family. It is not
`HUMAN_EXPERT_REFERENCE`, does not become human validation by repetition, and cannot by itself
support an unrestricted `QUALIFIED` market-label claim.

## 2. R0 versus frozen R1 candidate on the same reference

| Metric | R0 | R1 development candidate |
| --- | ---: | ---: |
| Macro F1 | 0.852003 | **0.925532** |
| Positive-reference coverage | 0.742424 | **0.893939** |
| Stage 3 TP / truth | 29 / 39 | **39 / 39** |
| Stage 3 recall | 0.743590 | **1.000000** |
| Stage 4 TP / truth | 20 / 27 | 20 / 27 |
| Stage 4 recall | 0.740741 | 0.740741 |
| Definite-negative false-positive rate | 0 | 0 |
| Ambiguous unique-assertion rate | 0 | 0 |

The selected trial is
`sha256:c21032608ab96d6abae81ba5709bf3e2c3f9eebb29ccdcdccf715dc2c4325bfc`:

```text
probe_volume_ratio20_min              1.25 -> 1.0
acceptance_hold_sessions              2    -> 2
acceptance_signed_distance_min        0    -> 0
acceptance_window_volume_ratio20_min  1.0  -> 1.0
```

The observed improvement is directly attributable on the same 120-context v2 development reference:
lowering the Stage-3 probe activity threshold recovered ten Stage-3 false negatives without adding
one definite-negative false positive, one Stage-4 false positive, or one ambiguous unique assertion.
R0 itself remained development-gate eligible; R1 is selected because it dominates R0 on Stage-3
expression and overall coverage while preserving the hard-error controls, not because R0 was
invalid.

## 3. Hard-gate behavior and parameter identifiability

The highest raw diagnostic scores were **not** eligible. Both `hold=1` variants reached macro F1
0.970588 and positive-reference coverage 0.954545, but their ambiguous unique-assertion rates were
0.238095 and 0.285714, above the frozen 0.10 ceiling. The hard gate therefore rejected the same
short-hold behavior that made development v1 misleading.

All `hold=3` variants were also rejected. They failed the Stage-4 precision-lower-bound and macro-F1
noninferiority gates; several additionally fell below the positive-reference coverage floor. The
v2 evidence therefore identifies `acceptance_hold_sessions=2` inside the preregistered search space
without relying on a score-only winner.

Four R1 trials passed all development hard gates. Two use `probe=1.0` and two use the R0
`probe=1.25`; within each pair, `acceptance_signed_distance_min=0` and `0.0025` produced identical
scores. The data therefore **do not identify** the signed-distance choice. `0` is frozen because it
is the unchanged, lower-complexity tie break relative to R0, not because the experiment proved zero
to be market truth. This claim ceiling is carried into the candidate config.

R2 was not run. The accepted R1 candidate has no attributable expression shortfall that justifies
opening the preregistered R2 model budget at this stage.

## 4. D/E acceptance

D passes its current development gate because the v2 supplement closes the Stage-3 sparsity and
ambiguity-audit defects that made v1 inconclusive, while preserving T-only reviewer evidence,
independent resolution accounting, deterministic artifact binding, and the synthetic-vs-human
claim ceiling.

E passes its current development gate because all 18 frozen R1 trials were retained, hard gates ran
before development ranking, the selected candidate improves R0 on the same immutable reference,
parameter non-identifiability is explicitly retained rather than hidden, and no R2/search expansion,
arbitration read, permanent-blind read, strategy result, or future settlement influenced selection.

`config/stage-learning/development-candidate.v1.json` is the sole frozen handoff from E to F. F may
not mutate this candidate after held-out result access. Any behavior-changing repair or parameter
change creates a new candidate version and consumes a new explicitly authorized validation budget;
it cannot relabel the already viewed holdout as untouched.

## 5. Authority ceiling and next gate

This closeout does **not** authorize a general 2022-2024 or 2025 data opening, a production LabelCard,
strategy consumption, daily scheduling, notification, paper/live trading, or an unrestricted
`QUALIFIED` claim. The candidate remains development evidence derived from
`SYNTHETIC_EXPERT_CONSENSUS`.

The next gate is `P3-STAGE-001-F`: freeze a one-time, read-only 2022-2024 arbitration evaluation
contract before any result access; preserve 2025 permanent-blind untouched; run the exact frozen
candidate without parameter search; retain failures; and cap a successful synthetic-reference
result at a bounded `SHADOW` external-semantic disposition unless a higher reference tier is later
obtained.
