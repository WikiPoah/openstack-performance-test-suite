# Controlled Benchmark Evidence

This document records review-safe evidence from a controlled live execution of
the OpenStack release-regression suite. It demonstrates this completed proof
sequence:

```text
controlled original environment
-> recorded baseline
-> preserved immutable evidence
-> changed environment
-> candidate run
-> automatic baseline/candidate comparison
-> documented regression or failure detection
```

The known-good baseline passed. After the environment owner deliberately
changed the test environment, the same released v0.3.0 suite and configuration
contract rejected the candidate with `FUNCTIONAL_FAILURE` after detecting four
consumer-facing regressions. The first candidate run did not classify every
seeded regression: a separately known consumer-visible performance regression
escaped the suite's original performance coverage. This revealed a measurement
and policy-coverage gap rather than a comparator defect.

## Baseline Run

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Configuration | `devstack-release-regression` |
| Role | `BASELINE` |
| Overall result | **PASS** |
| Observation count | 21 |

All 21 observations passed functionally. The run established approved evidence
for later comparison with a candidate release; it was not intended to establish
universal platform performance.

### Performance-evaluated observations

These three observations were configured for performance comparison:

| Scenario | Target | Successful samples | p50 | p95 | Verdict |
|---|---|---:|---:|---:|---|
| `identity.service_discovery` | `perf` | 10 | 0.076780 s | 0.089562 s | PASS |
| `image.boot_discovery` | `cirros-0.6.3-x86_64-disk` | 10 | 0.071509 s | 0.113055 s | PASS |
| `vm.network_attachment_lifecycle` | `perf-net` | 3 | 13.582803 s | 15.517354 s | PASS |

### Functional-only coverage

The remaining 18 observations were functional-only under the approved
comparison configuration:

- Infrastructure: `infrastructure.server_attachment / corp-db`.
- WordPress: `wordpress.home`, `wordpress.search.release`,
  `wordpress.rest.posts`, and `wordpress.login`.
- Static site: `static.home`, `static.about`, `static.products`, `static.team`,
  and `static.contact`.
- Service HTTP: `nginx.status`, `tomcat.home`, `tomcat.examples`, and
  `tomcat.hello_world`.
- Backend reachability: `backend.mariadb`, `backend.apache`, `backend.tomcat`,
  and `backend.nginx`.

All passed. Some functional-only observations contain timing data in the raw
evidence, but the approved comparison configuration does not interpret those
timings as performance thresholds.

## VM Lifecycle and Cleanup Evidence

- Configured VM samples: 3.
- Actual VM samples: 3.
- Samples executed sequentially.
- Network attachment verification was enabled and passed.
- Workflow-owned cleanup was confirmed for all three executions.
- An independent read-only check found 0 remaining suite-owned servers.
- No explicit Neutron port deletion was required.

Cleanup is part of the observed workflow result because a regression suite must
not leave its benchmark resources behind after successful lifecycle execution.
These statements describe this controlled run and do not claim a broader
environment guarantee.

## Artifact Integrity

The controlled runs generated these artifacts:

| Role | Run ID | Artifact basename | SHA-256 |
|---|---|---|---|
| Baseline | `2026b4a7-92fd-4f32-9ef3-2dcfae5833c1` | `devstack-release-regression-baseline-20260904T152530Z-2026b4a7-92fd-4f32-9ef3-2dcfae5833c1.json` | `87fd2a06e19a72fa9a731e882499c494b80d70ed63c6d59b05fc549fc753e255` |
| Candidate | `f83fe24c-1cd4-46e7-b2d6-3865038a311d` | `devstack-release-regression-candidate-20260904T162152Z-f83fe24c-1cd4-46e7-b2d6-3865038a311d.json` | `5752ba77b97f040055c2ff9c3878eda7dfd0b4d535a046009cae217608cb4892` |

The baseline artifact was preserved after its run, and its preserved copy was
verified byte-for-byte against the original using its digest. Serialization and
deserialization round-trip validation passed, stored statistics agreed with
the raw samples, and the baseline contained no failed samples. It also passed
the controlled validation's sensitive-data review.

Both raw artifacts remain outside the public Git repository. This repository
records summarized, review-safe evidence instead of publishing
environment-specific raw results. This documentation task did not rerun or
independently reverify the live evidence.

## Interpretation and Limitations

p50 is the median successful timing and represents the typical observation.
p95 represents the slower observed tail. Both statistics are calculated from
successful samples only.

Ten samples provide useful bounded evidence for comparison with a controlled
candidate run, but they do not establish strong production tail-latency
guarantees. The VM observation contains only three samples, so its p95 requires
particular caution. This benchmark demonstrates controlled release-regression
behavior; it is not a production capacity benchmark, sustained load test, or
statistically rigorous characterization of production-scale latency.

## Candidate Comparison

The environment owner deliberately changed the test environment after the
baseline was preserved. One controlled candidate run then used the same
released v0.3.0 suite and `devstack-release-regression` configuration contract.

### Performance comparison

All three performance-evaluated observations remained functionally valid and
received performance verdict `PASS`.

| Scenario / target | Baseline p50 | Candidate p50 | Δ p50 | Baseline p95 | Candidate p95 | Δ p95 | Functional | Performance |
|---|---:|---:|---:|---:|---:|---:|---|---|
| `identity.service_discovery / perf` | 0.076780 s | 0.081 s | +0.004 s (+5.3%) | 0.089562 s | 0.088 s | -0.001 s (-1.5%) | PASS | PASS |
| `image.boot_discovery / cirros-0.6.3-x86_64-disk` | 0.071509 s | 0.075 s | +0.003 s (+4.4%) | 0.113055 s | 0.162 s | +0.049 s (+43.2%) | PASS | PASS |
| `vm.network_attachment_lifecycle / perf-net` | 13.582803 s | 13.556 s | -0.027 s (-0.2%) | 15.517354 s | 13.732 s | -1.785 s (-11.5%) | PASS | PASS |

The reported 43.2% image-discovery p95 increase did not by itself constitute a
performance regression. The configured policy uses the larger of its relative
and absolute allowances, and the suite reported `PASS`; no alternative verdict
has been recalculated for this document. The three-sample VM comparison remains
bounded release-regression evidence rather than a statistically strong
production tail-latency estimate.

### Functional regressions detected

The unchanged suite detected exactly four candidate functional failures. Each
target stopped after its failed warm-up, so no misleading timed samples were
collected from a functionally invalid endpoint.

| Scenario / target | Timed samples | Candidate verdict | Failure |
|---|---:|---|---|
| `product.static_site / static.products` | 0 | FUNCTIONAL_FAILURE | `static.products warm-up failed: expected HTTP status 200` |
| `product.service_http / tomcat.home` | 0 | FUNCTIONAL_FAILURE | `tomcat.home warm-up failed: expected HTTP status 200` |
| `product.service_http / tomcat.examples` | 0 | FUNCTIONAL_FAILURE | `tomcat.examples warm-up failed: expected HTTP status 200` |
| `product.service_http / tomcat.hello_world` | 0 | FUNCTIONAL_FAILURE | `tomcat.hello_world warm-up failed: expected HTTP status 200` |

These are regressions in the changed candidate environment, not failures of the
test suite. The suite identified the affected consumer surfaces but did not
diagnose their root cause.

### Unaffected functional coverage

All other functional-only observations passed:

- Infrastructure: `infrastructure.server_attachment / corp-db`.
- WordPress: `wordpress.home`, `wordpress.search.release`,
  `wordpress.rest.posts`, and `wordpress.login`.
- Static site: `static.home`, `static.about`, `static.team`, and
  `static.contact`.
- Service HTTP: `nginx.status`.
- Backend reachability: `backend.mariadb`, `backend.apache`, `backend.tomcat`,
  and `backend.nginx`.

The backend Tomcat listener remained reachable even though the public Tomcat
HTTP endpoints failed. This establishes that listener reachability and
consumer-facing HTTP behavior differed during the run, without implying a root
cause.

### Candidate VM and cleanup evidence

- Configured VM samples: 3.
- Actual VM samples: 3.
- Samples executed sequentially, and all three passed.
- Network attachment verification passed.
- Workflow-owned cleanup was confirmed for every completed execution.
- No explicit Neutron port deletion occurred.

The product functional failures did not cause a VM lifecycle or cleanup
failure.

### Overall decision

- Functional verdict: **FUNCTIONAL_FAILURE**
- Performance verdict: **PASS**
- Overall candidate verdict: **FUNCTIONAL_FAILURE**
- Overall comparison verdict: **FUNCTIONAL_FAILURE**
- Detected regressions: one static-site and three Tomcat HTTP failures
- Candidate VM cleanup result: **PASS**

Four required consumer-facing endpoints failed functional assertions, and
functional failure dominates the release decision. The performance-evaluated
observations remained within their configured tolerances. The candidate was
therefore rejected for functional regression, not performance regression.

In summary, the known-good baseline passed and its evidence was preserved and
checksummed. After the environment was changed, the same released suite and
configuration detected four regressions while unaffected functionality and
performance-gated workflows continued to pass. This is the intended
baseline-to-candidate release-regression decision rather than merely a
collection of independent checks. However, the reported performance `PASS`
describes only the three observations that were performance-gated at the time;
it is not evidence that the changed release contained no other performance
regression.

## Performance-coverage follow-up

The first candidate evidence remains part of the assessment record because it
demonstrated both successful functional detection and a concrete limitation in
the original measurement surface. The suite is being extended with a bounded
`product.page_delivery / wordpress.home` observation that measures the primary
HTML response together with directly referenced same-origin stylesheets,
scripts, and images.

Controlled proof of that improvement is still pending. The required sequence
is:

1. Complete non-live implementation review.
2. Restore the known-good Release 1.0 environment.
3. Capture and preserve a new baseline containing the page-delivery observation.
4. Redeploy Release 2.0.
5. Run one candidate with the exact same improved suite and configuration.
6. Record the automatic comparison and whether it detects the previously
   missed performance regression.

No timing from the original baseline will be used as evidence for an
observation that it did not contain.
