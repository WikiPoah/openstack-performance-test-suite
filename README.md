# OpenStack Performance Test Suite

A Python suite for measuring and regression-testing useful workflows on
OpenStack. It focuses on what a platform consumer can accomplish, rather than
testing OpenStack internals in isolation. The goal is to identify functional
breakage and performance deterioration between comparable platform releases
and environments.

## Why This Project

A platform consumer needs more than individual APIs that return successfully.
Useful workflows must continue to function correctly and within expected
performance ranges after a platform change. This suite represents those
workflows directly so functional results and timing measurements can be
reviewed together.

## Testing Approach

The suite is designed around consumer-facing workflows that combine:

- Functional validation of the resulting platform resource.
- Timing of meaningful operations from a defined start to a defined outcome.
- Repeatable measurements that can be compared across comparable runs and
  platform releases.
- Cleanup as part of correctness, so test resources do not accumulate.
- External environment configuration rather than hard-coded cloud details.

The repository currently contains the first implemented workflow described
below. Broader measurement and comparison capabilities are planned.

## Current Capabilities

The repository currently provides:

- An OpenStack connection factory that delegates configuration and
  authentication to `openstacksdk`.
- `Environment` and `WorkflowRunResult` models for deployment metadata,
  functional outcomes, durations, and failure context.
- The `vm.lifecycle` workflow for one-server provisioning and cleanup.
- Unit tests that mock the SDK boundary and do not require a live cloud.

### VM Lifecycle

The `vm.lifecycle` workflow receives an existing OpenStack connection and
resolved image, flavor, and network IDs. Its sequence is:

```text
create server -> wait for ACTIVE -> validate -> delete -> verify deletion
```

Provisioning duration is measured from immediately before the create request is
initiated through confirmed `ACTIVE` state. Validation and cleanup time are
excluded from that measurement. The duration is intended primarily for
regression comparison between comparable runs and environments, not as a
universal benchmark claim about OpenStack.

The workflow performs targeted cleanup of the server it created. Provisioning
and validation failures are reported as failed results, cleanup is attempted
when a usable server identifier exists, and cleanup failures are also reported
as failures. When both primary and cleanup failures occur, their contexts are
retained in the result.

## Installation

The project requires Python 3.11 or newer. Create a virtual environment and
install the package with its development dependency:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Running Tests

Run the current unit suite with:

```bash
.venv/bin/python -m pytest -q
```

The unit tests use mocks, so they do not require OpenStack credentials or a
live OpenStack environment.

## OpenStack Configuration

Live workflows use an existing OpenStack connection. Cloud configuration and
credentials are supplied externally through the standard `clouds.yaml`
configuration supported by `openstacksdk`; credential values must never be
committed to this repository.

The application code does not hard-code a particular cloud, project, image,
flavor, network, or infrastructure environment.

## Roadmap

Planned work, not current functionality, includes:

- BDD scenarios for runnable regression workflows.
- Additional consumer workflows covering Keystone, Nova, Neutron, and Glance.
- Machine-readable result output and baseline/regression comparison.
- Product-oriented, read-only checks for supported applications.
- Repeated sampling and concurrency where they provide useful evidence.

## Design Principles

- Keep workflows portable across OpenStack environments.
- Keep configuration and credentials outside the repository.
- Treat deterministic, targeted cleanup as part of test correctness.
- Prefer useful regression measurements over synthetic benchmark claims.
- Evaluate the platform from the perspective of its consumers and products.
