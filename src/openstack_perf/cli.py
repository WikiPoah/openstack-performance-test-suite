import argparse
import sys

from openstack_perf.artifacts import ArtifactError
from openstack_perf.config import ConfigurationError, load_config
from openstack_perf.reporting import (
    EXIT_ERROR,
    comparison_exit_code,
    render_comparison_summary,
    render_run_summary,
    run_exit_code,
)
from openstack_perf.results import RunRole
from openstack_perf.runner import RunnerError, compare_artifacts, run_regression


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigurationError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="openstack-perf",
        description="Run and compare OpenStack consumer regression evidence.",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_ArgumentParser
    )

    validate = commands.add_parser(
        "validate-config", help="validate configuration without external access"
    )
    validate.add_argument("--config", required=True)

    compare = commands.add_parser(
        "compare", help="compare existing baseline and candidate artifacts"
    )
    compare.add_argument("--config", required=True)
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)

    run = commands.add_parser("run", help="execute one live regression run")
    run.add_argument("--config", required=True)
    run.add_argument("--output-dir", required=True)
    run.add_argument("--role", required=True, choices=("baseline", "candidate"))
    run.add_argument("--baseline")
    run.add_argument("--live", action="store_true")
    return parser


def main(argv=None, *, environ=None, stdout=None, stderr=None) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        config = load_config(arguments.config)
        if arguments.command == "validate-config":
            print(f"Configuration valid: {config.name}", file=output)
            return 0
        if arguments.command == "compare":
            comparison = compare_artifacts(
                config, arguments.baseline, arguments.candidate
            )
            print(render_comparison_summary(comparison), file=output)
            return comparison_exit_code(comparison)

        role = RunRole(arguments.role)
        if arguments.baseline and role is not RunRole.CANDIDATE:
            raise ConfigurationError(
                "--baseline is valid only with --role candidate"
            )
        outcome = run_regression(
            config,
            role=role,
            output_dir=arguments.output_dir,
            live=arguments.live,
            baseline_path=arguments.baseline,
            environ=environ,
        )
        print(render_run_summary(outcome.run, outcome.artifact_path), file=output)
        if outcome.comparison is not None:
            print("", file=output)
            print(render_comparison_summary(outcome.comparison), file=output)
            return comparison_exit_code(outcome.comparison)
        return run_exit_code(outcome.run)
    except (ArtifactError, ConfigurationError, RunnerError, ValueError) as exc:
        print(f"error: {exc}", file=errors)
        return EXIT_ERROR
    except Exception as exc:
        print(f"error: internal execution failed: {type(exc).__name__}", file=errors)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
