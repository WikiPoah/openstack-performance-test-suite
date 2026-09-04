"""Offline command integration tests."""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from openstack_perf.artifacts import write_run_artifact
from openstack_perf.cli import main
from openstack_perf.results import (
    AssertionResult,
    CleanSnapshotStatus,
    EnvironmentFingerprint,
    FunctionalVerdict,
    RegressionRunResult,
    RunMetadata,
    RunRole,
    ScenarioObservation,
    TimingSample,
)
from openstack_perf.statistics import calculate_timing_statistics


def _config(tmp_path):
    path = tmp_path / "regression.toml"
    path.write_text(
        '''schema_version = 1
name = "offline-example"

[release]
platform_release = "2026.1"
source_branch = "stable/2026.1"
application_release = "1.0"
clean_snapshot = "unknown"

[consumer]
cloud = "test-cloud"
project = "test-project"
region = "RegionOne"
image = "test-image"
flavor = "test-flavor"
network = "test-network"

[scenarios.service_discovery]
enabled = true
comparison_mode = "performance"
samples = 1
[scenarios.boot_image]
enabled = false
comparison_mode = "functional_only"
samples = 1
[scenarios.infrastructure_state]
enabled = false
comparison_mode = "functional_only"
[scenarios.web_application]
enabled = false
comparison_mode = "functional_only"
samples = 1
[scenarios.application_services]
enabled = false
comparison_mode = "functional_only"
samples = 1
[scenarios.vm_lifecycle]
enabled = false
comparison_mode = "functional_only"
samples = 3
verify_network_attachment = true
provisioning_timeout_seconds = 180
cleanup_timeout_seconds = 120

[[comparison.policies]]
scenario_id = "identity.service_discovery"
target_id = "test-project"
minimum_sample_count = 1
p50_relative = 0.1
''',
        encoding="utf-8",
    )
    return path


def _run(role, run_id, duration):
    sample = TimingSample(1, duration)
    observation = ScenarioObservation(
        "identity.service_discovery",
        "test-project",
        "Service discovery",
        FunctionalVerdict.PASS,
        (AssertionResult("available", True),),
        (sample,),
        calculate_timing_statistics((duration,)),
    )
    return RegressionRunResult(
        RunMetadata(
            run_id,
            role,
            "2026-09-04T10:00:00Z",
            "2026-09-04T10:01:00Z",
            CleanSnapshotStatus.UNKNOWN,
            "offline-example",
        ),
        EnvironmentFingerprint(
            "test-cloud", "RegionOne", "2026.1", "stable/2026.1", "1.0"
        ),
        (observation,),
    )


def test_offline_validate_and_compare_commands_use_real_files(tmp_path):
    config = _config(tmp_path)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    write_run_artifact(baseline, _run(RunRole.BASELINE, "baseline", 1.0))
    write_run_artifact(candidate, _run(RunRole.CANDIDATE, "candidate", 1.05))
    output = StringIO()

    with patch("openstack.connect") as connection, patch("subprocess.run") as ssh:
        validate_code = main(
            ["validate-config", "--config", str(config)], stdout=output
        )
        compare_code = main(
            [
                "compare", "--config", str(config),
                "--baseline", str(baseline), "--candidate", str(candidate),
            ],
            stdout=output,
        )

    assert validate_code == 0
    assert compare_code == 0
    assert "Overall: PASS" in output.getvalue()
    connection.assert_not_called()
    ssh.assert_not_called()
