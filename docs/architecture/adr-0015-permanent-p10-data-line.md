# ADR 0015: Permanent P10 data acquisition and quality ownership

- Status: Accepted
- Date: 2026-10-03
- Authority: explicit Owner instruction and follow-up in the P3/P5 continuation
- Work item: `P10-DATA-000`
- Governing design: `../design/lld/12-permanent-data-line.md`
- Ownership policy: `../../config/data-ownership.v1.json`

## Context

Research input qualification repeatedly expanded into data recovery and temporary quality repair.
Acquisition, warehouse reliability and shared defects were spread across P1, meta and unclassified
issues. The Owner assigned acquisition and all data-quality/governance issues to permanent P10.
Other business lines may only register those issues and choose bypass, temporary ignore or
acceptance of a known limitation, focusing on their business problem.

## Decision

P10 owns acquisition, quality diagnosis/repair, source/PIT evidence, lineage, retention, publication
and handoff. It is parallel and permanent; P1 historical acceptance remains unchanged.

Consumers own inputs, minimal admission checks, domain algorithms, scientific reference criteria,
business disposition and handoff acceptance. They must not maintain private source-recovery or
quality pipelines. Registering a defect does not stop the whole line: choose and disclose a
business route; stop only a step violating an existing hard safety/PIT/partition/activation gate.
Acceptance of a limitation never proves missing provenance or repairs data.

Preserve existing issue ids, titles, evidence, subgroup dependencies and execution gates. Migrate
the reviewed open inventory only; closed historical issues stay unchanged. Earlier temporary
approvals/outputs remain sealed but confer no future repair authority outside P10. The standing
epic remains open while bounded children may close with evidence.

## Consequences

P3 #412 and P5 #402 retain business input admission, not source recovery/repair. Their required
inputs remain unaccepted, and their frozen research/activation gates remain intact. #424 remains
a P10 subgroup with its task-scoped SSH override and release boundaries.

P10 may return unavailable evidence but cannot invent original provenance. Business owners may
accept a disclosed limitation only within their contract; changing ownership does not change
cutoffs, windows, partitions, exceptions, trading safeguards or research truth. No deployment,
collection activation, cleanup or P6 production mutation is authorized by this decision.
