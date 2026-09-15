from quanthus.policy import find_project_root

ROOT = find_project_root()
CONTROL = ROOT / "ops/bin/quanthus-stage-agentdock-control"


def source() -> str:
    return CONTROL.read_text(encoding="utf-8")


def test_control_surface_is_bounded_to_rx6800_qiuyu_and_exact_commands() -> None:
    text = source()
    assert '[[ "$(id -un)" == "qiuyu" ]]' in text
    assert '[[ "$(hostname)" == "RX6800" ]]' in text
    for command in (
        "deploy)",
        "doctor)",
        "submit-p011-d1)",
        "submit-p012-g1)",
        "submit-p013-a)",
        "submit-p013-c)",
        "submit-p013-d)",
        "submit-p014-a)",
        "submit-p014-b)",
        "submit-p014-c)",
        "submit-p015-a)",
        "submit-p015-b)",
        "submit-p015-c)",
        "submit-p016-b)",
        "submit-p017-b)",
        "submit-p018-b)",
        "submit-p020-b)",
        "seal-p014-review)",
        "seal-p015-review)",
        "status)",
    ):
        assert command in text
    for forbidden in ("cancel)", "serve)", "eval ", "bash -c", '"$@"'):
        assert forbidden not in text


def test_deploy_accepts_only_exact_current_github_main_and_clean_checkout() -> None:
    text = source()
    assert '[[ -z "$(git -C "${repo_root}" status --porcelain)" ]]' in text
    assert "+refs/heads/main:refs/remotes/stage-control/main" in text
    assert '[[ "${target}" == "${remote_main}" ]]' in text
    assert 'git -C "${repo_root}" checkout --detach "${target}"' in text
    assert 'restart_and_verify "${target}"' in text


def test_submit_is_hard_bound_to_named_stage_kinds_and_runtime_revision() -> None:
    text = source()
    assert 'p011_kind="quanthus.stage-orderly-consolidation-joint-d1-prepare.v1"' in text
    assert 'p012_g1_kind="quanthus.stage-atomic-proposition-g1-prepare.v1"' in text
    assert 'p013_a_kind="quanthus.stage-development-resource-capacity-census.v1"' in text
    assert 'p013_c_kind="quanthus.stage-development-resource-plan.v1"' in text
    assert 'p013_d_kind="quanthus.stage-atomic-proposition-successor-d1-prepare.v1"' in text
    assert 'p014_a_kind="quanthus.stage-development-resource-admission-screen.v1"' in text
    assert 'p014_b_kind="quanthus.stage-development-resource-qualified-plan.v1"' in text
    assert (
        'p014_c_kind="quanthus.stage-atomic-proposition-admission-qualified-d1-prepare.v1"'
        in text
    )
    assert 'p015_a_kind="quanthus.stage-complete-reserve-review-readiness.v1"' in text
    assert 'p015_b_kind="quanthus.stage-complete-reserve-additional-review-set.v1"' in text
    assert 'p015_c_kind="quanthus.stage-complete-reserve-additional-review-prepare.v1"' in text
    assert 'p016_b_kind="quanthus.stage-combined-atomic-scoring.v1"' in text
    assert 'p017_b_kind="quanthus.stage-stratum-normalized-instability.v1"' in text
    assert 'p018_b_kind="quanthus.stage-instability-channel-decomposition.v1"' in text
    assert 'p020_b_kind="quanthus.stage-downside-lifecycle-resource-plan.v1"' in text
    assert '[[ "$(service_revision)" == "${target}" ]]' in text
    assert 'bash "${worker}" submit --kind "${p011_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p012_g1_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p013_a_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p013_c_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p013_d_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p014_a_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p014_b_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p014_c_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p015_a_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p015_b_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p015_c_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p016_b_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p017_b_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p018_b_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" submit --kind "${p020_b_kind}" --code-revision "${target}"' in text
    assert 'bash "${worker}" seal-p014-review --job-id "$2"' in text
    assert 'bash "${worker}" seal-p015-review --job-id "$2"' in text
    assert 'p014_review_submission="/tmp/quanthus-p014-review-submission.json"' in text
    assert 'p015_review_submission="/tmp/quanthus-p015-review-submission.json"' in text
    assert '== "agentdock:agentdock"' in text
    assert '== "644"' in text
    assert "524288" in text


def test_status_accepts_only_stage_job_ids() -> None:
    text = source()
    assert '[[ "$2" =~ ^stage_[0-9a-f]{32}$ ]]' in text
    assert 'bash "${worker}" status --job-id "$2"' in text


def test_control_script_has_no_generic_file_or_shell_forwarding() -> None:
    text = source()
    assert "/home/qiuyu/quanthus-code/Quanthus" in text
    assert "/home/qiuyu/quanthus-data/training" in text
    assert "/home/qiuyu/.ssh/quanthus-github-ro" in text
    assert "rm -rf" not in text
    assert "chmod" not in text
    assert "chown" not in text
    assert "\nsudo " not in text
