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

The repository contains focused consumer workflows together with result,
statistics, artifact, and baseline-comparison foundations.

## Current Capabilities

The repository currently provides:

- An OpenStack connection factory that delegates configuration and
  authentication to `openstacksdk`.
- `Environment` and `WorkflowRunResult` models for deployment metadata,
  functional outcomes, durations, and failure context.
- The `vm.lifecycle` workflow for one-server provisioning and cleanup.
- Read-only checks for authenticated project scope, required service endpoint
  discovery, and expected boot-image metadata.
- A read-only infrastructure check for one configured critical server and its
  exact network attachment.
- Immutable regression observations, deterministic p50/p95 statistics,
  schema-versioned JSON artifacts, and configurable baseline comparison.
- Four consumer-facing `pytest-bdd` scenarios that verify a consumer can:
  - Provision and remove a virtual machine.
  - Provision a workload with an address on the requested network.
  - Discover required services and a usable boot image.
  - Confirm configured critical infrastructure remains correctly attached.
- Unit tests that mock the SDK boundary and do not require a live cloud.

### Platform Discovery

The platform-discovery check uses a fresh SDK connection for every sample. It
measures authentication, confirmation of the expected project scope, and
resolution of the compute, network, and image service endpoints. Returned
authentication tokens are neither retained nor serialized.

The boot-image check uses an already authenticated connection and measures
image lookup followed by authoritative metadata retrieval. It requires the
configured image to be active and to expose a usable ID, matching name, image
formats, and positive size. It does not download or modify image data.

### Read-only Infrastructure State

The infrastructure check validates one explicitly configured existing server.
It confirms the authenticated project, active server state, and exactly one
port matching the server ID, configured network, and expected fixed IP. This
check performs no create, update, or delete operations and is intended for use
with a separately configured read-only cloud account.

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

### Network Attachment

The network scenario extends the VM lifecycle with focused attachment checks.
While the VM is active, the workflow verifies that a port associated with its
exact server ID belongs to the requested network and has a fixed IP. After the
VM is deleted, it confirms that the same automatically managed port also
disappears.

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

This normal test command excludes tests marked `integration`, so it does not
create resources in a live OpenStack environment. The unit tests use mocks and
do not require OpenStack credentials.

To run the live BDD scenarios, explicitly select integration tests and opt in
to live access:

```bash
OPENSTACK_PERF_RUN_LIVE=1 \
OPENSTACK_PERF_CLOUD=my-cloud \
OPENSTACK_PERF_IMAGE=my-image \
OPENSTACK_PERF_FLAVOR=my-flavor \
OPENSTACK_PERF_NETWORK=my-network \
.venv/bin/python -m pytest tests/integration/test_vm_lifecycle_bdd.py -m integration -q
```

Both explicit integration-test selection and `OPENSTACK_PERF_RUN_LIVE=1` are
required. The remaining variables identify the externally configured cloud and
the image, flavor, and network by name. The scenario resolves those names to
resource IDs before calling the existing VM lifecycle workflow.

The discovery and read-only infrastructure scenarios use the same explicit
live opt-in. Their resource names are supplied through
`OPENSTACK_PERF_PROJECT`, `OPENSTACK_PERF_IMAGE`,
`OPENSTACK_PERF_CORP_CLOUD`, `OPENSTACK_PERF_CORP_PROJECT`,
`OPENSTACK_PERF_CORP_SERVER`, `OPENSTACK_PERF_CORP_NETWORK`, and
`OPENSTACK_PERF_CORP_FIXED_IP`. The infrastructure cloud must be configured
externally as `devstack-corp-ro` with read-only access to the expected project;
the infrastructure scenario rejects any other cloud alias.

Test-created servers use an `openstack-perf-bdd-` name prefix for clear
ownership. Cleanup targets only the exact server created by the workflow; the
suite does not perform broad name-based cleanup. Automatically managed Neutron
ports are observed for deletion but are never manually deleted by the suite.

## OpenStack Configuration

Live workflows use an existing OpenStack connection. Cloud configuration and
credentials are supplied externally through the standard `clouds.yaml`
configuration supported by `openstacksdk`; credential values must never be
committed to this repository.

The application code does not hard-code a particular cloud, project, image,
flavor, network, or infrastructure environment. The live BDD scenarios read
only configuration names from environment variables; authentication details
remain in the external OpenStack configuration.

## Roadmap

Planned work, not current functionality, includes:

- Broader BDD scenarios for consumer-facing regression workflows.
- Product-oriented, read-only checks for supported applications.
- Repeated sampling and concurrency where they provide useful evidence.

## Design Principles

- Keep workflows portable across OpenStack environments.
- Keep configuration and credentials outside the repository.
- Treat deterministic, targeted cleanup as part of test correctness.
- Prefer useful regression measurements over synthetic benchmark claims.
- Evaluate the platform from the perspective of its consumers and products.
