import pytest
import json
from unittest.mock import Mock
from data_import.config import Config

# Mock CsvAlgos class
class MockCsvAlgos:
    def copy(self, *args, **kwargs):
        pass

@pytest.fixture
def mock_csv_algos(monkeypatch):
    # Mock the importlib.import_module to return a module with MockCsvAlgos
    mock_module = Mock()
    mock_module.CsvAlgos = MockCsvAlgos
    monkeypatch.setattr("importlib.import_module", Mock(return_value=mock_module))
    return mock_module

@pytest.fixture
def temp_config_file(tmp_path):
    # Create a temporary JSON file for testing
    config_data = [
        {
            "type": "test_type",
            "cols": [
                [
                    {"name": "event_id", "col": 0, "type": "int", "func": "copy"},
                    {"name": "veh_id", "col": 1, "type": "int", "func": "copy"},
                    {"name": "time", "col": 2, "type": "float", "func": "copy"}
                ]
            ]
        }
    ]
    config_file = tmp_path / "config.json"
    with open(config_file, 'w') as f:
        json.dump(config_data, f)
    return config_file

def test_config_load_success(temp_config_file, mock_csv_algos):
    config = Config(str(temp_config_file))
    result = config.get_config("test_type")
    expected = [
        [
            {"name": "event_id", "col": 0, "type": "int", "func": "copy", "parms": []},
            {"name": "veh_id", "col": 1, "type": "int", "func": "copy", "parms": []},
            {"name": "time", "col": 2, "type": "float", "func": "copy", "parms": []}
        ]
    ]
    assert result == expected
    assert config.configuration["test_type"]["min"] == 3

def test_config_file_not_found():
    config = Config("non_existent.json")
    with pytest.raises(FileNotFoundError, match="Configuration file not found: non_existent.json"):
        config.get_config("test_type")

def test_invalid_json(tmp_path):
    config_file = tmp_path / "invalid.json"
    with open(config_file, 'w') as f:
        f.write("invalid json")
    config = Config(str(config_file))
    with pytest.raises(ValueError, match="Invalid JSON in configuration file"):
        config.get_config("test_type")

def test_missing_required_keys(temp_config_file, mock_csv_algos):
    invalid_config = [{"type": "test_type", "cols": [[]]}]
    with open(temp_config_file, 'w') as f:
        json.dump(invalid_config, f)
    config = Config(str(temp_config_file))
    with pytest.raises(ValueError, match="Missing required coluna names"):
        config.get_config("test_type")

def test_invalid_cols_format(temp_config_file, mock_csv_algos):
    invalid_config = [{"type": "test_type", "cols": "invalid"}]
    with open(temp_config_file, 'w') as f:
        json.dump(invalid_config, f)
    config = Config(str(temp_config_file))
    with pytest.raises(ValueError, match="Cols for type 'test_type' must be an array of arrays"):
        config.get_config("test_type")

def test_invalid_func(temp_config_file, mock_csv_algos):
    invalid_config = [
        {
            "type": "test_type",
            "cols": [
                [
                    {"name": "event_id", "col": 0, "type": "int", "func": "invalid_func"},
                    {"name": "veh_id", "col": 1, "type": "int", "func": "copy"},
                    {"name": "time", "col": 2, "type": "float", "func": "copy"}
                ]
            ]
        }
    ]
    with open(temp_config_file, 'w') as f:
        json.dump(invalid_config, f)
    config = Config(str(temp_config_file))
    with pytest.raises(ValueError, match="Invalid or non-public function 'invalid_func'"):
        config.get_config("test_type")

def test_misconfiguration(temp_config_file, mock_csv_algos):
    invalid_config = [
        {
            "type": "test_type",
            "cols": [
                [
                    {"name": "event_id", "col": -1, "type": "int", "func": "copy"},
                    {"name": "veh_id", "col": 1, "type": "int", "func": "copy"},
                    {"name": "time", "col": 2, "type": "float", "func": "copy"}
                ]
            ]
        }
    ]
    with open(temp_config_file, 'w') as f:
        json.dump(invalid_config, f)
    config = Config(str(temp_config_file))
    with pytest.raises(ValueError, match="Misconfiguration in column 'event_id'"):
        config.get_config("test_type")

def test_key_not_found(temp_config_file, mock_csv_algos):
    config = Config(str(temp_config_file))
    with pytest.raises(KeyError, match="Configuration key 'unknown_type' not found"):
        config.get_config("unknown_type")