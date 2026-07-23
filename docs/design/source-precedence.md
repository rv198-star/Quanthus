# Design Source Precedence

The Notion pages remain useful for discussion and rationale. The repository is the approved
implementation baseline.

## Precedence

1. Frozen configuration and passing contract tests.
2. Repository PRD, HLD, and LLD.
3. P0 decision blocks on the Notion feedback page.
4. Original Notion chapter text.

## Known superseded Notion text

The original Chapter 01 contains two statements that must not guide implementation:

- "forward-adjusted plus raw price dual track" is superseded by unadjusted prices plus
  effective-date adjustment factors;
- "current sector constituents as a fallback" is superseded by disabling the sector expert
  and sector filter in official results.

The static level-one industry mapping remains allowed only for the concentration cap and must
be marked as such in data lineage.
