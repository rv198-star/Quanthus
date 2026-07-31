import json

from quanthus.cli import main


def _invoke(capsys, *arguments: str) -> dict[str, object]:
    assert main(list(arguments)) == 0
    output = capsys.readouterr().out
    value = json.loads(output)
    assert isinstance(value, dict)
    return value


def test_attribution_cli_plans_runs_resumes_and_verifies_a_release(
    tmp_path, capsys
) -> None:
    run_root = tmp_path / "runs"
    publication_root = tmp_path / "publications"
    common = ("--fixture", "synthetic-v1", "--run-root", str(run_root))

    planned = _invoke(capsys, "attribution-plan", *common)
    assert planned["state"] == "planned"
    assert planned["source_kind"] == "synthetic_fixture_not_strategy_evidence"

    initial_status = _invoke(capsys, "attribution-status", *common)
    assert initial_status["state"] == "planned"
    assert initial_status["claim_count"] == 0

    completed = _invoke(
        capsys,
        "attribution-run",
        *common,
        "--publication-root",
        str(publication_root),
    )
    assert completed["state"] == "evaluated"
    assert completed["synthetic_fixture"] is True
    assert (publication_root / "catalog.json").is_file()

    evaluated_status = _invoke(capsys, "attribution-status", *common)
    assert evaluated_status["state"] == "evaluated"
    assert evaluated_status["claim_count"] > 0

    verified = _invoke(
        capsys,
        "attribution-verify",
        "--release-dir",
        str(completed["release_dir"]),
    )
    assert verified["verified"] is True
    assert verified["formal_strategy_evidence"] is False
    assert verified["release_digest"] == completed["release_digest"]

    resumed = _invoke(
        capsys,
        "attribution-resume",
        *common,
        "--publication-root",
        str(publication_root),
    )
    assert resumed["release_id"] == completed["release_id"]
    assert resumed["workspace_digest"] == completed["workspace_digest"]
