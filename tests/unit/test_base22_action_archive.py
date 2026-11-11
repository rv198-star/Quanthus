from __future__ import annotations

import hashlib
import io
import json
import tarfile
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_action_archive import (
    CONTRACT_ID,
    add_response,
    response_bytes,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError

DATA = b'{"rows":[[0,null,1.2345678901],[false,"original",2]],"unknown":{}}\n'
SHA = "sha256:" + hashlib.sha256(DATA).hexdigest()
NAME = "em_raw/000001.json"


def test_original_bytes_scalars_arrays_and_unknown_fields_survive_sequential_archive(tmp_path):
    path = tmp_path / "raw.tar"
    with tarfile.open(path, "w|", format=tarfile.PAX_FORMAT) as archive:
        add_response(archive, NAME, DATA, SHA)
    restored = list(response_bytes(path, {NAME: (SHA, len(DATA))}))
    assert restored == [(NAME, SHA, DATA)]
    assert json.loads(restored[0][2], parse_float=Decimal) == json.loads(DATA, parse_float=Decimal)


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "changed", "symlink"])
def test_incomplete_changed_unknown_or_linked_members_fail_closed(tmp_path, fault):
    path = tmp_path / "raw.tar"
    with tarfile.open(path, "w|", format=tarfile.PAX_FORMAT) as archive:
        if fault != "missing":
            add_response(archive, NAME, DATA, SHA)
        if fault in ("extra", "duplicate"):
            add_response(archive, NAME if fault == "duplicate" else "em_raw/000002.json", DATA, SHA)
        elif fault == "changed":
            info = tarfile.TarInfo("em_raw/000002.json")
            info.size = len(DATA)
            info.pax_headers = {"original_sha256": SHA, "contract_id": CONTRACT_ID}
            archive.addfile(info, io.BytesIO(DATA[:-1] + b" "))
        elif fault == "symlink":
            info = tarfile.TarInfo("em_raw/000002.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "/old/source"
            archive.addfile(info)
    expected = {NAME: (SHA, len(DATA))}
    if fault in ("changed", "symlink"):
        expected["em_raw/000002.json"] = (SHA, len(DATA))
    with pytest.raises(BoundedError):
        list(response_bytes(path, expected))


@pytest.mark.parametrize(
    "name,data,sha",
    [
        ("../000001.json", DATA, SHA),
        (NAME, b"", SHA),
        (NAME, DATA, "sha256:" + "0" * 64),
    ],
)
def test_writer_refuses_wrong_path_or_unproven_original_bytes(tmp_path, name, data, sha):
    with (
        tarfile.open(tmp_path / "raw.tar", "w|", format=tarfile.PAX_FORMAT) as archive,
        pytest.raises(BoundedError),
    ):
        add_response(archive, name, data, sha)
