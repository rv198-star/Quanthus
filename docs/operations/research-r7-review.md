# R7 real-data decision review

Parent: [frozen engineering design](../design/research-r7-multiop-mvp-v1.md).
Work items: #651/#652/#653; R7 milestone21. The artifact is a fixed retrospective research view.

## Read the delivered package

Use Python 3.10+ and the versioned Quanthus source. Cold queries use only the four-file package
and standard code; they do not contact DWD or reopen the old input paths. Obtain the package
from the immutable HF revision in the [acceptance receipt](../acceptance/research-r7-multiop-20261113.md).

The HF release has eight files, including HTML and evidence. The query reader requires a
separate **four-file core directory**. Do not pass the eight-file release directory as
`--package`; it correctly rejects it with `R7_PACKAGE_FILES`.
After downloading the pinned release, set `R7_RELEASE` to that directory and `R7_CORE`
to a new directory. This copies existing bytes without rebuilding market inputs:

```bash
export R7_RELEASE=/absolute/path/to/downloaded-A2
export R7_CORE=/absolute/path/to/new-r7-core
python3 - <<'PY'
import hashlib
import os
import shutil
from pathlib import Path

release = Path(os.environ["R7_RELEASE"])
core = Path(os.environ["R7_CORE"])
names = ("READY", "manifest.json", "rows.jsonl", "summary.json")
for name in names:
    path = release / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"invalid release member: {name}")
expected = "e22c9caa24396b3acf51aaacd18a7f0f26156c6b079647ca4f78d3dc07f19d4a"
if hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest() != expected:
    raise ValueError("R7 release manifest identity mismatch")
core.mkdir(parents=True, exist_ok=False)
for name in names:
    shutil.copyfile(release / name, core / name)
print(core)
PY
```

The next query authenticates READY and all payload hashes. Keep generated HTML and query
receipts outside `R7_CORE`. The [MVP v0 bundle](research-mvp-v0.md) already provides this
layout as `r7-core/`.

Set `PACKAGE` to its physical directory, `VIEW` to a new HTML file outside that directory,
and use the trusted manifest below. From the repository root:

```bash
PYTHONPATH=src python3 -B scripts/research_r7_review.py query \
  --package "$PACKAGE" \
  --manifest-sha256 sha256:e22c9caa24396b3acf51aaacd18a7f0f26156c6b079647ca4f78d3dc07f19d4a \
  --html "$VIEW"
```

The HTML search filters security, date, final action, expert/veto reason and unavailability
reason. A fixed example is `000001 2020-04-10`. Expand the expert and provenance sections to
inspect the full recorded values. The header reports complete rows; the live counter reports
currently visible cards. No orders or fills are created by reading.

The CLI also supports `--security`, `--date`, `--action`, `--reason` and `--limit` (1–1000).
Its JSON reports `matched`, `returned` and `truncated` separately. `verify` authenticates the
same package; each read checks trusted manifest, READY, exact file set and payload hashes.

## Understand this sample

The fixed three securities have 21 open April2020 sessions each: 63 reviews, including six
insufficient-warmup rows and 57 rows with all three operators available. Trend state expresses
the accepted trend-rule semantics; structural position and volume are explanation sidecars.
Position Risk consumes the original RSI/volatility features. Expert inputs and policy remain
independent of the optional sidecar, and all before/after decision digests agree.

These security-day probes have not qualified as P4 opportunities. The original entry guardian
therefore blocks entry; final actions are `no_action`, while actual expert opinions and their
reason codes remain visible. Source observation cutoff is in2026 and simulated business time
is in2020. Execution status is `NOT_RUN_DECISION_REVIEW`; there are no historical position or
fill claims in this package.

For a fresh build, first activate an input-qualified bounded request under the existing
exploration/TPlan rules. The request fixes the three-security window and complete input
closure; `build --source ... --package ...` is intentionally not a general arbitrary-data API.
For reproduction of this accepted A2 scope, use the delivered source, the SHA-verified
126-member native root as `SOURCE`, and a new empty destination as `REBUILT_PACKAGE`:

```bash
PYTHONPATH=src python3 -B scripts/research_r7_review.py build \
  --source "$SOURCE" --package "$REBUILT_PACKAGE"
```

The source loads the versioned A2 request. The manifest records the actual build commit;
compare rows/summary and the semantic digest when reproducing from a later documentation
commit. Changing source scope, policy or input requires its own bounded request.
R8 owns a new opportunity/decision question and full capital comparison. R9 retains the
learning-input, independent-reference and local-update gates of #540/#546.
