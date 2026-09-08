# P3-STAGE-001 D/E synthetic development iteration v1 audit

| Field | Value |
| --- | --- |
| Evidence scope | development-only diagnostic; not confirmatory qualification |
| Job | `stage_46270ac8a6994039861005e742f47203` |
| Code revision | `217e197960a790af0fff7bfdca02349afc4c24b7` |
| D pilot manifest | `sha256:806706515260785d1efb82fada675d802812f8f8c8c7c16e285ede61a90b3dd0` |
| Synthetic review manifest | `sha256:244437c9f0fe8d6270db4b8b30f2254f427b3365203d715c4850c659a5861cf4` |
| Resolution manifest | `sha256:ba77afc6fa3d2a61eba4c5612f4825ace2b337df1ca77eb04691d24dade677cc` |
| E learning manifest | `sha256:4c381483173c7369f753c64d5592e254f3c7123b2a1897f5b45aab13299860bf` |
| Workflow manifest | `sha256:bb73bf3260e8f25b8f66e7da04d64839eaa3eaaa0eec7636f10473f03f191622` |
| Disposition | `INCONCLUSIVE / do not accept development rank #1` |

## Result retained

The first synthetic-development workflow completed successfully as an engineering Run. The 120-context T-only reference contained 92 `definite`, 24 `coexistence`, and 4 `ambiguous_set` resolutions. The two synthetic primary archetypes disagreed on 10/120 contexts; all ten were adjudicated by the frozen third archetype. The institutional challenge lane inspected 32 contexts and the quantitative evidence auditor issued zero PIT/evidence vetoes. The evidence tier remains `SYNTHETIC_EXPERT_CONSENSUS`; it is not human-expert validation.

R0 used the frozen 1.25 probe-volume threshold, two-session acceptance hold, zero signed-distance margin and 1.0 acceptance-activity threshold. On the v1 development sample its macro F1 was 0.9643. The diagnostic rank #1 R1 setting reduced the hold requirement to one session and reached macro F1 0.9915, with stage-4 recall increasing from 0.8667 to 0.9667. Both had zero observed false positives on the small definite corpus.

That diagnostic improvement is **not accepted as a parameter-selection result**. Only three definite stage-3 references existed, so the probe threshold was not statistically or semantically identifiable. Across the 18 frozen R1 trials, changing the probe-volume threshold among 1.0/1.25/1.5 and the signed-distance margin among 0/0.0025 frequently produced identical scores; the sample therefore did not discriminate those parameters. More importantly, the one-session hold family raised unique assertions on the four ambiguous references from 1/4 under R0 to 2/4. The previous ranking implementation prioritized macro F1 before ambiguity control and therefore contradicted the governing design priority of hard gates before multi-objective selection.

The first `coverage` field is also retained only as legacy evidence: it counted any predicted 3/4 label over all definite references, including explicit negative controls in the denominator. It is not an appropriate positive-reference coverage metric and is removed from development selection authority in the successor implementation.

## Corrective development iteration

The v1 evidence is not rewritten. The successor is a new development iteration with four explicit changes:

1. `synthetic-expert-panel.v2.json` makes activity strength part of the stage-3 semantic review instead of treating any available activity value as sufficient.
2. The next 120-context sample is a deterministic development supplement, excludes all first-pilot contexts, and deliberately covers 40 stage-3 probes across the frozen activity thresholds, 30 hold=1/2/3 acceptance boundaries, 20 near-anchor loss negatives, 15 reclaim ambiguity controls, and 15 mature-held coexistence controls.
3. `development-selection.v1.json` applies hard sufficiency, ambiguity, negative-control, precision and positive-reference-coverage gates before ranking. If no R1 trial passes, the only permitted result is `INCONCLUSIVE`; no rank #1 is emitted.
4. D now publishes exact `learning-inputs.jsonl` beside the blinded reviewer packet. E consumes that immutable T-only projection instead of scanning DWD and rebuilding StructureV2 a second time inside the same Job.

This corrective iteration remains entirely inside 2012-2021 development authority. It does not open 2022-2024 arbitration, 2025 permanent blind, formal LabelCard qualification, SHADOW, strategy consumption, or trading. R2 remains closed unless a later accepted R1 audit demonstrates an attributable expression shortfall rather than sample insufficiency or ambiguity-control failure.
