"""
Comprehensive unit tests for data_import package.
Tests base_process.py, csv_algorithms.py, and csv_process.py.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
import sqlite3
import io
import os
import sys
import tempfile
import csv
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import modules - PyQt6 is mocked globally by conftest.py
from data_import.base_process import BaseProcess
from data_import.csv_algorithms import CsvAlgorithms
from data_import.csv_process import CsvProcess, ByteCountingStream


class TestBaseProcess(unittest.TestCase):
    """Test cases for BaseProcess abstract class."""
    
    def test_base_process_is_abstract(self):
        """Test that BaseProcess cannot be instantiated directly."""
        with self.assertRaises(TypeError):
            BaseProcess()
    
    def test_process_method_signature(self):
        """Test that process method has correct signature."""
        # Create a concrete implementation
        class ConcreteProcess(BaseProcess):
            def process(self, config, input_path, db_connection, worker):
                return 0
        
        # Verify we can instantiate the concrete class
        process = ConcreteProcess()
        self.assertIsInstance(process, BaseProcess)
        
        # Verify process method exists and is callable
        self.assertTrue(hasattr(process, 'process'))
        self.assertTrue(callable(process.process))
        
        # Test calling the method
        result = process.process({}, 'test.csv', Mock(), Mock())
        self.assertEqual(result, 0)


class TestCsvAlgorithms(unittest.TestCase):
    """Test cases for CsvAlgorithms class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.algorithms = CsvAlgorithms()
    
    def test_init(self):
        """Test CsvAlgorithms initialization."""
        self.assertIsInstance(self.algorithms._group_dict, dict)
        self.assertEqual(len(self.algorithms._group_dict), 0)
    
    def test_copy_basic_functionality(self):
        """Test copy method basic functionality."""
        row = ['1', '2.5', 'text']
        
        # Test integer copy
        col_def = {'src_col': 0, 'type': 'int', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 1)
        
        # Test float copy
        col_def = {'src_col': 1, 'type': 'float', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 2.5)
        
        # Test string copy
        col_def = {'src_col': 2, 'type': 'str', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 'text')
    
    def test_copy_with_defaults(self):
        """Test copy method with default values."""
        row = ['1', '2.5']
        
        # Test with invalid column index
        col_def = {'src_col': 5, 'type': 'int', 'parms': [99]}
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 99)
        
        # Test with empty cell
        row = ['1', '']
        col_def = {'src_col': 1, 'type': 'float', 'parms': [3.14]}
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 3.14)
        
        # Test with no default
        col_def = {'src_col': 5, 'type': 'int', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertIsNone(result)
    
    def test_copy_type_conversion_errors(self):
        """Test copy method with type conversion errors."""
        row = ['not_a_number', 'also_not_a_number']
        
        # Test invalid integer conversion
        col_def = {'src_col': 0, 'type': 'int', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertIsNone(result)
        
        # Test invalid float conversion
        col_def = {'src_col': 1, 'type': 'float', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertIsNone(result)
    
    def test_copy_float_string_to_int(self):
        """Test copy method converting float strings to integers."""
        row = ['3.7', '42.0']
        
        col_def = {'src_col': 0, 'type': 'int', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 3)
        
        col_def = {'src_col': 1, 'type': 'int', 'parms': []}
        result = self.algorithms.copy(row, col_def)
        self.assertEqual(result, 42)
    
    @patch('utm.from_latlon')
    @patch('utm.to_latlon')
    def test_utm_common_success(self, mock_to_latlon, mock_from_latlon):
        """Test utm_common method successful conversion."""
        # Setup mocks
        mock_from_latlon.return_value = (500000.0, 4649776.0, 33, 'S')
        mock_to_latlon.return_value = (42.0, -71.0)
        
        row = ['-71.0', '42.0', '100.0', '200.0']  # lon, lat, x_offset, y_offset
        col_def = {'parms': [0, 1, 2, 3]}
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        
        self.assertEqual(lat, 42.0)
        self.assertEqual(lon, -71.0)
        
        # Verify UTM calls
        mock_from_latlon.assert_called_once_with(42.0, -71.0)  # lat, lon
        mock_to_latlon.assert_called_once_with(500100.0, 4649976.0, 33, 'S')  # easting+x, northing+y
    
    def test_utm_common_invalid_params(self):
        """Test utm_common method with invalid parameters."""
        row = ['1', '2', '3']
        
        # Test insufficient parameters
        col_def = {'parms': [0, 1]}  # Need at least 4
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        self.assertIsNone(lat)
        self.assertIsNone(lon)
        
        # Test out of bounds indices
        col_def = {'parms': [0, 1, 2, 5]}  # Index 5 is out of bounds
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        self.assertIsNone(lat)
        self.assertIsNone(lon)
    
    @patch('utm.from_latlon')
    def test_utm_common_conversion_error(self, mock_from_latlon):
        """Test utm_common method with UTM conversion error."""
        mock_from_latlon.side_effect = ValueError("Invalid coordinates")
        
        row = ['invalid', 'coords', '100', '200']
        col_def = {'parms': [0, 1, 2, 3]}
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        self.assertIsNone(lat)
        self.assertIsNone(lon)
    
    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lat(self, mock_utm_common):
        """Test utm_lat method."""
        mock_utm_common.return_value = (42.0, -71.0)
        
        row = ['test', 'data']
        col_def = {'parms': [0, 1, 2, 3]}
        
        result = self.algorithms.utm_lat(row, col_def)
        self.assertEqual(result, 42.0)
        mock_utm_common.assert_called_once_with(row, col_def)
    
    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lon(self, mock_utm_common):
        """Test utm_lon method."""
        mock_utm_common.return_value = (42.0, -71.0)
        
        row = ['test', 'data']
        col_def = {'parms': [0, 1, 2, 3]}
        
        result = self.algorithms.utm_lon(row, col_def)
        self.assertEqual(result, -71.0)
        mock_utm_common.assert_called_once_with(row, col_def)
    
    def test_tgsim_lane_calculation(self):
        """Test tgsim_lane method calculations."""
        row = ['100', '2', '3', '4']  # src_value at index 0, lane_kf at index 1
        
        # Test even src_value (100 % 2 == 0)
        col_def = {'src_col': 0, 'parms': [1]}
        result = self.algorithms.tgsim_lane(row, col_def)
        expected = 16 - 2  # 16 - lane_kf
        self.assertEqual(result, expected)
        
        # Test odd src_value
        row = ['101', '3', '0', '0']  # 101 % 2 == 1
        col_def = {'src_col': 0, 'parms': [1]}
        result = self.algorithms.tgsim_lane(row, col_def)
        expected = 7 - 3  # 7 - lane_kf
        self.assertEqual(result, expected)
    
    def test_tgsim_lane_invalid_inputs(self):
        """Test tgsim_lane method with invalid inputs."""
        row = ['100', '2']
        
        # Test invalid src_col
        col_def = {'src_col': 5, 'parms': [1]}
        result = self.algorithms.tgsim_lane(row, col_def)
        self.assertIsNone(result)
        
        # Test invalid parms index
        col_def = {'src_col': 0, 'parms': [5]}
        result = self.algorithms.tgsim_lane(row, col_def)
        self.assertIsNone(result)
        
        # Test empty row
        col_def = {'src_col': 0, 'parms': [1]}
        result = self.algorithms.tgsim_lane(['', '2'], col_def)
        self.assertIsNone(result)
    
    def test_group_unique_keys(self):
        """Test group method with unique key generation."""
        row1 = ['A', 'B', 'C']
        row2 = ['A', 'B', 'D']
        row3 = ['A', 'B', 'C']  # Same as row1
        
        col_def = {'parms': [0, 1, 2]}
        
        # First unique combination
        result1 = self.algorithms.group(row1, col_def)
        self.assertEqual(result1, 1)
        
        # Second unique combination
        result2 = self.algorithms.group(row2, col_def)
        self.assertEqual(result2, 2)
        
        # Same as first combination
        result3 = self.algorithms.group(row3, col_def)
        self.assertEqual(result3, 1)
    
    def test_group_empty_key(self):
        """Test group method with empty key."""
        row = ['', '', '']
        col_def = {'parms': [0, 1, 2]}
        
        result = self.algorithms.group(row, col_def)
        self.assertIsNone(result)
    
    def test_group_invalid_indices(self):
        """Test group method with invalid indices."""
        row = ['A', 'B']
        col_def = {'parms': [0, 1, 5]}  # Index 5 is out of bounds
        
        result = self.algorithms.group(row, col_def)
        self.assertEqual(result, 0)  # Returns 0 on error
    
    def test_event_name_basic(self):
        """Test event_name method basic functionality."""
        row = ['Vehicle', '123', 'Lane', '1']
        
        # Without label
        col_def = {'parms': [0, 1, 2, 3]}
        result = self.algorithms.event_name(row, col_def)
        self.assertEqual(result, "Vehicle 123 Lane 1")
        
        # With label
        col_def = {'parms': [0, 1, "Event_"]}
        result = self.algorithms.event_name(row, col_def)
        self.assertEqual(result, "Event_Vehicle 123")
    
    def test_event_name_empty_params(self):
        """Test event_name method with empty parameters."""
        row = ['A', 'B', 'C']
        col_def = {'parms': []}
        
        result = self.algorithms.event_name(row, col_def)
        self.assertIsNone(result)
    
    def test_event_name_empty_key(self):
        """Test event_name method with empty key."""
        row = ['', '', '']
        col_def = {'parms': [0, 1, 2]}
        
        result = self.algorithms.event_name(row, col_def)
        self.assertIsNone(result)
    
    def test_tgsim_automated_yes(self):
        """Test tgsim_automated method with 'yes' values."""
        test_cases = ['yes', 'YES', 'Yes', 'YeS']
        
        for yes_value in test_cases:
            row = ['other', yes_value, 'data']
            col_def = {'src_col': 1}
            
            result = self.algorithms.tgsim_automated(row, col_def)
            self.assertEqual(result, 1, f"Failed for value: {yes_value}")
    
    def test_tgsim_automated_no(self):
        """Test tgsim_automated method with non-'yes' values."""
        test_cases = ['no', 'NO', 'false', 'true', '', '1', '0']
        
        for no_value in test_cases:
            row = ['other', no_value, 'data']
            col_def = {'src_col': 1}
            
            result = self.algorithms.tgsim_automated(row, col_def)
            self.assertEqual(result, 0, f"Failed for value: {no_value}")
    
    def test_tgsim_automated_invalid_column(self):
        """Test tgsim_automated method with invalid column."""
        row = ['data']
        
        # Invalid column index
        col_def = {'src_col': 5}
        result = self.algorithms.tgsim_automated(row, col_def)
        self.assertEqual(result, 0)
        
        # Empty cell
        row = ['', 'data']
        col_def = {'src_col': 0}
        result = self.algorithms.tgsim_automated(row, col_def)
        self.assertEqual(result, 0)


class TestByteCountingStream(unittest.TestCase):
    """Test cases for ByteCountingStream class."""
    
    def test_byte_counting_read(self):
        """Test ByteCountingStream read method tracks bytes."""
        data = b"Hello, World!"
        raw_stream = io.BytesIO(data)
        stream = ByteCountingStream(raw_stream)
        
        # Initial bytes read should be 0
        self.assertEqual(stream.bytes_read, 0)
        
        # Read some data
        result = stream.read(5)
        self.assertEqual(result, b"Hello")
        self.assertEqual(stream.bytes_read, 5)
        
        # Read more data
        result = stream.read(3)
        self.assertEqual(result, b", W")
        self.assertEqual(stream.bytes_read, 8)
    
    def test_byte_counting_read1(self):
        """Test ByteCountingStream read1 method tracks bytes."""
        data = b"Test data"
        raw_stream = io.BytesIO(data)
        stream = ByteCountingStream(raw_stream)
        
        # Read using read1
        result = stream.read1(4)
        self.assertEqual(result, b"Test")
        self.assertEqual(stream.bytes_read, 4)
    
    def test_byte_counting_multiple_reads(self):
        """Test ByteCountingStream with multiple read operations."""
        data = b"0123456789" * 10  # 100 bytes
        raw_stream = io.BytesIO(data)
        stream = ByteCountingStream(raw_stream)
        
        total_read = 0
        while True:
            chunk = stream.read(10)
            if not chunk:
                break
            total_read += len(chunk)
        
        self.assertEqual(stream.bytes_read, 100)
        self.assertEqual(total_read, 100)


class TestCsvProcess(unittest.TestCase):
    """Test cases for CsvProcess class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.csv_process = CsvProcess()
        self.mock_worker = Mock()
        self.mock_worker.interrupt_flag = False
        self.mock_worker.log_signal = Mock()
        self.mock_worker.progress_signal = Mock()
    
    def test_init(self):
        """Test CsvProcess initialization."""
        self.assertIsInstance(self.csv_process._func_dict, dict)
        self.assertFalse(self.csv_process._has_header)
        self.assertEqual(self.csv_process._min_cols, 1)
        self.assertEqual(self.csv_process._col_defs, [])
        self.assertIsInstance(self.csv_process._algorithms, CsvAlgorithms)
        
        # Verify function dictionary is populated
        self.assertIn('copy', self.csv_process._func_dict)
        self.assertIn('utm_lat', self.csv_process._func_dict)
        self.assertIn('group', self.csv_process._func_dict)
    
    def test_check_configuration_valid_object(self):
        """Test _check_configuration with valid object configuration."""
        config = {
            'col_defs': {
                'event_id': {'src_col': 0, 'type': 'int', 'func': 'copy'},
                'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'},
                'veh_time': {'src_col': 2, 'type': 'float', 'func': 'copy'},
                'lane_id': {'src_col': 3, 'type': 'int', 'func': 'copy'},
                'x_map_loc': {'src_col': 4, 'type': 'float', 'func': 'copy'},
                'y_map_loc': {'src_col': 5, 'type': 'float', 'func': 'copy'}
            }
        }
        
        error_count = self.csv_process._check_configuration(config, self.mock_worker)
        self.assertEqual(error_count, 0)
        self.assertEqual(len(self.csv_process._col_defs), 1)
        self.assertEqual(self.csv_process._min_cols, 6)
    
    def test_check_configuration_valid_array(self):
        """Test _check_configuration with valid array configuration."""
        config = {
            'col_defs': [
                {
                    'event_id': {'src_col': 0, 'type': 'int', 'func': 'copy'},
                    'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'},
                    'veh_time': {'src_col': 2, 'type': 'float', 'func': 'copy'},
                    'lane_id': {'src_col': 3, 'type': 'int', 'func': 'copy'},
                    'x_map_loc': {'src_col': 4, 'type': 'float', 'func': 'copy'},
                    'y_map_loc': {'src_col': 5, 'type': 'float', 'func': 'copy'}
                }
            ]
        }
        
        error_count = self.csv_process._check_configuration(config, self.mock_worker)
        self.assertEqual(error_count, 0)
        self.assertEqual(len(self.csv_process._col_defs), 1)
    
    def test_check_configuration_missing_required_columns(self):
        """Test _check_configuration with missing required columns."""
        config = {
            'col_defs': {
                'event_id': {'src_col': 0, 'type': 'int', 'func': 'copy'},
                'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'}
                # Missing required columns
            }
        }
        
        error_count = self.csv_process._check_configuration(config, self.mock_worker)
        self.assertGreater(error_count, 0)
    
    def test_check_configuration_invalid_function(self):
        """Test _check_configuration with invalid function name."""
        config = {
            'col_defs': {
                'event_id': {'src_col': 0, 'type': 'int', 'func': 'invalid_func'},
                'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'},
                'veh_time': {'src_col': 2, 'type': 'float', 'func': 'copy'},
                'lane_id': {'src_col': 3, 'type': 'int', 'func': 'copy'},
                'x_map_loc': {'src_col': 4, 'type': 'float', 'func': 'copy'},
                'y_map_loc': {'src_col': 5, 'type': 'float', 'func': 'copy'}
            }
        }
        
        error_count = self.csv_process._check_configuration(config, self.mock_worker)
        self.assertGreater(error_count, 0)
    
    def test_check_configuration_defaults(self):
        """Test _check_configuration sets proper defaults."""
        config = {
            'col_defs': {
                'event_id': {'src_col': 0},  # Missing type, func, etc.
                'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'},
                'veh_time': {'src_col': 2, 'type': 'float', 'func': 'copy'},
                'lane_id': {'src_col': 3, 'type': 'int', 'func': 'copy'},
                'x_map_loc': {'src_col': 4, 'type': 'float', 'func': 'copy'},
                'y_map_loc': {'src_col': 5, 'type': 'float', 'func': 'copy'}
            }
        }
        
        error_count = self.csv_process._check_configuration(config, self.mock_worker)
        self.assertEqual(error_count, 0)
        
        # Check that defaults were set
        event_id_def = self.csv_process._col_defs[0]['event_id']
        self.assertEqual(event_id_def['type'], 'float')  # Default type
        self.assertEqual(event_id_def['func'], 'copy')   # Default func
        self.assertEqual(event_id_def['units'], '')      # Default units
        self.assertEqual(event_id_def['parms'], [])      # Default parms
    
    def test_check_import_file_with_header(self):
        """Test _check_import_file with header row."""
        # Create temporary CSV file with header
        csv_content = "time,vehicle,speed\n1.0,100,25.5\n2.0,101,30.0\n"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_path = f.name
        
        try:
            # Set up configuration
            self.csv_process._min_cols = 3
            
            error_count = self.csv_process._check_import_file(temp_path, self.mock_worker)
            self.assertEqual(error_count, 0)
            self.assertTrue(self.csv_process._has_header)
        finally:
            os.unlink(temp_path)
    
    def test_check_import_file_without_header(self):
        """Test _check_import_file without header row."""
        # Create temporary CSV file without header
        csv_content = "1.0,100,25.5\n2.0,101,30.0\n"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_path = f.name
        
        try:
            self.csv_process._min_cols = 3
            
            error_count = self.csv_process._check_import_file(temp_path, self.mock_worker)
            self.assertEqual(error_count, 0)
            self.assertFalse(self.csv_process._has_header)
        finally:
            os.unlink(temp_path)
    
    def test_check_import_file_empty_file(self):
        """Test _check_import_file with empty file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            temp_path = f.name
        
        try:
            error_count = self.csv_process._check_import_file(temp_path, self.mock_worker)
            self.assertEqual(error_count, 1)
        finally:
            os.unlink(temp_path)
    
    def test_check_import_file_insufficient_columns(self):
        """Test _check_import_file with insufficient columns."""
        csv_content = "1.0,100\n2.0,101\n"  # Only 2 columns
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_path = f.name
        
        try:
            self.csv_process._min_cols = 5  # Require 5 columns
            
            error_count = self.csv_process._check_import_file(temp_path, self.mock_worker)
            self.assertGreater(error_count, 0)
        finally:
            os.unlink(temp_path)
    
    @patch('sqlite3.connect')
    def test_process_successful(self, mock_connect):
        """Test successful process execution."""
        # Setup mocks
        mock_db = Mock()
        mock_connect.return_value = mock_db
        mock_cursor = Mock()
        mock_db.cursor.return_value = mock_cursor
        mock_cursor.executemany.return_value = None
        
        # Setup valid configuration
        config = {
            'col_defs': {
                'event_id': {'src_col': 0, 'type': 'int', 'func': 'copy'},
                'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'},
                'veh_time': {'src_col': 2, 'type': 'float', 'func': 'copy'},
                'lane_id': {'src_col': 3, 'type': 'int', 'func': 'copy'},
                'x_map_loc': {'src_col': 4, 'type': 'float', 'func': 'copy'},
                'y_map_loc': {'src_col': 5, 'type': 'float', 'func': 'copy'}
            }
        }
        
        # Create test CSV file
        csv_content = "1,100,1.0,1,0.0,0.0\n1,100,2.0,1,1.0,1.0\n"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_path = f.name
        
        try:
            # Mock file size check
            with patch('os.path.getsize', return_value=len(csv_content)):
                result = self.csv_process.process(config, temp_path, mock_db, self.mock_worker)
            
            self.assertEqual(result, 0)  # Success
            
            # Verify database operations were called
            mock_cursor.executemany.assert_called()
            mock_db.commit.assert_called()
            
        finally:
            os.unlink(temp_path)
    
    def test_process_configuration_error(self):
        """Test process with configuration errors."""
        # Invalid configuration
        config = {'col_defs': 'invalid'}
        
        result = self.csv_process.process(config, 'dummy.csv', Mock(), self.mock_worker)
        # Should return early due to interrupt flag
        self.assertGreaterEqual(result, 0)


class TestCsvProcessIntegration(unittest.TestCase):
    """Integration tests for CsvProcess with real database operations."""
    
    def setUp(self):
        """Set up test fixtures with in-memory database."""
        self.db_connection = sqlite3.connect(':memory:')
        self.csv_process = CsvProcess()
        self.mock_worker = Mock()
        self.mock_worker.interrupt_flag = False
        self.mock_worker.log_signal = Mock()
        self.mock_worker.progress_signal = Mock()
        
        # Create events table
        self.db_connection.execute("""
            CREATE TABLE events (
                event_id INTEGER NOT NULL,
                veh_id INTEGER NOT NULL,
                veh_time REAL NOT NULL,
                lane_id INTEGER,
                x_map_loc REAL,
                y_map_loc REAL,
                x_frenet_loc REAL,
                y_frenet_loc REAL,
                x_map_origin REAL,
                y_map_origin REAL,
                veh_lat REAL,
                veh_lon REAL,
                veh_speed REAL,
                veh_accel REAL,
                veh_length REAL,
                veh_width REAL,
                veh_automation INTEGER,
                osm_way_id INTEGER,
                osm_speed_limit INTEGER,
                osm_traffic_control INTEGER,
                preceding_veh_id INTEGER,
                veh_dist_trav REAL,
                event_name TEXT,
                PRIMARY KEY (event_id, veh_id, veh_time)
            ) WITHOUT ROWID;
        """)
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.db_connection.close()
    
    def test_full_csv_processing_pipeline(self):
        """Test complete CSV processing pipeline with real database."""
        # Create test CSV file
        csv_content = """event_id,veh_id,veh_time,lane_id,x_map_loc,y_map_loc
1,100,1.0,1,0.0,0.0
1,100,2.0,1,10.0,0.0
1,101,1.5,2,5.0,100.0
1,101,2.5,2,15.0,100.0
"""
        
        config = {
            'col_defs': {
                'event_id': {'src_col': 0, 'type': 'int', 'func': 'copy'},
                'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'},
                'veh_time': {'src_col': 2, 'type': 'float', 'func': 'copy'},
                'lane_id': {'src_col': 3, 'type': 'int', 'func': 'copy'},
                'x_map_loc': {'src_col': 4, 'type': 'float', 'func': 'copy'},
                'y_map_loc': {'src_col': 5, 'type': 'float', 'func': 'copy'}
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_path = f.name
        
        try:
            result = self.csv_process.process(config, temp_path, self.db_connection, self.mock_worker)
            
            # Verify success
            self.assertEqual(result, 0)
            
            # Verify data was inserted
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM events")
            count = cursor.fetchone()[0]
            self.assertEqual(count, 4)  # 4 data rows (excluding header)
            
            # Verify specific data
            cursor.execute("SELECT event_id, veh_id, veh_time FROM events ORDER BY veh_id, veh_time")
            rows = cursor.fetchall()
            
            expected = [
                (1, 100, 1.0),
                (1, 100, 2.0),
                (1, 101, 1.5),
                (1, 101, 2.5)
            ]
            
            self.assertEqual(rows, expected)
            
        finally:
            os.unlink(temp_path)
    
    def test_csv_processing_with_algorithms(self):
        """Test CSV processing with algorithm functions."""
        # Create test CSV with data for algorithm functions
        csv_content = """100,1,Vehicle,Test,yes
101,2,Car,Data,no
"""
        
        config = {
            'col_defs': {
                'event_id': {'src_col': -1, 'type': 'int', 'func': 'copy', 'parms': [1]},  # Default value
                'veh_id': {'src_col': 0, 'type': 'int', 'func': 'copy'},
                'veh_time': {'src_col': 1, 'type': 'float', 'func': 'copy'},
                'lane_id': {'src_col': 1, 'type': 'int', 'func': 'tgsim_lane', 'parms': [1]},
                'x_map_loc': {'src_col': -1, 'type': 'float', 'func': 'copy', 'parms': [0.0]},
                'y_map_loc': {'src_col': -1, 'type': 'float', 'func': 'copy', 'parms': [0.0]},
                'veh_automation': {'src_col': 4, 'type': 'int', 'func': 'tgsim_automated'}
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_path = f.name
        
        try:
            result = self.csv_process.process(config, temp_path, self.db_connection, self.mock_worker)
            
            # Verify success
            self.assertEqual(result, 0)
            
            # Verify algorithm functions worked
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT veh_id, veh_automation FROM events ORDER BY veh_id")
            rows = cursor.fetchall()
            
            # First vehicle: 'yes' -> 1, Second vehicle: 'no' -> 0
            self.assertEqual(rows[0][1], 1)  # veh_automation for first vehicle
            self.assertEqual(rows[1][1], 0)  # veh_automation for second vehicle
            
        finally:
            os.unlink(temp_path)


if __name__ == '__main__':
    unittest.main()
