from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openstack_perf.cli import main
from openstack_perf.reporting import (
    EXIT_ERROR,
    EXIT_FUNCTIONAL_FAILURE,
    EXIT_PERFORMANCE_REGRESSION,
)
from openstack_perf.results import FunctionalVerdict, OverallVerdict


EXAMPLE = Path(__file__).parents[2] / "config" / "regression.example.toml"


def test_validate_config_is_offline():
    stdout = StringIO()
    with patch("openstack.connect") as connection, patch("subprocess.run") as ssh:
        exit_code = main(
            ["validate-config", "--config", str(EXAMPLE)], stdout=stdout
        )

    assert exit_code == 0
    assert "Configuration valid: devstack-release-regression" in stdout.getvalue()
    connection.assert_not_called()
    ssh.assert_not_called()


def test_run_without_both_live_gates_fails_concisely(tmp_path):
    stderr = StringIO()
    with patch("openstack_perf.runner.create_connection") as connection:
        exit_code = main(
            [
                "run", "--config", str(EXAMPLE), "--output-dir", str(tmp_path),
                "--role", "baseline",
            ],
            environ={},
            stderr=stderr,
        )

    assert exit_code == EXIT_ERROR
    assert stderr.getvalue().startswith("error: live execution requires")
    assert "Traceback" not in stderr.getvalue()
    connection.assert_not_called()


def test_invalid_config_error_goes_to_stderr_without_traceback(tmp_path):
    config = tmp_path / "invalid.toml"
    config.write_text("schema_version = 99\n", encoding="utf-8")
    stderr = StringIO()

    exit_code = main(
        ["validate-config", "--config", str(config)], stderr=stderr
    )

    assert exit_code == EXIT_ERROR
    assert stderr.getvalue().startswith("error:")
    assert "Traceback" not in stderr.getvalue()


def test_unexpected_execution_error_is_sanitized(tmp_path):
    stderr = StringIO()
    with patch(
        "openstack_perf.cli.run_regression",
        side_effect=RuntimeError("token=private"),
    ):
        exit_code = main(
            [
                "run", "--live", "--config", str(EXAMPLE),
                "--output-dir", str(tmp_path), "--role", "baseline",
            ],
            environ={"OPENSTACK_PERF_RUN_LIVE": "1"},
            stderr=stderr,
        )

    assert exit_code == EXIT_ERROR
    assert stderr.getvalue() == "error: internal execution failed: RuntimeError\n"
    assert "private" not in stderr.getvalue()


def test_argument_error_uses_documented_error_exit_code():
    stderr = StringIO()

    exit_code = main(["compare"], stderr=stderr)

    assert exit_code == EXIT_ERROR
    assert stderr.getvalue().startswith("error: the following arguments are required")


def test_run_functional_failure_returns_exit_code_one(tmp_path):
    outcome = SimpleNamespace(
        run=SimpleNamespace(functional_verdict=FunctionalVerdict.FAILURE),
        artifact_path=tmp_path / "candidate.json",
        comparison=None,
    )
    with patch("openstack_perf.cli.run_regression", return_value=outcome), patch(
        "openstack_perf.cli.render_run_summary", return_value="summary"
    ):
        exit_code = main(
            [
                "run",
                "--live",
                "--config",
                str(EXAMPLE),
                "--output-dir",
                str(tmp_path),
                "--role",
                "candidate",
            ],
            environ={"OPENSTACK_PERF_RUN_LIVE": "1"},
        )

    assert exit_code == EXIT_FUNCTIONAL_FAILURE


@pytest.mark.parametrize(
    "verdict,expected",
    [
        (OverallVerdict.PERFORMANCE_REGRESSION, EXIT_PERFORMANCE_REGRESSION),
        (OverallVerdict.INSUFFICIENT_EVIDENCE, EXIT_ERROR),
    ],
)
def test_compare_returns_regression_or_insufficient_exit_code(verdict, expected):
    comparison = SimpleNamespace(verdict=verdict)
    with patch(
        "openstack_perf.cli.compare_artifacts", return_value=comparison
    ), patch(
        "openstack_perf.cli.render_comparison_summary", return_value="summary"
    ):
        exit_code = main(
            [
                "compare",
                "--config",
                str(EXAMPLE),
                "--baseline",
                "baseline.json",
                "--candidate",
                "candidate.json",
            ]
        )

    assert exit_code == expected
