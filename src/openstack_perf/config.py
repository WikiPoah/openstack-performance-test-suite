from dataclasses import dataclass
import ipaddress
import math
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

from openstack_perf.comparison import (
    ComparisonPolicy,
    Metric,
    MetricTolerance,
)
from openstack_perf.results import CleanSnapshotStatus


CONFIG_SCHEMA_VERSION = 1
MAX_CONFIGURATION_SLUG_LENGTH = 96
COMPARISON_MODES = {"performance", "functional_only"}
SECRET_KEY_PARTS = {
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
}


class ConfigurationError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True)
class ReleaseConfig:
    platform_release: str
    source_branch: str
    application_release: str
    clean_snapshot: CleanSnapshotStatus


@dataclass(frozen=True)
class ConsumerConfig:
    cloud: str
    project: str
    region: str
    image: str
    flavor: str
    network: str


@dataclass(frozen=True)
class CorpConfig:
    cloud: str
    project: str
    server: str
    network: str
    fixed_ip: str


@dataclass(frozen=True)
class BackendConfig:
    target_id: str
    name: str
    host: str
    port: int


@dataclass(frozen=True)
class ProductConfig:
    base_url: str
    http_timeout_seconds: float
    maximum_body_bytes: int
    tomcat_base_url: str | None = None
    expected_release_title: str | None = None
    bastion: str | None = None
    backends: tuple[BackendConfig, ...] = ()


@dataclass(frozen=True)
class ScenarioConfig:
    enabled: bool
    comparison_mode: str
    samples: int | None = None


@dataclass(frozen=True)
class VmScenarioConfig:
    enabled: bool
    comparison_mode: str
    samples: int
    verify_network_attachment: bool
    provisioning_timeout_seconds: float
    cleanup_timeout_seconds: float


@dataclass(frozen=True)
class ScenarioSet:
    service_discovery: ScenarioConfig
    boot_image: ScenarioConfig
    infrastructure_state: ScenarioConfig
    web_application: ScenarioConfig
    application_services: ScenarioConfig
    vm_lifecycle: VmScenarioConfig


@dataclass(frozen=True)
class RuntimeConfig:
    name: str
    release: ReleaseConfig
    consumer: ConsumerConfig | None
    corp: CorpConfig | None
    product: ProductConfig | None
    scenarios: ScenarioSet
    comparison_policies: tuple[ComparisonPolicy, ...]
    functional_only_keys: tuple[tuple[str, str], ...]
    schema_version: int = CONFIG_SCHEMA_VERSION


def load_config(path: str | Path) -> RuntimeConfig:
    """Load and strictly validate a non-secret TOML configuration."""
    try:
        with Path(path).open("rb") as config_file:
            document = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"unable to load configuration: {exc}") from None
    try:
        return _parse_config(document)
    except ConfigurationError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(str(exc)) from None


def _parse_config(document) -> RuntimeConfig:
    _reject_secret_keys(document)
    root = _table(
        document,
        "configuration",
        {"schema_version", "name", "release", "consumer", "corp", "product", "scenarios", "comparison"},
    )
    schema_version = _integer(root, "schema_version")
    if schema_version != CONFIG_SCHEMA_VERSION:
        raise ConfigurationError(
            f"unsupported configuration schema version: {schema_version}"
        )
    name = _nonempty(root, "name")
    artifact_name_slug(name)
    release = _parse_release(_required_table(root, "release"))
    scenarios = _parse_scenarios(_required_table(root, "scenarios"))
    if not any(
        scenario.enabled
        for scenario in (
            scenarios.service_discovery,
            scenarios.boot_image,
            scenarios.infrastructure_state,
            scenarios.web_application,
            scenarios.application_services,
            scenarios.vm_lifecycle,
        )
    ):
        raise ConfigurationError("at least one scenario must be enabled")

    needs_corp = scenarios.infrastructure_state.enabled
    needs_product = (
        scenarios.web_application.enabled
        or scenarios.application_services.enabled
    )
    consumer = _optional_section(root, "consumer", _parse_consumer, True)
    corp = _optional_section(root, "corp", _parse_corp, needs_corp)
    product = _optional_section(
        root,
        "product",
        lambda data: _parse_product(
            data,
            needs_web=scenarios.web_application.enabled,
            needs_services=scenarios.application_services.enabled,
        ),
        needs_product,
    )
    if needs_corp and corp.cloud != "devstack-corp-ro":
        raise ConfigurationError(
            "infrastructure state requires the approved read-only corp cloud"
        )
    if scenarios.application_services.enabled and (
        product.bastion != "wiki@172.24.4.20"
    ):
        raise ConfigurationError(
            "application services require the approved bastion"
        )
    policies = _parse_comparison(root.get("comparison", {}))

    provisional = RuntimeConfig(
        name=name,
        release=release,
        consumer=consumer,
        corp=corp,
        product=product,
        scenarios=scenarios,
        comparison_policies=policies,
        functional_only_keys=(),
    )
    modes = _expected_comparison_modes(provisional)
    policy_keys = {
        (policy.scenario_id, policy.target_id) for policy in policies
    }
    expected_keys = set(modes)
    unexpected = policy_keys - expected_keys
    if unexpected:
        raise ConfigurationError(
            "comparison policy targets a disabled or unknown observation: "
            + _format_keys(unexpected)
        )
    functional_only = tuple(
        sorted(key for key, mode in modes.items() if mode == "functional_only")
    )
    performance = {key for key, mode in modes.items() if mode == "performance"}
    missing = performance - policy_keys
    if missing:
        raise ConfigurationError(
            "performance observations require comparison policies: "
            + _format_keys(missing)
        )
    overlap = set(functional_only) & policy_keys
    if overlap:
        raise ConfigurationError(
            "functional-only observations cannot have performance policies: "
            + _format_keys(overlap)
        )
    sample_limits = _expected_sample_limits(provisional)
    impossible = {
        (policy.scenario_id, policy.target_id)
        for policy in policies
        if (policy.scenario_id, policy.target_id) in sample_limits
        and policy.minimum_sample_count
        > sample_limits[(policy.scenario_id, policy.target_id)]
    }
    if impossible:
        raise ConfigurationError(
            "comparison minimum exceeds configured sample count: "
            + _format_keys(impossible)
        )
    return RuntimeConfig(
        name=name,
        release=release,
        consumer=consumer,
        corp=corp,
        product=product,
        scenarios=scenarios,
        comparison_policies=policies,
        functional_only_keys=functional_only,
    )


def _parse_release(data):
    data = _table(
        data,
        "release",
        {"platform_release", "source_branch", "application_release", "clean_snapshot"},
    )
    try:
        clean_snapshot = CleanSnapshotStatus(_nonempty(data, "clean_snapshot"))
    except ValueError:
        raise ConfigurationError(
            "release.clean_snapshot must be clean, not_clean, or unknown"
        ) from None
    return ReleaseConfig(
        _nonempty(data, "platform_release"),
        _nonempty(data, "source_branch"),
        _nonempty(data, "application_release"),
        clean_snapshot,
    )


def _parse_consumer(data):
    data = _table(
        data,
        "consumer",
        {"cloud", "project", "region", "image", "flavor", "network"},
    )
    return ConsumerConfig(
        *(
            _nonempty(data, field)
            for field in ("cloud", "project", "region", "image", "flavor", "network")
        )
    )


def _parse_corp(data):
    data = _table(
        data,
        "corp",
        {"cloud", "project", "server", "network", "fixed_ip"},
    )
    fixed_ip = _nonempty(data, "fixed_ip")
    ipaddress.ip_address(fixed_ip)
    return CorpConfig(
        _nonempty(data, "cloud"),
        _nonempty(data, "project"),
        _nonempty(data, "server"),
        _nonempty(data, "network"),
        fixed_ip,
    )


def _parse_product(data, *, needs_web, needs_services):
    data = _table(
        data,
        "product",
        {"base_url", "tomcat_base_url", "expected_release_title", "bastion", "http_timeout_seconds", "maximum_body_bytes", "backends"},
    )
    base_url = _safe_url(_nonempty(data, "base_url"), "product.base_url")
    tomcat_url = _optional_url(data, "tomcat_base_url")
    if needs_services and tomcat_url is None:
        raise ConfigurationError("tomcat_base_url must be a non-empty string")
    timeout = _number(data, "http_timeout_seconds")
    if timeout <= 0 or timeout > 10:
        raise ConfigurationError(
            "product.http_timeout_seconds must be greater than zero and at most 10"
        )
    body_limit = _integer(data, "maximum_body_bytes")
    if body_limit < 1 or body_limit > 2 * 1024 * 1024:
        raise ConfigurationError(
            "product.maximum_body_bytes must be between 1 and 2097152"
        )
    backends_data = data.get("backends", [])
    if not isinstance(backends_data, list):
        raise ConfigurationError("product.backends must be an array of tables")
    backends = tuple(_parse_backend(item) for item in backends_data)
    if needs_services and not backends:
        raise ConfigurationError("product.backends must not be empty")
    ids = [backend.target_id for backend in backends]
    if len(ids) != len(set(ids)):
        raise ConfigurationError("product backend target IDs must be unique")
    expected_title = _optional_nonempty(data, "expected_release_title")
    if needs_web and expected_title is None:
        raise ConfigurationError(
            "expected_release_title must be a non-empty string"
        )
    bastion = _optional_nonempty(data, "bastion")
    if needs_services and bastion is None:
        raise ConfigurationError("bastion must be a non-empty string")
    return ProductConfig(
        base_url,
        timeout,
        body_limit,
        tomcat_url,
        expected_title,
        bastion,
        backends,
    )


def _parse_backend(data):
    data = _table(data, "product backend", {"target_id", "name", "host", "port"})
    target_id = _nonempty(data, "target_id")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]*", target_id):
        raise ConfigurationError("backend target_id must be a stable lowercase ID")
    host = _nonempty(data, "host")
    ipaddress.ip_address(host)
    port = _integer(data, "port")
    if not 1 <= port <= 65535:
        raise ConfigurationError("backend port must be between 1 and 65535")
    return BackendConfig(target_id, _nonempty(data, "name"), host, port)


def _parse_scenarios(data):
    names = {
        "service_discovery",
        "boot_image",
        "infrastructure_state",
        "web_application",
        "application_services",
        "vm_lifecycle",
    }
    data = _table(data, "scenarios", names)
    for name in names:
        if name not in data:
            raise ConfigurationError(f"missing required section: scenarios.{name}")
    return ScenarioSet(
        service_discovery=_parse_sampled_scenario(data["service_discovery"], "service_discovery"),
        boot_image=_parse_sampled_scenario(data["boot_image"], "boot_image"),
        infrastructure_state=_parse_basic_scenario(data["infrastructure_state"], "infrastructure_state"),
        web_application=_parse_sampled_scenario(data["web_application"], "web_application"),
        application_services=_parse_sampled_scenario(data["application_services"], "application_services"),
        vm_lifecycle=_parse_vm_scenario(data["vm_lifecycle"]),
    )


def _parse_basic_scenario(data, name):
    data = _table(data, f"scenarios.{name}", {"enabled", "comparison_mode"})
    return ScenarioConfig(_boolean(data, "enabled"), _comparison_mode(data))


def _parse_sampled_scenario(data, name):
    data = _table(
        data, f"scenarios.{name}", {"enabled", "comparison_mode", "samples"}
    )
    samples = _integer(data, "samples")
    if samples < 1:
        raise ConfigurationError(f"scenarios.{name}.samples must be at least 1")
    return ScenarioConfig(
        _boolean(data, "enabled"), _comparison_mode(data), samples
    )


def _parse_vm_scenario(data):
    data = _table(
        data,
        "scenarios.vm_lifecycle",
        {"enabled", "comparison_mode", "samples", "verify_network_attachment", "provisioning_timeout_seconds", "cleanup_timeout_seconds"},
    )
    samples = _integer(data, "samples")
    if samples < 1:
        raise ConfigurationError("scenarios.vm_lifecycle.samples must be at least 1")
    provisioning = _number(data, "provisioning_timeout_seconds")
    cleanup = _number(data, "cleanup_timeout_seconds")
    if provisioning <= 0 or cleanup <= 0:
        raise ConfigurationError("VM timeouts must be greater than zero")
    return VmScenarioConfig(
        _boolean(data, "enabled"),
        _comparison_mode(data),
        samples,
        _boolean(data, "verify_network_attachment"),
        provisioning,
        cleanup,
    )


def _parse_comparison(data):
    data = _table(data, "comparison", {"policies"})
    policies_data = data.get("policies", [])
    if not isinstance(policies_data, list):
        raise ConfigurationError("comparison.policies must be an array of tables")
    policies = tuple(_parse_policy(item) for item in policies_data)
    keys = [(policy.scenario_id, policy.target_id) for policy in policies]
    if len(keys) != len(set(keys)):
        raise ConfigurationError("comparison policy keys must be unique")
    return policies


def _parse_policy(data):
    allowed = {
        "scenario_id", "target_id", "minimum_sample_count",
        "p50_relative", "p50_absolute_seconds",
        "p95_relative", "p95_absolute_seconds",
    }
    data = _table(data, "comparison policy", allowed)
    tolerances = []
    for metric, prefix in ((Metric.P50, "p50"), (Metric.P95, "p95")):
        relative = _optional_number(data, f"{prefix}_relative")
        absolute = _optional_number(data, f"{prefix}_absolute_seconds")
        if relative is not None or absolute is not None:
            tolerances.append(MetricTolerance(metric, relative, absolute))
    if not tolerances:
        raise ConfigurationError("comparison policy must configure p50 or p95")
    return ComparisonPolicy(
        _nonempty(data, "scenario_id"),
        _nonempty(data, "target_id"),
        _integer(data, "minimum_sample_count"),
        tuple(tolerances),
    )


def _expected_comparison_modes(config):
    modes = {}

    def add(keys, scenario):
        if scenario.enabled:
            modes.update((key, scenario.comparison_mode) for key in keys)

    consumer = config.consumer
    product = config.product
    corp = config.corp
    if consumer:
        add((("identity.service_discovery", consumer.project),), config.scenarios.service_discovery)
        add((("image.boot_discovery", consumer.image),), config.scenarios.boot_image)
        vm_scenario_id = (
            "vm.network_attachment_lifecycle"
            if config.scenarios.vm_lifecycle.verify_network_attachment
            else "vm.lifecycle"
        )
        add(((vm_scenario_id, consumer.network),), config.scenarios.vm_lifecycle)
    if corp:
        add((("infrastructure.server_attachment", corp.server),), config.scenarios.infrastructure_state)
    if product:
        add(
            tuple(("product.wordpress", target) for target in (
                "wordpress.home", "wordpress.search.release", "wordpress.rest.posts", "wordpress.login"
            ))
            + tuple(("product.static_site", target) for target in (
                "static.home", "static.about", "static.products", "static.team", "static.contact"
            )),
            config.scenarios.web_application,
        )
        add(
            tuple(("product.service_http", target) for target in (
                "nginx.status", "tomcat.home", "tomcat.examples", "tomcat.hello_world"
            ))
            + tuple(("product.backend_reachability", item.target_id) for item in product.backends),
            config.scenarios.application_services,
        )
    return modes


def _expected_sample_limits(config):
    limits = {}

    def add(keys, count):
        limits.update((key, count) for key in keys)

    consumer = config.consumer
    product = config.product
    if config.scenarios.service_discovery.enabled:
        add(
            (("identity.service_discovery", consumer.project),),
            config.scenarios.service_discovery.samples,
        )
    if config.scenarios.boot_image.enabled:
        add(
            (("image.boot_discovery", consumer.image),),
            config.scenarios.boot_image.samples,
        )
    if config.scenarios.vm_lifecycle.enabled:
        scenario_id = (
            "vm.network_attachment_lifecycle"
            if config.scenarios.vm_lifecycle.verify_network_attachment
            else "vm.lifecycle"
        )
        add(
            ((scenario_id, consumer.network),),
            config.scenarios.vm_lifecycle.samples,
        )
    if product and config.scenarios.web_application.enabled:
        web_targets = (
            *(('product.wordpress', target) for target in (
                "wordpress.home",
                "wordpress.search.release",
                "wordpress.rest.posts",
                "wordpress.login",
            )),
            *(('product.static_site', target) for target in (
                "static.home",
                "static.about",
                "static.products",
                "static.team",
                "static.contact",
            )),
        )
        add(web_targets, config.scenarios.web_application.samples)
    if product and config.scenarios.application_services.enabled:
        add(
            tuple(
                ("product.service_http", target)
                for target in (
                    "nginx.status",
                    "tomcat.home",
                    "tomcat.examples",
                    "tomcat.hello_world",
                )
            ),
            config.scenarios.application_services.samples,
        )
        add(
            tuple(
                ("product.backend_reachability", item.target_id)
                for item in product.backends
            ),
            1,
        )
    return limits


def _optional_section(root, name, parser, required):
    if name not in root:
        if required:
            raise ConfigurationError(f"missing required section: {name}")
        return None
    return parser(root[name])


def _required_table(data, key):
    if key not in data:
        raise ConfigurationError(f"missing required section: {key}")
    return data[key]


def _table(value, label, allowed):
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be a table")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ConfigurationError(
            f"unknown field in {label}: {sorted(unknown)[0]}"
        )
    return value


def _nonempty(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value


def _optional_nonempty(data, key):
    if key not in data:
        return None
    return _nonempty(data, key)


def _integer(data, key):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{key} must be an integer")
    return value


def _number(data, key):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{key} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ConfigurationError(f"{key} must be finite")
    return value


def _optional_number(data, key):
    if key not in data:
        return None
    return _number(data, key)


def _boolean(data, key):
    value = data.get(key)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{key} must be boolean")
    return value


def _comparison_mode(data):
    mode = _nonempty(data, "comparison_mode")
    if mode not in COMPARISON_MODES:
        raise ConfigurationError(
            "comparison_mode must be performance or functional_only"
        )
    return mode


def _safe_url(value, label):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(f"{label} must be an HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ConfigurationError(f"{label} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigurationError(f"{label} must not contain query or fragment")
    return value


def _optional_url(data, key):
    if key not in data:
        return None
    return _safe_url(_nonempty(data, key), f"product.{key}")


def artifact_name_slug(configuration_name):
    slug = re.sub(r"[^a-z0-9]+", "-", configuration_name.lower()).strip("-")
    if not slug:
        raise ConfigurationError(
            "configuration name must contain an ASCII letter or digit"
        )
    if len(slug) > MAX_CONFIGURATION_SLUG_LENGTH:
        raise ConfigurationError(
            "configuration name slug must be at most "
            f"{MAX_CONFIGURATION_SLUG_LENGTH} characters"
        )
    return slug


def _reject_secret_keys(value, path="configuration"):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SECRET_KEY_PARTS):
                raise ConfigurationError(
                    f"secret-bearing configuration field is forbidden: {path}.{key}"
                )
            _reject_secret_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


def _format_keys(keys):
    return ", ".join(f"{scenario}/{target}" for scenario, target in sorted(keys))
