from quanthus.policy import find_project_root

ROOT = find_project_root()
LAUNCHER = ROOT / "ops/bin/quanthus-stage-agentdock-launcher"


def source() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_launcher_is_stable_fixed_path_dispatch_to_repo_helper() -> None:
    text = source()
    assert '[[ "$(id -un)" == "qiuyu" ]]' in text
    assert '[[ "$(hostname)" == "RX6800" ]]' in text
    assert 'repo_root="/home/qiuyu/quanthus-code/Quanthus"' in text
    assert 'repo_helper="${repo_root}/ops/bin/quanthus-stage-agentdock-control"' in text
    assert 'exec /bin/bash -s -- "$@" < "${repo_helper}"' in text


def test_launcher_refuses_mutable_or_untracked_helper_drift() -> None:
    text = source()
    assert '[[ -f "${repo_helper}" && ! -L "${repo_helper}" ]]' in text
    assert "stat -c '%U:%G'" in text
    assert '== "qiuyu:qiuyu"' in text
    assert (
        'git -C "${repo_root}" diff --name-only HEAD -- '
        'ops/bin/quanthus-stage-agentdock-control'
    ) in text
    assert (
        'git -C "${repo_root}" diff --cached --name-only HEAD -- '
        'ops/bin/quanthus-stage-agentdock-control'
    ) in text
    assert (
        'git -C "${repo_root}" cat-file -e '
        '"HEAD:ops/bin/quanthus-stage-agentdock-control"'
    ) in text


def test_launcher_does_not_gain_root_or_generic_install_authority() -> None:
    text = source()
    for forbidden in (
        "\nsudo ",
        "install ",
        "chmod ",
        "chown ",
        "rm -rf",
        "eval ",
        "exec sh -c",
        "exec bash -c",
    ):
        assert forbidden not in text
