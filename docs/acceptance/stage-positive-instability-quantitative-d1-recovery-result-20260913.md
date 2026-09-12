# P3-STAGE-010-D1 Recovery Result — 2026-09-13

## Disposition

```text
D1 DEVELOPMENT ARTIFACT: ACCEPTED BY INDEPENDENT VERIFICATION
WORKER TERMINAL STATUS: FAILED / RETAINED AS IMMUTABLE HISTORY
FAILURE CLASS: POST-RUN VERIFIER CLI CONTRACT MISMATCH
NEW DEVELOPMENT RUN: NOT AUTHORIZED
NEW MARKET READ: NOT AUTHORIZED
CANDIDATE-BLIND REVIEW: READY
PRESERVED 2021 EVALUATION: UNREAD
```

The single authorized evidence-transport recovery Job was:

- Job: `stage_8f2bc43d4b1e4943bca4c3e31214c8a5`;
- code revision: `b7155e3c1052437fbd24b4617a6bf0a9ab166a86`;
- input digest: `sha256:e78f554af60feb1348c2540e30c113905b2369e090137bccd2fde66b3fdc4bd3`;
- transport recovery of: `stage_2612f623a94b44ba86ed71128d16bf48`.

The development process completed and wrote exactly one governed artifact. The Worker then entered its
post-run verification stage, but its generic verifier invocation used `verify --artifact <path>` while
this one module at revision `b7155e3` exposed `verify <artifact>`. `argparse` therefore exited with code
2 before the Worker could publish `result_ref`. This was a verifier CLI interface defect after the
result-bearing process, not a market-data, sampling, semantic, threshold, candidate or evidence failure.
The original terminal status is not rewritten.

## Independent artifact verification

The existing immutable artifact was verified directly in place with the module at the exact result-
bearing revision. No artifact was rebuilt and no market data was reread.

Verified artifact:

- run: `stage-positive-instability-quantitative-d1-20260912T154942Z`;
- manifest: `sha256:506cb9ea1425afc1f123636603ae21ae969ac5903421f3dc4910db903476ba8c`;
- file count: 8;
- fresh pool: 512;
- development identity capacity: 256;
- selected development cases: 160;
- development shortfall: 0;
- development selection: `sha256:160cfd29885c3506bcf27ef1036fc596683d3175c5d3169a0172c44eb99711d3`;
- development evidence: `sha256:9b22e002a20971ed47c933b0dfea73e42c21b7cd1f1471a350704486d0d1a163`;
- path facts: `sha256:d509baae4419ebf3509f5f6ed382e8e0cab8dcf9483edadc564004f144c61c98`;
- threshold basis: `sha256:97c53f1551bf62a0b444a523bb57689ae44f5c918f0ad15774ca0ace6ea3233e`;
- fixed candidate grid: `sha256:2781679a81a96a5c4adaa488ee555ec13ad53b0361a712b77050e04048fe6332`;
- candidate count: 9.

The verifier also proved:

- `opinion_count = 0`;
- `candidate_frozen = false`;
- `parameter_search = false`;
- `review_ready = true`;
- preserved evaluation identities = 160;
- preserved evaluation identity digest = `sha256:8bbbd7ca7110d06bfe07db6c2f555cc093b0c1a259aa666e359a637f9157b1e8`;
- preserved evaluation plan digest = `sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7`;
- preserved 2021 evaluation market-data read = false.

Machine evidence is frozen at
`config/stage-learning/positive-instability-quantitative-d1-recovery-result.v1.json`.

## Verifier correction

The verifier CLI was aligned to the Stage Worker contract in commit
`e8ca262ee0ae063d621e327c451145ae77d81ec8`. The correction changes only the verifier argument shape
and adds a regression test. It does not change D1 data, selection, thresholds, candidate grid, gates,
Stage 2, relocation semantics or preserved-evaluation bindings.

## Authority after recovery

D1 is now complete for its result-bearing market-data work. No additional D1 Job, technical retry,
transport recovery, semantic retry or D1 market-data read is authorized.

The next permitted action is candidate-blind Primary review of the already-prepared 160 cases. Review
must consume only the D1 evidence transport and must remain blind to threshold basis and candidate
outputs. Candidate scoring remains blocked until the review ledger is durably sealed. The preserved
2021 evaluation remains unread until D2 freezes at most one candidate. 2022-2025, official labels,
daily activation, strategy and trading remain closed.
