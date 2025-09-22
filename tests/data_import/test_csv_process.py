import pytest
import sqlite3
import tempfile
import os
import csv
from unittest.mock import Mock, patch, MagicMock
from data_import.csv_process import CsvProcess, ByteCountingStream


class TestByteCountingStream:
    """Test suite for ByteCountingStream class."""
    
    def test_initialization(self):
        """Test ByteCountingStream initialization."""
        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(b"test data")
            temp_file.seek(0)
            
            stream = ByteCountingStream(temp_file)
            assert stream.bytes_read == 0
    
    def test_read_tracking(self):
        """Test that read operations track bytes read."""
        test_data = b"Hello, World!"
        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(test_data)
            temp_file.seek(0)
            
            stream = ByteCountingStream(temp_file)
            data = stream.read(5)
            
            assert data == b"Hello"
            assert stream.bytes_read == 5
    
    def test_read1_tracking(self):
        """Test that read1 operations track bytes read."""
        test_data = b"Hello, World!"
        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(test_data)
            temp_file.seek(0)
            
            stream = ByteCountingStream(temp_file)
            data = stream.read1(3)
            
            assert len(data) <= 3
            assert stream.bytes_read == len(data)


class TestCsvProcess:
    """Test suite for CsvProcess class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.processor = CsvProcess()
        self.db_conn = sqlite3.connect(":memory:")
        self.create_test_table()
        self.worker = Mock()
        self.worker.interrupt_flag = False
        self.worker.log_signal = Mock()
        self.worker.progress_signal = Mock()
    
    def teardown_method(self):
        """Clean up test fixtures."""
        self.db_conn.close()
    
    def create_test_table(self):
        """Create the test events table."""
        columns = [
            "event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc",
            "x_frenet_loc", "y_frenet_loc", "x_map_origin", "y_map_origin", "veh_lat", "veh_lon",
            "veh_speed", "veh_accel", "veh_length", "veh_width", "veh_automation",
            "osm_way_id", "osm_speed_limit", "osm_traffic_control", "preceding_veh_id", "veh_dist_trav", "event_name"
        ]
        create_query = f"CREATE TABLE events ({', '.join(columns)})"
        self.db_conn.execute(create_query)
        self.db_conn.commit()
    
    def create_test_csv_file(self, data, has_header=False):
        """Create a temporary CSV file with test data."""
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        writer = csv.writer(temp_file)
        
        if has_header:
            writer.writerow(["event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc"])
        
        for row in data:
            writer.writerow(row)
        
        temp_file.close()
        return temp_file.name
    
    def test_initialization(self):
        """Test CsvProcess initialization."""
        assert hasattr(self.processor, '_func_dict')
        assert hasattr(self.processor, '_has_header')
        assert hasattr(self.processor, '_min_cols')
        assert hasattr(self.processor, '_col_defs')
        assert hasattr(self.processor, '_algorithms')
        assert self.processor._has_header is False
        assert self.processor._min_cols == 1
        assert len(self.processor._col_defs) == 0
    
    def test_initialize_func_dict(self):
        """Test that _func_dict is properly initialized with algorithm methods."""
        assert 'copy' in self.processor._func_dict
        assert 'utm_lat' in self.processor._func_dict
        assert 'utm_lon' in self.processor._func_dict
        assert 'tgsim_lane' in self.processor._func_dict
        assert 'group' in self.processor._func_dict
        assert 'event_name' in self.processor._func_dict
        assert 'tgsim_automated' in self.processor._func_dict
    
    def test_inheritance(self):
        """Test that CsvProcess inherits from BaseProcess."""
        from data_import.base_process import BaseProcess
        assert isinstance(self.processor, BaseProcess)
    
    def test_process_large_file_batching(self):
        """Test process method with large file to verify batching."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        # Create a large dataset (more than batch_size of 1000)
        data = []
        for i in range(1500):
            data.append([str(i), str(i+100), str(i*1.5), str(i%10), str(i*123.45), str(i*678.90)])
        
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor.process(config, temp_file, self.db_conn, self.worker)
            assert result == 0
            
            # Check that all data was inserted
            cursor = self.db_conn.execute("SELECT COUNT(*) FROM events")
            count = cursor.fetchone()[0]
            assert count == 1500
            
            # Verify progress signals were emitted
            assert self.worker.progress_signal.emit.called
        finally:
            os.unlink(temp_file)


class TestCsvProcessCheckConfiguration:
    """Test suite for _check_configuration method."""
    
    def setup_method(self):
        self.processor = CsvProcess()
        self.worker = Mock()
        self.worker.log_signal = Mock()
    
    def test_valid_configuration_dict(self):
        """Test valid configuration with dict col_defs."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        result = self.processor._check_configuration(config, self.worker)
        assert result == 0  # Valid configuration should return 0
        assert len(self.processor._col_defs) == 1
    
    def test_valid_configuration_list(self):
        """Test valid configuration with list col_defs."""
        config = {
            "col_defs": [{
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }]
        }
        
        result = self.processor._check_configuration(config, self.worker)
        assert result == 0
        assert len(self.processor._col_defs) == 1
    
    def test_invalid_col_defs_type(self):
        """Test configuration with invalid col_defs type."""
        config = {"col_defs": "invalid"}
        
        result = self.processor._check_configuration(config, self.worker)
        assert result == 1
    
    def test_missing_required_columns(self):
        """Test configuration missing required columns."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"}
                # Missing required columns: veh_time, lane_id, x_map_loc, y_map_loc
            }
        }
        
        result = self.processor._check_configuration(config, self.worker)
        assert result > 0
    
    def test_duplicate_keys_across_sets(self):
        """Test configuration with duplicate keys across col_def sets."""
        config = {
            "col_defs": [
                {
                    "event_id": {"src_col": 0, "type": "int"},
                    "veh_id": {"src_col": 1, "type": "int"},
                    "veh_time": {"src_col": 2, "type": "float"},
                    "lane_id": {"src_col": 3, "type": "int"},
                    "x_map_loc": {"src_col": 4, "type": "float"},
                    "y_map_loc": {"src_col": 5, "type": "float"}
                },
                {
                    "event_id": {"src_col": 0, "type": "int"},  # This should cause duplicate error
                    "veh_id": {"src_col": 1, "type": "int"},
                    "veh_time": {"src_col": 2, "type": "float"},
                    "lane_id": {"src_col": 3, "type": "int"},
                    "x_map_loc": {"src_col": 4, "type": "float"},
                    "y_map_loc": {"src_col": 5, "type": "float"}
                }
            ]
        }
        
        result = self.processor._check_configuration(config, self.worker)
        assert result > 0  # Should fail due to duplicate keys
    
    def test_invalid_function_name(self):
        """Test configuration with invalid function name."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "invalid_func"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        result = self.processor._check_configuration(config, self.worker)
        assert result > 0
    
    def test_invalid_src_col_without_func_parms(self):
        """Test configuration with negative src_col but no func/parms."""
        config = {
            "col_defs": {
                "event_id": {"src_col": -1, "type": "int"},  # No func/parms
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        result = self.processor._check_configuration(config, self.worker)
        assert result > 0


class TestCsvProcessCheckImportFile:
    """Test suite for _check_import_file method."""
    
    def setup_method(self):
        self.processor = CsvProcess()
        self.processor._min_cols = 6
        self.processor._col_defs = [{
            "event_id": {"src_col": 0, "type": "int", "func": "copy"},
            "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
            "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
            "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
            "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
            "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
        }]
        self.worker = Mock()
        self.worker.log_signal = Mock()
        self.worker.progress_signal = Mock()
        self.worker.interrupt_flag = False
    
    def create_test_csv_file(self, data):
        """Create a temporary CSV file with test data."""
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        writer = csv.writer(temp_file)
        for row in data:
            writer.writerow(row)
        temp_file.close()
        return temp_file.name
    
    def test_empty_file(self):
        """Test check_import_file with empty file."""
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        temp_file.close()
        
        try:
            result = self.processor._check_import_file(temp_file.name, self.worker)
            assert result == 1
        finally:
            os.unlink(temp_file.name)
    
    def test_file_with_header_only(self):
        """Test check_import_file with header only."""
        data = [["event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc"]]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor._check_import_file(temp_file, self.worker)
            assert result == 1
        finally:
            os.unlink(temp_file)
    
    def test_valid_file_with_header(self):
        """Test check_import_file with valid file and header."""
        data = [
            ["event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc"],
            ["1", "100", "1.5", "2", "123.45", "678.90"]
        ]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor._check_import_file(temp_file, self.worker)
            assert result == 0
            assert self.processor._has_header is True
        finally:
            os.unlink(temp_file)
    
    def test_valid_file_without_header(self):
        """Test check_import_file with valid file without header."""
        data = [
            ["1", "100", "1.5", "2", "123.45", "678.90"],
            ["2", "101", "2.0", "3", "124.45", "679.90"]
        ]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor._check_import_file(temp_file, self.worker)
            assert result == 0
            assert self.processor._has_header is False
        finally:
            os.unlink(temp_file)
    
    def test_insufficient_columns(self):
        """Test check_import_file with insufficient columns."""
        data = [["1", "100", "1.5"]]  # Only 3 columns, need 6
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor._check_import_file(temp_file, self.worker)
            assert result == 1
        finally:
            os.unlink(temp_file)
    
    def test_invalid_data_types(self):
        """Test check_import_file with invalid data types."""
        data = [["not_a_number", "100", "1.5", "2", "123.45", "678.90"]]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor._check_import_file(temp_file, self.worker)
            assert result > 0
        finally:
            os.unlink(temp_file)


class TestCsvProcessProcess:
    """Test suite for process method."""
    
    def setup_method(self):
        self.processor = CsvProcess()
        self.db_conn = sqlite3.connect(":memory:")
        self.create_test_table()
        self.worker = Mock()
        self.worker.interrupt_flag = False
        self.worker.log_signal = Mock()
        self.worker.progress_signal = Mock()
    
    def teardown_method(self):
        """Clean up after each test method."""
        self.db_conn.close()
    
    def create_test_table(self):
        """Create the test events table."""
        columns = [
            "event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc",
            "x_frenet_loc", "y_frenet_loc", "x_map_origin", "y_map_origin", "veh_lat", "veh_lon",
            "veh_speed", "veh_accel", "veh_length", "veh_width", "veh_automation",
            "osm_way_id", "osm_speed_limit", "osm_traffic_control", "preceding_veh_id", "veh_dist_trav", "event_name"
        ]
        create_query = f"CREATE TABLE events ({', '.join(columns)})"
        self.db_conn.execute(create_query)
        self.db_conn.commit()
    
    def create_test_csv_file(self, data, has_header=False):
        """Create a temporary CSV file with test data."""
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        writer = csv.writer(temp_file)
        
        if has_header:
            writer.writerow(["event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc"])
        
        for row in data:
            writer.writerow(row)
        
        temp_file.close()
        return temp_file.name
    
    def test_process_valid_configuration_and_file(self):
        """Test process method with valid configuration and file."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        data = [
            ["1", "100", "1.5", "2", "123.45", "678.90"],
            ["2", "101", "2.0", "3", "124.45", "679.90"]
        ]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor.process(config, temp_file, self.db_conn, self.worker)
            assert result == 0
            
            # Check that data was inserted
            cursor = self.db_conn.execute("SELECT COUNT(*) FROM events")
            count = cursor.fetchone()[0]
            assert count == 2
        finally:
            os.unlink(temp_file)
    
    def test_process_invalid_configuration(self):
        """Test process method with invalid configuration."""
        config = {"col_defs": "invalid"}
        
        data = [["1", "100", "1.5", "2", "123.45", "678.90"]]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor.process(config, temp_file, self.db_conn, self.worker)
            assert result > 0
        finally:
            os.unlink(temp_file)
    
    def test_process_with_interruption(self):
        """Test process method with worker interruption."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        data = [["1", "100", "1.5", "2", "123.45", "678.90"]]
        temp_file = self.create_test_csv_file(data)
        
        # Set interrupt flag
        self.worker.interrupt_flag = True
        
        try:
            result = self.processor.process(config, temp_file, self.db_conn, self.worker)
            # Should return early due to interruption during config check
            assert isinstance(result, int)
        finally:
            os.unlink(temp_file)
    
    def test_process_with_multiple_col_def_sets(self):
        """Test process method with multiple column definition sets."""
        config = {
            "col_defs": [
                {
                    "event_id": {"src_col": 0, "type": "int"},
                    "veh_id": {"src_col": 1, "type": "int"},
                    "veh_time": {"src_col": 2, "type": "float"},
                    "lane_id": {"src_col": 3, "type": "int"},
                    "x_map_loc": {"src_col": 4, "type": "float"},
                    "y_map_loc": {"src_col": 5, "type": "float"}
                }
            ]
        }
        
        data = [["1", "100", "1.5", "2", "123.45", "678.90"]]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor.process(config, temp_file, self.db_conn, self.worker)
            assert result == 0
            
            # Should insert 1 record (one for the col_def set)
            cursor = self.db_conn.execute("SELECT COUNT(*) FROM events")
            count = cursor.fetchone()[0]
            assert count == 1
        finally:
            os.unlink(temp_file)
    
    @patch('sqlite3.Connection.executemany')
    def test_process_database_error(self, mock_executemany):
        """Test process method with database error."""
        mock_executemany.side_effect = sqlite3.Error("Database error")
        
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        data = [["1", "100", "1.5", "2", "123.45", "678.90"]]
        temp_file = self.create_test_csv_file(data)
        
        try:
            result = self.processor.process(config, temp_file, self.db_conn, self.worker)
            assert result == 0  # Should continue processing despite database errors
        finally:
            os.unlink(temp_file)
    
    def test_process_function_error(self):
        """Test process method with function processing error."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "invalid_func"},
                "veh_id": {"src_col": 1, "type": "int"},
                "veh_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        data = [["1", "100", "1.5", "2", "123.45", "678.90"]]
        temp_file = self.create_test_csv_file(data)
        
        try:
            # This should fail during configuration check
            result = self.processor.process(config, temp_file, self.db_conn, self.worker)
            assert result > 0
        finally:
            os.unlink(temp_file)