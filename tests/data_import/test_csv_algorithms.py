import pytest
import utm
from typing import Dict, Any, List
from data_import.csv_algorithms import CsvAlgorithms

class TestCsvAlgorithms:
    @pytest.fixture(scope="function")
    def csv_algos(self):
        """Fixture to create a fresh CsvAlgorithms instance for each test."""
        return CsvAlgorithms()

    def test_copy_valid_int(self, csv_algos):
        """Test copying a valid integer value."""
        row = ["1", "b", "c"]
        col_def = {"src_col": 0, "type": "int"}
        assert csv_algos.copy(row, col_def) == 1

    def test_copy_valid_float(self, csv_algos):
        """Test copying a valid float value."""
        row = ["1.5", "b", "c"]
        col_def = {"src_col": 0, "type": "float"}
        assert csv_algos.copy(row, col_def) == 1.5

    def test_copy_valid_string(self, csv_algos):
        """Test copying a valid string value."""
        row = ["a", "b", "c"]
        col_def = {"src_col": 0, "type": "string"}
        assert csv_algos.copy(row, col_def) == "a"

    def test_copy_invalid_index(self, csv_algos):
        """Test copying with an invalid column index."""
        row = ["a", "b", "c"]
        col_def = {"src_col": 5, "type": "string", "params": ["default"]}
        assert csv_algos.copy(row, col_def) == "default"
        col_def = {"src_col": -1, "type": "string", "params": ["default"]}
        assert csv_algos.copy(row, col_def) == "default"
        col_def = {"src_col": 5, "type": "string", "params": []}
        assert csv_algos.copy(row, col_def) is None

    def test_copy_empty_value(self, csv_algos):
        """Test copying an empty value."""
        row = ["", "b", "c"]
        col_def = {"src_col": 0, "type": "string", "params": ["default"]}
        assert csv_algos.copy(row, col_def) == "default"

    def test_copy_invalid_float(self, csv_algos):
        """Test copying an invalid float value."""
        row = ["invalid", "b", "c"]
        col_def = {"src_col": 0, "type": "float"}
        assert csv_algos.copy(row, col_def) is None

    def test_utm_common_valid(self, mocker):
        """Test UTM conversion with valid inputs."""
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = ["-74.0060", "40.7128", "1000", "2000"]
        col_def = {"params": [0, 1, 2, 3]}  # map_origin_x, map_origin_y, x_map_loc, y_map_loc
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        assert abs(lat - 40.730811) < 0.01
        assert abs(lon - -73.993895) < 0.01

    def test_utm_common_insufficient_params(self, mocker):
        """Test UTM conversion with insufficient params."""
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = ["-74.0060", "40.7128", "1000", "2000"]
        col_def = {"params": [0, 1, 2]}  # Missing one param
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None

    def test_utm_common_invalid_index(self, mocker):
        """Test UTM conversion with invalid column indices."""
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = ["-74.0060", "40.7128"]  # Only 2 columns
        col_def = {"params": [0, 1, 2, 3]}
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None

    def test_utm_common_invalid_float(self, mocker):
        """Test UTM conversion with invalid float values."""
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = ["invalid", "40.7128", "1000", "2000"]
        col_def = {"params": [0, 1, 2, 3]}
        lat, lon = CsvAlgorithms.utm_common(row, col_def)
        assert lat is None
        assert lon is None

    def test_utm_lat_valid(self, csv_algos, mocker):
        """Test latitude from UTM with valid inputs."""
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = ["-74.0060", "40.7128", "1000", "2000"]
        col_def = {"params": [0, 1, 2, 3]}
        lat = csv_algos.utm_lat(row, col_def)
        assert abs(lat - 40.730811) < 0.01

    def test_utm_lon_valid(self, csv_algos, mocker):
        """Test longitude from UTM with valid inputs."""
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = ["-74.0060", "40.7128", "1000", "2000"]
        col_def = {"params": [0, 1, 2, 3]}
        lon = csv_algos.utm_lon(row, col_def)
        assert abs(lon - -73.993895) < 0.01

    def test_tgsim_lane_valid_even(self, csv_algos):
        """Test tgsim lane calculation for even src_value."""
        row = ["2", "3"]
        col_def = {"src_col": 0, "params": [1]}  # src_col=0 (2), lane_kf=3
        lane_id = csv_algos.tgsim_lane(row, col_def)
        assert lane_id == 16 - 3  # 13

    def test_tgsim_lane_valid_odd(self, csv_algos):
        """Test tgsim lane calculation for odd src_value."""
        row = ["1", "3"]
        col_def = {"src_col": 0, "params": [1]}  # src_col=0 (1), lane_kf=3
        lane_id = csv_algos.tgsim_lane(row, col_def)
        assert lane_id == 7 - 3  # 4

    def test_tgsim_lane_invalid_index(self, csv_algos):
        """Test tgsim lane with invalid column indices."""
        row = ["1"]
        col_def = {"src_col": 0, "params": [1]}  # params[0]=1 is out of bounds
        assert csv_algos.tgsim_lane(row, col_def) is None
        col_def = {"src_col": 2, "params": [0]}  # src_col=2 is out of bounds
        assert csv_algos.tgsim_lane(row, col_def) is None

    def test_tgsim_lane_empty_value(self, csv_algos):
        """Test tgsim lane with empty src_col value."""
        row = ["", "3"]
        col_def = {"src_col": 0, "params": [1]}
        assert csv_algos.tgsim_lane(row, col_def) is None

    def test_tgsim_lane_invalid_float(self, csv_algos):
        """Test tgsim lane with invalid float values."""
        row = ["invalid", "3"]
        col_def = {"src_col": 0, "params": [1]}
        assert csv_algos.tgsim_lane(row, col_def) is None

    def test_tgsim_lane_no_params(self, csv_algos):
        """Test tgsim lane with missing params."""
        row = ["1", "3"]
        col_def = {"src_col": 0, "params": []}
        assert csv_algos.tgsim_lane(row, col_def) is None

    def test_group_new_key(self, csv_algos):
        """Test grouping with a new key."""
        row = ["x", "y", "z"]
        col_def = {"params": [0, 1]}  # Columns 0 and 1
        assert csv_algos.group(row, col_def) == 0
        assert csv_algos._group_dict == {"x y": 0}

    def test_group_existing_key(self, csv_algos):
        """Test grouping with an existing key."""
        row1 = ["x", "y", "z"]
        row2 = ["x", "y", "w"]
        col_def = {"params": [0, 1]}
        assert csv_algos.group(row1, col_def) == 0
        assert csv_algos.group(row2, col_def) == 0
        assert len(csv_algos._group_dict) == 1

    def test_group_different_key(self, csv_algos):
        """Test grouping with different keys."""
        row1 = ["x", "y", "z"]
        row2 = ["a", "b", "c"]
        col_def = {"params": [0, 1]}
        assert csv_algos.group(row1, col_def) == 0
        assert csv_algos.group(row2, col_def) == 1
        assert csv_algos._group_dict == {"x y": 0, "a b": 1}

    def test_group_out_of_bounds_params(self, csv_algos):
        """Test grouping with out-of-bounds column indices."""
        row = ["x", "y"]
        col_def = {"params": [0, 2]}  # Col 2 is out of bounds
        assert csv_algos.group(row, col_def) == 0
        assert csv_algos._group_dict == {"x": 0}

    def test_group_invalid_index(self, csv_algos):
        """Test grouping with invalid column indices."""
        row = ["x"]
        col_def = {"params": [1]}  # Col 1 is out of bounds
        assert csv_algos.group(row, col_def) is None

    def test_group_empty_values(self, csv_algos):
        """Test grouping with empty values."""
        row = ["x", "", "z"]
        col_def = {"params": [0, 1]}
        assert csv_algos.group(row, col_def) == 0
        assert csv_algos._group_dict == {"x": 0}