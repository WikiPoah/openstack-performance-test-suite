import openstack


def create_connection(cloud_name: str) -> openstack.connection.Connection:
    """
    Create an OpenStack SDK connection for a named cloud configuration.

    The cloud name is resolved by openstacksdk from clouds.yaml, environment
    variables, or other registered credential sources. Configuration,
    authentication, and credential resolution are delegated entirely to the SDK.

    Args:
        cloud_name: Name of the cloud in clouds.yaml (e.g., "pf9-prod").

    Returns:
        An openstacksdk Connection configured for the named cloud.

    Raises:
        Propagates openstacksdk exceptions if the cloud is not found,
        authentication fails, or connection creation fails. Callers receive
        actionable errors from the SDK rather than generic wrappers.
    """
    return openstack.connect(cloud=cloud_name)
