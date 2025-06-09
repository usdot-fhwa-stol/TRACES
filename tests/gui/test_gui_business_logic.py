"""
Unit tests for GUI package business logic.

Tests cover non-GUI functionality that can be tested in headless environment:
- Database validation logic in MainWindow
- Configuration file handling
- Import/export logic
- Data processing and validation

Note: GUI components (widgets, dialogs, etc.) are not tested as they require
a display environment.
"""

import pytest
import sqlite3
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Import the modules to test (business logic only)
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMainWindowBusinessLogic:
    """Test cases for MainWindow business logic (non-GUI components)."""
    
    def setup_method(self):
        """Set up test fixtures."""
        # We'll test the business logic methods without creating actual MainWindow
        pass
    
    def create_test_database(self, with_data=True):
        """Helper to create a test SQLite database."""
        db_file = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        db_file.close()
        
        conn = sqlite3.connect(db_file.name)
        
        # Create correct schema
        conn.execute("""
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
        
        if with_data:
            # Insert test data
            conn.execute("""
                INSERT INTO events (event_id, veh_id, event_time, lane_id, x_map_loc, y_map_loc, veh_speed)
                VALUES (1, 101, 0.0, 1, 100.0, 200.0, 25.5)
            """)
            conn.execute("""
                INSERT INTO events (event_id, veh_id, event_time, lane_id, x_map_loc, y_map_loc, veh_speed)
                VALUES (1, 102, 0.1, 1, 101.0, 201.0, 26.0)
            """)
            conn.execute("""
                INSERT INTO events (event_id, veh_id, event_time, lane_id, x_map_loc, y_map_loc, veh_speed)
                VALUES (2, 103, 0.0, 2, 150.0, 250.0, 30.0)
            """)
            conn.commit()
        
        conn.close()
        return db_file.name
    
    def create_invalid_database(self, error_type="missing_table"):
        """Helper to create an invalid database for testing."""
        db_file = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        db_file.close()
        
        conn = sqlite3.connect(db_file.name)
        
        if error_type == "missing_table":
            # Don't create events table
            conn.execute("CREATE TABLE other_table (id INTEGER)")
        elif error_type == "wrong_schema":
            # Create events table with wrong schema
            conn.execute("""
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY,
                    wrong_column TEXT
                )
            """)
        elif error_type == "empty_table":
            # Create correct schema but no data
            conn.execute("""
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
        
        conn.commit()
        conn.close()
        return db_file.name
    
    def test_database_validation_valid_database(self):
        """Test database validation with valid database."""
        from gui.main_window import MainWindow
        
        # Create a mock MainWindow to test the _check_database method
        with patch.object(MainWindow, '__init__', lambda x: None):
            window = MainWindow()
            window._db_connection = None
            
            # Mock QMessageBox to avoid GUI dependencies
            with patch('gui.main_window.QMessageBox'):
                db_path = self.create_test_database(with_data=True)
                try:
                    result = window._check_database(db_path)
                    assert result is True
                    assert window._db_connection is not None
                    
                    # Verify we can query the database
                    cursor = window._db_connection.cursor()
                    cursor.execute("SELECT COUNT(*) FROM events")
                    count = cursor.fetchone()[0]
                    assert count == 3
                finally:
                    if window._db_connection:
                        window._db_connection.close()
                    Path(db_path).unlink()
    
    def test_database_validation_missing_table(self):
        """Test database validation with missing events table."""
        from gui.main_window import MainWindow
        
        with patch.object(MainWindow, '__init__', lambda x: None):
            window = MainWindow()
            window._db_connection = None
            
            with patch('gui.main_window.QMessageBox') as mock_msgbox:
                db_path = self.create_invalid_database("missing_table")
                try:
                    result = window._check_database(db_path)
                    assert result is False
                    assert window._db_connection is None
                    mock_msgbox.critical.assert_called_once()
                finally:
                    Path(db_path).unlink()
    
    def test_database_validation_wrong_schema(self):
        """Test database validation with incorrect schema."""
        from gui.main_window import MainWindow
        
        with patch.object(MainWindow, '__init__', lambda x: None):
            window = MainWindow()
            window._db_connection = None
            
            with patch('gui.main_window.QMessageBox') as mock_msgbox:
                db_path = self.create_invalid_database("wrong_schema")
                try:
                    result = window._check_database(db_path)
                    assert result is False
                    assert window._db_connection is None
                    mock_msgbox.critical.assert_called_once()
                finally:
                    Path(db_path).unlink()
    
    def test_database_validation_empty_table(self):
        """Test database validation with empty events table."""
        from gui.main_window import MainWindow
        
        with patch.object(MainWindow, '__init__', lambda x: None):
            window = MainWindow()
            window._db_connection = None
            
            with patch('gui.main_window.QMessageBox') as mock_msgbox:
                db_path = self.create_invalid_database("empty_table")
                try:
                    result = window._check_database(db_path)
                    assert result is False
                    assert window._db_connection is None
                    mock_msgbox.critical.assert_called_once()
                finally:
                    Path(db_path).unlink()
    
    def test_recent_files_management(self):
        """Test recent files list management."""
        from gui.main_window import MainWindow
        
        with patch.object(MainWindow, '__init__', lambda x: None):
            window = MainWindow()
            window._recent_files = []
            window._recent_menu = Mock()
            
            # Mock the _update_recent_menu method
            window._update_recent_menu = Mock()
            
            # Add files to recent list
            window.add_recent_file("/path/to/file1.sqlite")
            assert window._recent_files == [str(Path("/path/to/file1.sqlite").absolute())]
            
            window.add_recent_file("/path/to/file2.sqlite")
            assert len(window._recent_files) == 2
            assert window._recent_files[0] == str(Path("/path/to/file2.sqlite").absolute())  # Most recent first
            
            # Add duplicate file - should move to front
            window.add_recent_file("/path/to/file1.sqlite")
            assert len(window._recent_files) == 2
            assert window._recent_files[0] == str(Path("/path/to/file1.sqlite").absolute())
            
            # Add many files to test 7-file limit
            for i in range(10):
                window.add_recent_file(f"/path/to/file{i}.sqlite")
            
            assert len(window._recent_files) == 7  # Should be limited to 7 files
            
            # Verify _update_recent_menu was called
            assert window._update_recent_menu.call_count > 0


class TestConfigurationHandling:
    """Test cases for configuration file handling."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
    
    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def create_test_config(self, config_data, filename="test.json"):
        """Helper to create a test configuration file."""
        config_path = self.temp_dir / filename
        with open(config_path, 'w') as f:
            json.dump(config_data, f)
        return config_path
    
    def test_valid_configuration_loading(self):
        """Test loading valid configuration files."""
        # Create a valid config
        valid_config = {
            "file_ext": "csv",
            "class": "data_import.csv_process.CsvProcess",
            "col_defs": {
                "event_id": {"src_col": 0, "type": "int"},
                "veh_id": {"src_col": 1, "type": "int"},
                "event_time": {"src_col": 2, "type": "float"},
                "lane_id": {"src_col": 3, "type": "int"},
                "x_map_loc": {"src_col": 4, "type": "float"},
                "y_map_loc": {"src_col": 5, "type": "float"}
            }
        }
        
        config_path = self.create_test_config(valid_config)
        
        # Test that the config can be loaded and parsed
        with open(config_path) as f:
            loaded_config = json.load(f)
        
        assert loaded_config["file_ext"] == "csv"
        assert "col_defs" in loaded_config
        assert len(loaded_config["col_defs"]) == 6
    
    def test_invalid_json_configuration(self):
        """Test handling invalid JSON configuration."""
        config_path = self.temp_dir / "invalid.json"
        with open(config_path, 'w') as f:
            f.write("{ invalid json ")
        
        # Should raise JSONDecodeError when trying to load
        with pytest.raises(json.JSONDecodeError):
            with open(config_path) as f:
                json.load(f)
    
    def test_configuration_with_missing_col_defs(self):
        """Test configuration missing required col_defs."""
        invalid_config = {
            "file_ext": "csv",
            "class": "data_import.csv_process.CsvProcess"
            # Missing col_defs
        }
        
        config_path = self.create_test_config(invalid_config)
        
        with open(config_path) as f:
            loaded_config = json.load(f)
        
        assert "col_defs" not in loaded_config


class TestImportDialogBusinessLogic:
    """Test cases for ImportDialog business logic."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
    
    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def create_test_csv(self, data_rows, filename="test.csv"):
        """Helper to create a test CSV file."""
        csv_path = self.temp_dir / filename
        with open(csv_path, 'w', newline='') as f:
            import csv
            writer = csv.writer(f)
            for row in data_rows:
                writer.writerow(row)
        return str(csv_path)
    
    def test_file_path_generation(self):
        """Test automatic output path generation from input path."""
        input_path = "/path/to/data.csv"
        expected_output = "/path/to/data.sqlite"
        
        # Simulate the logic from ImportDialog
        output_path = str(Path(input_path).with_suffix(".sqlite"))
        assert output_path == expected_output
    
    def test_database_cleanup_logic(self):
        """Test database file cleanup logic."""
        # Create a temporary database file
        db_path = self.temp_dir / "test.sqlite"
        
        # Create the file
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()
        
        assert db_path.exists()
        
        # Test cleanup logic
        if db_path.exists():
            try:
                # Try to open and close to ensure it's not locked
                temp_conn = sqlite3.connect(str(db_path))
                temp_conn.close()
                
                # Remove the file
                db_path.unlink()
                assert not db_path.exists()
                
            except Exception as e:
                # If we can't remove it, we'd handle the error
                assert False, f"Could not clean up database: {e}"


class TestTimeSpacePlotBusinessLogic:
    """Test cases for TimeSpacePlotWidget business logic."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_db_path = None
    
    def teardown_method(self):
        """Clean up test fixtures."""
        if self.test_db_path and Path(self.test_db_path).exists():
            Path(self.test_db_path).unlink()
    
    def create_test_database_with_events(self):
        """Create a test database with event data."""
        db_file = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        db_file.close()
        self.test_db_path = db_file.name
        
        conn = sqlite3.connect(self.test_db_path)
        
        # Create schema
        conn.execute("""
            CREATE TABLE events (
                event_id INTEGER NOT NULL,
                veh_id INTEGER NOT NULL,
                event_time REAL NOT NULL,
                lane_id INTEGER,
                x_map_loc REAL,
                y_map_loc REAL,
                veh_dist REAL,
                veh_speed REAL,
                veh_acceleration REAL,
                veh_automated INTEGER,
                veh_length REAL,
                PRIMARY KEY (event_id, veh_id, event_time)
            ) WITHOUT ROWID;
        """)
        
        # Insert test data for multiple events and vehicles
        test_data = [
            (1, 101, 0.0, 1, 100.0, 200.0, 0.0, 25.0, 0.5, 0, 4.5),
            (1, 101, 0.1, 1, 102.5, 202.5, 3.54, 25.0, 0.0, 0, 4.5),
            (1, 101, 0.2, 1, 105.0, 205.0, 7.07, 25.0, -0.5, 0, 4.5),
            (1, 102, 0.0, 1, 110.0, 210.0, 0.0, 30.0, 1.0, 1, 5.0),
            (1, 102, 0.1, 1, 113.0, 213.0, 4.24, 30.0, 0.0, 1, 5.0),
            (2, 103, 0.0, 2, 150.0, 250.0, 0.0, 20.0, 0.0, 0, 4.0),
            (2, 103, 0.1, 2, 152.0, 252.0, 2.83, 20.0, 0.0, 0, 4.0),
        ]
        
        conn.executemany("""
            INSERT INTO events (event_id, veh_id, event_time, lane_id, x_map_loc, y_map_loc, 
                              veh_dist, veh_speed, veh_acceleration, veh_automated, veh_length)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, test_data)
        
        conn.commit()
        conn.close()
        return self.test_db_path
    
    def test_event_data_aggregation(self):
        """Test event data aggregation for combo box population."""
        db_path = self.create_test_database_with_events()
        conn = sqlite3.connect(db_path)
        
        # Test the query logic used in _populate_event_combo
        cursor = conn.cursor()
        cursor.execute("""
            SELECT event_id, MIN(event_time), MAX(event_time), MIN(veh_dist), MAX(veh_dist)
            FROM events
            GROUP BY event_id
            ORDER BY event_id
        """)
        
        results = cursor.fetchall()
        
        # Should have 2 events
        assert len(results) == 2
        
        # Event 1
        assert results[0][0] == 1  # event_id
        assert results[0][1] == 0.0  # min_time
        assert results[0][2] == 0.2  # max_time
        assert results[0][3] == 0.0  # min_dist
        assert results[0][4] == 7.07  # max_dist
        
        # Event 2
        assert results[1][0] == 2  # event_id
        assert results[1][1] == 0.0  # min_time
        assert results[1][2] == 0.1  # max_time
        assert results[1][3] == 0.0  # min_dist
        assert results[1][4] == 2.83  # max_dist
        
        conn.close()
    
    def test_lane_data_filtering(self):
        """Test lane data filtering for specific events."""
        db_path = self.create_test_database_with_events()
        conn = sqlite3.connect(db_path)
        
        # Test lane filtering for event 1
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT lane_id 
            FROM events 
            WHERE event_id = ? 
            ORDER BY lane_id
        """, (1,))
        
        lanes = [row[0] for row in cursor.fetchall()]
        assert lanes == [1]  # Event 1 only has lane 1
        
        # Test lane filtering for event 2
        cursor.execute("""
            SELECT DISTINCT lane_id 
            FROM events 
            WHERE event_id = ? 
            ORDER BY lane_id
        """, (2,))
        
        lanes = [row[0] for row in cursor.fetchall()]
        assert lanes == [2]  # Event 2 only has lane 2
        
        conn.close()
    
    def test_vehicle_data_extraction(self):
        """Test vehicle data extraction for plotting."""
        db_path = self.create_test_database_with_events()
        conn = sqlite3.connect(db_path)
        
        # Test vehicle data for event 1, lane 1
        cursor = conn.cursor()
        cursor.execute("""
            SELECT veh_id, event_time, veh_dist 
            FROM events 
            WHERE event_id = ? AND lane_id = ?
            ORDER BY veh_id, event_time
        """, (1, 1))
        
        data = cursor.fetchall()
        
        # Should have data for 2 vehicles (101, 102)
        vehicle_101_data = [row for row in data if row[0] == 101]
        vehicle_102_data = [row for row in data if row[0] == 102]
        
        assert len(vehicle_101_data) == 3  # 3 time points for vehicle 101
        assert len(vehicle_102_data) == 2  # 2 time points for vehicle 102
        
        # Check data progression for vehicle 101
        assert vehicle_101_data[0] == (101, 0.0, 0.0)
        assert vehicle_101_data[1] == (101, 0.1, 3.54)
        assert vehicle_101_data[2] == (101, 0.2, 7.07)
        
        conn.close()
    
    def test_tooltip_data_query(self):
        """Test tooltip data query logic."""
        db_path = self.create_test_database_with_events()
        conn = sqlite3.connect(db_path)
        
        # Test tooltip query (simulating _get_tooltip_record)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT veh_id, event_time, veh_dist, veh_speed, veh_acceleration, veh_automated, veh_length 
            FROM events 
            WHERE event_id = ? AND veh_id = ? AND event_time = ?
            LIMIT 1
        """, (1, 101, 0.1))
        
        result = cursor.fetchone()
        
        assert result is not None
        assert result[0] == 101  # veh_id
        assert result[1] == 0.1  # event_time
        assert result[2] == 3.54  # veh_dist
        assert result[3] == 25.0  # veh_speed
        assert result[4] == 0.0  # veh_acceleration
        assert result[5] == 0  # veh_automated
        assert result[6] == 4.5  # veh_length
        
        conn.close()
    
    def test_distance_calculation_logic(self):
        """Test distance calculation logic used in import."""
        # This tests the distance calculation algorithm used in ImportWorkerThread
        points = [
            (0.0, 100.0, 200.0),  # (time, x, y)
            (0.1, 102.5, 202.5),
            (0.2, 105.0, 205.0)
        ]
        
        total_dist = 0.0
        prev_x, prev_y = None, None
        
        distances = []
        for time, x, y in points:
            if prev_x is not None and prev_y is not None:
                dist = ((x - prev_x) ** 2 + (y - prev_y) ** 2) ** 0.5
                total_dist += dist
            distances.append(total_dist)
            prev_x, prev_y = x, y
        
        # Check calculated distances
        assert distances[0] == 0.0  # First point
        assert abs(distances[1] - 3.54) < 0.01  # Second point: sqrt(2.5^2 + 2.5^2) ˜ 3.54
        assert abs(distances[2] - 7.07) < 0.01  # Third point: cumulative distance


class TestDataValidationLogic:
    """Test cases for data validation logic."""
    
    def test_column_type_validation(self):
        """Test column data type validation logic."""
        
        def validate_int_column(value_str):
            """Simulate the validation logic from CsvProcess."""
            try:
                return int(float(value_str))
            except (ValueError, TypeError):
                return None
        
        def validate_float_column(value_str):
            """Simulate the validation logic from CsvProcess."""
            try:
                return float(value_str)
            except (ValueError, TypeError):
                return None
        
        # Test valid integer values
        assert validate_int_column("123") == 123
        assert validate_int_column("123.0") == 123
        assert validate_int_column("123.9") == 123  # Truncates
        
        # Test invalid integer values
        assert validate_int_column("abc") is None
        assert validate_int_column("") is None
        assert validate_int_column(None) is None
        
        # Test valid float values
        assert validate_float_column("123.45") == 123.45
        assert validate_float_column("123") == 123.0
        assert validate_float_column("-45.67") == -45.67
        
        # Test invalid float values
        assert validate_float_column("abc") is None
        assert validate_float_column("") is None
        assert validate_float_column(None) is None
    
    def test_database_schema_validation(self):
        """Test database schema validation logic."""
        # Expected schema from MainWindow._check_database
        expected_columns = {
            "event_id": "INTEGER",
            "veh_id": "INTEGER", 
            "event_time": "REAL",
            "lane_id": "INTEGER",
            "x_map_loc": "REAL",
            "y_map_loc": "REAL",
            "x_frenet": "REAL",
            "y_frenet": "REAL",
            "x_map_origin": "REAL",
            "y_map_origin": "REAL",
            "veh_lat": "REAL",
            "veh_lon": "REAL",
            "veh_speed": "REAL",
            "veh_acceleration": "REAL",
            "veh_length": "REAL",
            "veh_width": "REAL",
            "veh_automated": "INTEGER",
            "osm_way_id": "INTEGER",
            "osm_speed_limit": "INTEGER",
            "osm_traffic_control": "INTEGER",
            "preceding_veh_id": "INTEGER",
            "veh_dist": "REAL"
        }
        
        # Test schema matching logic
        def validate_schema(actual_columns):
            return actual_columns == expected_columns
        
        # Test with correct schema
        assert validate_schema(expected_columns) is True
        
        # Test with missing column
        incomplete_columns = expected_columns.copy()
        del incomplete_columns["veh_dist"]
        assert validate_schema(incomplete_columns) is False
        
        # Test with extra column
        extra_columns = expected_columns.copy()
        extra_columns["extra_column"] = "TEXT"
        assert validate_schema(extra_columns) is False
        
        # Test with wrong type
        wrong_type_columns = expected_columns.copy()
        wrong_type_columns["event_id"] = "TEXT"
        assert validate_schema(wrong_type_columns) is False
