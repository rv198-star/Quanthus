# P3-STAGE-009-A Four-Responsibility Adversarial Review

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-009-A |
| Design | `../design/stage-positive-instability-proposition-repair.md` |
| Machine design | `../../config/stage-learning/positive-instability-repair-design.v1.json` |
| Review mode | one ChatGPT assistant simulating four responsibilities |
| Independent experts / humans | no |
| Result | DESIGN APPROVED / 0 open directional blockers |

## 1. Review method

This review is same-origin adversarial review, not independent expert validation. Four responsibilities
were simulated separately:

1. short-term trader;
2. fund manager;
3. quant researcher;
4. data / evidence-governance reviewer.

Each responsibility had to provide:

- one reason the successor direction is correct;
- at least one counterexample;
- one implementation veto;
- one response to another responsibility's objection.

The consumed P3-STAGE-008 evidence was used only to identify failure shapes already exposed. No
numerical threshold was selected from it.

## 2. Short-term trader review

### Why the direction is correct

A trader distinguishes “price is moving somewhere” from “price cannot organize itself”. A range can
expand during a clean trend, limit-up sequence, repricing gap or orderly trend leg. Conversely, a
stock can remain inside a broad range yet repeatedly traverse and reverse through it. Therefore
`width expansion OR center migration` is not a faithful instability definition.

### Counterexamples

**Monotonic trend with expanding range.** A smooth 20-session advance may widen the current range and
move its center dramatically. Calling that direction-neutral disorder would recreate the Stage-008
failure.

**One-step repricing then flat.** A large gap followed by a stable plateau moves both range and center.
At daily resolution it is relocation, not positive instability.

**Up-down-up ending at the range edge.** Endpoint traversal can be high while the path contains a
large adverse reversal. Traversal alone therefore cannot become the new directional veto or the new
instability proof.

### Implementation veto

Reject any implementation where `width_change`, `center_shift`, `range_width` or
`directional_efficiency` can independently emit `structural_instability_20=TRUE`.

### Response to fund objection

The fund view is right that broadness alone can describe a stable valuation band. The broad-reversal
channel therefore needs path-disorder corroboration, not just a wide box.

## 3. Fund-manager review

### Why the direction is correct

A fund manager cares about whether a structure is converging, relocating, or losing organization.
Center movement is especially dangerous as a standalone signal: a company can reprice to a new level
while volatility contracts. “Contraction + center shift” should often be a migrating structure or
`NEITHER`, not disorder.

### Counterexamples

**Strong contraction with large center migration.** This is the exact failure family exposed by 008.
The new design correctly removes center shift from the truth function.

**Broad stable box.** A high absolute range width can persist without current disorder. Broadness alone
cannot be positive instability.

**Broad reversal while the range is contracting.** Requiring fresh expansion would be too strict. A
previously extreme range can contract modestly while still containing violent two-sided reversals.
The broad-reversal channel is therefore necessary.

### Implementation veto

Reject any rule that makes positive width expansion a necessary condition for all instability. That
would erase legitimate broad-reversal disorder and overfit the post-hoc 008 counterfactual.

### Response to trader objection

The trader's one-step repricing case is exactly why path organization must be represented separately
from range movement. A clean one-way move should be relocation-positive and instability-negative.

## 4. Quant-research review

### Why the direction is correct

The defect is structural: old R0 OR'ed raw correlated measurements into a label. The successor instead
uses a small OR of explainable **channels**, each of which is itself a conjunction of semantically
different dimensions. That creates interpretable false-positive attribution and avoids a weighted
score whose meaning changes as correlated features move together.

### Counterexamples and risks

**Micro-range noise.** Directional efficiency can be close to zero in a trivially narrow range. A
path-disorder signal therefore requires amplitude or expansion significance.

**Zero-net broad oscillation.** Counter-direction excursion is mathematically unavailable when net
direction is zero. The implementation must not manufacture a sign. Low traversal + low efficiency +
material width is a valid zero-net route.

**Channel overlap.** Expanding disorder and broad-reversal disorder can both be true. That is legal;
the system must report both supporting channels rather than double-count them in a score.

**Consumed-evidence leakage.** The diagnostic result that removing center-only authority would have
crossed 008 gates is strong causal evidence about the defect, but using its observed metric optimum to
choose 009 thresholds would invalidate future evidence.

### Implementation veto

- no opaque weighted composite;
- no post-hoc threshold choice from 008 cases;
- no automatic grid expansion after a result;
- no duplicate Fact family for information already represented by range width, width change,
  directional efficiency, traversal and counter excursion.

### Response to data-governance objection

A proposition contract can safely reuse implemented-unqualified Path Geometry in B/C synthetic and
consumed-evidence diagnostics, but that does not promote those Facts to runtime-qualified status.
Qualification remains a later D concern.

## 5. Data / evidence-governance review

### Why the direction is correct

The root cause is partly a conformance defect: approved 008 design said center shift alone cannot
assert Stage 0, while executable candidate logic still inherited that branch. The successor must make
evidence authority explicit in machine-readable form so a future implementation cannot silently
reintroduce the shortcut.

### Counterexamples and evidence hazards

**Listing / coordinate reset.** A reset can create apparent range movement, center movement or path
excursion. Existing contiguous admitted-row and coordinate-reset semantics must remain authoritative;
missing/reset input never becomes Stage 0.

**Recent-window contamination.** 009 must continue using Primary-first reference semantics. No 10-day
modifier may mutate the 20-day instability judgement.

**Zero-net unavailable value.** `counter-direction-excursion-share@20` being unavailable for zero net
is not missing market data and must not fail the entire Primary proposition if the broad-reversal
channel has another valid zero-net path.

### Implementation veto

- do not add a new Fact merely to encode the new proposition;
- do not put thresholds into Fact identities;
- do not reopen 2022–2025 or runtime Jobs during B;
- do not call same-origin role simulation independent validation;
- keep reason codes and supporting-channel ledger auditable.

### Response to quant objection

The quant concern about channel overlap is resolved by storing channel truth separately and applying
logical OR once. Evidence provenance must preserve both channels without numerical accumulation.

## 6. Cross-review findings

| Finding | Resolution |
| --- | --- |
| PI-001 center shift retained truth authority in old executable logic | CLOSED — center shift removed from proposition truth function |
| PI-002 expansion alone can describe clean directional repricing | CLOSED — expanding-disorder requires path-organization evidence |
| PI-003 expansion cannot be mandatory because broad reversal may persist during contraction | CLOSED — separate broad-reversal channel |
| PI-004 zero-net oscillation makes counter-excursion unavailable | CLOSED — zero-net route uses low traversal + low efficiency + material width; no invented direction |
| PI-005 broadness/low efficiency can occur in trivial micro noise | CLOSED IN DESIGN — future thresholds must include materiality; no thresholds selected in A |
| PI-006 channel overlap could become hidden double counting | CLOSED — logical channel OR, no weighted composite |
| PI-007 008 evaluation could contaminate threshold choice | CLOSED — permanently diagnostic-only for 009 |
| PI-008 new Fact could be added prematurely | CLOSED — no new Fact authorized by A |
| PI-009 Stage 2 could be collateral-damaged | CLOSED — Stage 2 frozen |
| PI-010 same-origin review could be overstated | CLOSED — provenance explicitly same-origin simulation |

## 7. Final verdict

```text
DESIGN: APPROVED
DIRECTIONAL DESIGN BLOCKERS: 0 OPEN
P3-STAGE-009-A: CLOSED
P3-STAGE-009-B: READY — IMPLEMENTATION ONLY
MARKET DATA READ: NOT AUTHORIZED
RUNTIME JOB: NOT AUTHORIZED
PARAMETER SEARCH: NOT AUTHORIZED
2022-2024 / 2025: CLOSED
STAGE 2 CHANGES: NOT AUTHORIZED
```

This verdict means the semantic architecture is internally coherent across the four simulated
responsibilities. It is not independent expert validation and does not qualify the proposition or its
future thresholds.
