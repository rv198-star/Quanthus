# P10-DATA-HIST-1D-FULL-001 — independent full daily dataset

Owner 2026-11-11 explicitly requested construction now and replacement of the HF
default release. [#619](https://github.com/rv198-star/Quanthus/issues/619) owns this
delivery; [#615](https://github.com/rv198-star/Quanthus/issues/615) retains the later
four-product Base 2.6 build. Historical #602 remains closed.

The product is Hist-1D v2, 1990-12-19 through the sealed Base 2.5.1 cutoff
2026-10-31 (last observed trading date 2026-10-30). It composes the immutable
Hist v1 daily files with modern Base daily files and a self-contained reference
closure. Existing parent packages and production consumers retain their identities.

`config/pipelines/hist-1d-full-v1.json` fixes the parents, merge and acceptance
profile. `hist_1d_full.py` materializes independent files and source-preserving
references; `hist_1d_full_audit.py` checks the complete bar member identity,
duplicates/FKs/scope, historical coverage and binary ST, factor events, and inherited
modern reference limitations. The modern segment retains its parent's governance
grade. All bar source bytes and source knowledge clocks are preserved.

`read_hist_1d.py` resolves main to one immutable revision and verifies a selected
historical or modern partition against its manifest. Publication uses a separate
candidate branch before changing the default, removes obsolete parent paths in the
same guarded commit, and preserves old revision
`0824ed0bcec6b979777a387a41ca3a8c0e491098`. Cloud member identities, modern and old
anonymous reads, and a fresh-directory full-member recovery are required evidence.

Acceptance stages: fixed code/inputs and focused tests; full local build/audit and
make check; full archive recovery; HF candidate verification; guarded main publication
and anonymous fixed-revision verification. Run outputs, market files and credentials
stay outside Git. Completion is bound to the actual publication revision and receipts.
