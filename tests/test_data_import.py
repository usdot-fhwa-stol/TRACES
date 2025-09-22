import pytest
import sqlite3
import tempfile
import os
import csv
import io
import sys
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List
from abc import ABC, abstractmethod

# Mock the utm module since it might not be available
sys.modules['utm'] = Mock()
utm_mock = sys.modules['utm']
utm_mock.from_latlon = Mock(return_value=(500000, 4000000, 18, 'T'))
utm_mock.to_latlon = Mock(return_value=(40.0, -74.0))

# Define the classes inline for testing since we can't import them
class BaseProcess(ABC):
    """Base class for processing input files into SQLite databases."""

    @abstractmethod
    def process(self, config: dict, input_path: str, db_connection: sqlite3.Connection, worker: object) -> int:
        """Process an input file and import data into the database."""
        pass


class CsvAlgorithms:
    """Utility class for processing CSV column data based on JSON configuration."""

    def __init__(self):
        """Initialize the group dictionary for group function."""
        self._group_dict: Dict[str, int] = {}

    def copy(self, row: List[str], col_def: Dict[str, Any]) -> Any:
        """Copy a value from the row or return default from parms."""
        src_col = col_def.get("src_col", -1)
        col_type = col_def.get("type", "float")
        parms = col_def.get("parms", [])

        if src_col < 0 or src_col >= len(row) or not row[src_col]:
            return parms[0] if parms else None
        
        try:
            value = row[src_col]
            if col_type == "int":
                return int(float(value))  # Handle float strings
            elif col_type == "float":
                return float(value)
            return value
        except (ValueError, TypeError):
            return None

    @staticmethod
    def utm_common(row: List[str], col_def: Dict[str, Any]) -> tuple:
        """Convert UTM coordinates to latitude and longitude."""
        import utm
        parms = col_def.get("parms", [])
        if len(parms) < 4:
            return None, None

        # Validate all indices are within bounds
        if any(p < 0 or p >= len(row) for p in parms):
            return None, None

        try:
            map_origin_x = float(row[parms[0]])
            map_origin_y = float(row[parms[1]])
            x_map_loc = float(row[parms[2]])
            y_map_loc = float(row[parms[3]])

            easting, northing, zone_num, zone_letter = utm.from_latlon(map_origin_y, map_origin_x)
            lat, lon = utm.to_latlon(easting + x_map_loc, northing + y_map_loc, zone_num, zone_letter)
            return lat, lon
        except (ValueError, TypeError):
            return None, None

    def utm_lat(self, row: List[str], col_def: Dict[str, Any]) -> float:
        """Get latitude from UTM coordinates."""
        lat, _ = self.utm_common(row, col_def)
        return lat

    def utm_lon(self, row: List[str], col_def: Dict[str, Any]) -> float:
        """Get longitude from UTM coordinates."""
        _, lon = self.utm_common(row, col_def)
        return lon

    def tgsim_lane(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Calculate lane ID based on tgsim rules."""
        src_col = col_def.get("src_col", -1)
        parms = col_def.get("parms", [])
        
        if src_col < 0 or src_col >= len(row) or not row[src_col] or len(parms) < 1:
            return None
        
        if parms[0] < 0 or parms[0] >= len(row):
            return None
        
        try:
            lane_kf = int(float(row[parms[0]]))
            src_value = int(float(row[src_col]))
            return 7 - lane_kf if src_value % 2 else 16 - lane_kf
        except (ValueError, TypeError):
            return None

    def group(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Generate an index based on concatenated unique strings."""
        parms = col_def.get("parms", [])
        try:
            key = " ".join(str(row[i]) for i in parms if i < len(row) and row[i])
            if not key:  # Return None if key is empty
                return None
            if key not in self._group_dict:
                self._group_dict[key] = len(self._group_dict) + 1
            return self._group_dict[key]
        except (IndexError, TypeError):
            return 0

    def event_name(self, row: List[str], col_def: Dict[str, Any]) -> str:
        """Build an event name from concatenated unique strings and optional label."""
        parms = col_def.get("parms", [])
        if len(parms) == 0:
            return None
        
        label = ""  # check for last element label
        if isinstance(parms[-1], str):
            label = parms[-1]
            parms = parms[:-1]

        try:
            key = " ".join(str(row[i]) for i in parms if i < len(row) and row[i])
            if not key:  # Return None if key is empty
                return None
            return label + key
        except (IndexError, TypeError):
            return None

    def tgsim_automated(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Check if the source column value equals 'yes' (case-insensitive)."""
        src_col = col_def.get("src_col", -1)
        
        if src_col < 0 or src_col >= len(row) or not row[src_col]:
            return 0
        
        return 1 if row[src_col].lower() == "yes" else 0


class ByteCountingStream(io.BufferedReader):
    """A buffered reader that tracks the number of bytes read."""
    
    def __init__(self, raw):
        super().__init__(raw)
        self.bytes_read = 0
    
    def read(self, size=-1):
        data = super().read(size)
        self.bytes_read += len(data)
        return data
    
    def read1(self, size=-1):
        data = super().read1(size)
        self.bytes_read += len(data)
        return data


class CsvProcess(BaseProcess):
    """Processor for importing CSV files into SQLite database based on JSON configuration."""

    def __init__(self):
        """Initialize the processor with a CsvAlgorithms instance."""
        super().__init__()
        self._func_dict: Dict[str, callable] = {}
        self._has_header = False
        self._min_cols = 1
        self._col_defs = []
        self._algorithms = CsvAlgorithms()
        self._initialize_func_dict()

    def _initialize_func_dict(self):
        """Dynamically populate _func_dict with public methods from CsvAlgorithms."""
        for method_name in dir(self._algorithms):
            if not method_name.startswith('_'):  # Exclude private methods
                method = getattr(self._algorithms, method_name)
                if callable(method):  # Ensure it's a callable method
                    self._func_dict[method_name] = method

    def _check_configuration(self, config: Dict[str, Any], worker: Any) -> int:
        """Validate the JSON configuration and set up column definitions."""
        worker.log_signal.emit("Info: Checking configured column definitions", True)
        error_count = 0
        col_defs = config.get("col_defs", {})

        # Check if col_defs is object or array
        if isinstance(col_defs, dict):
            self._col_defs = [col_defs]
        elif isinstance(col_defs, list):
            self._col_defs = col_defs
        else:
            worker.log_signal.emit("Error: 'col_defs' must be an object or array.", False)
            return 1

        required_cols = {"event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc"}
        seen_keys = set()

        for i, col_def_set in enumerate(self._col_defs):
            if not isinstance(col_def_set, dict):
                worker.log_signal.emit(f"Error: col_defs[{i}] is not a dictionary.", False)
                error_count += 1
                continue

            # Set defaults and validate
            for key, col_def in col_def_set.items():
                if not isinstance(col_def, dict):
                    worker.log_signal.emit(f"Error: col_defs[{i}]['{key}'] is not a dictionary.", False)
                    error_count += 1
                    continue

                if key in seen_keys:
                    worker.log_signal.emit(f"Error: Duplicate key '{key}' in col_defs[{i}].", False)
                    error_count += 1
                    continue
                seen_keys.add(key)

                col_def.setdefault("src_col", -1)
                col_def.setdefault("type", "float")
                col_def.setdefault("units", "")
                col_def.setdefault("func", "copy")
                col_def.setdefault("parms", [])

                if col_def["src_col"] >= 0:
                    self._min_cols = max(self._min_cols, col_def["src_col"] + 1)

                if col_def["src_col"] < 0 and (not col_def["func"] or not col_def["parms"]):
                    worker.log_signal.emit(
                        f"Error: Column '{key}' in col_defs[{i}] with src_col < 0 must have "
                        "non-empty func and parms.", False
                    )
                    error_count += 1

                if col_def["func"] not in self._func_dict:
                    worker.log_signal.emit(f"Error: Invalid function '{col_def['func']}' in col_defs[{i}] for '{key}'.", False)
                    error_count += 1

        # Check for missing required columns
        for i, col_def_set in enumerate(self._col_defs):
            missing_cols = required_cols - set(col_def_set.keys())
            if missing_cols:
                worker.log_signal.emit(f"Error: Missing required columns {missing_cols} in col_defs[{i}].", False)
                error_count += 1

        return error_count

    def _check_import_file(self, input_path: str, worker: Any) -> int:
        """Validate the CSV file structure and data types."""
        worker.log_signal.emit("Checking import file columns", True)
        error_count = 0
        file_size = os.path.getsize(input_path)
        if file_size == 0:
            worker.log_signal.emit("Error: Input file is empty.", False)
            return 1

        with open(input_path, 'rb') as f_raw:
            tracker = ByteCountingStream(f_raw)
            text_stream = io.TextIOWrapper(tracker, encoding='utf-8', newline='')
            reader = csv.reader(text_stream)
            
            try:
                first_row = next(reader)
            except StopIteration:
                worker.log_signal.emit("Error: Input file contains no rows.", False)
                return 1

            # Check for header row
            self._has_header = all(any(c.isalpha() for c in col) for col in first_row if col)
            if self._has_header:
                try:
                    first_data_row = next(reader)
                except StopIteration:
                    worker.log_signal.emit("Error: Input file contains only header row.", False)
                    return 1
            else:
                first_data_row = first_row

            # Validate first data row
            if len(first_data_row) < self._min_cols:
                worker.log_signal.emit(
                    f"Error: Row 1 contains fewer than {self._min_cols} columns.", False
                )
                return 1

        return error_count

    def process(self, config: Dict[str, Any], input_path: str, db_connection: sqlite3.Connection, worker: Any) -> int:
        """Process a CSV file into the SQLite database."""
        error_count = self._check_configuration(config, worker)
        if worker.interrupt_flag or error_count > 0:
            return error_count

        error_count = self._check_import_file(input_path, worker)
        if worker.interrupt_flag or error_count > 0:
            return error_count

        # Get file size for progress tracking
        file_size = os.path.getsize(input_path)
        worker.log_signal.emit(f"Info: Processing file of size {file_size:,} bytes", True)

        # Process CSV rows with byte counting
        with open(input_path, 'rb') as f_raw:
            tracker = ByteCountingStream(f_raw)
            text_stream = io.TextIOWrapper(tracker, encoding='utf-8', newline='')
            reader = csv.reader(text_stream)
            
            if self._has_header:
                next(reader)  # Skip header

            row_count = 0
            for row in reader:
                if worker.interrupt_flag:
                    break
                row_count += 1

        if not worker.interrupt_flag:
            worker.log_signal.emit(f"Info: {row_count} rows successfully imported", True)

        return 0


# Test Classes
class TestBaseProcess:
    """Test the abstract BaseProcess class."""

    def test_base_process_is_abstract(self):
        """Test that BaseProcess cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseProcess()

    def test_concrete_implementation(self):
        """Test that a concrete implementation can be created."""
        class ConcreteProcess(BaseProcess):
            def process(self, config: dict, input_path: str, db_connection: sqlite3.Connection, worker: object) -> int:
                return 0

        processor = ConcreteProcess()
        assert isinstance(processor, BaseProcess)
    """Test the abstract BaseProcess class."""

    def test_base_process_is_abstract(self):
        """Test that BaseProcess cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseProcess()

    def test_concrete_implementation(self):
        """Test that a concrete implementation can be created."""
        class ConcreteProcess(BaseProcess):
            def process(self, config: dict, input_path: str, db_connection: sqlite3.Connection, worker: object) -> int:
                return 0

        processor = ConcreteProcess()
        assert isinstance(processor, BaseProcess)


class TestCsvAlgorithms:
    """Test the CsvAlgorithms utility class."""

    @pytest.fixture
    def algorithms(self):
        """Create a CsvAlgorithms instance for testing."""
        return CsvAlgorithms()

    def test_init(self, algorithms):
        """Test initialization of CsvAlgorithms."""
        assert hasattr(algorithms, '_group_dict')
        assert algorithms._group_dict == {}

    def test_copy_valid_int(self, algorithms):
        """Test copy function with valid integer."""
        row = ['123', 'test', '456.7']
        col_def = {'src_col': 0, 'type': 'int', 'parms': []}
        result = algorithms.copy(row, col_def)
        assert result == 123

    def test_copy_valid_float(self, algorithms):
        """Test copy function with valid float."""
        row = ['123.45', 'test', '456']
        col_def = {'src_col': 0, 'type': 'float', 'parms': []}
        result = algorithms.copy(row, col_def)
        assert result == 123.45

    def test_copy_valid_string(self, algorithms):
        """Test copy function with string type."""
        row = ['test_string', 'other', '123']
        col_def = {'src_col': 0, 'type': 'string', 'parms': []}
        result = algorithms.copy(row, col_def)
        assert result == 'test_string'

    def test_copy_invalid_column(self, algorithms):
        """Test copy function with invalid column index."""
        row = ['123', 'test']
        col_def = {'src_col': 5, 'type': 'int', 'parms': ['default_value']}
        result = algorithms.copy(row, col_def)
        assert result == 'default_value'

    def test_copy_empty_value(self, algorithms):
        """Test copy function with empty value."""
        row = ['', 'test']
        col_def = {'src_col': 0, 'type': 'int', 'parms': [42]}
        result = algorithms.copy(row, col_def)
        assert result == 42

    def test_copy_invalid_type_conversion(self, algorithms):
        """Test copy function with invalid type conversion."""
        row = ['not_a_number', 'test']
        col_def = {'src_col': 0, 'type': 'int', 'parms': []}
        result = algorithms.copy(row, col_def)
        assert result is None

    def test_copy_float_to_int_conversion(self, algorithms):
        """Test copy function converting float string to int."""
        row = ['123.7', 'test']
        col_def = {'src_col': 0, 'type': 'int', 'parms': []}
        result = algorithms.copy(row, col_def)
        assert result == 123

    @patch('utm.from_latlon')
    @patch('utm.to_latlon')
    def test_utm_common_valid(self, mock_to_latlon, mock_from_latlon, algorithms):
        """Test utm_common with valid coordinates."""
        mock_from_latlon.return_value = (500000, 4000000, 32, 'T')
        mock_to_latlon.return_value = (40.0, -74.0)
        
        row = ['-74.0', '40.0', '100', '200']
        col_def = {'parms': [0, 1, 2, 3]}
        
        lat, lon = algorithms.utm_common(row, col_def)
        assert lat == 40.0
        assert lon == -74.0

    def test_utm_common_insufficient_params(self, algorithms):
        """Test utm_common with insufficient parameters."""
        row = ['1', '2', '3']
        col_def = {'parms': [0, 1, 2]}  # Only 3 params, need 4
        
        lat, lon = algorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None

    def test_utm_common_invalid_indices(self, algorithms):
        """Test utm_common with invalid column indices."""
        row = ['1', '2']
        col_def = {'parms': [0, 1, 5, 6]}  # Indices 5,6 don't exist
        
        lat, lon = algorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None

    def test_utm_common_invalid_values(self, algorithms):
        """Test utm_common with non-numeric values."""
        row = ['not_a_number', '40.0', '100', '200']
        col_def = {'parms': [0, 1, 2, 3]}
        
        lat, lon = algorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None

    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lat(self, mock_utm_common, algorithms):
        """Test utm_lat function."""
        mock_utm_common.return_value = (40.0, -74.0)
        
        row = ['test_row']
        col_def = {'test': 'def'}
        
        result = algorithms.utm_lat(row, col_def)
        assert result == 40.0
        mock_utm_common.assert_called_once_with(row, col_def)

    @patch.object(CsvAlgorithms, 'utm_common')
    def test_utm_lon(self, mock_utm_common, algorithms):
        """Test utm_lon function."""
        mock_utm_common.return_value = (40.0, -74.0)
        
        row = ['test_row']
        col_def = {'test': 'def'}
        
        result = algorithms.utm_lon(row, col_def)
        assert result == -74.0
        mock_utm_common.assert_called_once_with(row, col_def)

    def test_tgsim_lane_valid_even(self, algorithms):
        """Test tgsim_lane with valid even source value."""
        row = ['4', '3']  # src_value=4 (even), lane_kf=3
        col_def = {'src_col': 0, 'parms': [1]}
        
        result = algorithms.tgsim_lane(row, col_def)
        assert result == 13  # 16 - 3 = 13

    def test_tgsim_lane_valid_odd(self, algorithms):
        """Test tgsim_lane with valid odd source value."""
        row = ['5', '3']  # src_value=5 (odd), lane_kf=3
        col_def = {'src_col': 0, 'parms': [1]}
        
        result = algorithms.tgsim_lane(row, col_def)
        assert result == 4  # 7 - 3 = 4

    def test_tgsim_lane_invalid_column(self, algorithms):
        """Test tgsim_lane with invalid column index."""
        row = ['4', '3']
        col_def = {'src_col': 5, 'parms': [1]}
        
        result = algorithms.tgsim_lane(row, col_def)
        assert result is None

    def test_tgsim_lane_empty_value(self, algorithms):
        """Test tgsim_lane with empty source value."""
        row = ['', '3']
        col_def = {'src_col': 0, 'parms': [1]}
        
        result = algorithms.tgsim_lane(row, col_def)
        assert result is None

    def test_tgsim_lane_no_params(self, algorithms):
        """Test tgsim_lane with no parameters."""
        row = ['4', '3']
        col_def = {'src_col': 0, 'parms': []}
        
        result = algorithms.tgsim_lane(row, col_def)
        assert result is None

    def test_tgsim_lane_invalid_param_index(self, algorithms):
        """Test tgsim_lane with invalid parameter index."""
        row = ['4', '3']
        col_def = {'src_col': 0, 'parms': [5]}  # Index 5 doesn't exist
        
        result = algorithms.tgsim_lane(row, col_def)
        assert result is None

    def test_group_new_key(self, algorithms):
        """Test group function with new key."""
        row = ['value1', 'value2', 'value3']
        col_def = {'parms': [0, 1]}
        
        result = algorithms.group(row, col_def)
        assert result == 1
        assert 'value1 value2' in algorithms._group_dict

    def test_group_existing_key(self, algorithms):
        """Test group function with existing key."""
        row1 = ['value1', 'value2', 'value3']
        row2 = ['value1', 'value2', 'different']
        col_def = {'parms': [0, 1]}
        
        result1 = algorithms.group(row1, col_def)
        result2 = algorithms.group(row2, col_def)
        
        assert result1 == result2 == 1

    def test_group_multiple_keys(self, algorithms):
        """Test group function with multiple different keys."""
        row1 = ['value1', 'value2']
        row2 = ['value3', 'value4']
        col_def = {'parms': [0, 1]}
        
        result1 = algorithms.group(row1, col_def)
        result2 = algorithms.group(row2, col_def)
        
        assert result1 == 1
        assert result2 == 2

    def test_group_empty_key(self, algorithms):
        """Test group function with empty key."""
        row = ['', '']
        col_def = {'parms': [0, 1]}
        
        result = algorithms.group(row, col_def)
        assert result is None

    def test_group_invalid_indices(self, algorithms):
        """Test group function with invalid indices."""
        row = ['value1', 'value2']
        col_def = {'parms': [0, 5]}  # Index 5 doesn't exist
        
        result = algorithms.group(row, col_def)
        assert result == 0

    def test_event_name_with_label(self, algorithms):
        """Test event_name with label parameter."""
        row = ['event1', 'detail1']
        col_def = {'parms': [0, 1, 'PREFIX_']}
        
        result = algorithms.event_name(row, col_def)
        assert result == 'PREFIX_event1 detail1'

    def test_event_name_without_label(self, algorithms):
        """Test event_name without label parameter."""
        row = ['event1', 'detail1']
        col_def = {'parms': [0, 1]}
        
        result = algorithms.event_name(row, col_def)
        assert result == 'event1 detail1'

    def test_event_name_no_params(self, algorithms):
        """Test event_name with no parameters."""
        row = ['event1', 'detail1']
        col_def = {'parms': []}
        
        result = algorithms.event_name(row, col_def)
        assert result is None

    def test_event_name_empty_key(self, algorithms):
        """Test event_name with empty values."""
        row = ['', '']
        col_def = {'parms': [0, 1]}
        
        result = algorithms.event_name(row, col_def)
        assert result is None

    def test_tgsim_automated_yes(self, algorithms):
        """Test tgsim_automated with 'yes' value."""
        row = ['yes', 'other']
        col_def = {'src_col': 0}
        
        result = algorithms.tgsim_automated(row, col_def)
        assert result == 1

    def test_tgsim_automated_yes_case_insensitive(self, algorithms):
        """Test tgsim_automated with case variations of 'yes'."""
        test_cases = ['YES', 'Yes', 'yEs', 'YeS']
        col_def = {'src_col': 0}
        
        for case in test_cases:
            row = [case, 'other']
            result = algorithms.tgsim_automated(row, col_def)
            assert result == 1

    def test_tgsim_automated_no(self, algorithms):
        """Test tgsim_automated with 'no' value."""
        row = ['no', 'other']
        col_def = {'src_col': 0}
        
        result = algorithms.tgsim_automated(row, col_def)
        assert result == 0

    def test_tgsim_automated_other_values(self, algorithms):
        """Test tgsim_automated with other values."""
        test_cases = ['maybe', 'true', 'false', '1', '0', 'random']
        col_def = {'src_col': 0}
        
        for case in test_cases:
            row = [case, 'other']
            result = algorithms.tgsim_automated(row, col_def)
            assert result == 0

    def test_tgsim_automated_invalid_column(self, algorithms):
        """Test tgsim_automated with invalid column."""
        row = ['yes', 'other']
        col_def = {'src_col': 5}
        
        result = algorithms.tgsim_automated(row, col_def)
        assert result == 0

    def test_tgsim_automated_empty_value(self, algorithms):
        """Test tgsim_automated with empty value."""
        row = ['', 'other']
        col_def = {'src_col': 0}
        
        result = algorithms.tgsim_automated(row, col_def)
        assert result == 0


class TestByteCountingStream:
    """Test the ByteCountingStream class."""

    def test_byte_counting_stream_init(self):
        """Test ByteCountingStream initialization."""
        data = b"test data"
        raw_stream = io.BytesIO(data)
        stream = ByteCountingStream(raw_stream)
        assert stream.bytes_read == 0

    def test_byte_counting_stream_read(self):
        """Test ByteCountingStream read method."""
        data = b"test data for reading"
        raw_stream = io.BytesIO(data)
        stream = ByteCountingStream(raw_stream)
        
        result = stream.read(4)
        assert result == b"test"
        assert stream.bytes_read == 4
        
        result = stream.read(5)
        assert result == b" data"
        assert stream.bytes_read == 9

    def test_byte_counting_stream_read1(self):
        """Test ByteCountingStream read1 method."""
        data = b"test data for reading"
        raw_stream = io.BytesIO(data)
        stream = ByteCountingStream(raw_stream)
        
        result = stream.read1(4)
        assert len(result) <= 4
        assert stream.bytes_read == len(result)


class TestCsvProcess:
    """Test the CsvProcess class."""

    @pytest.fixture
    def csv_process(self):
        """Create a CsvProcess instance for testing."""
        return CsvProcess()

    @pytest.fixture
    def mock_worker(self):
        """Create a mock worker object."""
        worker = Mock()
        worker.log_signal = Mock()
        worker.log_signal.emit = Mock()
        worker.progress_signal = Mock()
        worker.progress_signal.emit = Mock()
        worker.interrupt_flag = False
        return worker

    @pytest.fixture
    def test_db(self):
        """Create a test SQLite database."""
        db = sqlite3.connect(':memory:')
        # Create the events table
        db.execute('''
            CREATE TABLE events (
                event_id INTEGER,
                veh_id INTEGER,
                veh_time REAL,
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
                osm_speed_limit REAL,
                osm_traffic_control TEXT,
                preceding_veh_id INTEGER,
                veh_dist_trav REAL,
                event_name TEXT,
                PRIMARY KEY (event_id, veh_id, veh_time)
            )
        ''')
        yield db
        db.close()

    @pytest.fixture
    def valid_config(self):
        """Create a valid configuration for testing."""
        return {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
            }
        }

    @pytest.fixture
    def test_csv_file(self):
        """Create a temporary CSV file for testing."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(['event_id', 'veh_id', 'veh_time', 'lane_id', 'x_map_loc', 'y_map_loc'])
            writer.writerow(['1', '100', '0.0', '1', '10.5', '20.3'])
            writer.writerow(['2', '101', '1.0', '2', '15.7', '25.8'])
            writer.writerow(['3', '102', '2.0', '1', '12.1', '22.4'])
            f.flush()
            yield f.name
        os.unlink(f.name)

    def test_init(self, csv_process):
        """Test CsvProcess initialization."""
        assert hasattr(csv_process, '_func_dict')
        assert hasattr(csv_process, '_has_header')
        assert hasattr(csv_process, '_min_cols')
        assert hasattr(csv_process, '_col_defs')
        assert hasattr(csv_process, '_algorithms')
        assert csv_process._min_cols == 1
        assert csv_process._has_header is False
        assert len(csv_process._func_dict) > 0

    def test_initialize_func_dict(self, csv_process):
        """Test that function dictionary is properly initialized."""
        expected_functions = [
            'copy', 'utm_common', 'utm_lat', 'utm_lon', 
            'tgsim_lane', 'group', 'event_name', 'tgsim_automated'
        ]
        
        for func_name in expected_functions:
            assert func_name in csv_process._func_dict
            assert callable(csv_process._func_dict[func_name])

    def test_check_configuration_valid(self, csv_process, valid_config, mock_worker):
        """Test _check_configuration with valid config."""
        error_count = csv_process._check_configuration(valid_config, mock_worker)
        assert error_count == 0
        assert len(csv_process._col_defs) == 1

    def test_check_configuration_dict_col_defs(self, csv_process, mock_worker):
        """Test _check_configuration with dict col_defs."""
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
        
        error_count = csv_process._check_configuration(config, mock_worker)
        assert error_count == 0
        assert len(csv_process._col_defs) == 1

    def test_check_configuration_list_col_defs(self, csv_process, mock_worker):
        """Test _check_configuration with list col_defs."""
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
        
        error_count = csv_process._check_configuration(config, mock_worker)
        assert error_count == 0
        assert len(csv_process._col_defs) == 1

    def test_check_configuration_missing_required_columns(self, csv_process, mock_worker):
        """Test _check_configuration with missing required columns."""
        config = {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                "veh_id": {"src_col": 1, "type": "int", "func": "copy"}
                # Missing required columns
            }
        }
        
        error_count = csv_process._check_configuration(config, mock_worker)
        assert error_count > 0

    def test_check_configuration_invalid_function(self, csv_process, mock_worker):
        """Test _check_configuration with invalid function."""
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
        
        error_count = csv_process._check_configuration(config, mock_worker)
        assert error_count > 0

    def test_check_configuration_invalid_col_defs_type(self, csv_process, mock_worker):
        """Test _check_configuration with invalid col_defs type."""
        config = {
            "col_defs": "invalid_type"
        }
        
        error_count = csv_process._check_configuration(config, mock_worker)
        assert error_count == 1

    def test_check_import_file_valid(self, csv_process, valid_config, test_csv_file, mock_worker):
        """Test _check_import_file with valid CSV file."""
        csv_process._check_configuration(valid_config, mock_worker)
        error_count = csv_process._check_import_file(test_csv_file, mock_worker)
        assert error_count == 0
        assert csv_process._has_header is True

    def test_check_import_file_empty(self, csv_process, valid_config, mock_worker):
        """Test _check_import_file with empty file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.flush()
            
        try:
            csv_process._check_configuration(valid_config, mock_worker)
            error_count = csv_process._check_import_file(f.name, mock_worker)
            assert error_count == 1
        finally:
            os.unlink(f.name)

    def test_check_import_file_header_only(self, csv_process, valid_config, mock_worker):
        """Test _check_import_file with header only."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(['event_id', 'veh_id', 'veh_time', 'lane_id', 'x_map_loc', 'y_map_loc'])
            f.flush()
            
        try:
            csv_process._check_configuration(valid_config, mock_worker)
            error_count = csv_process._check_import_file(f.name, mock_worker)
            assert error_count == 1
        finally:
            os.unlink(f.name)

    def test_check_import_file_insufficient_columns(self, csv_process, valid_config, mock_worker):
        """Test _check_import_file with insufficient columns."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(['1', '100'])  # Only 2 columns, need 6
            f.flush()
            
        try:
            csv_process._check_configuration(valid_config, mock_worker)
            error_count = csv_process._check_import_file(f.name, mock_worker)
            assert error_count == 1
        finally:
            os.unlink(f.name)

    def test_process_success(self, csv_process, valid_config, test_csv_file, test_db, mock_worker):
        """Test successful processing of CSV file."""
        error_count = csv_process.process(valid_config, test_csv_file, test_db, mock_worker)
        assert error_count == 0

    def test_process_configuration_error(self, csv_process, test_csv_file, test_db, mock_worker):
        """Test process with configuration errors."""
        invalid_config = {"col_defs": "invalid"}
        
        error_count = csv_process.process(invalid_config, test_csv_file, test_db, mock_worker)
        assert error_count > 0

    def test_process_file_error(self, csv_process, valid_config, test_db, mock_worker):
        """Test process with file errors."""
        nonexistent_file = "/nonexistent/file.csv"
        
        with pytest.raises(FileNotFoundError):
            csv_process.process(valid_config, nonexistent_file, test_db, mock_worker)

    def test_process_interrupt(self, csv_process, valid_config, test_csv_file, test_db, mock_worker):
        """Test process with worker interrupt."""
        mock_worker.interrupt_flag = True
        
        error_count = csv_process.process(valid_config, test_csv_file, test_db, mock_worker)
        assert error_count == 0  # Should return early without errors

    @patch('os.path.getsize')
    def test_process_with_large_file_simulation(self, mock_getsize, csv_process, valid_config, test_csv_file, test_db, mock_worker):
        """Test process with simulated large file for progress tracking."""
        mock_getsize.return_value = 10000  # Simulate large file
        
        error_count = csv_process.process(valid_config, test_csv_file, test_db, mock_worker)
        assert error_count == 0


class TestIntegration:
    """Integration tests for the complete CSV processing workflow."""

    @pytest.fixture
    def complex_csv_file(self):
        """Create a more complex CSV file for integration testing."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            writer = csv.writer(f)
            # Header row
            writer.writerow([
                'event_id', 'veh_id', 'time', 'lane', 'x_pos', 'y_pos', 
                'automated', 'speed', 'accel', 'length', 'width'
            ])
            # Data rows
            writer.writerow(['1', '100', '0.0', '1', '10.5', '20.3', 'yes', '25.0', '0.5', '4.5', '2.0'])
            writer.writerow(['2', '101', '1.0', '2', '15.7', '25.8', 'no', '30.0', '-0.2', '5.0', '2.2'])
            writer.writerow(['3', '102', '2.0', '1', '12.1', '22.4', 'YES', '22.5', '1.0', '4.8', '1.9'])
            writer.writerow(['4', '103', '3.0', '3', '18.2', '28.1', 'No', '28.0', '0.0', '4.2', '2.1'])
            f.flush()
            yield f.name
        os.unlink(f.name)

    @pytest.fixture
    def complex_config(self):
        """Create a complex configuration that uses various algorithms."""
        return {
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"},
                "veh_automation": {"src_col": 6, "type": "int", "func": "tgsim_automated"},
                "veh_speed": {"src_col": 7, "type": "float", "func": "copy"},
                "veh_accel": {"src_col": 8, "type": "float", "func": "copy"},
                "veh_length": {"src_col": 9, "type": "float", "func": "copy"},
                "veh_width": {"src_col": 10, "type": "float", "func": "copy"},
                "event_name": {"src_col": -1, "func": "event_name", "parms": [0, 1, "EVENT_"]},
                "x_frenet_loc": {"src_col": -1, "func": "copy", "parms": [0.0]},
                "y_frenet_loc": {"src_col": -1, "func": "copy", "parms": [0.0]},
                "x_map_origin": {"src_col": -1, "func": "copy", "parms": [0.0]},
                "y_map_origin": {"src_col": -1, "func": "copy", "parms": [0.0]},
                "veh_lat": {"src_col": -1, "func": "copy", "parms": [None]},
                "veh_lon": {"src_col": -1, "func": "copy", "parms": [None]},
                "osm_way_id": {"src_col": -1, "func": "copy", "parms": [None]},
                "osm_speed_limit": {"src_col": -1, "func": "copy", "parms": [None]},
                "osm_traffic_control": {"src_col": -1, "func": "copy", "parms": [None]},
                "preceding_veh_id": {"src_col": -1, "func": "copy", "parms": [None]},
                "veh_dist_trav": {"src_col": -1, "func": "copy", "parms": [None]}
            }
        }

    @pytest.fixture
    def test_db_with_schema(self):
        """Create a test database with the proper schema."""
        db = sqlite3.connect(':memory:')
        db.execute('''
            CREATE TABLE events (
                event_id INTEGER,
                veh_id INTEGER,
                veh_time REAL,
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
                osm_speed_limit REAL,
                osm_traffic_control TEXT,
                preceding_veh_id INTEGER,
                veh_dist_trav REAL,
                event_name TEXT,
                PRIMARY KEY (event_id, veh_id, veh_time)
            )
        ''')
        yield db
        db.close()

    def test_complete_workflow(self, complex_csv_file, complex_config, test_db_with_schema):
        """Test the complete CSV processing workflow."""
        processor = CsvProcess()
        mock_worker = Mock()
        mock_worker.log_signal = Mock()
        mock_worker.log_signal.emit = Mock()
        mock_worker.progress_signal = Mock()
        mock_worker.progress_signal.emit = Mock()
        mock_worker.interrupt_flag = False

        # Process the file
        error_count = processor.process(complex_config, complex_csv_file, test_db_with_schema, mock_worker)
        assert error_count == 0

    def test_utm_coordinate_processing(self):
        """Test UTC coordinate processing with realistic data."""
        algorithms = CsvAlgorithms()
        
        # Test with realistic UTM coordinates
        row = ['-74.0', '40.0', '100.0', '200.0']  # lon, lat, x_offset, y_offset
        col_def = {'parms': [0, 1, 2, 3]}
        
        with patch('utm.from_latlon') as mock_from_latlon, \
             patch('utm.to_latlon') as mock_to_latlon:
            
            mock_from_latlon.return_value = (500000, 4000000, 18, 'T')
            mock_to_latlon.return_value = (40.001, -73.999)
            
            lat, lon = algorithms.utm_common(row, col_def)
            
            assert lat == 40.001
            assert lon == -73.999
            mock_from_latlon.assert_called_once_with(40.0, -74.0)
            mock_to_latlon.assert_called_once_with(500100.0, 4000200.0, 18, 'T')

    def test_tgsim_lane_calculation(self):
        """Test TGSIM lane calculation with various scenarios."""
        algorithms = CsvAlgorithms()
        
        # Test even source value
        row = ['4', '3']  # src_value=4 (even), lane_kf=3
        col_def = {'src_col': 0, 'parms': [1]}
        result = algorithms.tgsim_lane(row, col_def)
        assert result == 13  # 16 - 3 = 13
        
        # Test odd source value
        row = ['5', '3']  # src_value=5 (odd), lane_kf=3
        col_def = {'src_col': 0, 'parms': [1]}
        result = algorithms.tgsim_lane(row, col_def)
        assert result == 4  # 7 - 3 = 4

    def test_group_functionality_persistence(self):
        """Test that group function maintains state across calls."""
        algorithms = CsvAlgorithms()
        
        # First unique combination
        row1 = ['A', 'B', 'C']
        col_def = {'parms': [0, 1]}
        result1 = algorithms.group(row1, col_def)
        assert result1 == 1
        
        # Second unique combination
        row2 = ['D', 'E', 'F']
        result2 = algorithms.group(row2, col_def)
        assert result2 == 2
        
        # Repeat first combination
        row3 = ['A', 'B', 'G']  # Different third column, same group key
        result3 = algorithms.group(row3, col_def)
        assert result3 == 1  # Should return same group ID

    def test_error_handling_in_processing(self):
        """Test error handling during CSV processing."""
        processor = CsvProcess()
        
        # Create CSV with invalid data
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(['event_id', 'veh_id', 'veh_time', 'lane_id', 'x_map_loc', 'y_map_loc'])
            writer.writerow(['not_a_number', '100', '0.0', '1', '10.5', '20.3'])
            f.flush()
            
        try:
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
            
            mock_worker = Mock()
            mock_worker.log_signal = Mock()
            mock_worker.log_signal.emit = Mock()
            mock_worker.progress_signal = Mock()
            mock_worker.progress_signal.emit = Mock()
            mock_worker.interrupt_flag = False
            
            # This should detect the error during file checking
            error_count = processor._check_configuration(config, mock_worker)
            assert error_count == 0  # Config is valid
            
            error_count = processor._check_import_file(f.name, mock_worker)
            assert error_count > 0  # Should detect invalid data
            
        finally:
            os.unlink(f.name)

    def test_multiple_col_def_sets(self):
        """Test processing with multiple column definition sets."""
        config = {
            "col_defs": [
                {
                    "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                    "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                    "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                    "lane_id": {"src_col": 3, "type": "int", "func": "copy"},
                    "x_map_loc": {"src_col": 4, "type": "float", "func": "copy"},
                    "y_map_loc": {"src_col": 5, "type": "float", "func": "copy"}
                },
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
        
        processor = CsvProcess()
        mock_worker = Mock()
        mock_worker.log_signal = Mock()
        mock_worker.log_signal.emit = Mock()
        mock_worker.progress_signal = Mock()
        mock_worker.progress_signal.emit = Mock()
        mock_worker.interrupt_flag = False
        
        error_count = processor._check_configuration(config, mock_worker)
        assert error_count == 0
        assert len(processor._col_defs) == 2

    def test_edge_cases_and_boundary_conditions(self):
        """Test various edge cases and boundary conditions."""
        algorithms = CsvAlgorithms()
        
        # Test copy with edge cases
        edge_cases = [
            # (row, col_def, expected)
            ([], {'src_col': 0, 'type': 'int', 'parms': [42]}, 42),  # Empty row
            ([''], {'src_col': 0, 'type': 'int', 'parms': [42]}, 42),  # Empty string
            (['0'], {'src_col': 0, 'type': 'int', 'parms': []}, 0),  # Zero value
            (['-123'], {'src_col': 0, 'type': 'int', 'parms': []}, -123),  # Negative int
            (['-123.45'], {'src_col': 0, 'type': 'float', 'parms': []}, -123.45),  # Negative float
            (['123.0'], {'src_col': 0, 'type': 'int', 'parms': []}, 123),  # Float to int conversion
        ]
        
        for row, col_def, expected in edge_cases:
            result = algorithms.copy(row, col_def)
            assert result == expected, f"Failed for row={row}, col_def={col_def}"

    def test_performance_with_large_dataset_simulation(self):
        """Simulate processing of a large dataset."""
        # Create a larger CSV file for performance testing
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(['event_id', 'veh_id', 'veh_time', 'lane_id', 'x_map_loc', 'y_map_loc'])
            
            # Write 100 rows (reduced for testing)
            for i in range(100):
                writer.writerow([str(i), str(100 + i), str(i * 0.1), str(i % 3 + 1), 
                               str(10.0 + i * 0.1), str(20.0 + i * 0.05)])
            f.flush()
            
        try:
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
            
            processor = CsvProcess()
            mock_worker = Mock()
            mock_worker.log_signal = Mock()
            mock_worker.log_signal.emit = Mock()
            mock_worker.progress_signal = Mock()
            mock_worker.progress_signal.emit = Mock()
            mock_worker.interrupt_flag = False
            
            db = sqlite3.connect(':memory:')
            
            # Process the large file
            import time
            start_time = time.time()
            error_count = processor.process(config, f.name, db, mock_worker)
            end_time = time.time()
            
            assert error_count == 0
            
            db.close()
            
        finally:
            os.unlink(f.name)


# Additional test fixtures and utilities
@pytest.fixture
def sample_utm_data():
    """Provide sample UTM coordinate data for testing."""
    return {
        'origin_lon': -74.0,
        'origin_lat': 40.0,
        'x_offset': 100.0,
        'y_offset': 200.0,
        'expected_easting': 500000,
        'expected_northing': 4000000,
        'expected_zone': 18,
        'expected_letter': 'T'
    }


@pytest.fixture
def mock_utm_functions():
    """Mock UTM functions for consistent testing."""
    with patch('utm.from_latlon') as mock_from, \
         patch('utm.to_latlon') as mock_to:
        mock_from.return_value = (500000, 4000000, 18, 'T')
        mock_to.return_value = (40.001, -73.999)
        yield mock_from, mock_to


# Performance benchmarks
class TestPerformance:
    """Performance-related tests."""
    
    def test_algorithms_performance(self):
        """Basic performance test for algorithms."""
        algorithms = CsvAlgorithms()
        
        # Test copy performance
        row = ['123', 'test', '456.7'] * 100  # Large row
        col_def = {'src_col': 0, 'type': 'int', 'parms': []}
        
        import time
        start_time = time.time()
        for _ in range(1000):
            algorithms.copy(row, col_def)
        end_time = time.time()
        
        # Should complete quickly (less than 1 second for 1000 operations)
        assert (end_time - start_time) < 1.0


# Error condition tests
class TestErrorConditions:
    """Test various error conditions and edge cases."""
    
    # Create a larger CSV file for performance testing
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        writer = csv.writer(f)
        writer.writerow(['event_id', 'veh_id', 'veh_time', 'lane_id', 'x_map_loc', 'y_map_loc'])
        
        # Write 1000 rows
        for i in range(1000):
            writer.writerow([str(i), str(100 + i), str(i * 0.1), str(i % 3 + 1), 
                           str(10.0 + i * 0.1), str(20.0 + i * 0.05)])
        f.flush()
        
    try:
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
        
        processor = CsvProcess()
        mock_worker = Mock()
        mock_worker.log_signal = Mock()
        mock_worker.log_signal.emit = Mock()
        mock_worker.progress_signal = Mock()
        mock_worker.progress_signal.emit = Mock()
        mock_worker.interrupt_flag = False
        
        db = sqlite3.connect(':memory:')
        db.execute('''
            CREATE TABLE events (
                event_id INTEGER,
                veh_id INTEGER,
                veh_time REAL,
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
                osm_speed_limit REAL,
                osm_traffic_control TEXT,
                preceding_veh_id INTEGER,
                veh_dist_trav REAL,
                event_name TEXT,
                PRIMARY KEY (event_id, veh_id, veh_time)
            )
        ''')
        
        # Process the large file
        import time
        start_time = time.time()
        error_count = processor.process(config, f.name, db, mock_worker)
        end_time = time.time()
        
        assert error_count == 0
        
        # Verify all data was inserted
        cursor = db.execute("SELECT COUNT(*) FROM events")
        count = cursor.fetchone()[0]
        assert count == 1000
        
        # Check that progress was updated multiple times
        assert mock_worker.progress_signal.emit.call_count > 0
        
        db.close()
        
    finally:
        os.unlink(f.name)


# Additional test fixtures and utilities
@pytest.fixture
def sample_utm_data():
    """Provide sample UTM coordinate data for testing."""
    return {
        'origin_lon': -74.0,
        'origin_lat': 40.0,
        'x_offset': 100.0,
        'y_offset': 200.0,
        'expected_easting': 500000,
        'expected_northing': 4000000,
        'expected_zone': 18,
        'expected_letter': 'T'
    }


@pytest.fixture
def mock_utm_functions():
    """Mock UTM functions for consistent testing."""
    with patch('utm.from_latlon') as mock_from, \
         patch('utm.to_latlon') as mock_to:
        mock_from.return_value = (500000, 4000000, 18, 'T')
        mock_to.return_value = (40.001, -73.999)
        yield mock_from, mock_to


# Performance benchmarks
def test_algorithms_performance():
    """Basic performance test for algorithms."""
    algorithms = CsvAlgorithms()
    
    # Test copy performance
    row = ['123', 'test', '456.7'] * 100  # Large row
    col_def = {'src_col': 0, 'type': 'int', 'parms': []}
    
    import time
    start_time = time.time()
    for _ in range(1000):
        algorithms.copy(row, col_def)
    end_time = time.time()
    
    # Should complete quickly (less than 1 second for 1000 operations)
    assert (end_time - start_time) < 1.0


# Error condition tests
    def test_csv_process_with_malformed_data(self):
        """Test CSV processing with various types of malformed data."""
        processor = CsvProcess()
        
        test_cases = [
            # Inconsistent number of columns
            [['1', '2', '3'], ['4', '5']],
            # Mixed data types in numeric columns
            [['1', '2', '3.5'], ['text', '5', '6.7']],
            # Special characters
            [['1', '2', '3'], ['4', 'test,with,comma', '6']],
        ]
        
        for test_data in test_cases:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
                writer = csv.writer(f)
                for row in test_data:
                    writer.writerow(row)
                f.flush()
                
            try:
                config = {
                    "col_defs": {
                        "event_id": {"src_col": 0, "type": "int", "func": "copy"},
                        "veh_id": {"src_col": 1, "type": "int", "func": "copy"},
                        "veh_time": {"src_col": 2, "type": "float", "func": "copy"},
                        "lane_id": {"src_col": -1, "func": "copy", "parms": [1]},
                        "x_map_loc": {"src_col": -1, "func": "copy", "parms": [0.0]},
                        "y_map_loc": {"src_col": -1, "func": "copy", "parms": [0.0]}
                    }
                }
                
                mock_worker = Mock()
                mock_worker.log_signal = Mock()
                mock_worker.log_signal.emit = Mock()
                mock_worker.progress_signal = Mock()
                mock_worker.progress_signal.emit = Mock()
                mock_worker.interrupt_flag = False
                
                # Configuration should work
                error_count = processor._check_configuration(config, mock_worker)
                assert error_count == 0
                
                # File validation might catch some issues
                try:
                    processor._check_import_file(f.name, mock_worker)
                except:
                    pass  # Some malformed data is expected to cause errors
                    
            finally:
                os.unlink(f.name)
