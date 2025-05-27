import pytest
import utm
from data_import.csv_algorithms import CsvAlgorithms

class TestCsvAlgorithms:
    @pytest.fixture
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

    def test_utm_lat(self, csv_algos):
        # Test with known UTM coordinates
        # Map origin: (40.7128, -74.0060) ~ NYC
        # Offset: (1000, 2000) meters
        row = [40.7128, -74.0060, 1000, 2000]
        lat = csv_algos.utm_lat(row, 0, [0, 1, 2, 3])
        assert abs(lat - 40.7308) < 0.001  # Approximate expected latitude

    def test_utm_lon(self, csv_algos):
        # Same test case as above
        row = [40.7128, -74.0060, 1000, 2000]
        lon = csv_algos.utm_lon(row, 0, [0, 1, 2, 3])
        assert abs(lon - -73.9939) < 0.001  # Approximate expected longitude

    def test_utm_common(self):
        # Direct test of static method
        lat, lon = CsvAlgorithms._CsvAlgorithms__utm_common(40.7128, -74.0060, 1000, 2000)
        assert abs(lat - 40.7308) < 0.001
        assert abs(lon - -73.9939) < 0.001

    def test_group_new_key(self, csv_algos):
        row = ["x", "y", "z"]
        params = [1, 2]  # 1-based indices: columns 0 and 1
        assert csv_algos.group(row, 0, params) == 0
        assert csv_algos._group_lookup == {"x y": 0}

    def test_group_existing_key(self, csv_algos):
        row1 = ["x", "y", "z"]
        row2 = ["x", "y", "w"]
        params = [1, 2]
        assert csv_algos.group(row1, 0, params) == 0
        assert csv_algos.group(row2, 0, params) == 0
        assert len(csv_algos._group_lookup) == 1

    def test_group_different_key(self, csv_algos):
        row1 = ["x", "y", "z"]
        row2 = ["a", "b", "c"]
        params = [1, 2]
        assert csv_algos.group(row1, 0, params) == 0
        assert csv_algos.group(row2, 0, params) == 1
        assert csv_algos._group_lookup == {"x y": 0, "a b": 1}

    def test_group_out_of_bounds_params(self, csv_algos):
        row = ["x", "y"]
        params = [1, 3]  # 3 is out of bounds
        assert csv_algos.group(row, 0, params) == 0
        assert csv_algos._group_lookup == {"x": 0}
