# Head2 exact forty-session facts

`quanthus.stage_learning.head2_facts.build_head2_window_facts` is the separate
`head2-exact40-facts-v1` entry point for the approved retrospective comparison.
The generic Order and Trend bundles still require 60 and 61 observations for
their other concepts. Their admission requirements and bytes remain unchanged.

The caller supplies exactly 40 already admitted sessions for one security,
listing and venue, ending at the declared 2012–2021 anchor. The entry point
rejects wrong counts, anchors, identities, order or session continuity. It does
not sort, pad, fetch older observations, qualify sources or grant training use.
Native calendar/venue, raw correspondence and complete action coverage must
already be qualified by P10 and bound to the caller's frozen request.

Current and preceding widths use nonoverlapping 20-session windows. Directional
efficiency and the two path facts use the last 21 closes for 20 transitions.
The existing formula, measurement specification, Decimal values, source member
closure, observation revision and business clock are reused exactly. Regression
tests compare the full measurement dictionaries against the generic bundles.

An action coordinate change anywhere in the 40 sessions, or any rejected input,
produces UNAVAILABLE without adjusting prices, borrowing older state or emitting
a negative label. Zero prior width preserves the unavailable width-change fact.
A zero net direction preserves the undefined counter-direction fact and its
explicit reason; the separately versioned Head2 encoder handles its mask.

`reference_facts()` returns the existing reference schema with exact decimal
text and revision IDs. It supplies facts, not labels or numeric label thresholds.
Freeze the qualitative rubric before inspecting new facts. Independent blind
roles must not see issuer identity, role, candidate/R0 predictions or each other.
Keep disagreements and ambiguous evidence UNKNOWN in the original denominator.

The producer does not assert historical public/platform availability, calibrate
probabilities, activate P3 economic replay or modify P6. Source qualification,
reference support, model execution and consumer admission remain separate gates.

Focused check:

```sh
PYTHONPATH=src TZ=Asia/Shanghai python -m pytest -q tests/unit/test_stage_head2_facts.py
```
