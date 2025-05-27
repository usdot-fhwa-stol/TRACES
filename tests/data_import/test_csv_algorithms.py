import pytest
import utm
from data_import.csv_algorithms import CsvAlgorithms


class TestCsvAlgorithms:
    @pytest.fixture(scope="function")
    def csv_algos(self):
        return CsvAlgorithms()

    def test_copy_valid_column(self, csv_algos):
        row = ["a", "b", "c"]
        assert csv_algos.copy(row, 1, ["default"]) == "b"

    def test_copy_invalid_column(self, csv_algos):
        row = ["a", "b", "c"]
        assert csv_algos.copy(row, 5, ["default"]) == "default"
        assert csv_algos.copy(row, -1, ["default"]) == "default"
        assert csv_algos.copy(row, 5, []) is None

    def test_utm_lat(self, csv_algos, mocker):
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = [40.7128, -74.0060, 1000, 2000]
        lat = csv_algos.utm_lat(row, 0, [0, 1, 2, 3])
        assert abs(lat - 40.730811) < 0.01

    def test_utm_lon(self, csv_algos, mocker):
        mocker.patch('utm.from_latlon', return_value=(555000, 4500000, 18, 'N'))
        mocker.patch('utm.to_latlon', return_value=(40.730811, -73.993895))
        row = [40.7128, -74.0060, 1000, 2000]
        lon = csv_algos.utm_lon(row, 0, [0, 1, 2, 3])
        assert abs(lon - -73.993895) < 0.01

    def test_utm_lat_invalid_index(self, csv_algos):
        row = [40.7128, -74.0060]
        with pytest.raises(IndexError):
            csv_algos.utm_lat(row, 0, [0, 1, 2, 3])

    def test_utm_lat_invalid_float(self, csv_algos):
        row = ["invalid", -74.0060, 1000, 2000]
        with pytest.raises(ValueError):
            csv_algos.utm_lat(row, 0, [0, 1, 2, 3])

    def test_group_new_key(self, csv_algos):
        row = ["x", "y", "z"]
        params = [0, 1]  # 0-based indices: columns 0 and 1
        assert csv_algos.group(row, 0, params) == 0
        assert csv_algos._group_lookup == {"x y": 0}

    def test_group_existing_key(self, csv_algos):
        row1 = ["x", "y", "z"]
        row2 = ["x", "y", "w"]
        params = [0, 1]  # 0-based indices
        assert csv_algos.group(row1, 0, params) == 0
        assert csv_algos.group(row2, 0, params) == 0
        assert len(csv_algos._group_lookup) == 1

    def test_group_different_key(self, csv_algos):
        row1 = ["x", "y", "z"]
        row2 = ["a", "b", "c"]
        params = [0, 1]  # 0-based indices
        assert csv_algos.group(row1, 0, params) == 0
        assert csv_algos.group(row2, 0, params) == 1
        assert csv_algos._group_lookup == {"x y": 0, "a b": 1}

    def test_group_out_of_bounds_params(self, csv_algos):
        row = ["x", "y"]
        params = [0, 2]  # 0-based: col 0 ("x"), col 2 (out of bounds, skipped)
        assert csv_algos.group(row, 0, params) == 0
        assert csv_algos._group_lookup == {"x": 0}

    def test_group_invalid_index(self, csv_algos):
        row = ["x"]
        params = [1]  # 0-based index 1 is out of bounds
        assert csv_algos.group(row, 0, params) == 0
        assert csv_algos._group_lookup == {"": 0}
