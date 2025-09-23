import pytest
import os
import sys
from unittest.mock import patch, Mock
from src.utils import get_resource_path


class TestUtils:
    """Test suite for utils.py utility functions."""

    def setup_method(self):
        """Set up test fixtures."""
        # Store original sys attributes for restoration
        self.original_meipass = getattr(sys, '_MEIPASS', None)

    def teardown_method(self):
        """Clean up test fixtures."""
        # Restore original sys._MEIPASS
        if self.original_meipass is not None:
            sys._MEIPASS = self.original_meipass
        elif hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')

    def test_get_resource_path_normal_development(self):
        """Test get_resource_path in normal development environment."""
        # Ensure sys._MEIPASS doesn't exist (normal development)
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/current/working/directory'
            
            result = get_resource_path('config.json')
            
            mock_abspath.assert_called_once_with('.')
            expected_path = os.path.join('/current/working/directory', 'config.json')
            assert result == expected_path

    def test_get_resource_path_pyinstaller_frozen(self):
        """Test get_resource_path in PyInstaller frozen environment."""
        # Simulate PyInstaller frozen environment
        sys._MEIPASS = '/tmp/_MEI123456'
        
        result = get_resource_path('config.json')
        
        expected_path = os.path.join('/tmp/_MEI123456', 'config.json')
        assert result == expected_path

    def test_get_resource_path_with_subdirectory(self):
        """Test get_resource_path with subdirectory path."""
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/current/working/directory'
            
            result = get_resource_path('templates/config.json')
            
            expected_path = os.path.join('/current/working/directory', 'templates/config.json')
            assert result == expected_path

    def test_get_resource_path_with_nested_subdirectories(self):
        """Test get_resource_path with deeply nested path."""
        sys._MEIPASS = '/frozen/app/path'
        
        result = get_resource_path('resources/templates/styles/main.css')
        
        expected_path = os.path.join('/frozen/app/path', 'resources/templates/styles/main.css')
        assert result == expected_path

    def test_get_resource_path_empty_string(self):
        """Test get_resource_path with empty string."""
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/current/working/directory'
            
            result = get_resource_path('')
            
            expected_path = os.path.join('/current/working/directory', '')
            assert result == expected_path

    def test_get_resource_path_root_file(self):
        """Test get_resource_path with root-level file."""
        sys._MEIPASS = '/application/bundle'
        
        result = get_resource_path('main.exe')
        
        expected_path = os.path.join('/application/bundle', 'main.exe')
        assert result == expected_path

    @patch('os.path.abspath')
    def test_get_resource_path_abspath_called_with_dot(self, mock_abspath):
        """Test that os.path.abspath is called with '.' in development mode."""
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        mock_abspath.return_value = '/some/path'
        
        get_resource_path('test.txt')
        
        mock_abspath.assert_called_once_with('.')

    def test_get_resource_path_meipass_none(self):
        """Test get_resource_path when sys._MEIPASS exists but is None."""
        sys._MEIPASS = None
        
        # When _MEIPASS is None, getattr returns None (not the fallback)
        # This will cause os.path.join to fail with TypeError
        with pytest.raises(TypeError, match="expected str, bytes or os.PathLike object, not NoneType"):
            get_resource_path('file.txt')

    def test_get_resource_path_returns_string(self):
        """Test that get_resource_path always returns a string."""
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/test/path'
            
            result = get_resource_path('resource.txt')
            
            assert isinstance(result, str)

    def test_get_resource_path_type_annotation(self):
        """Test that function has correct type annotations."""
        import inspect
        from src.utils import get_resource_path
        
        sig = inspect.signature(get_resource_path)
        
        # Check parameter type annotation
        assert sig.parameters['relative_path'].annotation == str
        
        # Check return type annotation
        assert sig.return_annotation == str

    def test_get_resource_path_with_windows_paths(self):
        """Test get_resource_path with Windows-style paths."""
        sys._MEIPASS = 'C:\\Program Files\\MyApp'
        
        result = get_resource_path('resources\\config.ini')
        
        expected_path = os.path.join('C:\\Program Files\\MyApp', 'resources\\config.ini')
        assert result == expected_path

    def test_get_resource_path_with_forward_slashes(self):
        """Test get_resource_path with forward slashes (Unix-style)."""
        sys._MEIPASS = '/opt/myapp'
        
        result = get_resource_path('resources/data/file.json')
        
        expected_path = os.path.join('/opt/myapp', 'resources/data/file.json')
        assert result == expected_path

    def test_get_resource_path_with_special_characters(self):
        """Test get_resource_path with special characters in path."""
        sys._MEIPASS = '/app with spaces/special-chars_123'
        
        result = get_resource_path('file with spaces & symbols.txt')
        
        expected_path = os.path.join('/app with spaces/special-chars_123', 'file with spaces & symbols.txt')
        assert result == expected_path

    def test_get_resource_path_preserves_relative_path_structure(self):
        """Test that relative path structure is preserved in the result."""
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/base'
            
            test_cases = [
                'simple.txt',
                'folder/file.txt',
                'deep/nested/structure/file.dat',
                '../parent/file.txt',
                './current/file.txt'
            ]
            
            for test_path in test_cases:
                result = get_resource_path(test_path)
                expected = os.path.join('/base', test_path)
                assert result == expected

    def test_get_resource_path_getattr_behavior(self):
        """Test the getattr behavior for sys._MEIPASS."""
        # Test when _MEIPASS doesn't exist
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/fallback/path'
            
            # The getattr should return the fallback (abspath result)
            result = get_resource_path('test.txt')
            
            mock_abspath.assert_called_once_with('.')
            expected = os.path.join('/fallback/path', 'test.txt')
            assert result == expected

    def test_get_resource_path_docstring_exists(self):
        """Test that the function has appropriate documentation."""
        from src.utils import get_resource_path
        
        assert get_resource_path.__doc__ is not None
        assert 'PyInstaller' in get_resource_path.__doc__
        assert 'frozen' in get_resource_path.__doc__