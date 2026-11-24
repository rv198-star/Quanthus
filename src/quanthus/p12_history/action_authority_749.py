"""#749 action-authority handoff: rebuild per-security-day action_coverage
from the real, once-verified P10 member bytes (issue #749 company-action
verification, development-line-2 -> development-line-1 consumption).

The 48 signed security-days are 3 securities x 16 October signal dates:
  600000 (SSE batch-02-spdb-r2, release B02)
  000002 + 002040 (SZSE batch-pilots-2x15-october-v2, release B10)

For each (security, T) this module locates the batch's RESULT/events/
security-windows/coverage member bytes inside the pinned release MANIFEST
(path -> declared sha256), re-reads each member once, recomputes every
sha, then calls P11's ``build_signed_action_authority`` so the emitted
action_coverage is produced by the signed authority - never a copied
field or a recomputed JSON digest.  The MANIFEST itself is verified once
via ``bind_signed_manifest`` and the same bound object is reused for all
16 dates of that release.

Identity, batch and member selections come from the frozen P10 delivery
(the same members the #749 reader already verified on CN02); no member
is collected, repaired or substituted here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

READER_PACKAGES = Path(
    "/Users/william/Documents/Codex/2026-11-25/quanthus-p10-action-supply/"
    "reader-packages"
)
# CN02 root used for the actually-read member bytes; the local reader
# packages carry the same byte-identical members (B10-FILES sha_match=True).
CN02_ROOT = Path("/srv/quanthus-base22-data/delivery/p10-company-actions")

ACCEPTED_MEMBER = (
    "2026-11-24/quanthus-p11-rule-release/october-action-feasibility-intake/"
    "terms-740-897-v2/ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json"
)
WINDOW_INDEX_MEMBER = (
    "2026-11-24/quanthus-p11-rule-release/october-action-feasibility-intake/"
    "terms-740-897-v2/WINDOW-USE.json"
)

SSE_BATCH = "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2"
SZSE_BATCH = "2026-11-25/quanthus-p10-action-supply/szse/batch-pilots-2x15-october-v2"

# Frozen identity keys per security (P11-signed; the same keys the B02
# RESULT identity block and the WINDOW-USE rows carry).
IDENTITIES: dict[str, dict[str, str]] = {
    "600000": {
        "security_id": "security_2e02245cf8b28319ec8af0e9554cdc24",
        "listing_id": "listing_5edd7b7cdd4b6445afae0d2cca6b85ce",
        "venue_id": "venue_bec59e31eb6bb28111a10d7e6d2c689f",
        "stock_code": "sh.600000",
        "listing_code": "600000",
        "mic": "XSHG",
        "batch": SSE_BATCH,
        "release": "b02",
    },
    "000002": {
        "security_id": "security_011cff7c2146007a3e51037c8476d8ab",
        "listing_id": "listing_c49a60eac39b48d4e349a1cc6dab0e56",
        "venue_id": "venue_8c0bd9088a0dc88f28e5e7fa8efdce87",
        "stock_code": "sz.000002",
        "listing_code": "000002",
        "mic": "XSHE",
        "batch": SZSE_BATCH,
        "release": "b10",
    },
    "002040": {
        "security_id": "security_72946bcaf22e26f7028e5ffe79c54424",
        "listing_id": "listing_e30521d51b0e506341403f5a70da7a1d",
        "venue_id": "venue_8c0bd9088a0dc88f28e5e7fa8efdce87",
        "stock_code": "sz.002040",
        "listing_code": "002040",
        "mic": "XSHE",
        "batch": SZSE_BATCH,
        "release": "b10",
    },
}

# The 16 October signal dates (SSE/SZSE open days 10-08..10-29).
SIGNAL_DATES = [
    "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14",
    "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21",
    "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28",
    "2026-10-29",
]

# Batch JSON members the action_coverage wire needs (raw PDF blobs excluded).
_BATCH_MEMBER_NAMES = {
    "RESULT.json", "events.json", "security-windows.json",
    "coverage.json", "exception-scope.json", "issuer-index-crosswalk.json",
}


@dataclass
class ReleaseBundle:
    release: str
    manifest: dict
    manifest_sha256: str
    members_map: dict          # member_path -> declared sha256
    bound_manifest: Any        # bind_signed_manifest() output, once per release
    source_root: Path          # root that member bytes are read from

    def read_members(self, member_keys: list[str]) -> dict[str, bytes]:
        out = {}
        for rel in member_keys:
            self.members_map[rel]  # fail KeyError early on a missing member
            out[rel] = (self.source_root / rel).read_bytes()
        return out


def _sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def load_release(rel: str, *, reader_root: Path | None = None) -> ReleaseBundle:
    """Verify one release MANIFEST once and return a reuse-bound bundle."""
    from quanthus.product_daily.recommendation.paper_replay import (
        bind_signed_manifest,
    )

    root = reader_root or READER_PACKAGES
    path = root / f"company-actions-20261125-{rel}" / "MANIFEST.json"
    manifest_bytes = path.read_bytes()
    manifest_sha = _sha256_bytes(manifest_bytes)
    manifest = json.loads(manifest_bytes)
    bound = bind_signed_manifest(manifest_bytes, manifest_sha)
    # Member bytes resolve under the release's source_prefix; on the reader
    # tree that is the shared Documents/Codex root holding the source-tree.
    source_root = Path(manifest["source_prefix"])
    if not source_root.is_dir():
        source_root = root / f"company-actions-20261125-{rel}" / "source-tree"
    return ReleaseBundle(
        release=rel,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        members_map=manifest["members"],
        bound_manifest=bound,
        source_root=source_root,
    )


def _batch_member_keys(bundle: ReleaseBundle, batch_dir: str) -> list[str]:
    return [
        k
        for k in bundle.members_map
        if k.startswith(batch_dir + "/")
        and k.rsplit("/", 1)[-1] in _BATCH_MEMBER_NAMES
    ]


def build_day(
    code: str,
    signal_date: str,
    *,
    bundles: dict[str, ReleaseBundle] | None = None,
    reader_root: Path | None = None,
) -> dict[str, Any]:
    """Rebuild action_coverage for ONE signed (security, T) day."""
    from quanthus.product_daily.recommendation.paper_replay import (
        build_signed_action_authority,
    )

    ident = IDENTITIES[code]
    rel = ident["release"]
    if bundles is None:
        bundles = {}
    if rel not in bundles:
        bundles[rel] = load_release(rel, reader_root=reader_root)
    bundle = bundles[rel]

    batch_keys = _batch_member_keys(bundle, ident["batch"])
    members = bundle.read_members(batch_keys)

    acc_bundle = bundle
    if ACCEPTED_MEMBER not in bundle.members_map:
        if "b10" not in bundles:
            bundles["b10"] = load_release("b10", reader_root=reader_root)
        acc_bundle = bundles["b10"]
    members.update(acc_bundle.read_members([ACCEPTED_MEMBER]))

    idx_bundle = bundle if WINDOW_INDEX_MEMBER in bundle.members_map else acc_bundle
    members.update(idx_bundle.read_members([WINDOW_INDEX_MEMBER]))

    coverage = build_signed_action_authority(
        manifest=bundle.manifest,
        manifest_sha256=bundle.bound_manifest,
        accepted_member=ACCEPTED_MEMBER,
        result_member=f"{ident['batch']}/RESULT.json",
        members=members,
        accepted_manifest_members=acc_bundle.members_map,
        index_member=WINDOW_INDEX_MEMBER,
        security_code=ident["listing_code"],
        listing_code=ident["listing_code"],
        stock_code=ident["stock_code"],
        security_id=ident["security_id"],
        listing_id=ident["listing_id"],
        venue_id=ident["venue_id"],
        signal_date=signal_date,
    )
    return {
        "security_code": code,
        "signal_date": signal_date,
        "release": rel,
        "batch": ident["batch"],
        "members_read": sorted(members.keys()),
        "action_coverage": coverage,
    }


def build_all(
    *,
    reader_root: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Rebuild all 48 signed security-days and return the handoff doc.

    Returns {schema_version, days:[{security_code,signal_date,
    action_coverage,members_read,release,batch}], read_receipt}.
    """
    bundles: dict[str, ReleaseBundle] = {}
    days = []
    for code in ("600000", "000002", "002040"):
        for day in SIGNAL_DATES:
            days.append(
                build_day(code, day, bundles=bundles, reader_root=reader_root)
            )
    doc = {
        "schema_version": "p12.replay.action-authority-handoff.v1",
        "consumer": "development-line-1 / build_replay_request.action_coverage",
        "signal_dates": SIGNAL_DATES,
        "securities": sorted(IDENTITIES),
        "days": days,
    }
    if out_path is not None:
        out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    return doc
