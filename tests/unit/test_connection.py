from unittest.mock import MagicMock, patch
from openstack_perf.connection import create_connection


def test_create_connection():
    """Cloud name is passed to SDK and connection is returned unchanged."""
    mock_connection = MagicMock()
    
    with patch("openstack.connect", return_value=mock_connection) as mock_connect:
        result = create_connection("my-cloud")
        
        mock_connect.assert_called_once_with(cloud="my-cloud")
        assert result is mock_connection
