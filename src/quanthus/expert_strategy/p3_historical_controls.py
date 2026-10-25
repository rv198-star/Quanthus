"""Two fixed component controls over the already qualified 2012–2021 fixture.

This successor reuses each role's own immutable, previously independently built
prerequisite. It does not rebuild features, repair sources, extend dates or load
the separate protected-window risk-component experiment.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import hashlib
import json
import os
import resource
import socket
import sys
import time
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_restructuring_successor as parent
from quanthus.expert_strategy.p3_dated_share_validation import business_bundle
from quanthus.expert_strategy.p3_history_review import (
    BUSINESS_FILES,
    ArchiveContract,
    ReviewedStudy,
    read_json,
    require,
    validate_bundle,
)

VERSION = "p3-historical-fixed-controls-v1"
REQUEST = "config/research/p3-strat-003/historical-fixed-controls.v1.json"
COMPONENTS = {
    "TC": {"entry_cap": True, "held_risk": False},
    "TH": {"entry_cap": False, "held_risk": True},
}


def load_request(repository: Path) -> dict[str, Any]:
    old = parent.load_request(repository)
    value, _ = read_json(repository / REQUEST)
    require(isinstance(value, dict), "CONTROL_REQUEST_TYPE")
    request: dict[str, Any] = value
    require(
        request["schema_version"] == VERSION
        and request["parent_request_digest"] == digest_value(old)
        and request["source_manifest_sha256"] == old["source_manifest_sha256"]
        and request["source_information_cutoff"] == old["source_information_cutoff"]
        and request["scope"] == old["scope"]
        and request["components"] == COMPONENTS
        and request["entry_cap"] == "0.20"
        and request["optional_rights_parent_policy_sha256"] == old["optional_rights_policy_sha256"]
        and request["optional_paid_rights"] == "DO_NOT_SUBSCRIBE"
        and request["reuse"] == "OWN_ROLE_VERIFIED_IMMUTABLE_PREREQUISITE"
        and all(
            request[key] is False
            for key in (
                "parameter_search",
                "new_source_collection",
                "historical_PIT_claim",
                "production_authorized",
                "P6_mutation_authorized",
                "source_guards_waived",
            )
        ),
        "CONTROL_REQUEST",
    )
    return request


class FixedControlRuntime(parent.RestructuringRuntime):
    """Keep the exact parent data namespace; version the new control separately."""

    def __init__(self, *args: Any, reference_work: Path, **kwargs: Any) -> None:
        repository: Path = args[0]
        self.control_request = load_request(repository)
        self.control_digest = digest_value(self.control_request)
        self.reference_work = reference_work
        super().__init__(*args, **kwargs)
        # Do not forge old B/T/TCH optional-rights receipts as TC/TH approvals.
        # A distinct successor declares the same zero-election policy, retaining
        # the exact qualified event and original policy identity as its parents.
        self.control_rights_dispositions = [
            {
                "kind": VERSION + "/optional-rights",
                "arm": arm,
                "control_request_digest": self.control_digest,
                "parent_policy_sha256": self.control_request[
                    "optional_rights_parent_policy_sha256"
                ],
                "parent_disposition": self.rights_dispositions[1],
                "decision": "DO_NOT_SUBSCRIBE",
                "subscription_cash_delta": "0",
                "subscription_share_delta": 0,
                "historical_PIT_claim": False,
            }
            for arm in COMPONENTS
        ]

    def _validate_intervention_id(self, intervention_id: str | None) -> None:
        require(intervention_id in COMPONENTS, "CONTROL_ARM")

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        require(intervention_id in COMPONENTS, "CONTROL_ARM")
        return "variant." + str(intervention_id)

    def _held_intervention_id(self, intervention_id: str | None) -> str | None:
        self._validate_intervention_id(intervention_id)
        return None if intervention_id == "TH" else "intervention.position-risk-base-policy"

    def _evaluate_entry_decision(
        self, runner: Any, *, intervention_id: str | None, **kwargs: Any
    ) -> Any:
        self._validate_intervention_id(intervention_id)
        # Exactly the TCH capped entry or T uncapped entry, with held management
        # selected independently above. No expert/threshold/cost changes.
        return super()._evaluate_entry_decision(
            runner, intervention_id="TCH" if intervention_id == "TC" else "T", **kwargs
        )

    def _baseline_variant_directory(self) -> str:
        # Formal metric comparison reads the exact own-role B anchor, never a
        # primary result in the independent process and never replays B again.
        return str(self.reference_work / "replays/variant.B")

    def _code_version(self) -> str:
        return super()._code_version() + ":" + VERSION + ":" + self.control_digest

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._parameter_payload(intervention_id),
            "control_request_digest": self.control_digest,
            "components": COMPONENTS[str(intervention_id)],
            "control_rights_dispositions_digest": digest_value(self.control_rights_dispositions),
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "purpose": VERSION,
            "execution_adapter": VERSION,
            "execution_digest": self.control_digest,
            "parent_request_digest": self.request_digest,
            "control_request_digest": self.control_digest,
            "input_snapshot_namespace": self.snapshot_id,
            "components": COMPONENTS[str(intervention_id)],
            "optional_rights_successor_policy_digest": self.control_digest,
            "control_rights_dispositions_digest": digest_value(self.control_rights_dispositions),
            "prerequisite_disposition": "VERIFIED_OWN_ROLE_REUSE",
            "independently_rebuilt_this_job": False,
            "strict_B_D_strategy_or_production_accepted": False,
        }

    def _execute_frame(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = super()._execute_frame(*args, **kwargs)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "stage": "frame_blocked",
                        "frame": args[0],
                        "decision_date": args[1].snapshot.market_date.isoformat(),
                        "execution_date": args[1].execution_date.isoformat(),
                        "error_type": type(exc).__name__,
                        "reason": str(exc),
                        "data_repair_or_skip_authorized": False,
                    }
                ),
                flush=True,
            )
            raise
        if args[0] % 250 == 0:
            print(json.dumps({"stage": "frame_complete", "frame": args[0]}), flush=True)
        return result


def review_controls(freeze: dict[str, Any]) -> ReviewedStudy:
    """Full cold graph and accounting acceptance of exactly one new fixed cell."""
    contract = ArchiveContract.from_dict(freeze["parent_archive_contract"])
    arm = freeze["arm"]
    require(arm in COMPONENTS, "CONTROL_ARM")
    namespace = contract.purpose + "/" + contract.request_digest.removeprefix("sha256:")
    all_inodes: set[tuple[int, int]] = set()
    graphs: dict[str, Any] = {}
    summaries: dict[str, Any] = {}
    primary: dict[str, Any] = {}
    coordinates: dict[str, str] = {}
    proofs = {}
    for role in ("primary", "independent_cold_reproduction"):
        record = freeze["roles"][role]
        work = Path(record["work"])
        complete, complete_sha = read_json(work / "replays/COMPLETE.json")
        require(
            complete["status"] == "COMPLETE"
            and complete["role"] == role
            and complete["variant"] == arm
            and complete["control_request_digest"] == freeze["control_request_digest"]
            and complete["source_manifest_sha256"] == contract.source_manifest_sha256
            and complete["production_authorized"] is False
            and set(complete["input_physical_proof"]) == set(record["bindings"]),
            "CONTROL_COMPLETION",
        )
        # The role hashes every explicit member before execution. Stable inode,
        # mtime AND ctime after the run prove that the same files were not
        # rewritten, avoiding another all-source hashing pass.
        for name, before in complete["input_physical_proof"].items():
            after = Path(name).stat()
            require(
                before["sha256"] == record["bindings"][name]["sha256"]
                and (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                    after.st_nlink,
                )
                == (
                    before["device"],
                    before["inode"],
                    before["bytes"],
                    before["mtime_ns"],
                    before["ctime_ns"],
                    1,
                ),
                "CONTROL_SOURCE_CHANGED_AFTER_READ",
            )
        observed, coordinates_sha = read_json(work / "event-coordinates.json")
        require(len(observed) == freeze["candidate_keys"], "CONTROL_COORDINATES")
        if role == "primary":
            coordinates = observed
        else:
            require(observed == coordinates, "CONTROL_COORDINATE_EQUIVALENCE")
        require(
            {p.name for p in (work / "replays").iterdir()} == {"COMPLETE.json", "variant." + arm},
            "CONTROL_REPLAY_FILE_SET",
        )
        folder = work / "replays" / ("variant." + arm)
        require(
            {p.name for p in folder.iterdir()} == BUSINESS_FILES | {"manifest.json"}
            and set(complete["file_sha256"]) == BUSINESS_FILES | {"manifest.json"},
            "CONTROL_ARTIFACT_FILE_SET",
        )
        files = {}
        hashes = {}
        for name in sorted(BUSINESS_FILES | {"manifest.json"}):
            path = folder / name
            files[name], hashes[name] = read_json(path, complete["file_sha256"][name])
            st = path.stat()
            identity = (st.st_dev, st.st_ino)
            require(identity not in all_inodes, "CONTROL_SHARED_INODE")
            all_inodes.add(identity)
        manifest = files.pop("manifest.json")
        require(
            manifest["purpose"] == manifest["execution_adapter"] == VERSION
            and manifest["variant_id"] == "variant." + arm
            and manifest["variant"] == arm
            and manifest["source_manifest_sha256"] == contract.source_manifest_sha256
            and manifest["source_information_cutoff"] == contract.source_information_cutoff
            and manifest["scope"] == contract.scope
            and manifest["execution_digest"] == freeze["control_request_digest"]
            and manifest["parent_request_digest"] == contract.request_digest
            and manifest["input_snapshot_namespace"] == namespace
            and manifest["optional_rights_policy_sha256"] == contract.optional_rights_policy_sha256
            and manifest["optional_rights_successor_policy_digest"]
            == freeze["control_request_digest"]
            and manifest["components"] == COMPONENTS[arm]
            and manifest["no_subscription_all_arms"] is True
            and all(
                manifest[k] is False
                for k in (
                    "historical_availability_claim",
                    "execution_authorized",
                    "production_authorized",
                    "independently_rebuilt_this_job",
                    "strict_B_D_strategy_or_production_accepted",
                )
            ),
            "CONTROL_MANIFEST",
        )
        require(
            digest_value({name: digest_value(v) for name, v in sorted(files.items())})
            == manifest["replay_digest"]
            == complete["replay_digest"],
            "CONTROL_SEMANTIC_DIGEST",
        )
        graph = business_bundle(
            files,
            coordinates,
            snapshot=namespace,
            anchor_snapshot=namespace,
            snapshot_suffixes=[],
        )
        summary = validate_bundle(files, coordinates, contract)
        if role == "primary":
            primary[arm], graphs[arm], summaries[arm] = files, graph, summary
        else:
            require(graph == graphs[arm], "CONTROL_GRAPH_MISMATCH")
            require(summary == summaries[arm], "CONTROL_ACCOUNTING_MISMATCH")
        isolation, isolation_sha = read_json(work / "isolation.json")
        require(
            isolation["kernel_landlock"]
            is isolation["seccomp_network_denied"]
            is isolation["legacy_provider_disabled"]
            is True
            and isolation["native_denied"] == record["forbidden"],
            "CONTROL_ISOLATION",
        )
        proofs[role] = {
            "completion_sha256": complete_sha,
            "coordinates_sha256": coordinates_sha,
            "isolation_sha256": isolation_sha,
            "files": hashes,
            "elapsed_seconds": complete["elapsed_seconds"],
            "peak_RSS_bytes": complete["peak_RSS_bytes"],
        }
    return ReviewedStudy(
        primary,
        coordinates,
        summaries,
        {
            "status": "PASS_FIXED_CONTROL_REPRODUCTION",
            "roles": proofs,
            "source_inputs_unchanged": True,
            "business_graph_equal": True,
            "independently_rebuilt_prerequisites_this_job": False,
            "own_previously_independently_built_inputs_reused": True,
            "production_authorized": False,
        },
    )


def verify_bindings(bindings: dict[str, Any]) -> dict[str, Any]:
    """Rehash complete explicit closure once; no traversal/source discovery."""
    proof = {}
    for name, record in bindings.items():
        path = Path(name)
        for part in (path, *path.parents):
            require(not part.is_symlink(), "CONTROL_INPUT_SYMLINK")
        before = path.stat()
        require(path.is_file() and before.st_nlink == 1, "CONTROL_INPUT_PHYSICAL")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while raw := handle.read(4 * 1024 * 1024):
                digest.update(raw)
        after = path.stat()
        require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            and before.st_size == record["bytes"]
            and "sha256:" + digest.hexdigest() == record["sha256"],
            "CONTROL_INPUT_CHANGED",
        )
        proof[name] = {
            **record,
            "device": before.st_dev,
            "inode": before.st_ino,
            "mtime_ns": before.st_mtime_ns,
            "ctime_ns": before.st_ctime_ns,
        }
    return proof


def isolate_role(allowed: list[Path], forbidden: list[Path]) -> dict[str, Any]:
    """Linux kernel read/network isolation, including native Arrow denial probes."""
    libc = ctypes.CDLL(None, use_errno=True)

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    require(libc.syscall(444, 0, 0, 1) >= 1, "CONTROL_LANDLOCK_ABI")
    access = 1 | 4 | 8  # execute, read_file, read_dir
    spec = Ruleset(access)
    fd = libc.syscall(444, ctypes.byref(spec), ctypes.sizeof(spec), 0)
    require(fd >= 0, "CONTROL_LANDLOCK_CREATE")
    try:
        for path in allowed:
            path = path.resolve(strict=True)
            parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(access if path.is_dir() else 4, parent_fd)
                require(
                    libc.syscall(445, fd, 1, ctypes.byref(rule), 0) == 0,
                    "CONTROL_LANDLOCK_RULE",
                )
            finally:
                os.close(parent_fd)
        require(libc.prctl(38, 1, 0, 0, 0) == 0, "CONTROL_NO_NEW_PRIVS")
        require(libc.syscall(446, fd, 0) == 0, "CONTROL_LANDLOCK_ENTER")
    finally:
        os.close(fd)
    library = ctypes.util.find_library("seccomp")
    require(library is not None, "CONTROL_SECCOMP_LIBRARY")
    sec = ctypes.CDLL(str(library))
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = sec.seccomp_init(0x7FFF0000)
    require(bool(ctx), "CONTROL_SECCOMP_CREATE")
    try:
        for name in (b"socket", b"socketpair", b"connect"):
            number = sec.seccomp_syscall_resolve_name(name)
            require(number >= 0, "CONTROL_SECCOMP_SYSCALL")
            require(
                sec.seccomp_rule_add(ctx, 0x50000 | errno.EACCES, number, 0) == 0,
                "CONTROL_SECCOMP_RULE",
            )
        require(sec.seccomp_load(ctx) == 0, "CONTROL_SECCOMP_ENTER")
    finally:
        sec.seccomp_release(ctx)
    try:
        connection = socket.socket()
    except PermissionError:
        pass
    else:
        connection.close()
        raise ValueError("HISTORY_REVIEW_CONTROL_NETWORK_ALLOWED")
    import pyarrow as pa  # type: ignore[import-untyped]

    probes = []
    for path in forbidden:
        try:
            with pa.OSFile(str(path), "r"):
                pass
        except PermissionError:
            probes.append(str(path))
        else:
            raise ValueError("HISTORY_REVIEW_CONTROL_NATIVE_READ_ALLOWED")
    return {"kernel_landlock": True, "seccomp_network_denied": True, "native_denied": probes}


def run_role(freeze: dict[str, Any], role: str) -> dict[str, Any]:
    """One new arm, one new process; never regenerate the accepted prerequisites."""
    start = time.monotonic()
    repository = Path(freeze["repository"])
    record = freeze["roles"][role]
    work = Path(record["work"])
    arm = freeze["arm"]
    request = load_request(repository)
    require(digest_value(request) == freeze["control_request_digest"], "CONTROL_FROZEN_REQUEST")
    require(arm in COMPONENTS, "CONTROL_ARM")
    require(role in {"primary", "independent_cold_reproduction"}, "CONTROL_ROLE")
    require(not (work / "replays").exists(), "CONTROL_OUTPUT_EXISTS")
    proof = verify_bindings(record["bindings"])
    admission, _ = read_json(Path(record["admission"]))
    allowed = [
        repository / "src",
        repository / "config",
        repository / "docs",
        work,
        *[Path(record[key]) for key in ("source", "parent_source", "prepared", "prior_prepared")],
        Path(record["reference_work"]) / "replays/variant.B/decisions.json",
        Path(sys.prefix),
        *[
            Path(p)
            for p in ("/usr", "/lib", "/lib64", "/etc", "/dev", "/proc", "/sys")
            if Path(p).exists()
        ],
    ]
    isolation = isolate_role(allowed, [Path(p) for p in record["forbidden"]])
    from quanthus.backtest.data_provider import ReplayDataProvider

    def reject_legacy(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("CONTROL_LEGACY_PROVIDER_FORBIDDEN")

    ReplayDataProvider.open = reject_legacy  # type: ignore[method-assign]
    isolation["legacy_provider_disabled"] = True
    with (work / "isolation.json").open("x") as handle:
        json.dump(isolation, handle, indent=2)
    with FixedControlRuntime(
        repository,
        Path(record["source"]),
        Path(record["prepared"]),
        admission,
        parent_source=Path(record["parent_source"]),
        prior_prepared=Path(record["prior_prepared"]),
        reference_work=Path(record["reference_work"]),
    ) as runtime:
        require(len(runtime.events) == freeze["candidate_keys"], "CONTROL_CANDIDATES")
        coordinates = {
            "entry:" + event.record.event_id: event.panel_security_id
            + "@"
            + event.record.identity.signal_date.isoformat()
            for event in runtime.events
        }
        with (work / "event-coordinates.json").open("x") as handle:
            json.dump(coordinates, handle, sort_keys=True)
            handle.write("\n")
        output = work / "replays" / ("variant." + arm)
        output.mkdir(parents=True)
        print(
            json.dumps(
                {
                    "stage": "runtime_ready",
                    "role": role,
                    "arm": arm,
                    "frames": len(runtime.frames),
                    "elapsed_seconds": time.monotonic() - start,
                }
            ),
            flush=True,
        )
        evidence, artifacts = runtime.run_variant(arm, output)
        for name, raw in artifacts.items():
            with (output / name).open("xb") as handle:
                handle.write(raw)
        result = {
            "kind": VERSION,
            "status": "COMPLETE",
            "role": role,
            "control_request_digest": freeze["control_request_digest"],
            "source_manifest_sha256": request["source_manifest_sha256"],
            "prerequisite_reuse": "VERIFIED_OWN_ROLE_REUSE",
            "input_physical_proof": proof,
            "variant": arm,
            "replay_digest": evidence.replay_digest,
            "file_sha256": {
                name: "sha256:" + hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()
            },
            "bytes": sum(len(raw) for raw in artifacts.values()),
            "elapsed_seconds": time.monotonic() - start,
            "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "production_authorized": False,
        }
    with (work / "replays/COMPLETE.json").open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument(
        "--role", choices=("primary", "independent_cold_reproduction"), required=True
    )
    args = parser.parse_args()
    freeze, _ = read_json(args.freeze)
    result = run_role(freeze, args.role)
    print(
        json.dumps(
            {
                "stage": "role_complete",
                "role": args.role,
                "bytes": result["bytes"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
