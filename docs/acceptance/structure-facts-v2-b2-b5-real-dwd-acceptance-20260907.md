# Facts v2 B1-B5: supplemental terminal-result cross-check

| Field | Value |
| --- | --- |
| Authority | Supplemental verification evidence; no independent delivery status |
| Canonical closeout | `structure-facts-v2-b1-b5-final-acceptance-20260907.md` |
| Result-bearing revision | `723f22feeb7dfbecb2bffbc83662ced7bb29822d` |
| Run A | `job_5165e1bd99614b6186b58f228c770971` |
| Run B | `job_064e89c9ebbe4ed49badd8e07b78ea88` |

The terminal MCP records and immutable Run manifests were read back for both independently
executed Jobs. Input-readiness, structure, ADM and result identities match those recorded in the
canonical closeout. Both Jobs ended succeeded, progress 100, error null. Re-submission of B's same
idempotency key returned created=false and did not create a duplicate Job.

A separate precision-28 Decimal calculation over the twelve MCP-returned T-visible sample rows
recomputed `close / frozen_level - 1` exactly. All twelve matched. Visible start/held/lost/below/
reclaimed signs, start age/hold, and nonnegative hold<=age were consistent. Frozen reference keys
and prior-loss links in the selected loss/reclaim sequences were also reviewed. No future returns
or P2 outcomes were used for sample selection or this semantic judgment. This is author-side
verification, not external human or independent-model certification. It does not independently
authenticate source timestamps or the whole warehouse.

A read-only comparison with the first runtime implementation commit `e5b9b03` also confirmed the
calculation specification stayed unchanged through the prerequisite/provenance repairs:

```text
calculation spec:
sha256:e405f68fd1efed10c3e02cc643a35c2b479cace074c9df7bfca75ef0e0c46cc1

current bounded request:
sha256:c9a8082f351499ca7b2a2f0469d1c405c6e8b3ad5b428e7f9e48e0935316b92d
```

Each Job reported one independently rebuilt application TMP prerequisite and zero cross-Job cache
hits. This is not a claim that host OS page caches were flushed. A's Job wall was 78.574 seconds,
B's 77.569 seconds. The difference between Job wall and the listed child-stage timings is not
separately attributed. Physical audit/runtime/manifest hashes differ as expected; actual operation
clocks must not be fabricated to force A/B equality.

The scope and child closure remain owned by the canonical closeout and its B2, B3/B4 and B5 evidence
files. Full public/platform historical availability, economic value, broader geometry/activity,
formal investment expertise and forward execution are not established by this supplemental check.
