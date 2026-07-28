# Design Source Precedence

The repository is the approved implementation and design baseline. Notion pages and attached
discussion documents remain useful as historical rationale or exploratory input, but they do not
change repository authority by themselves.

## Repository authority

- Frozen configuration and contract tests are executable policy and behavior evidence.
- Approved PRD, HLD, and LLD documents own current product intent, boundaries, and design.
- Accepted ADRs own the decision and rationale that changed those baselines; the affected HLD/LLD
  current-state text must be reconciled in the same change.
- Runbooks own operating procedure. Acceptance and audit files own point-in-time evidence.
- Drafts and exploratory documents own no implementation or delivery authority.

Executable policy and approved design are required to agree. A conflict is a conformance defect;
the higher executable runtime authority is not permission to leave the design stale, and approved
prose is not permission to ignore a frozen contract.

## External-source precedence

When a repository decision conflicts with older external material, use this order:

1. Frozen repository configuration, contract tests, and approved repository design.
2. Accepted repository ADRs awaiting same-change baseline reconciliation.
3. P0 decision blocks on the Notion feedback page.
4. Original Notion chapter text and later exploratory discussions.

## Known superseded Notion text

The original Chapter 01 contains two statements that must not guide implementation:

- "forward-adjusted plus raw price dual track" is superseded by unadjusted prices plus
  effective-date adjustment factors;
- "current sector constituents as a fallback" is superseded by disabling the sector expert
  and sector filter in official results.

The static level-one industry mapping remains allowed only for the concentration cap and must
be marked as such in data lineage.
