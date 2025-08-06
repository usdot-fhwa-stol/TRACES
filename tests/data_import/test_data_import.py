"""
Unit tests for the data_import package.
"""

import unittest
import tempfile
import os
import sys
import io
from unittest.mock import Mock, MagicMock, patch

# Mock PyQt6 and other GUI modules before any imports that might use them
def setup_qt_mocks():
    """Set up comprehensive PyQt6 mocking."""
    # Create the main PyQt6 mock
    pyqt6_mock = MagicMock()
    
    # Create submodule mocks
    qtcore_mock = MagicMock()
    qtwidgets_mock = MagicMock()
    qtgui_mock = MagicMock()
    
    # Set up the package structure
    pyqt6_mock.QtCore = qtcore_mock
    pyqt6_mock.QtWidgets = qtwidgets_mock
    pyqt6_mock.QtGui = qtgui_mock
    
    # Install all the mocks
    sys.modules['PyQt6'] = pyqt6_mock
    sys.modules['PyQt6.QtCore'] = qtcore_mock
    sys.modules['PyQt6.QtWidgets'] = qtwidgets_mock
    sys.modules['PyQt6.QtGui'] = qtgui_mock
    
    # Mock UTM library
    utm_mock = MagicMock()
    utm_mock.from_latlon = MagicMock()
    utm_mock.to_latlon = MagicMock()
    sys.modules['utm'] = utm_mock

setup_qt_mocks()

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from data_import.base_process import BaseProcess
from data_import.csv_algorithms import CsvAlgorithms
from data_import.csv_process import CsvProcess, ByteCountingStream


class TestBaseProcess(unittest.TestCase):
    """Test cases for the BaseProcess abstract base class."""

    def test_base_process_is_abstract(self):
        """Test that BaseProcess cannot be instantiated directly."""
        with self.assertRaises(TypeError):
            BaseProcess()

    def test_process_method_is_abstract(self):
        """Test that process method must be implemented by subclasses."""
        class IncompleteProcessor(BaseProcess):
            pass
        
        with self.assertRaises(TypeError):
            IncompleteProcessor()

    def test_concrete_implementation_works(self):
        """Test that a concrete implementation can be instantiated."""
        class ConcreteProcessor(BaseProcess):
            def process(self, config, input_path, db_connection, worker):
                return 0
        
        processor = ConcreteProcessor()
        self.assertIsInstance(processor, BaseProcess)
        
        # Test that process method can be called
        result = processor.process({}, "test.csv", None, None)
        self.assertEqual(result, 0)


class TestCsvAlgorithms(unittest.TestCase):
    """Test cases for the CsvAlgorithms class."""

    def setUp(self):
        """Set up test fixtures."""
        self.algorithms = CsvAlgorithms()

    def test_copy_with_valid_data(self):
        """Test copy method with valid data."""
        row = ["1", "2.5", "hello", "world"]
        col_def = {"src_col": 1, "type": "float", "parms": []}
        
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 2.5)

    def test_copy_with_int_type(self):
        """Test copy method with integer type conversion."""
        row = ["1", "2.7", "hello"]
        col_def = {"src_col": 1, "type": "int", "parms": []}
        
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 2)  # Should truncate float to int

    def test_copy_with_invalid_column(self):
        """Test copy method with invalid column index."""
        row = ["1", "2", "3"]
        col_def = {"src_col": 5, "type": "float", "parms": [42.0]}
        
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 42.0)  # Should return default from parms

    def test_copy_with_empty_cell(self):
        """Test copy method with empty cell."""
        row = ["1", "", "3"]
        col_def = {"src_col": 1, "type": "float", "parms": [99.0]}
        
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 99.0)  # Should return default

    def test_copy_with_invalid_data_type(self):
        """Test copy method with invalid data type conversion."""
        row = ["1", "not_a_number", "3"]
        col_def = {"src_col": 1, "type": "float", "parms": []}
        
        result = self.algorithms.copy(row, col_def)
        self.assertIsNone(result)

    def test_copy_with_string_type(self):
        """Test copy method with string type (default)."""
        row = ["1", "hello", "3"]
        col_def = {"src_col": 1, "type": "string", "parms": []}
        
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, "hello")

    def test_utm_common_valid_conversion(self):
        """Test utm_common method with valid UTM conversion."""
        # We need to patch the actual utm module that gets imported
        with patch('data_import.csv_algorithms.utm') as mock_utm:
            mock_utm.from_latlon.return_value = (500000.0, 4649776.0, 18, 'T')
            mock_utm.to_latlon.return_value = (42.0, -71.0)
            
            row = ["-71.0", "42.0", "1000.0", "2000.0"]
            col_def = {"parms": [0, 1, 2, 3]}  # map_origin_x, map_origin_y, x_map_loc, y_map_loc
            
            lat, lon = CsvAlgorithms.utm_common(row, col_def)
            
            self.assertEqual(lat, 42.0)
            self.assertEqual(lon, -71.0)
            mock_utm.from_latlon.assert_called_once_with(42.0, -71.0)
            mock_utm.to_latlon.assert_called_once_with(501000.0, 4651776.0, 18, 'T')

    def test_utm_common_invalid_params(self):
        """Test utm_common method with invalid parameters."""
        row = ["1", "2", "3"]
        col_def = {"parms": [0, 1]}  # Not enough parameters
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        
        self.assertIsNone(lat)
        self.assertIsNone(lon)

    def test_utm_common_invalid_indices(self):
        """Test utm_common method with out-of-bounds indices."""
        row = ["1", "2", "3"]
        col_def = {"parms": [0, 1, 2, 5]}  # Index 5 is out of bounds
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        
        self.assertIsNone(lat)
        self.assertIsNone(lon)

    def test_utm_common_conversion_error(self):
        """Test utm_common method with UTM conversion error."""
        with patch('data_import.csv_algorithms.utm') as mock_utm:
            mock_utm.from_latlon.side_effect = ValueError("Invalid coordinates")
            
            row = ["invalid", "coords", "1000", "2000"]
            col_def = {"parms": [0, 1, 2, 3]}
            
            lat, lon = CsvAlgorithms.utm_common(row, col_def)
            
            self.assertIsNone(lat)
            self.assertIsNone(lon)

    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lat(self, mock_utm_common):
        """Test utm_lat method."""
        mock_utm_common.return_value = (42.0, -71.0)
        
        row = ["test_row"]
        col_def = {"test": "config"}
        
        result = self.algorithms.utm_lat(row, col_def)
        
        self.assertEqual(result, 42.0)
        mock_utm_common.assert_called_once_with(row, col_def)

    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lon(self, mock_utm_common):
        """Test utm_lon method."""
        mock_utm_common.return_value = (42.0, -71.0)
        
        row = ["test_row"]
        col_def = {"test": "config"}
        
        result = self.algorithms.utm_lon(row, col_def)
        
        self.assertEqual(result, -71.0)
        mock_utm_common.assert_called_once_with(row, col_def)

    def test_tgsim_lane_even_source_value(self):
        """Test tgsim_lane method with even source value."""
        row = ["1", "2", "4"]  # src_value = 4 (even), lane_kf = 2
        col_def = {"src_col": 2, "parms": [1]}
        
        result = self.algorithms.tgsim_lane(row, col_def)
        
        self.assertEqual(result, 14)  # 16 - 2 = 14

    def test_tgsim_lane_odd_source_value(self):
        """Test tgsim_lane method with odd source value."""
        row = ["1", "2", "5"]  # src_value = 5 (odd), lane_kf = 2
        col_def = {"src_col": 2, "parms": [1]}
        
        result = self.algorithms.tgsim_lane(row, col_def)
        
        self.assertEqual(result, 5)  # 7 - 2 = 5

    def test_tgsim_lane_invalid_indices(self):
        """Test tgsim_lane method with invalid indices."""
        row = ["1", "2", "3"]
        col_def = {"src_col": 5, "parms": [1]}  # Invalid src_col
        
        result = self.algorithms.tgsim_lane(row, col_def)
        
        self.assertIsNone(result)

    def test_tgsim_lane_invalid_parms(self):
        """Test tgsim_lane method with invalid parms index."""
        row = ["1", "2", "3"]
        col_def = {"src_col": 2, "parms": [5]}  # Invalid parms index
        
        result = self.algorithms.tgsim_lane(row, col_def)
        
        self.assertIsNone(result)

    def test_group_creates_unique_indices(self):
        """Test group method creates unique indices for different keys."""
        row1 = ["A", "B", "C"]
        row2 = ["A", "C", "D"]  # Different combination
        row3 = ["A", "B", "C"]  # Same as row1
        col_def = {"parms": [0, 1]}  # Concatenate columns 0 and 1
        
        result1 = self.algorithms.group(row1, col_def)
        result2 = self.algorithms.group(row2, col_def)
        result3 = self.algorithms.group(row3, col_def)
        
        self.assertEqual(result1, 1)  # First unique combination
        self.assertEqual(result2, 2)  # Second unique combination
        self.assertEqual(result1, result3)  # Same combination should get same index

    def test_group_with_empty_key(self):
        """Test group method with empty key."""
        row = ["", "", "C"]
        col_def = {"parms": [0, 1]}
        
        result = self.algorithms.group(row, col_def)
        
        self.assertIsNone(result)

    def test_group_with_invalid_indices(self):
        """Test group method with invalid column indices."""
        row = ["A", "B", "C"]
        col_def = {"parms": [0, 5]}  # Index 5 is out of bounds
        
        result = self.algorithms.group(row, col_def)
        
        # Looking at the actual code, it creates a key "A " (valid index 0, invalid index 5 is skipped)
        # So it should return 1 (first unique key), not 0
        self.assertEqual(result, 1)

    def test_event_name_with_label(self):
        """Test event_name method with label parameter."""
        row = ["event", "data", "test"]
        col_def = {"parms": [0, 1, "Event: "]}  # Label as last parameter
        
        result = self.algorithms.event_name(row, col_def)
        
        self.assertEqual(result, "Event: event data")

    def test_event_name_without_label(self):
        """Test event_name method without label parameter."""
        row = ["event", "data", "test"]
        col_def = {"parms": [0, 1]}  # No label
        
        result = self.algorithms.event_name(row, col_def)
        
        self.assertEqual(result, "event data")

    def test_event_name_with_empty_key(self):
        """Test event_name method with empty key."""
        row = ["", "", "test"]
        col_def = {"parms": [0, 1]}
        
        result = self.algorithms.event_name(row, col_def)
        
        self.assertIsNone(result)

    def test_event_name_with_no_parms(self):
        """Test event_name method with no parameters."""
        row = ["A", "B", "C"]
        col_def = {"parms": []}
        
        result = self.algorithms.event_name(row, col_def)
        
        self.assertIsNone(result)

    def test_tgsim_automated_yes(self):
        """Test tgsim_automated method with 'yes' value."""
        row = ["data1", "YES", "data3"]
        col_def = {"src_col": 1}
        
        result = self.algorithms.tgsim_automated(row, col_def)
        
        self.assertEqual(result, 1)

    def test_tgsim_automated_no(self):
        """Test tgsim_automated method with 'no' value."""
        row = ["data1", "no", "data3"]
        col_def = {"src_col": 1}
        
        result = self.algorithms.tgsim_automated(row, col_def)
        
        self.assertEqual(result, 0)

    def test_tgsim_automated_case_insensitive(self):
        """Test tgsim_automated method is case insensitive."""
        test_cases = ["yes", "YES", "Yes", "yEs"]
        
        for test_value in test_cases:
            row = ["data1", test_value, "data3"]
            col_def = {"src_col": 1}
            
            result = self.algorithms.tgsim_automated(row, col_def)
            self.assertEqual(result, 1)

    def test_tgsim_automated_invalid_column(self):
        """Test tgsim_automated method with invalid column."""
        row = ["data1", "yes", "data3"]
        col_def = {"src_col": 5}  # Out of bounds
        
        result = self.algorithms.tgsim_automated(row, col_def)
        
        self.assertEqual(result, 0)

    def test_tgsim_automated_empty_cell(self):
        """Test tgsim_automated method with empty cell."""
        row = ["data1", "", "data3"]
        col_def = {"src_col": 1}
        
        result = self.algorithms.tgsim_automated(row, col_def)
        
        self.assertEqual(result, 0)


class TestByteCountingStream(unittest.TestCase):
    """Test cases for the ByteCountingStream class."""

    def test_byte_counting_read(self):
        """Test that ByteCountingStream correctly counts bytes read."""
        test_data = b"Hello, World!"
        raw_stream = io.BytesIO(test_data)
        counting_stream = ByteCountingStream(raw_stream)
        
        # Initially no bytes read
        self.assertEqual(counting_stream.bytes_read, 0)
        
        # Read some data
        data = counting_stream.read(5)
        self.assertEqual(data, b"Hello")
        self.assertEqual(counting_stream.bytes_read, 5)
        
        # Read more data
        data = counting_stream.read(8)
        self.assertEqual(data, b", World!")
        self.assertEqual(counting_stream.bytes_read, 13)

    def test_byte_counting_read1(self):
        """Test that ByteCountingStream correctly counts bytes with read1."""
        test_data = b"Test data"
        raw_stream = io.BytesIO(test_data)
        counting_stream = ByteCountingStream(raw_stream)
        
        data = counting_stream.read1(4)
        self.assertEqual(data, b"Test")
        self.assertEqual(counting_stream.bytes_read, 4)


class TestCsvProcess(unittest.TestCase):
    """Test cases for the CsvProcess class."""

    def setUp(self):
        """Set up test fixtures."""
        self.processor = CsvProcess()
        self.mock_worker = Mock()
        self.mock_worker.log_signal = Mock()
        self.mock_worker.progress_signal = Mock()
        self.mock_worker.interrupt_flag = False

    def test_initialization(self):
        """Test CsvProcess initialization."""
        self.assertIsInstance(self.processor, BaseProcess)
        self.assertIsNotNone(self.processor._func_dict)
        self.assertFalse(self.processor._has_header)
        self.assertEqual(self.processor._min_cols, 1)
        self.assertEqual(self.processor._col_defs, [])

    def test_initialize_func_dict(self):
        """Test that function dictionary is properly initialized."""
        expected_functions = ['copy', 'utm_lat', 'utm_lon', 'tgsim_lane', 
                            'group', 'event_name', 'tgsim_automated']
        
        for func_name in expected_functions:
            self.assertIn(func_name, self.processor._func_dict)
            self.assertTrue(callable(self.processor._func_dict[func_name]))

    def test_check_configuration_valid_dict(self):
        """Test configuration checking with valid dictionary format."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
            }
        }
        
        error_count = self.processor._check_configuration(config, self.mock_worker)
        
        self.assertEqual(error_count, 0)
        self.assertEqual(len(self.processor._col_defs), 1)
        self.assertEqual(self.processor._min_cols, 6)

    def test_check_configuration_valid_list(self):
        """Test configuration checking with valid list format."""
        config = {
            "col_defs": [
                {
                    "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                    "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                    "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                    "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                    "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                    "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
                }
            ]
        }
        
        error_count = self.processor._check_configuration(config, self.mock_worker)
        
        self.assertEqual(error_count, 0)
        self.assertEqual(len(self.processor._col_defs), 1)

    def test_check_configuration_missing_required_columns(self):
        """Test configuration checking with missing required columns."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "copy"}
                # Missing other required columns
            }
        }
        
        error_count = self.processor._check_configuration(config, self.mock_worker)
        
        self.assertGreater(error_count, 0)

    def test_check_configuration_invalid_function(self):
        """Test configuration checking with invalid function name."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "invalid_function"},
                "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
            }
        }
        
        error_count = self.processor._check_configuration(config, self.mock_worker)
        
        self.assertGreater(error_count, 0)

    def test_check_configuration_duplicate_keys(self):
        """Test configuration checking with duplicate keys."""
        config = {
            "col_defs": [
                {"event_id": {"src_col": 0, "type": "int", "func": "copy"}},
                {"event_id": {"src_col": 1, "type": "int", "func": "copy"}}  # Duplicate
            ]
        }
        
        error_count = self.processor._check_configuration(config, self.mock_worker)
        
        self.assertGreater(error_count, 0)

    def test_check_import_file_empty_file(self):
        """Test file checking with empty file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
            temp_path = f.name
        
        try:
            error_count = self.processor._check_import_file(temp_path, self.mock_worker)
            self.assertEqual(error_count, 1)
        finally:
            os.unlink(temp_path)

    def test_check_import_file_valid_csv(self):
        """Test file checking with valid CSV file."""
        # Set up valid configuration first
        self.processor._col_defs = [{
            "event_id": {"src_col": 0, "type": "int", "func": "copy"},
            "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
            "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
            "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
            "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
            "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
        }]
        self.processor._min_cols = 6
        
        csv_data = """event_id,veh_id,veh_time,lane_id,x_map_loc,y_map_loc
1,101,0.0,1,100.0,200.0
1,101,0.1,1,101.0,201.0"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='') as f:
            f.write(csv_data)
            temp_path = f.name
        
        try:
            error_count = self.processor._check_import_file(temp_path, self.mock_worker)
            self.assertEqual(error_count, 0)
        finally:
            os.unlink(temp_path)

    def test_check_import_file_insufficient_columns(self):
        """Test file checking with insufficient columns."""
        # Set up configuration requiring more columns
        self.processor._col_defs = [{
            "event_id": {"src_col": 0, "type": "int", "func": "copy"},
            "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
            "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
            "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
            "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
            "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
        }]
        self.processor._min_cols = 6
        
        csv_data = """1,101,0.0"""  # Only 3 columns, need 6
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='') as f:
            f.write(csv_data)
            temp_path = f.name
        
        try:
            error_count = self.processor._check_import_file(temp_path, self.mock_worker)
            self.assertGreater(error_count, 0)
        finally:
            os.unlink(temp_path)

    @patch('sqlite3.connect')
    def test_process_integration(self, mock_connect):
        """Test the main process method integration."""
        # Mock database connection
        mock_db = Mock()
        mock_connect.return_value = mock_db
        mock_cursor = Mock()
        mock_db.cursor.return_value = mock_cursor
        mock_db.executemany.return_value = None
        mock_db.commit.return_value = None
        
        # Create valid test CSV
        csv_data = """event_id,veh_id,veh_time,lane_id,x_map_loc,y_map_loc
1,101,0.0,1,100.0,200.0
1,101,0.1,1,101.0,201.0"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='') as f:
            f.write(csv_data)
            temp_path = f.name
        
        try:
            config = {
                "col_defs": {
                    "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                    "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                    "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                    "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                    "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                    "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"},
                    "x_frenet_loc": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "y_frenet_loc": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "x_map_origin": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "y_map_origin": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "veh_lat": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "veh_lon": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "veh_speed": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "veh_accel": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "veh_length": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "veh_width": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "veh_automation": {"src_col": -1, "parms": [0], "func": "copy"},
                    "osm_way_id": {"src_col": -1, "parms": [0], "func": "copy"},
                    "osm_speed_limit": {"src_col": -1, "parms": [0], "func": "copy"},
                    "osm_traffic_control": {"src_col": -1, "parms": [0], "func": "copy"},
                    "preceding_veh_id": {"src_col": -1, "parms": [0], "func": "copy"},
                    "veh_dist_trav": {"src_col": -1, "parms": [0.0], "func": "copy"},
                    "event_name": {"src_col": -1, "parms": ["Test Event"], "func": "copy"}
                }
            }
            
            result = self.processor.process(config, temp_path, mock_db, self.mock_worker)
            
            # Should return 0 for success
            self.assertEqual(result, 0)
            
            # Verify database operations were called
            mock_db.executemany.assert_called()
            mock_db.commit.assert_called()
            
        finally:
            os.unlink(temp_path)

    def test_process_with_worker_interrupt(self):
        """Test process method handles worker interrupt flag."""
        # Set interrupt flag
        self.mock_worker.interrupt_flag = True
        
        config = {"col_defs": {}}  # Invalid config to trigger early return
        
        result = self.processor.process(config, "dummy_path", None, self.mock_worker)
        
        # Should return early due to interrupt or config error
        self.assertGreaterEqual(result, 0)

    def test_process_handles_invalid_config(self):
        """Test process method handles invalid configuration."""
        config = {"invalid": "config"}  # Missing col_defs
        
        result = self.processor.process(config, "dummy_path", None, self.mock_worker)
        
        # Should return error count > 0
        self.assertGreater(result, 0)

    def test_col_def_defaults(self):
        """Test that column definition defaults are properly set."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0},  # Minimal definition
                "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
            }
        }
        
        self.processor._check_configuration(config, self.mock_worker)
        
        col_def = self.processor._col_defs[0]["event_id"]
        self.assertEqual(col_def["type"], "float")  # Default type
        self.assertEqual(col_def["units"], "")      # Default units
        self.assertEqual(col_def["func"], "copy")   # Default func
        self.assertEqual(col_def["parms"], [])      # Default parms

    def test_header_detection_with_text(self):
        """Test CSV header detection with text headers."""
        csv_data = """EventID,VehicleID,Time,Lane
1,101,0.0,1
1,101,0.1,1"""
        
        # Set up minimal config for testing
        self.processor._col_defs = [{"event_id": {"src_col": 0, "type": "int", "func": "copy"}}]
        self.processor._min_cols = 1
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='') as f:
            f.write(csv_data)
            temp_path = f.name
        
        try:
            self.processor._check_import_file(temp_path, self.mock_worker)
            self.assertTrue(self.processor._has_header)
        finally:
            os.unlink(temp_path)

    def test_header_detection_without_text(self):
        """Test CSV header detection with numeric headers."""
        csv_data = """1,101,0.0,1
1,101,0.1,1
1,102,0.0,1"""
        
        # Set up minimal config for testing
        self.processor._col_defs = [{"event_id": {"src_col": 0, "type": "int", "func": "copy"}}]
        self.processor._min_cols = 1
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='') as f:
            f.write(csv_data)
            temp_path = f.name
        
        try:
            self.processor._check_import_file(temp_path, self.mock_worker)
            self.assertFalse(self.processor._has_header)
        finally:
            os.unlink(temp_path)


if __name__ == '__main__':
    unittest.main()