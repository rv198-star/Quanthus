# P4 Research ADM Industry Source Digest Correction

| Field | Value |
| --- | --- |
| Status | implementation correction; #74 acceptance still open |
| Work item | `P4-EVENT-002` |
| Production negative Run | `run_4cc0a39eda714162a088895bb3b00204` |
| ADM policy | `p4-event-research-adm@1.1.0` |
| Corrected ADM policy digest | `sha256:4ac838ac190d89847edfd93b29a876028dc599818be24f123810311c36e0c7c1` |
| Corrected source-content digest | `sha256:20b51d52070b8976f9f8989ff0f54124aea098ca749167bedbddbed947c3196b` |
| Source policy | `p4-event-source.direct-dwd-v2.1-retrospective@2.2.0` |
| Source-policy digest | `sha256:b5373edc2f30d7d4786acb91c18fcc95c79517ef2bc1f8203c75d035f0e7f8b6` |

## Finding

Production Run A v8 passed the BaoStock package-version gate introduced after v7, then failed closed
because the historical-industry source digest differed from the ADM policy. The data rows were not
different. BaoStock 0.9.3 exposes two inconsistent version strings:

- module `baostock.__version__`: `00.9.30`;
- installed Python distribution metadata: `0.9.3`.

The initial ADM 1.1 checkpoint computed its source digest while the implementation still preferred
the module field, so `sha256:609fe99e...` accidentally included `00.9.30`. The production fix now
uses `importlib.metadata.version("baostock")` as the installed-package authority and uses module
`__version__` only for injected test doubles.

## Data equivalence

The exact same 259-security request was recomputed with the same fixed source dates and hierarchy:

- selected securities: 259;
- primary 2021-09-30 provider rows: 4,632;
- legacy 2012-12-31 provider rows: 2,547;
- 256 primary CSRC-2012 resolutions;
- 3 legacy-name crosswalk resolutions (`600102 -> C31`, `600263 -> E48`, `600991 -> C36`).

Changing only `provider_version` from `00.9.30` to `0.9.3` changes the canonical source digest from
`sha256:609fe99ea5eb2d29237a5c0112779ae58491270a80f390f099fb798de07382c9` to
`sha256:20b51d52070b8976f9f8989ff0f54124aea098ca749167bedbddbed947c3196b`.
No industry membership, candidate rule, threshold, split, DWD input, or event identity semantic was
changed by this correction.
