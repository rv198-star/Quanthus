# Facts v2: bounded derivative parent-authority repair

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-004` / #216; B2 #219 |
| Predecessor repair | `structure-facts-v2-provider-scope-repair-20260907.md` |
| Last failed revision | `fd7224bb33abf741cd1e08cf36819881e051cd61` |
| Last failed Job | `job_b1714e40e8064d0bb864753aff0a8d77` |
| Terminal state | failed, no `result_ref`; no successful result-bearing Facts v2 run exists yet |
| Failure stage | `provider-open`, before B2 admission / B3-B5 facts / consumer / P2 |
| Change class | provenance/authority repair only; no Facts v2 calculation, population, scoring or execution change |

## 1. Why the previous scope repair was still incomplete

The prior pre-result resource repair correctly reduced the physical Direct-DWD materialization from the general 2011-2021 development scope to the frozen Facts v2 window. That removed the observed DuckDB out-of-memory path and allowed materialization to advance far enough to expose the next contract failure.

Revision `fd7224b` then failed closed with:

```text
structure_v2_failure:provider-open:ConfigurationError:
direct DWD canonical projection receipt binding differs
```

This failure is correct. `config/p4-direct-dwd-canonical-projection.v1.json` freezes the accepted full canonical projection identity, including its provider config digest, history start, development scope, row counts and canonical table identities. The new bounded provider has a different scope/config and therefore MUST NOT claim byte/semantic equality to that full canonical projection.

The error was not evidence that the bounded source data are wrong. It proved that the provenance relation had been modeled incorrectly: the bounded TMP projection is a **derivative of the accepted canonical source authority**, not the canonical projection itself.

## 2. Rejected repair

Do not weaken `_bind_canonical_projection_identity()` to compare only the DWD package/correction or to ignore provider config/scope/count mismatches. That would silently expand what the canonical receipt proves and would weaken every existing Direct-DWD caller.

Also do not force each bounded Facts v2 cold run to rebuild the full 2011-2021 canonical projection merely to satisfy receipt equality. The accepted sharded full-range path is deterministic and resource-bounded, but historical production evidence shows complete runs in roughly 1,600-2,900 seconds. That is disproportionate for the fixed three-security 2020Q1 Facts v2 qualification and would reintroduce large unnecessary TMP work.

## 3. Accepted pre-result repair shape

Introduce an explicit, opt-in bounded derivative session path:

```text
accepted DWD Base + correction
        +
accepted canonical projection receipt (parent authority)
        ↓
bounded Direct-DWD TMP projection
        ↓
Facts v2 B2-B5 qualification
```

The ordinary canonical path remains unchanged.

The bounded derivative path must:

1. validate the exact DWD Base and correction identities through the existing authority checks;
2. load the accepted canonical projection receipt and require its exact schema;
3. require the same materializer version and exact DWD Base/correction bindings as the current source authority;
4. retain the parent canonical bundle digest, parent receipt id and parent receipt SHA-256 as lineage only;
5. use the bounded provider's own config/scope/history start and physical/content digest as the derivative identity;
6. never set the bounded bundle's `canonical_identity_digest` to the parent's full-range canonical digest;
7. publish `projection_kind=bounded_derivative` and include the parent authority fields in the provider lineage used by B2 readiness;
8. require independent cold A/B reproduction of the bounded derivative and downstream semantic artifacts before any B2-B5 closure.

This makes the authority relation explicit without introducing a second canonical truth or a reusable cross-Job cache.

## 4. Implementation isolation

The additive entry point is `open_bounded_direct_dwd_session()` in `src/quanthus/backtest/direct_dwd_provider.py`.

It is intentionally separate from `ReplayDataProvider.open()` and from `open_direct_dwd_session()`. Existing P4, replay, economic and other Direct-DWD users retain their strict canonical-receipt behavior. Only the fixed Facts v2 runner opts into the bounded derivative semantics.

The parent authority validator does not treat parent scope/provider-config equality as a property of the child. It does require parent materializer, Base/correction authority and canonical identity to remain exact. Any parent receipt, DWD or materializer drift fails closed.

## 5. Frozen question remains unchanged

This repair does not change:

- security order `000001`, `300750`, `600519`;
- scan/report dates;
- 20/60 prior-window rules;
- raw CNY coordinate policy and action-epoch resets;
- anchor/interaction/episode semantics;
- compatibility consumer mapping;
- P2 maximum probes, quantity, fees or execution policy;
- review sample rule;
- retrospective claim ceiling;
- two-result cold A/B budget.

The calculation-spec digest remains the previously frozen value. The physical provider lineage changes because its provenance relation is now correctly represented.

## 6. Verification before a real retry

Focused regression must prove:

- parent canonical authority accepts exact Base/correction/materializer and rejects drift;
- ordinary canonical Direct-DWD projection tests remain unchanged;
- Facts v2 request/provider scope remains bounded;
- Facts v2 runner uses the explicit bounded derivative entry point;
- B2 readiness binds the derivative projection kind and parent authority fields;
- all existing Facts v2 semantic/tamper/Worker/document tests pass.

After repository gates pass, publish/deploy one corrected revision, submit only corrected A, and inspect terminal state. Only if A succeeds may the one independent same-revision B run be submitted. No parameter tuning or repeated successful reruns are permitted.

## 7. Claim ceiling

At this checkpoint:

```text
B1: CLOSED
B1-R: CLOSED
B2 implementation: present, real qualification OPEN
B3-B5 implementation: present, acceptance BLOCKED BY B2
Successful Facts v2 result-bearing run: NONE
Facts v2 runtime accepted: NO
Economic / Shadow / paper / live claims: NONE
```
