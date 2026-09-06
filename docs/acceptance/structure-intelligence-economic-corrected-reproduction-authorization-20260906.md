# Structure Intelligence corrected reproduction authorization (2026-09-06)

| Field | Value |
| --- | --- |
| Status | owner-authorized bounded reproduction |
| Work item | `P3-STRUCT-002` |
| Governing preregistration | `structure-intelligence-economic-preregistration-20260906.md` |
| Frozen run config | `config/structure-economic-run.development.v1.json` |
| Run-config digest | `sha256:9a8a14a4855ddb189da2d8303c558a460ce781e63425942cda845e45e3ef66de` |
| Economic implementation baseline | `d338e20cf8d0bfce09bfc68e6e56ee59b263f6f3` |
| Authorization scope | exactly one final same-revision corrected A/B reproduction pair |
| Parameter changes | forbidden |
| Sealed partitions | forbidden |

## 1. Why a new human decision is required

The preregistration allowed at most two result-bearing Jobs before a new human decision. That
budget was consumed by:

1. `17a34068ab1b07e7203eb03b6724a550e7f4453d` Run A, which published the complete frozen economic
   evidence but exposed a derived-lifecycle conformance defect; and
2. `d338e20cf8d0bfce09bfc68e6e56ee59b263f6f3` corrected Run A, which reproduced the complete
   economic evidence and corrected the lifecycle disposition to
   `prefer_continuous_structure_facts`.

The owner has now explicitly directed Quanthus to continue along the recommended path until a new
approval/discussion boundary or the next milestone is about to begin. This record treats that
instruction as the new human decision required to authorize one final corrected independent
reproduction pair.

## 2. Exact authorization

The authorized pair must use one exact published Git revision whose economic implementation is
semantically identical to `d338e20...`. A documentation-only authorization commit may change the
Git/code-revision identity used by the immutable package, but it must not change:

- the run-config bytes or digest;
- C0/C1/C2 definitions;
- the continuous or coarse score mappings;
- the 2020-2021 development population or Structure warm-up boundary;
- candidate order, capital, allocation, position management, exits, P2 matcher, costs, or corporate
  action handling;
- materiality thresholds or bootstrap method; or
- the lifecycle decision rule fixed by the derived-lifecycle repair.

The Event Worker may create exactly two fresh Jobs, A and B, for that one revision. The pair must run
serially under the existing event-queue concurrency and read DWD Base/correction only through the
existing read-only mounts.

## 3. Required closeout evidence

`P3-STRUCT-002` may close only if the final A/B pair agrees on:

- Structure ADM manifest digest;
- C0, C1, and C2 replay digests;
- all variant metrics;
- all C1-C0, C2-C0, and C2-C1 comparison values;
- lifecycle disposition `prefer_continuous_structure_facts`; and
- sealed-partition and execution-authority false claims.

The pair must also reproduce the already observed immutable economic core from corrected Run A:

- C0 return `0.06704618`, maximum drawdown `0.07102827`;
- C1 return `0.06433900`, maximum drawdown `0.06168277`;
- C2 return `0.05718376`, maximum drawdown `0.07360706`;
- C2-C1 return delta `-0.00715524`;
- C2-C1 maximum-drawdown delta `0.01192429`; and
- C2-C1 materiality `true` under the frozen rule.

Any divergence is a stop condition and requires a new owner review. No same-work-item tuning is
authorized.

## 4. Claim ceiling

This authorization does not convert the development comparison into forward evidence. It does not
authorize the thirteen-state draft, model training, Shadow, paper, live execution, sealed-year
access, or a new strategy topology. Its only purpose is to finish deterministic economic
qualification of `P3-STRUCT-002` under the already frozen experiment.
