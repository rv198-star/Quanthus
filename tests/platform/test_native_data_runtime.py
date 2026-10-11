import hashlib
import json

import pytest

from quanthus.platform import data_runtime
from quanthus.policy import ConfigurationError, find_project_root


def test_default_retains_frozen_provider(monkeypatch):
    monkeypatch.delenv("QUANTHUS_NATIVE_BASE23_CONSUMERS", raising=False)
    assert data_runtime.replay_provider_path() == "config/replay-data-provider.development.v2.json"
    assert data_runtime.event_source_policy_path() == "config/p4-event-scan-source.research.v1.json"


def test_native_selection_requires_exact_provider_receipt(tmp_path, monkeypatch):
    raw = json.loads(
        (find_project_root() / "config/replay-data-provider.base23.v1.json").read_text()
    )
    (tmp_path / "config").mkdir()
    provider = tmp_path / "config/replay-data-provider.base23.v1.json"
    provider.write_text(json.dumps(raw))
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(raw, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    receipt = {
        "schema_version": "quanthus.direct-dwd-canonical-projection.v1",
        "materializer_version": "direct-dwd-native-base23-v1",
        "provider_config_digest": digest,
    }
    monkeypatch.setenv("QUANTHUS_NATIVE_BASE23_CONSUMERS", "1")
    monkeypatch.setattr(data_runtime, "find_project_root", lambda: tmp_path)
    with pytest.raises(ConfigurationError, match="not published"):
        data_runtime.replay_provider_path()
    (tmp_path / "config/p4-direct-dwd-canonical-projection.base23.v1.json").write_text(
        json.dumps(receipt)
    )
    assert data_runtime.replay_provider_path() == "config/replay-data-provider.base23.v1.json"
    assert data_runtime.event_source_policy_path() == "config/p4-event-scan-source.base23.v1.json"
    raw["execution_authorized"] = True
    provider.write_text(json.dumps(raw))
    with pytest.raises(ConfigurationError, match="receipt differs"):
        data_runtime.replay_provider_path()


def test_invalid_deployment_flag_fails_closed(monkeypatch):
    monkeypatch.setenv("QUANTHUS_NATIVE_BASE23_CONSUMERS", "yes")
    with pytest.raises(ConfigurationError, match="0 or 1"):
        data_runtime.replay_provider_path()


def test_cli_defaults_preserve_explicit_frozen_paths(monkeypatch):
    from argparse import Namespace
    from pathlib import Path

    monkeypatch.setenv("QUANTHUS_NATIVE_BASE23_CONSUMERS", "1")
    explicit = Path("config/replay-data-provider.development.v2.json")
    args = Namespace(command="run-candidate-batch", provider_config=explicit)
    data_runtime.resolve_cli_source_defaults(args)
    assert args.provider_config == explicit
    monkeypatch.setattr(data_runtime, "replay_provider_path", lambda: "native-provider.json")
    monkeypatch.setattr(data_runtime, "event_source_policy_path", lambda: "native-policy.json")
    args = Namespace(command="run-p4-event-scan", provider_config=None, source_policy=None)
    data_runtime.resolve_cli_source_defaults(args)
    assert args.provider_config == Path("native-provider.json")
    assert args.source_policy == Path("native-policy.json")


def test_unrelated_cli_does_not_need_a_data_receipt(monkeypatch):
    from argparse import Namespace

    monkeypatch.setenv("QUANTHUS_NATIVE_BASE23_CONSUMERS", "1")
    data_runtime.resolve_cli_source_defaults(Namespace(command="doctor"))
