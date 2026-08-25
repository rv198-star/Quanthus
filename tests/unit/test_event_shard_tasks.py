from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch

from quanthus.event_library import shard_tasks
from quanthus.event_library.shard_tasks import _feature_panel, _release_file_cache


def test_shard_file_cache_release_handles_one_file_and_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"a" * 4096)
    second.write_bytes(b"b" * 4096)

    _release_file_cache((first,), flush=True)
    _release_file_cache((root,), flush=False)

    assert first.read_bytes() == b"a" * 4096
    assert second.read_bytes() == b"b" * 4096


def test_feature_panel_opens_decision_scope_but_materializes_execution_scope(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    decision_start = date(2012, 1, 4)
    decision_end = date(2021, 12, 30)
    execution_end = date(2021, 12, 31)
    provider = SimpleNamespace(
        scope=SimpleNamespace(decision_start=decision_start, decision_end=decision_end)
    )
    registry = object()
    session = object()
    observed: dict[str, object] = {}

    def open_session(_provider: object, **kwargs: object):  # type: ignore[no-untyped-def]
        observed["session"] = kwargs
        return nullcontext(session)

    output = tmp_path / "panel" / "panel.parquet"

    def build_panel(**kwargs: object):  # type: ignore[no-untyped-def]
        observed["panel"] = kwargs
        output.write_bytes(b"panel")
        return SimpleNamespace(
            path=output,
            parquet_bytes=5,
            report=SimpleNamespace(row_count=7, selected_security_count=1),
            to_receipt=lambda: {"panel": "receipt"},
        )

    monkeypatch.setattr(shard_tasks, "load_replay_data_provider_config", lambda _path: provider)
    monkeypatch.setattr(shard_tasks, "load_feature_registry", lambda _path: registry)
    monkeypatch.setattr(shard_tasks, "open_materialized_direct_dwd_session", open_session)
    monkeypatch.setattr(shard_tasks, "build_job_local_feature_panel", build_panel)
    monkeypatch.setattr(shard_tasks, "_release_file_cache", lambda *_args, **_kwargs: None)

    result = _feature_panel(
        {
            "provider_config": "config/provider.json",
            "feature_registry": "config/features.json",
            "counts": {"daily": 1},
            "scope_start": decision_start.isoformat(),
            "scope_end": execution_end.isoformat(),
            "security_ids": ["600001"],
            "projection_root": str(tmp_path / "projection"),
            "output": str(output),
            "receipt": str(tmp_path / "panel" / "receipt.json"),
        },
        tmp_path,
    )

    assert observed["session"] == {
        "root": tmp_path / "projection",
        "scope_start": decision_start,
        "scope_end": decision_end,
        "counts": {"daily": 1},
    }
    assert observed["panel"]["scope_end"] == execution_end  # type: ignore[index]
    assert observed["panel"]["allowed_security_ids"] == ("600001",)  # type: ignore[index]
    assert observed["panel"]["duckdb_memory_limit"] == "1GB"  # type: ignore[index]
    assert observed["panel"]["release_source_cache"] is True  # type: ignore[index]
    assert observed["panel"]["output_order"] == "security-date"  # type: ignore[index]
    assert observed["panel"]["spill_cache_release_interval_seconds"] == 1.0  # type: ignore[index]
    assert observed["panel"]["materialize_selected_sources"] is False  # type: ignore[index]
    assert result["rows"] == 7
