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

The central release question is: **Does the candidate release still work
correctly, and has its performance meaningfully regressed relative to an
approved baseline?**

## Engineering Choices

The implementation keeps its tooling proportional to that release-regression
goal:

- Python provides a readable automation layer and direct access to the
  OpenStack ecosystem. `openstacksdk` supplies established authentication,
  service discovery, and resource clients rather than duplicating OpenStack
  API behavior.
- `pytest` supports deterministic unit tests around external boundaries, while
  `pytest-bdd` expresses a small set of live scenarios in consumer-facing
  language without duplicating the underlying workflows.
- Python's standard-library HTTP facilities are sufficient for the bounded,
  GET-only product observations and avoid an unnecessary runtime dependency.
  Backend checks use the system SSH client so the operator's existing SSH
  configuration and host-key verification remain authoritative.
- TOML keeps non-secret runtime contracts human-reviewable. Schema-validated
  JSON artifacts provide portable, deterministic evidence that can be retained
  and compared without a database or dashboard.
- p50 represents typical successful timing and p95 exposes slower observations,
  subject to the deliberately bounded sample counts. Baseline/candidate
  comparison applies those measurements to a release decision rather than
  claiming universal platform performance.
- Explicit live authorization gates separate controlled infrastructure access
  from ordinary deterministic tests. The default test suite remains non-live.

Sustained load generation serves a different purpose, so tools such as Locust
or JMeter are outside the current scope rather than alternatives this suite
attempts to replace.

See [Controlled benchmark evidence](docs/benchmark-results.md) for the recorded
baseline/candidate experiments, the product-performance coverage gaps they
exposed, and the final generalized page-delivery result.

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
- Read-only product checks for the supported WordPress, static-site, nginx,
  Tomcat, and backend-listener contracts.
- A bounded page-delivery observation for the WordPress home page and its
  directly referenced same-origin stylesheets, scripts, and images.
- Immutable regression observations, deterministic p50/p95 statistics,
  schema-versioned JSON artifacts, and configurable baseline comparison.
- A TOML-configured regression runner and command-line interface that assemble
  complete baseline or candidate artifacts from the existing checks.
- Seven consumer-facing `pytest-bdd` scenarios that verify a consumer can:
  - Provision and remove a virtual machine.
  - Provision a workload with an address on the requested network.
  - Discover required services and a usable boot image.
  - Confirm configured critical infrastructure remains correctly attached.
  - Use the supported corporate web application paths.
  - Retrieve the application home page and its required resources.
  - Reach the application services across their public and backend tiers.
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

### Product Availability

The product checks issue bounded, read-only HTTP `GET` requests to explicitly
configured frontend and Tomcat base URLs. They validate the supported
WordPress pages and posts API, the five approved static-site paths, nginx
status output, and selected Tomcat pages. The expected WordPress release title
and application release are supplied externally rather than inferred from
page content. HTTP observations use one untimed warm-up followed by ten timed
requests, limit responses to 2 MiB, and reject cross-origin redirects.

The separate page-delivery observations cover the approved WordPress home page
and five static HTML surfaces. Each freezes a deterministic manifest of directly
referenced same-origin stylesheets, scripts, and images during its warm-up.
The WordPress target permits up to 32 direct resources; the static targets
permit up to 64. Each timed sample retrieves the primary HTML and its frozen
manifest sequentially, reporting the sum of their network/body-read durations.
This is a page-delivery proxy: it does not execute JavaScript, render the page,
or recursively crawl resources. Each response remains limited to 2 MiB and
each full delivery to 16 MiB.

The TOML target collection is authoritative for complete runner coverage. The
live BDD scenario remains a thin check of the same production mechanism through
the representative WordPress home page rather than maintaining a second target
list.

Backend availability is checked through the approved bastion using one
non-interactive system SSH invocation. The fixed remote check only opens and
closes TCP connections to the four documented backend listeners; it sends no
application payload, performs no authentication, and does not modify those
services. Host-key verification remains enabled through the user's existing
SSH configuration.

## Installation

The project requires Python 3.11 or newer. Create a virtual environment and
install the package with its development dependency:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Running Tests

Run the unit suite with:

```bash
.venv/bin/python -m pytest tests/unit -q
```

Run the complete default non-live suite with:

```bash
.venv/bin/python -m pytest -q
```

The default test configuration excludes tests marked `integration`, so this
command does not create resources in a live OpenStack environment. The unit
tests use mocks and do not require OpenStack credentials.

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

The product scenarios also require the explicit live opt-in. Their non-secret
inputs are supplied through `OPENSTACK_PERF_PRODUCT_BASE_URL`,
`OPENSTACK_PERF_TOMCAT_BASE_URL`,
`OPENSTACK_PERF_WORDPRESS_RELEASE_TITLE`,
`OPENSTACK_PERF_APPLICATION_RELEASE`, and
`OPENSTACK_PERF_PRODUCT_BASTION`. The backend scenario accepts only the
approved `wiki@172.24.4.20` bastion. Product checks are read-only: they use
HTTP `GET` and TCP connect-and-close operations only.

BDD-created servers use an `openstack-perf-bdd-` name prefix, while runner-created
servers use `openstack-perf-run-`, making suite ownership visible to operators.
Cleanup remains based on the exact ID of the server created by the workflow;
the suite does not perform broad name- or prefix-based cleanup. Automatically
managed Neutron ports are observed for deletion but are never manually deleted
by the suite.

## Running Release Regressions

Start from the checked-in non-secret example configuration:

```bash
cp config/regression.example.toml regression.toml
```

Review the resource names, expected product contract, release identity,
scenario selection, sampling, and comparison policies for the target
deployment. Credentials remain in external OpenStack and SSH configuration;
they do not belong in TOML.

Configuration can be validated without contacting any external system:

```bash
openstack-perf validate-config --config regression.toml
```

Establish a baseline against a known-good release:

```bash
OPENSTACK_PERF_RUN_LIVE=1 openstack-perf run \
  --live \
  --role baseline \
  --config regression.toml \
  --output-dir results/
```

Run a candidate and compare it with that immutable baseline:

```bash
OPENSTACK_PERF_RUN_LIVE=1 openstack-perf run \
  --live \
  --role candidate \
  --config regression.toml \
  --output-dir results/ \
  --baseline results/devstack-release-regression-baseline-....json
```

Existing artifacts can also be compared entirely offline:

```bash
openstack-perf compare \
  --config regression.toml \
  --baseline results/baseline.json \
  --candidate results/candidate.json
```

Every external runner operation requires both `--live` and
`OPENSTACK_PERF_RUN_LIVE=1`. Read-only observations execute first; the
network-verifying VM lifecycle executes last, sequentially, and stops after
its first failure. Artifacts use generated configuration/role/time/UUID names
and are create-new-only: an existing result artifact is never overwritten.
Candidate evidence is written before comparison and neither input artifact is
modified.

Comparison configuration explicitly marks observations as performance-gated
or functional-only. Functional failures, performance regressions, and missing
evidence remain distinct in the terminal summary. For each configured timing
metric, the permitted increase is the larger of the relative and absolute
allowances:

```text
allowed_delta = max(baseline × relative_tolerance, absolute_allowance)
```

A performance regression is reported only when the candidate increase exceeds
that allowed delta. The p50 value is the median, representing typical successful
timing, while p95 describes the slower end of the observed successful samples.
The strength of either percentile depends on sample count; a small VM sample
set is useful regression evidence but not a statistically strong tail-latency
estimate.

Process exit codes are:

- `0`: pass
- `1`: functional failure
- `2`: performance regression
- `3`: configuration/artifact/execution error or insufficient evidence

## OpenStack Configuration

Live workflows use an existing OpenStack connection. Cloud configuration and
credentials are supplied externally through the standard `clouds.yaml`
configuration supported by `openstacksdk`; credential values must never be
committed to this repository.

The application code does not hard-code a particular cloud, project, image,
flavor, network, or infrastructure environment. The live BDD scenarios read
only configuration names from environment variables; authentication details
remain in the external OpenStack configuration.

## Scope

The suite provides repeated regression sampling for its supported consumer
workflows. General-purpose load and stress testing, concurrency benchmarking,
monitoring, and automated remediation are outside its current scope.

## Design Principles

- Keep workflows portable across OpenStack environments.
- Keep configuration and credentials outside the repository.
- Treat deterministic, targeted cleanup as part of test correctness.
- Prefer useful regression measurements over synthetic benchmark claims.
- Evaluate the platform from the perspective of its consumers and products.
