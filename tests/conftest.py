import pytest
import sys
import os

# Add the source directory to the Python path
# This assumes the test files are in a 'tests' directory and the source files are in the parent directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

@pytest.fixture
def mock_utm():
    """Mock UTM library for testing."""
    import unittest.mock
    with unittest.mock.patch('utm.from_latlon') as mock_from:
        with unittest.mock.patch('utm.to_latlon') as mock_to:
            mock_from.return_value = (500000, 4000000, 10, 'S')
            mock_to.return_value = (36.1234, -115.5678)
            yield mock_from, mock_to
