# P3 Bounded Synthetic Attribution Publication Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded synthetic closure passed |
| Evidence date | 2026-07-31 |
| Scope | Registered attribution kernel, outcome-blind planning, resumable synchronous execution, atomic publication, local CLI, and read-only report-center consumption |
| Governing design | `docs/design/lld/10-monitoring-attribution/strategy-attribution.md` |
| Governing work | Issues #50 through #53; bounded synthetic portion of `P3-ATTR-001` only |
| Gate source | `config/p3-gates.v1.json` |

## 1. Decision

The bounded synthetic sequence implemented by Issues #50 through #53 passes its local closure.
The accepted surface registers and executes attribution methods, binds their outputs to resolvable
evidence receipts, compiles questions without reading outcomes, persists and resumes one local
synchronous Pipeline, deeply verifies a complete immutable release, and lets the read-only Web
workbench consume the exact publisher output.

This is plumbing acceptance, not formal strategy attribution. There is no formal strategy adapter,
no real market attribution result, no recurring report, and no access to a new arbitration or blind
partition. `P3-ATTR-001` remains `deferred`, `P3-DASH-001` remains `deferred`, and Issue #54 remains
blocked by its declared recovery conditions.

## 2. Acceptance matrix

| #53 acceptance concern | Evidence | Result |
| --- | --- | --- |
| Same semantic input produces identical run and publication bytes while publication time stays in a transport envelope | `test_double_run_and_double_publish_have_identical_semantic_bytes`; canonical serialization tests across kernel, planner, and Pipeline | passed |
| Every request, plan, experiment, receipt, claim, and workspace parent is parseable and can be deterministically reconstructed | `verify_attribution_release`; publication closure, embedded request, Pipeline status, and workspace reconstruction tests | passed |
| Fake digests, byte tampering, broken parents, context drift, interrupted staging, and repeated/conflicting publication fail closed | adversarial cases in `tests/unit/test_strategy_attribution_pipeline.py` and `tests/unit/test_strategy_attribution_publication.py` | passed |
| Invalid and unavailable results remain visible without becoming trusted directional evidence | publishable/display claim separation and synthetic-source reseal rejection tests | passed |
| The Web viewer consumes an actual synchronous Pipeline release and preserves published, imported, and synthetic trust differences | dynamic Python-to-Web publication test in `web/tests/rendered-html.test.mjs` | passed |
| A Web entry receives published trust only after `READY` binds the canonical checksum ledger and the ledger binds the consumed release/workspace bytes | checksum-chain success and workspace-tampering failure cases in `web/tests/rendered-html.test.mjs` | passed |
| The five minimum CLI commands plan, run, inspect, resume, publish, and deeply verify only the explicit synthetic fixture | `tests/unit/test_strategy_attribution_cli.py` | passed |
| No browser code recomputes attribution, upgrades evidence, ranks strategies, or treats a manual import as verified | browser source-boundary assertions and empty default publication catalog | passed |

## 3. Accepted behavior

- Planning bytes bind the complete request, PIT context, registered methods, capability manifest,
  question set, and deterministic seeds before the first executor call.
- The synchronous Pipeline stores immutable stage receipts, executes each shared physical node once,
  resumes incomplete work, reuses verified nodes, and reconstructs evaluated state before status or
  publication can succeed.
- The publisher reruns the deterministic kernel from verified evidence instead of accepting an
  executor conclusion. It stages the full semantic closure, writes a canonical checksum ledger,
  writes `READY` last, deep-verifies the staging tree, and then renames it atomically.
- The publication catalog exposes only deeply verified releases and includes explicit release,
  workspace, checksum-ledger, and readiness paths.
- The Web workbench verifies the transport integrity of the files it consumes. It still does not
  reproduce semantic attribution verification; authoritative Python verification remains the
  evidence owner.
- The synthetic fixture remains permanently labeled
  `synthetic_fixture_not_strategy_evidence`, and its manifest cannot be resealed as formal evidence.

## 4. Verification

- Combined attribution Python suite: passed 122 tests.
- Focused Ruff check for attribution production, CLI, tests, and traceability: passed.
- Web lint: passed.
- Web production build and 11 named contract subtests: passed, including one publication generated
  during the test by the actual synchronous Python Pipeline.
- Full repository `make check PYTHON=.venv/bin/python`: passed 505 tests, plus doctor, compile, and
  planning synchronization checks.

## 5. Residual boundary and Issue #54 recovery

Issue #54 must remain blocked until all of the following independently exist:

1. a formal strategy has frozen its native topology, version, and replay semantics;
2. at least one falsifiable attribution question and legal intervention are bound before target
   outcomes are read;
3. the resume conditions for `P3-ATTR-001` in `config/p3-gates.v1.json` receive separate approval;
4. any new partition access receives separate authorization; and
5. the real adapter proves that adapter-only integration does not change native baseline decisions.

The synthetic acceptance in this record satisfies none of those recovery conditions and grants no
authority to implement a formal adapter early.
