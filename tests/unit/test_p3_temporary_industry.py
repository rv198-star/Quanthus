from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus import p3_temporary_industry as module

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = json.loads(
    (ROOT / "config/research/p3-strat-003/single-security-industry-proposal.v1.json").read_text()
)
HIERARCHY = json.loads((ROOT / PROPOSAL["source"]["hierarchy_path"]).read_text())


def response(rows=None):
    return {
        "fields": module.FIELDS,
        "error_code": "0",
        "rows": rows
        if rows is not None
        else [
            [
                "2024-10-09",
                "sh.688287",
                "synthetic",
                "C39计算机、通信和其他电子设备制造业",
                "证监会行业分类",
            ]
        ],
    }


def test_fixed_proposal_and_activation():
    assert (
        module.sha(
            (
                ROOT / "config/research/p3-strat-003/single-security-industry-proposal.v1.json"
            ).read_bytes()
        )
        == module.PROPOSAL_SHA
    )
    activation = json.loads(
        (ROOT / "config/research/p3-p5-temporary-source-activation.v1.json").read_text()
    )
    for value in activation["sources"].values():
        assert module.sha((ROOT / value["proposal_path"]).read_bytes()) == value["proposal_sha256"]
    assert activation["temporary_success_closes_data_issues"] is False


def test_valid_static_response():
    result = module.qualify(response(), PROPOSAL, HIERARCHY)
    assert result["leaf_code"] == "C39"
    assert result["level1_code"] == "C"


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([], "EMPTY"),
        (response()["rows"] * 2, "CARDINALITY"),
        (
            [
                [
                    "2024-10-10",
                    "sh.688287",
                    "x",
                    "C39计算机、通信和其他电子设备制造业",
                    "证监会行业分类",
                ]
            ],
            "FUTURE",
        ),
        (
            [
                [
                    "2024-10-09",
                    "sz.688287",
                    "x",
                    "C39计算机、通信和其他电子设备制造业",
                    "证监会行业分类",
                ]
            ],
            "IDENTITY",
        ),
        ([["2024-10-09", "sh.688287", "x", "C39猜测行业", "证监会行业分类"]], "HIERARCHY"),
        (
            [
                [
                    "2024-10-09",
                    "sh.688287",
                    "x",
                    "C39计算机、通信和其他电子设备制造业",
                    "申万行业分类",
                ]
            ],
            "CLASSIFICATION",
        ),
        (
            [
                [
                    "2024-99-99",
                    "sh.688287",
                    "x",
                    "C39计算机、通信和其他电子设备制造业",
                    "证监会行业分类",
                ]
            ],
            None,
        ),
    ],
)
def test_terminal_invalid_responses(rows, reason):
    with pytest.raises(ValueError, match=reason):
        module.qualify(response(rows), PROPOSAL, HIERARCHY)


@pytest.mark.parametrize(
    "rows,expected",
    [
        (response()["rows"], "QUALIFIED_TEMPORARY_STATIC_SOURCE"),
        ([], "UNAVAILABLE_COMPLETE_EMPTY_RESPONSE"),
    ],
)
def test_one_query_durable_receipt_and_no_repeat(tmp_path, monkeypatch, rows, expected):
    calls = []

    class Result:
        fields = module.FIELDS
        error_code = "0"
        index = -1

        def next(self):
            self.index += 1
            return self.index < len(rows)

        def get_row_data(self):
            return rows[self.index]

    def query(**kwargs):
        calls.append(kwargs)
        return Result()

    monkeypatch.setattr(
        module, "validate_inputs", lambda *_: (PROPOSAL, HIERARCHY, "sha256:synthetic")
    )
    monkeypatch.setattr(module.importlib.metadata, "version", lambda _: "0.9.3")
    monkeypatch.setattr(module, "verify_code_receipt", lambda *_: "sha256:synthetic")
    monkeypatch.setattr(module, "bound_transport", lambda _: {"received_bytes": 0})
    monkeypatch.setattr(module, "RUNTIME_ROOT", tmp_path)
    tmp_path.chmod(0o700)
    monkeypatch.setitem(
        sys.modules,
        "baostock",
        SimpleNamespace(login=lambda: SimpleNamespace(error_code="0"), query_stock_industry=query),
    )
    output = tmp_path / "attempt"
    receipt = module.run(ROOT, tmp_path / "unused", output, "a" * 40)
    assert calls == [{"code": "sh.688287", "date": "2024-10-09"}]
    assert receipt["disposition"] == expected
    assert receipt["retrieved_at"][:4] != "2024"
    assert receipt["response_sha256"] == module.sha((output / "response.json").read_bytes())
    assert receipt["historical_as_known"] is False
    assert receipt["formal_DWD_repair"] is False
    assert receipt["B_accepted"] is False
    with pytest.raises(FileExistsError):
        module.run(ROOT, tmp_path / "unused", output, "a" * 40)
    with pytest.raises(ValueError, match="FIXED_PRIVATE_ATTEMPT_ROOT_REQUIRED"):
        module.run(ROOT, tmp_path / "unused", tmp_path / "another-attempt", "a" * 40)
    assert len(calls) == 1


def test_wrong_provider_version_stops_before_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "RUNTIME_ROOT", tmp_path)
    tmp_path.chmod(0o700)
    monkeypatch.setattr(
        module, "validate_inputs", lambda *_: (PROPOSAL, HIERARCHY, "sha256:synthetic")
    )
    monkeypatch.setattr(module.importlib.metadata, "version", lambda _: "0.9.4")
    monkeypatch.setattr(module, "verify_code_receipt", lambda *_: "sha256:synthetic")
    with pytest.raises(ValueError, match="PROVIDER_VERSION_MISMATCH"):
        module.run(ROOT, tmp_path / "unused", tmp_path / "attempt", "a" * 40)
    assert not (tmp_path / "attempt").exists()


def test_code_receipt_rejects_wrong_revision_and_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "RUNTIME_ROOT", tmp_path)
    record = {
        "code_revision": "a" * 40,
        "git_blob_verification": True,
        "files": {path: module.sha((ROOT / path).read_bytes()) for path in module.CODE_FILES},
    }
    path = tmp_path / "code-receipt.json"
    path.write_bytes(module.encoded(record))
    assert module.verify_code_receipt(ROOT, "a" * 40) == module.sha(path.read_bytes())
    with pytest.raises(ValueError, match="IMPLEMENTATION_RECEIPT_MISMATCH"):
        module.verify_code_receipt(ROOT, "b" * 40)
    record["files"][module.CODE_FILES[0]] = "sha256:changed"
    path.write_bytes(module.encoded(record))
    with pytest.raises(ValueError, match="IMPLEMENTATION_BYTES_MISMATCH"):
        module.verify_code_receipt(ROOT, "a" * 40)


def test_prepare_rejects_symlink_before_writing(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target)
    monkeypatch.setattr(module, "RUNTIME_ROOT", link)
    with pytest.raises(ValueError, match="SYMLINK_ATTEMPT_ROOT_FORBIDDEN"):
        module.prepare_code_receipt(ROOT, "a" * 40)
    assert list(target.iterdir()) == []


def test_transport_budget_bypasses_provider_catch(monkeypatch):
    context = SimpleNamespace(default_socket=SimpleNamespace(recv=lambda _: b"12345"))
    package = SimpleNamespace(common=SimpleNamespace(context=context))
    monkeypatch.setitem(sys.modules, "baostock", package)
    monkeypatch.setitem(sys.modules, "baostock.common", package.common)
    monkeypatch.setitem(sys.modules, "baostock.common.context", context)
    measured = module.bound_transport(4)
    with pytest.raises(module.SourceBudgetExceeded, match="BUDGET"):
        try:
            context.default_socket.recv(8192)
        except Exception:
            pytest.fail("provider must not swallow our deadline/transport budget")
    assert measured["received_bytes"] == 5
