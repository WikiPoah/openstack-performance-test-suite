from openstack_perf.config import RuntimeConfig
from openstack_perf.results import EnvironmentFingerprint, ServiceVersion


SERVICE_TYPES = ("compute", "identity", "image", "network")


def collect_environment_fingerprint(
    config: RuntimeConfig,
    connection,
) -> EnvironmentFingerprint:
    """Combine explicit deployment identity with narrow SDK version data."""
    versions = []
    cloud_config = connection.config
    for service_type in SERVICE_TYPES:
        api_version = _optional_version(cloud_config.get_api_version, service_type)
        microversion = _optional_version(
            cloud_config.get_default_microversion, service_type
        )
        if api_version:
            versions.append(ServiceVersion(f"{service_type}.api", api_version))
        if microversion:
            versions.append(
                ServiceVersion(f"{service_type}.microversion", microversion)
            )
    consumer = config.consumer
    return EnvironmentFingerprint(
        cloud=consumer.cloud,
        region=consumer.region,
        platform_release=config.release.platform_release,
        source_branch=config.release.source_branch,
        application_release=config.release.application_release,
        service_versions=tuple(sorted(versions, key=lambda item: item.service)),
    )


def _optional_version(getter, service_type: str) -> str | None:
    try:
        value = getter(service_type)
    except Exception:
        return None
    if value is None or isinstance(value, bool) or not isinstance(
        value, (str, int, float)
    ):
        return None
    value = str(value).strip()
    return value or None
