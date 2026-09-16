# P3-STAGE-030 strict PIT route four-party review

| Field | Value |
| --- | --- |
| Date | 2026-09-16 |
| Work item | `P3-STAGE-030` |
| Review target | next-phase objective, clock authority, gates, and stop conditions before PIT implementation |
| Machine preregistration | `../../config/stage-learning/stage-pit-public-reconstruction-prereg.v1.json` |
| Input result | P029 real-history layered replay |
| Review mode | four role-separated expert perspectives; this is architecture/product review, not external human certification |

## Decision

Proceed with **strict `PUBLIC_RECONSTRUCTION` / `counterfactual_recomputation`** for the exact P029
Stage10/11 population. Do **not** claim `PLATFORM_AS_KNOWN` for 2020. Quanthus did not historically
consume those observations, so there is no contemporaneous platform-receipt family to reconstruct.
Present-day DWD `knowledge_from`, file build time, or current artifact existence must not be renamed
into actual historical platform knowledge.

The first execution gate is a **capability audit**, not a Stage replay. It must establish whether the
exact causal input closure can be reconstructed under independently justified historical availability
and source-version rules. If not, P030 stops at the evidence boundary rather than weakening the PIT
definition.

The historical decision cutoff remains **15:30 Asia/Shanghai on T**, matching the existing bounded
Stage/F9 source adapter: after the T close and before earliest T+1-open execution. A later cutoff would
change the decision problem; an earlier cutoff would contradict the existing Stage observation
construction.

## Short-term / event-driven perspective

The useful question is not whether a 2026-built file can reproduce a 2020 chart; P029 already proved
that. The useful question is whether the same structural-failure and rebound description could have
been formed at T without future corrections.

`PUBLIC_RECONSTRUCTION` is therefore the relevant clock. An actual 2020 Quanthus platform receipt is
not required for a counterfactual trader-view study and cannot honestly be manufactured. The 15:30
cutoff is appropriate because Stage10/11 consumes completed daily information and the governing
profile cannot execute before T+1 open.

The review requires all ten P029 observations to remain in the denominator. If one becomes
unavailable under PIT evidence, that is a result, not permission to substitute another example.

**Disposition:** approve capability-audit-first public reconstruction; reject any semantic or cutoff
repair to preserve retrospective labels.

## Fund / portfolio-risk perspective

The main business risk is hindsight being mistaken for contemporaneous risk knowledge. Stage10 must
not remain valid merely because a later repaired source says the support failed, and Stage11 must not
appear earlier because a later source revision makes the rebound cleaner.

The route must also preserve **context exit semantics**. A corporate action or source correction that
was not known at T cannot retroactively change the coordinate system used by the historical risk
meeting. Conversely, if a required action was publicly known by T, the PIT route must honor it.

The denominator must remain 10/10 for a pass. Partial reconstruction may be informative, but it is
not enough to claim that the accepted Stage10/11 representation has crossed the PIT gate.

**Disposition:** approve the frozen route; treat incomplete PIT coverage as inconclusive, not as a
smaller success sample.

## Quant research perspective

P030 is a **data/clock validity experiment**, not a prediction test. Therefore retrospective Stage
predicate agreement must not be an acceptance gate. A correct PIT replay may disagree with P029; the
number and direction of those disagreements are primary evidence.

The quantitative outputs must distinguish:

- source-version/availability proof coverage;
- reconstructable versus unavailable observations;
- Stage10 and Stage11 predicate changes versus retrospective;
- dependency availability lag;
- any non-null `primary_stage` defect.

The largest technical risk is incomplete revision-family evidence. A present-day final historical
bar plus an economic session-close rule does not by itself prove that the same value/version was the
unique public version at T. The capability audit must either close that proof or fail closed.

**Disposition:** approve `PUBLIC_RECONSTRUCTION` with `RECOMPUTATION`; explicitly reject
`PLATFORM_AS_KNOWN`, forced retrospective equality, resampling, and inferred source arrival.

## Data-governance / warehouse perspective

Repository authority already separates business time, source availability, generation/publication,
and actual consumption. The existing B1-R selector requires complete revision families and
availability evidence; the accepted B2 Stage/Facts lane is explicitly retrospective only. P4 also
encountered the same DWD knowledge-time defect and later used a development retrospective economic
availability projection with `per_decision_publication_proven=false`. P030 must not relabel that
precedent as strict PIT.

The capability audit must evaluate each dependency class separately:

1. daily bar: session-completion is a candidate economic-availability rule, but source-version
   completeness still needs proof;
2. realized calendar: historical realized sessions may use conservative session/date completion,
   without claiming future schedule knowledge;
3. listing/security identity: only stable identity/effective-interval evidence may be used;
4. corporate-action coordinate reset: announcement/availability evidence must precede the cutoff;
5. quality/source revision: later-discovered quality or corrections cannot rewrite the historical
   denominator.

**Disposition:** approve capability-audit-first. If the exact source revision family cannot be proven,
close `BLOCKED_SOURCE_VERSION_OR_AVAILABILITY_LINEAGE` before Stage replay.

## Consensus route

All four perspectives converge on the same sequence:

1. **Capability audit** — prove or reject historical evidence sufficiency for the exact P029 causal
   closure. No Stage result yet.
2. **Protocol freeze** — only if the capability audit finds a defensible route; bind the exact source
   evidence, cutoff, revision-family rules, denominator, metrics, and fail-close semantics.
3. **Implementation** — build one bounded PIT selector/replay path using existing temporal contracts;
   do not alter Stage10/11 semantics.
4. **Exact run** — recompute the fixed 10-observation population. Do not require agreement with
   retrospective P029; record divergence.
5. **Four-party result review** — decide whether PIT has passed, is partial/inconclusive, or is blocked.
   No automatic successor.

## Explicit phase target

P030 succeeds only if **all 10** P029 observations can be recomputed from an authenticated historical
input/version closure under the frozen public-reconstruction clock. The success claim is limited to:

> the accepted Stage10/11 development projection is reconstructable from information defensibly
> available by the historical decision cutoff for this exact real-history population.

It does not claim prediction, economics, actual historical Quanthus consumption, official LabelCards,
production expert authority, strategy authority, or trading authority.

## Mandatory re-review triggers

Stop implementation and reconvene the four roles before continuing if any of the following occurs:

- `PUBLIC_RECONSTRUCTION` would need to become `PLATFORM_AS_KNOWN` or retrospective;
- the 15:30 historical cutoff needs to move;
- a new historical dataset/source authority must be introduced;
- incomplete revision history would be replaced by assumed arrival/version times;
- the 10-observation denominator would change;
- Stage10/11 thresholds or parent semantics would change;
- 10/10 complete reconstruction would be weakened to a partial pass.

These are objective changes, not implementation details.
