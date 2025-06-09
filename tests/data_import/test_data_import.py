"""
Unit tests for the data_import package.

Tests cover:
- BaseProcess abstract class
- CsvAlgorithms utility functions
- CsvProcess CSV import functionality
"""

import pytest
import sqlite3
import tempfile
import json
import csv
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

# Import the modules to test
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_import.base_process import BaseProcess
from data_import.csv_algorithms import CsvAlgorithms
from data_import.csv_process import CsvProcess, ByteCountingStream


class TestBaseProcess:
    """Test cases for BaseProcess abstract class."""
    
    def test_base_process_is_abstract(self):
        """Test that BaseProcess cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseProcess()
    
    def test_process_method_not_implemented(self):
        """Test that process method raises NotImplementedError."""
        class TestProcessor(BaseProcess):
            pass
        
        processor = TestProcessor()
        with pytest.raises(NotImplementedError):
            processor.process({}, "", None, None)


class TestCsvAlgorithms:
    """Test cases for CsvAlgorithms utility class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.algorithms = CsvAlgorithms()
    
    def test_init_creates_empty_group_dict(self):
        """Test that initialization creates empty group dictionary."""
        assert self.algorithms._group_dict == {}
    
    def test_copy_with_valid_data(self):
        """Test copy function with valid data."""
        row = ["123", "45.6", "text", "789.1"]
        
        # Test integer copy
        col_def = {"src_col": 0, "type": "int"}
        result = self.algorithms.copy(row, col_def)
        assert result == 123
        
        # Test float copy
        col_def = {"src_col": 1, "type": "float"}
        result = self.algorithms.copy(row, col_def)
        assert result == 45.6
        
        # Test string copy
        col_def = {"src_col": 2, "type": "str"}
        result = self.algorithms.copy(row, col_def)
        assert result == "text"
    
    def test_copy_with_float_string_to_int(self):
        """Test copy function converts float strings to integers."""
        row = ["123.0", "45.9"]
        col_def = {"src_col": 0, "type": "int"}
        result = self.algorithms.copy(row, col_def)
        assert result == 123
    
    def test_copy_with_invalid_data(self):
        """Test copy function with invalid data."""
        row = ["invalid", "not_a_number"]
        
        # Test invalid integer
        col_def = {"src_col": 0, "type": "int"}
        result = self.algorithms.copy(row, col_def)
        assert result is None
        
        # Test invalid float
        col_def = {"src_col": 1, "type": "float"}
        result = self.algorithms.copy(row, col_def)
        assert result is None
    
    def test_copy_with_out_of_bounds_column(self):
        """Test copy function with out of bounds column index."""
        row = ["123", "456"]
        col_def = {"src_col": 5, "type": "int", "parms": [999]}
        result = self.algorithms.copy(row, col_def)
        assert result == 999  # Should return default from parms
    
    def test_copy_with_negative_column_uses_default(self):
        """Test copy function with negative column uses default."""
        row = ["123", "456"]
        col_def = {"src_col": -1, "type": "int", "parms": [999]}
        result = self.algorithms.copy(row, col_def)
        assert result == 999
    
    def test_copy_with_empty_cell_uses_default(self):
        """Test copy function with empty cell uses default."""
        row = ["123", "", "789"]
        col_def = {"src_col": 1, "type": "int", "parms": [999]}
        result = self.algorithms.copy(row, col_def)
        assert result == 999
    
    @patch('utm.from_latlon')
    @patch('utm.to_latlon')
    def test_utm_common_valid_coordinates(self, mock_to_latlon, mock_from_latlon):
        """Test utm_common with valid coordinates."""
        # Mock UTM conversion functions
        mock_from_latlon.return_value = (500000, 4500000, 17, 'T')
        mock_to_latlon.return_value = (40.7589, -73.9851)
        
        row = ["-73.9851", "40.7589", "100.0", "200.0"]
        col_def = {"parms": [0, 1, 2, 3]}
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        
        assert lat == 40.7589
        assert lon == -73.9851
        mock_from_latlon.assert_called_once_with(40.7589, -73.9851)
        mock_to_latlon.assert_called_once_with(500100.0, 4500200.0, 17, 'T')
    
    def test_utm_common_insufficient_params(self):
        """Test utm_common with insufficient parameters."""
        row = ["1", "2", "3"]
        col_def = {"parms": [0, 1]}  # Only 2 params, need 4
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None
    
    def test_utm_common_out_of_bounds_params(self):
        """Test utm_common with out of bounds parameter indices."""
        row = ["1", "2"]
        col_def = {"parms": [0, 1, 5, 6]}  # Indices 5,6 are out of bounds
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None
    
    def test_utm_common_invalid_coordinate_values(self):
        """Test utm_common with invalid coordinate values."""
        row = ["invalid", "40.7589", "100.0", "200.0"]
        col_def = {"parms": [0, 1, 2, 3]}
        
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None
    
    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lat(self, mock_utm_common):
        """Test utm_lat function."""
        mock_utm_common.return_value = (40.7589, -73.9851)
        
        row = ["test"]
        col_def = {"parms": [0, 1, 2, 3]}
        
        result = self.algorithms.utm_lat(row, col_def)
        assert result == 40.7589
        mock_utm_common.assert_called_once_with(row, col_def)
    
    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lon(self, mock_utm_common):
        """Test utm_lon function."""
        mock_utm_common.return_value = (40.7589, -73.9851)
        
        row = ["test"]
        col_def = {"parms": [0, 1, 2, 3]}
        
        result = self.algorithms.utm_lon(row, col_def)
        assert result == -73.9851
        mock_utm_common.assert_called_once_with(row, col_def)
    
    def test_tgsim_lane_valid_data(self):
        """Test tgsim_lane with valid data."""
        # Test even src_value
        row = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "5"]
        col_def = {"src_col": 0, "parms": [11]}  # src_col=0 (value=1), parms[0]=11 (value=5)
        
        result = self.algorithms.tgsim_lane(row, col_def)
        # src_value = 1 (odd), so: 7 - lane_kf = 7 - 5 = 2
        assert result == 2
        
        # Test odd src_value
        row = ["2", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "5"]
        result = self.algorithms.tgsim_lane(row, col_def)
        # src_value = 2 (even), so: 16 - lane_kf = 16 - 5 = 11
        assert result == 11
    
    def test_tgsim_lane_invalid_data(self):
        """Test tgsim_lane with invalid data."""
        # Test with invalid src_col
        row = ["invalid", "2"]
        col_def = {"src_col": 0, "parms": [1]}
        result = self.algorithms.tgsim_lane(row, col_def)
        assert result is None
        
        # Test with out of bounds src_col
        row = ["1", "2"]
        col_def = {"src_col": 5, "parms": [1]}
        result = self.algorithms.tgsim_lane(row, col_def)
        assert result is None
        
        # Test with insufficient params
        row = ["1", "2"]
        col_def = {"src_col": 0, "parms": []}
        result = self.algorithms.tgsim_lane(row, col_def)
        assert result is None
    
    def test_group_creates_unique_indices(self):
        """Test group function creates unique indices for different keys."""
        row1 = ["A", "B", "C", "D"]
        row2 = ["A", "X", "C", "D"]
        row3 = ["A", "B", "C", "D"]  # Same as row1
        
        col_def = {"parms": [0, 1, 2]}
        
        # First unique combination
        result1 = self.algorithms.group(row1, col_def)
        assert result1 == 0
        
        # Second unique combination
        result2 = self.algorithms.group(row2, col_def)
        assert result2 == 1
        
        # Same as first combination
        result3 = self.algorithms.group(row3, col_def)
        assert result3 == 0
        
        # Verify group dictionary
        assert len(self.algorithms._group_dict) == 2
        assert "A B C" in self.algorithms._group_dict
        assert "A X C" in self.algorithms._group_dict
    
    def test_group_with_empty_values(self):
        """Test group function with empty values."""
        row = ["A", "", "C"]
        col_def = {"parms": [0, 1, 2]}
        
        result = self.algorithms.group(row, col_def)
        assert result == 0
        
        # Should create key "A C" (empty value skipped)
        assert "A C" in self.algorithms._group_dict
    
    def test_group_with_all_empty_values(self):
        """Test group function with all empty values."""
        row = ["", "", ""]
        col_def = {"parms": [0, 1, 2]}
        
        result = self.algorithms.group(row, col_def)
        assert result is None
    
    def test_group_with_out_of_bounds_params(self):
        """Test group function with out of bounds parameters."""
        row = ["A", "B"]
        col_def = {"parms": [0, 1, 5]}  # Index 5 is out of bounds
        
        result = self.algorithms.group(row, col_def)
        assert result is None


class TestByteCountingStream:
    """Test cases for ByteCountingStream helper class."""
    
    def test_byte_counting_stream_tracks_reads(self):
        """Test that ByteCountingStream correctly tracks bytes read."""
        data = b"Hello, World!"
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(data)
            tmp.flush()
            
            with open(tmp.name, 'rb') as f:
                stream = ByteCountingStream(f)
                
                # Initial state
                assert stream.bytes_read == 0
                
                # Read some data
                chunk1 = stream.read(5)
                assert chunk1 == b"Hello"
                assert stream.bytes_read == 5
                
                # Read more data
                chunk2 = stream.read(8)
                assert chunk2 == b", World!"
                assert stream.bytes_read == 13


class TestCsvProcess:
    """Test cases for CsvProcess class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.processor = CsvProcess()
        self.mock_worker = Mock()
        self.mock_worker.interrupt_flag = False
        self.mock_worker.log_signal = Mock()
        self.mock_worker.progress_signal = Mock()
    
    def test_initialization(self):
        """Test CsvProcess initialization."""
        assert hasattr(self.processor, '_func_dict')
        assert hasattr(self.processor, '_algorithms')
        assert 'copy' in self.processor._func_dict
        assert 'utm_lat' in self.processor._func_dict
        assert 'utm_lon' in self.processor._func_dict
        assert 'tgsim_lane' in self.processor._func_dict
        assert 'group' in self.processor._func_dict
    
    def test_check_configuration_valid_object(self):
        """Test configuration check with valid object format."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "event_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        errors = self.processor._check_configuration(config, self.mock_worker)
        assert errors == 0
        assert len(self.processor._col_defs) == 1
    
    def test_check_configuration_valid_array(self):
        """Test configuration check with valid array format."""
        config = {
            "col_defs": [
                {
                    "event_id": {"src_col": 0, "type": "int"},
                    "veh_id": {"src_col": 1, "type": "int"},
                    "event_time": {"src_col": 2, "type": "float"},
                    "lane_id": {"src_col": 3, "type": "int"},
                    "x_map_loc": {"src_col": 4, "type": "float"},
                    "y_map_loc": {"src_col": 5, "type": "float"}
                }
            ]
        }
        
        errors = self.processor._check_configuration(config, self.mock_worker)
        assert errors == 0
        assert len(self.processor._col_defs) == 1
    
    def test_check_configuration_missing_required_columns(self):
        """Test configuration check with missing required columns."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"}
                # Missing required columns: event_time, lane_id, x_map_loc, y_map_loc
            }
        }
        
        errors = self.processor._check_configuration(config, self.mock_worker)
        assert errors > 0
    
    def test_check_configuration_invalid_function(self):
        """Test configuration check with invalid function."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "invalid_function"},
                "veh_id": {"src_col": 1, "type": "int"},
                "event_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        errors = self.processor._check_configuration(config, self.mock_worker)
        assert errors > 0
    
    def test_check_configuration_duplicate_keys(self):
        """Test configuration check with duplicate keys."""
        config = {
            "col_defs": [
                {
                    "event_id": {"src_col": 0, "type": "int"},
                    "veh_id": {"src_col": 1, "type": "int"},
                    "event_time": {"src_col": 2, "type": "float"},
                    "lane_id": {"src_col": 3, "type": "int"},
                    "x_map_loc": {"src_col": 4, "type": "float"},
                    "y_map_loc": {"src_col": 5, "type": "float"}
                },
                {
                    "event_id": {"src_col": 10, "type": "int"},  # Duplicate key
                    "another_col": {"src_col": 11, "type": "float"}
                }
            ]
        }
        
        errors = self.processor._check_configuration(config, self.mock_worker)
        assert errors > 0
    
    def create_test_csv(self, data_rows, has_header=False):
        """Helper method to create a test CSV file."""
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        writer = csv.writer(tmp)
        
        if has_header:
            writer.writerow(["col1", "col2", "col3", "col4", "col5", "col6"])
        
        for row in data_rows:
            writer.writerow(row)
        
        tmp.flush()
        tmp.close()
        return tmp.name
    
    def test_check_import_file_valid_data(self):
        """Test import file check with valid data."""
        # Configure processor first
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "event_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        self.processor._check_configuration(config, self.mock_worker)
        
        # Create test CSV
        csv_path = self.create_test_csv([
            ["1", "101", "0.0", "1", "100.5", "200.7"],
            ["1", "102", "0.1", "1", "101.5", "201.7"],
            ["2", "103", "0.2", "2", "102.5", "202.7"]
        ], has_header=True)
        
        try:
            errors = self.processor._check_import_file(csv_path, self.mock_worker)
            assert errors == 0
            assert self.processor._has_header is True
        finally:
            Path(csv_path).unlink()
    
    def test_check_import_file_empty_file(self):
        """Test import file check with empty file."""
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        tmp.close()
        
        try:
            errors = self.processor._check_import_file(tmp.name, self.mock_worker)
            assert errors == 1
        finally:
            Path(tmp.name).unlink()
    
    def test_check_import_file_insufficient_columns(self):
        """Test import file check with insufficient columns."""
        # Configure processor to expect 6 columns
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "event_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        self.processor._check_configuration(config, self.mock_worker)
        
        # Create CSV with insufficient columns
        csv_path = self.create_test_csv([
            ["1", "101", "0.0"],  # Only 3 columns, need 6
        ])
        
        try:
            errors = self.processor._check_import_file(csv_path, self.mock_worker)
            assert errors == 1
        finally:
            Path(csv_path).unlink()
    
    def test_process_full_workflow(self):
        """Test complete process workflow."""
        # Create test database
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE events (
                event_id INTEGER NOT NULL,
                veh_id INTEGER NOT NULL,
                event_time REAL NOT NULL,
                lane_id INTEGER,
                x_map_loc REAL,
                y_map_loc REAL,
                x_frenet REAL,
                y_frenet REAL,
                x_map_origin REAL,
                y_map_origin REAL,
                veh_lat REAL,
                veh_lon REAL,
                veh_speed REAL,
                veh_acceleration REAL,
                veh_length REAL,
                veh_width REAL,
                veh_automated INTEGER,
                osm_way_id INTEGER,
                osm_speed_limit INTEGER,
                osm_traffic_control INTEGER,
                preceding_veh_id INTEGER,
                veh_dist REAL,
                PRIMARY KEY (event_id, veh_id, event_time)
            ) WITHOUT ROWID;
        """)
        
        # Create test CSV
        csv_path = self.create_test_csv([
            ["1", "101", "0.0", "1", "100.5", "200.7"],
            ["1", "102", "0.1", "1", "101.5", "201.7"],
            ["2", "103", "0.2", "2", "102.5", "202.7"]
        ])
        
        # Configure processor
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "event_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        try:
            errors = self.processor.process(config, csv_path, db, self.mock_worker)
            assert errors == 0
            
            # Verify data was inserted
            cursor = db.cursor()
            cursor.execute("SELECT COUNT(*) FROM events")
            count = cursor.fetchone()[0]
            assert count == 3
            
            # Verify specific data
            cursor.execute("SELECT event_id, veh_id, event_time FROM events ORDER BY event_time")
            rows = cursor.fetchall()
            expected = [(1, 101, 0.0), (1, 102, 0.1), (2, 103, 0.2)]
            assert rows == expected
            
        finally:
            Path(csv_path).unlink()
            db.close()
    
    def test_process_with_interrupt(self):
        """Test process workflow with interrupt flag set."""
        # Set interrupt flag
        self.mock_worker.interrupt_flag = True
        
        # Create minimal test data
        csv_path = self.create_test_csv([["1", "101", "0.0", "1", "100.5", "200.7"]])
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "event_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        try:
            errors = self.processor.process(config, csv_path, None, self.mock_worker)
            # Should return early due to interrupt flag
            assert errors == 0  # Configuration check should succeed before interrupt is checked
        finally:
            Path(csv_path).unlink()


# Pytest fixtures and test configuration
@pytest.fixture
def sample_config():
    """Provide a sample configuration for testing."""
    return {
        "file_ext": "csv",
        "class": "data_import.csv_process.CsvProcess",
        "col_defs": {
            "event_id": {"src_col": 0, "type": "int"},
            "veh_id": {"src_col": 1, "type": "int"},
            "event_time": {"src_col": 2, "type": "float"},
            "lane_id": {"src_col": 3, "type": "int"},
            "x_map_loc": {"src_col": 4, "type": "float"},
            "y_map_loc": {"src_col": 5, "type": "float"},
            "veh_speed": {"src_col": 6, "type": "float"},
            "veh_acceleration": {"src_col": 7, "type": "float"},
            "veh_length": {"src_col": 8, "type": "float"}
        }
    }


@pytest.fixture
def test_database():
    """Provide a test SQLite database."""
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE events (
            event_id INTEGER NOT NULL,
            veh_id INTEGER NOT NULL,
            event_time REAL NOT NULL,
            lane_id INTEGER,
            x_map_loc REAL,
            y_map_loc REAL,
            x_frenet REAL,
            y_frenet REAL,
            x_map_origin REAL,
            y_map_origin REAL,
            veh_lat REAL,
            veh_lon REAL,
            veh_speed REAL,
            veh_acceleration REAL,
            veh_length REAL,
            veh_width REAL,
            veh_automated INTEGER,
            osm_way_id INTEGER,
            osm_speed_limit INTEGER,
            osm_traffic_control INTEGER,
            preceding_veh_id INTEGER,
            veh_dist REAL,
            PRIMARY KEY (event_id, veh_id, event_time)
        ) WITHOUT ROWID;
    """)
    yield db
    db.close()
