# Historical carried-sell integration successor

Engineering continuation record for [P3 historical robustness](p3-historical-robustness.md).
This record explains implementation and evidence; the frozen requests govern scope.

The qualified optional-rights input passed the former 2014-10-24 failure region.
Both roles independently built the same feature/candidate prerequisite. The next
real stop was `formal_sell_bonus_share_unsupported`, before completing B.

P2 already applies corporate actions before the open, adjusts the same pending
sell proportionally, and rejects fractional shares or pending proportions. Its
existing synthetic replay proves the order adjustment. The formal P3 adapter had
an additional capability guard because lifecycle carry checks require unchanged
quantity. This is an execution integration gap, not evidence of another source
defect or a discretionary subscription decision.

`historical-bonus-integration.v1.json` binds a distinct execution adapter to the
unchanged scientific request, source and exact independently built preparation.
Every arm uses the same adapter. It consumes the actual P2 corporate-action event
before-images to reconcile the post-action/pre-trade ledger before invoking the
unchanged lifecycle validation. The action is not executed twice; its event and
the original order adjustment remain in the audit stream. Unexplained quantity
changes, inconsistent before-images and fractional pending proportions still fail.

The original formal adapter, position state machine, P2 matcher, source guards,
costs, shares entitlement, source clocks, frozen preparation and prior failed
runs remain unchanged. The new parameter/code metadata binds the execution
supplement. Its purpose remains the approved retrospective development comparison;
there is no original B/D, historical PIT, P6 or production admission.

Each role may reuse only its own immutable prerequisite, originally built
independently in this Job and rechecked against its full identity. This does not
claim another independent reconstruction. The cold role retains its independent
physical source and prerequisite and kernel-enforced denial of primary/old inputs
and source networking. It must run a fresh replay process. Previous spent compute,
failure evidence and the 16:50:56 UTC successor deadline are not reset.

Regression coverage includes the original guard, full carried exit through a
bonus and cash dividend, integral partial sell, fractional proportion rejection,
missing/tampered corporate-action evidence and exact execution admission.
Unit success alone is not three-arm comparison acceptance.


The first real integration attempt then exposed a second adapter mismatch:
`HISTORY_ACTION_BINDING`. Sell signals bind the new effective input snapshot,
while normalized actions retain their original governed source snapshot. The
original historical action guard expects an action-specific child run; a carried
sell executes both in one P2 sequence. Version 2 binds that exact active sell run
and validates its effective view separately from the unchanged original-action
source/cutoff check. The binding is cleared in `finally`; it does not apply to
other action calls. Wrong action sources remain rejected. The original v1
request and commit remain preserved; v2 has a distinct code-bound execution
request and retains all prior spent time and the same deadline.
