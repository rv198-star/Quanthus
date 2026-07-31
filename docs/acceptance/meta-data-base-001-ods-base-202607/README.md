# ODS July Base Acceptance

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-001` |
| Tracking issue | GitHub issue #47 |
| Evidence date | 2026-07-31 UTC |
| Evidence status | Final July Base built, mirrored, and manually promoted on both Base holders |
| Deployed revision | `55269c2b5459bbc3d1eb13cb539d253ed4b146f1` |
| Policies | `config/pipelines/ods-base.v1.json` and `config/pipelines/ods-base-final-through-20260731.v2.json` |
| Packages | `ods-base-through-20260730-v1` and candidate `ods-base-202607-v1` |
| Machine summaries | `provisional-through-20260730.json`, `final-candidate-through-20260731.json`, and `manual-promotion-through-20260731.json` |
| Production cutover | blocked; no `GO_FOR_CUTOVER.json` was recorded |

## 1. Verdict

The final `ods-base-202607-v1` candidate is present and independently deep-verified on Hermes and
CN01. It contains 20,946 files and 13,285,483,256 logical bytes through 2026-07-31. Both nodes bind
manifest `sha256:553665772eb9746dcbca3318ae15f1c9b421f351c6e734f8e475ee8309a71d4c`
and content digest
`sha256:91fb3be70e7b80cb22eaa800c00bb267a9a89202b83a14614c31b9d7a2f5d4d1`.
The CN01 full-fundamentals weekly release is retained as an explicit checksum-bound gap-fill, and
the provisional raw F10/full-statements scope gap is closed in this candidate.

At `2026-07-31T18:52:25Z`, explicit operator authority decoupled Base pointer publication from
the still-open normal dual-node weekly and catalog acceptance gates. The supported locked
promotion command independently deep-verified the target and the compare-and-swap precondition on
each node. Both node-local `BASE_CURRENT.json` files now bind `ods-base-202607-v1` and are
byte-identical at
`sha256:5560ea8e0b5d385273bab2124f50d0183d8acce641f24cfe8d703274311cf36c`.

This manual exception advances only the recovery-Base descriptor. The prior Base remains on both
nodes, `CATALOG_CURRENT.json` remains absent, and no timer, consumer, legacy pointer, or
`GO_FOR_CUTOVER` state changed. Normal weekly and catalog acceptance remains open as operational
chain evidence, but no longer blocks the final July Base descriptor.

### Provisional Phase A Record

The most complete currently governed Hermes ODS holdings through 2026-07-30 are frozen in one
immutable Base package and published checksum-identically on Hermes and CN01. Both nodes expose
the same package through their node-local `/srv/quanthus/ods/base/BASE_CURRENT.json`. OCI has no
target-chain ODS Base.

"Most complete" is bounded by acquired evidence, not by desired future source families. The Base
contains every governed canonical source, retained source-evidence revision, historical 1m/5m
part, and selected immutable daily release admitted by the reviewed policy. Every discovered
top-level or mixed-root source unit is classified. Unknown or unexplained required inputs are
zero. Raw F10 and full financial statements were never formally acquired and remain the one
declared scope gap; the included quarterly financial summary is not represented as that missing
raw archive.

This acceptance changes no legacy ODS pointer, timer, consumer, or cutover state. It authorizes the
provisional reconstruction seed only. Final July acceptance still requires normal accepted
2026-07-31 market, event, 1m, and 5m releases and a new immutable `ods-base-202607-v1`.

## 2. Frozen Source Inventory

| Property | Accepted value |
| --- | --- |
| Hermes inventory | `/srv/quanthus/ods/base/.inventories/ods-base-through-20260730-v1.source.json` |
| Inventory files | 20,743 |
| Inventory logical bytes | 11,145,679,742 |
| Selection digest | `sha256:33daefbc761abed2516fa64aa83d7bd0c147727d47e6c80544f25c596d817c3d` |
| Through date | 2026-07-30 |
| Unclassified governed source units | 0 |
| Unexplained required minute parts | 0 |
| Declared scope gap | `raw_f10_and_full_financial_statements=not_formally_acquired` |

The canonical source families cover adjusted and unadjusted daily bars, valuation, quarterly
financial summary, announcements, corporate actions, dividends, adjustment evidence, shareholder
counts, security identity, stock universe, ST, suspensions, indices, and trading calendars. The
Base also retains reviewed source/revision evidence without allowing it to supersede richer
canonical members.

Both historical minute trees contain all 4,992 governed per-security parts. The repaired
2026-07-24 1m and 5m releases each reconcile as 4,988 normal parts plus four governed no-data
cases: three suspensions and one accepted zero-turnover daily bar. No row-level favorable merge or
CN01 source fallback was used.

The immutable increment chain selects market, events, 1m, and 5m releases for every open session
from 2026-07-27 through 2026-07-30. The repaired 2026-07-24 intraday releases are additionally
bound as the historical archive closure. Source `knowledge_cutoff`, market dates, release ids,
checksums, and acquisition lineage remain unchanged in the package manifest.

## 3. Package Identity

| Property | Accepted value |
| --- | --- |
| Bundle id | `ods-base-through-20260730-v1` |
| File count | 20,743 |
| Logical bytes | 11,145,679,742 |
| Content digest | `sha256:e51644929fb4c5fc20f30db0652aa469c6164ac27d1ffcb1c864c2c46119589d` |
| Manifest SHA-256 | `d38b8332ddd5427104bbc6f71bf89df1c81f85cca507a1f9a80ac58662b7352e` |
| `READY` SHA-256 | `fcf541a7dfe3cc070c9e8dbabe920575dc921b739404371afabf718a9412b998` |
| `BASE_CURRENT.json` SHA-256 | `51624f796dd3219cd8ff364ff55990c51b1781ce84a497ab057ee2a5a972f707` |
| Build time | `2026-07-31T00:03:50.407416+00:00` |

Hermes and CN01 returned the same package id, through date, file count, logical bytes, manifest
SHA-256, and content digest from independent deep verification. The three file-level hashes above
are also byte-identical on both nodes.

## 4. Build, Transfer, And Restore Evidence

| Gate | Hermes | CN01 |
| --- | --- | --- |
| Exact Git revision | `4476361f6958f8c26b0df71e76074aae19198089` | same |
| Independent package deep verify | passed | passed on hidden staging and formal package |
| Clean-path manifest restore | package-native verify passed | staging verify used only package plus policy |
| Pointer publication | passed | passed after `--move-ingress` |
| Same-package promotion retry | passed | passed |
| Same-inventory build retry | passed with identical identity | not applicable; Hermes is builder |
| Hidden ingress after acceptance | absent | absent |

The transfer ran directly from Hermes to CN01 for approximately 25 minutes. It copied all members
except `READY` into `.incoming-ods-base-through-20260730-v1`, then wrote `READY` in a separate final
rsync. During transfer, CN01 had neither `READY` nor `BASE_CURRENT.json`. After staging deep
verification, promotion atomically renamed the hidden child and re-verified the formal path before
writing the pointer.

The CN01 staging verification is clean-path evidence because the verifier receives only the
package root and reviewed policy. It discovers every payload member from relative manifest paths
and does not accept an Hermes producer root. Published manifest members contain no dependency on
an absolute source path.

Repeating the Hermes build entry point against the identical frozen inventory returned the same
20,743 files, 11,145,679,742 bytes, manifest SHA-256, and content digest. Repeating publication on
both nodes took the idempotent branch and returned the same package identity without creating a
new package or advancing to another digest.

## 5. Capacity And Preservation

| Node | Free before | Free after | Published role |
| --- | ---: | ---: | --- |
| Hermes | 22,506,823,680 bytes | 11,266,015,232 bytes | authority and Base holder |
| CN01 | 73,042,751,488 bytes | 61,803,806,720 bytes | checksum-identical mirror |
| OCI | not applicable | no `/srv/quanthus/ods/base` | no target ODS retention |

No pre-existing Base, source release, failed attempt, legacy ODS asset, pointer, or timer was
deleted or modified. This work only created the new Base packages and their dedicated
`BASE_CURRENT.json` descriptors. No `GO_FOR_CUTOVER.json` exists on either Base holder. The
published Base is a recovery seed beside the existing lanes and does not authorize execution or
consumer migration.

Hermes retains enough free space for the final July child because unchanged members are eligible
for same-filesystem hard-link reuse. The final build still has a preflight free-space gate and may
not rely on that estimate without remeasurement.

## 6. Final Candidate Gate

Completed after the 2026-07-31 close:

1. Accept the normal market, events, 1m, and 5m ODS releases on both source lanes.
2. Freeze a new through-2026-07-31 inventory and build `ods-base-202607-v1` with the provisional
   package as parent.
3. Deep-verify and mirror the final package on Hermes and CN01 without advancing either pointer.
4. Re-run clean-path, idempotency, checksum identity, preservation, and no-cutover evidence.

The compare-and-swap pointer promotion completed on both nodes under the explicit manual exception
recorded in `manual-promotion-through-20260731.json`. The normal dual-node weekly and catalog gates
remain open as operational publication evidence. The provisional package, final package, and July
31 source releases remain immutable evidence. Cleanup is outside Issue #47 and requires explicit
later authority.
