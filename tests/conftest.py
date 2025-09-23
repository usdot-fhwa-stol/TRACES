import pytest
import sys
import os
from PyQt6.QtWidgets import QApplication

# Add the src directory to the Python path for imports
# This assumes conftest.py is in the test/ directory
test_dir = os.path.dirname(__file__)
src_dir = os.path.join(os.path.dirname(test_dir), 'src')
sys.path.insert(0, src_dir)

@pytest.fixture(scope="session")
def qapp():
    """Create a single QApplication instance for all tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Let pytest-qt handle cleanup

@pytest.fixture
def mock_utm():
    """Mock UTM library for testing."""
    import unittest.mock
    with unittest.mock.patch('utm.from_latlon') as mock_from:
        with unittest.mock.patch('utm.to_latlon') as mock_to:
            mock_from.return_value = (500000, 4000000, 10, 'S')
            mock_to.return_value = (36.1234, -115.5678)
            yield mock_from, mock_to