# ADR 0009: DWD Base V2.1 Rebuild Execution

- Status: Accepted and completed; R18 released
- Date: 2026-08-07
- Completion date: 2026-08-11
- Decision id: `META-DATA-PIT-001`

## Context

The July V2.1 rebuild needed more CPU, memory, and local ext4 capacity than CN01 could provide.
GPU-P100 was available temporarily and therefore executed the completed R18 build. Its name remains
in run ids and historical receipts as provenance.

The original execution plan also assigned build, import, install, and pointer authority to named
hosts and required separate approval statements between phases. That topology added coordination
without improving the integrity of a personal warehouse whose immutable packages can be verified
directly.

## Decision

Rebuild, audit, and promotion code is node-independent production code:

- any host may execute it when the exact inputs, code revision, ext4 semantics, capacity, and
  bounded resource settings pass preflight;
- checkpoint and audit receipts bind inputs and outputs by digest, not by hostname;
- a successful full build ends at a deeply verified, pointerless candidate;
- promotion deeply verifies source, staging, installed package, and the retained predecessor, then
  compare-and-swaps `BASE_CURRENT.json` under a local lock;
- no approval phrase, CN01 transfer, or named execution node is a software gate;
- the previous immutable Base remains available for rollback.

The frozen GPU-P100 contract and approval receipts are retained only as R18 historical evidence.
They are not current operating authority and are not required inputs to promotion.

## Result

Run `dwd-v2-1-gpu-p100-20260811T063015Z-5b90cc47-r18` produced the released
`dwd-base-202607-v2.1`. GPU-P100 was the machine used for that run; it is not a production
dependency. The release identity is governed by `config/releases/v2-build202607.json`.
