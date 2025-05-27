import csv
import json
import os
import sqlite3
from .base_process import BaseProcess
from .csv_algorithms import CsvAlgorithms


class CsvProcess(BaseProcess):
    """Processes CSV files into SQLite databases based on JSON configuration.

    Implements the BaseProcess interface for CSV file handling.
    """

    def process(self, config_file: str, input_file: str, dialog: object) -> dict:
        """Process a CSV file into a SQLite database.

        Args:
            config_file: Path to the JSON configuration file.
            input_file: Path to the CSV input file.
            dialog: Reference to the import dialog for progress updates.

        Returns:
            dict: Contains 'error_count' (int) and 'db_conn' (sqlite3.Connection or None).
        """
        config = self.parse_config(config_file, dialog)
        if not config:
            return {"error_count": 1, "db_conn": None}

        record_info = self.count_records(input_file, config, dialog)
        if not record_info:
            return {"error_count": 1, "db_conn": None}

        db_file = os.path.splitext(input_file)[0] + ".sqlite"
        db_conn = sqlite3.connect(db_file)
        return self.import_records(input_file, config, record_info, db_conn, dialog)

    def parse_config(self, config_file: str, dialog: object) -> list:
        """Parse the JSON configuration file.

        Args:
            config_file: Path to the JSON configuration file.
            dialog: Reference to the import dialog for logging.

        Returns:
            list: List of lists of column definitions, or empty list on error.
        """
        dialog.append_message(f"Parsing configuration file {config_file}")
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            dialog.append_message(f"Error parsing {config_file}: {str(e)}")
            return []

        if "cols" not in config:
            dialog.append_message("Required key 'cols' is missing")
            return []
        if not isinstance(config["cols"], list):
            dialog.append_message("Required key 'cols' is not an array")
            return []

        # Normalize cols to list of lists
        cols = config["cols"]
        if cols and isinstance(cols[0], dict):
            cols = [cols]

        # Validate and normalize column definitions
        result = []
        required_cols = {"event", "id", "time"}
        for i, col_set in enumerate(cols, 1):
            seen_names = set()
            col_defs = []
            for col in col_set:
                name = col.get("name")
                if not name:
                    dialog.append_message("Column definition missing 'name' key")
                    return []
                if name in seen_names:
                    dialog.append_message(f"Duplicate definition for {name}")
                    return []
                if name in required_cols:
                    required_cols.remove(name)
                seen_names.add(name)

                # Set defaults
                col_def = {
                    "name": name,
                    "col": col.get("col", -1),
                    "type": col.get("type", "float"),
                    "func": col.get("func", "copy"),
                    "parms": col.get("parms", [])
                }
                if col_def["col"] == -1 and col_def["func"] == "copy" and not col_def["parms"]:
                    dialog.append_message(f"No default value specified for optional {name}")
                    return []
                col_defs.append(col_def)

            result.append(col_defs)
            dialog.append_message(f"Set {i} contains {len(col_defs)} column definitions")

        if required_cols:
            for name in required_cols:
                dialog.append_message(f"Required definition for {name} is missing")
            return []

        dialog.append_message(f"Configuration file contains {len(cols)} sets of column definitions")
        for i, col_set in enumerate(cols, 1):
            required = [c for c in col_set if c["name"] in {"event", "id", "time"}]
            optional = [c for c in col_set if c["name"] not in {"event", "id", "time"}]
            dialog.append_message(f"Set {i} required columns: " + ", ".join(
                f"{c['name']} ({c['type']})" for c in required))
            dialog.append_message(f"Set {i} optional columns: " + ", ".join(
                f"{c['name']} ({c['type']})" for c in optional) if optional else "None")
        return result

    def count_records(self, input_file: str, config: list, dialog: object) -> dict:
        """Count records and determine CSV structure.

        Args:
            input_file: Path to the CSV input file.
            config: List of lists of column definitions.
            dialog: Reference to the import dialog for logging.

        Returns:
            dict: Contains 'min_cols', 'has_header', and 'total_records', or None on error.
        """
        dialog.append_message(f"Checking {input_file}")
        min_cols = 0
        for col_set in config:
            for col in col_set:
                min_cols = max(min_cols, col["col"] + 1)

        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=',')
                first_row = next(reader, None)
                if not first_row:
                    dialog.append_message("No records found")
                    return None
                if len(first_row) < min_cols:
                    dialog.append_message(f"Minimum {min_cols} columns not found")
                    return None

                has_header = all(s.isalnum() for s in first_row[:min_cols])
                if has_header:
                    dialog.append_message("Header detected")

                total_records = sum(1 for _ in reader) + (1 if not has_header else 0)
                if total_records == 0:
                    dialog.append_message("No records found")
                    return None

                return {
                    "min_cols": min_cols,
                    "has_header": has_header,
                    "total_records": total_records
                }
        except Exception as e:
            dialog.append_message(f"Error reading {input_file}: {str(e)}")
            return None

    def import_records(self, input_file: str, config: list, record_info: dict, db_conn: sqlite3.Connection, dialog: object) -> dict:
        """Import CSV records into the SQLite database.

        Args:
            input_file: Path to the CSV input file.
            config: List of lists of column definitions.
            record_info: Dictionary with 'min_cols', 'has_header', and 'total_records'.
            db_conn: SQLite database connection.
            dialog: Reference to the import dialog for logging.

        Returns:
            dict: Contains 'error_count' (int) and 'db_conn' (sqlite3.Connection).
        """
        error_count = 0
        algorithms = CsvAlgorithms()
        func_dict = {name: getattr(algorithms, name) for name in dir(algorithms)
                     if callable(getattr(algorithms, name)) and not name.startswith('_')}

        for col_set in config:
            for col in col_set:
                if col["func"] not in func_dict:
                    dialog.append_message(f"Required function {col['func']} is undefined")
                    error_count += 1
        if error_count > 0:
            return {"error_count": error_count, "db_conn": None}

        # Create trajectories table
        cursor = db_conn.cursor()
        cursor.execute("""
            CREATE TABLE trajectories (
                event INTEGER,
                id INTEGER,
                time REAL,
                lane_id INTEGER,
                x_map_loc REAL,
                y_map_loc REAL,
                x_frenet REAL,
                y_frenet REAL,
                x_map_origin REAL,
                y_map_origin REAL,
                veh_lat REAL,
                veh_lon REAL,
                speed REAL,
                acceleration REAL,
                length REAL,
                automated INTEGER,
                way_id REAL,
                preceding REAL,
                speed_limit REAL,
                width REAL,
                traffic_control REAL,
                PRIMARY KEY (event, id, time)
            )
        """)
        db_conn.commit()

        columns = ["event", "id", "time", "lane_id", "x_map_loc", "y_map_loc", "x_frenet",
                   "y_frenet", "x_map_origin", "y_map_origin", "veh_lat", "veh_lon",
                   "speed", "acceleration", "length", "automated", "way_id", "preceding",
                   "speed_limit", "width", "traffic_control"]
        insert_sql = f"INSERT INTO trajectories ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        batch = []
        row_count = 0

        with open(input_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=',')
            if record_info["has_header"]:
                next(reader)  # Skip header

            for import_record in reader:
                if dialog.is_stopped():
                    dialog.append_message("Import stopped by user")
                    break
                row_count += 1

                for col_set in config:
                    database_record = {col: None for col in columns}
                    valid_record = True
                    for col_def in col_set:
                        try:
                            value = func_dict[col_def["func"]](import_record, col_def["col"], col_def["parms"])
                            if value == "":
                                value = None
                            elif col_def["type"] == "int":
                                try:
                                    value = int(value) if value is not None else None
                                except (ValueError, TypeError):
                                    dialog.append_message(f"Column {col_def['name']} is not of type int")
                                    error_count += 1
                                    valid_record = False
                                    break
                            elif col_def["type"] == "float":
                                try:
                                    value = float(value) if value is not None else None
                                except (ValueError, TypeError):
                                    dialog.append_message(f"Column {col_def['name']} is not of type float")
                                    error_count += 1
                                    valid_record = False
                                    break
                            if col_def["name"] in {"event", "id", "time"} and value is None:
                                dialog.append_message(f"Column {col_def['name']} is required but empty")
                                error_count += 1
                                valid_record = False
                                break
                            database_record[col_def["name"]] = value
                        except Exception as e:
                            dialog.append_message(f"Error in {col_def['func']} for {col_def['name']}: {str(e)}")
                            error_count += 1
                            valid_record = False
                            break

                    if valid_record:
                        batch.append([database_record[col] for col in columns])

                if len(batch) >= 100 or row_count == record_info["total_records"]:
                    try:
                        cursor.executemany(insert_sql, batch)
                        db_conn.commit()
                    except sqlite3.IntegrityError:
                        pass  # Ignore duplicate record errors
                    batch = []
                    dialog.update_progress(row_count / record_info["total_records"] * 100)

                if error_count > 100:
                    dialog.append_message("Too many errors, stopping import")
                    break

        dialog.append_message(f"{row_count} records imported")
        return {"error_count": error_count, "db_conn": db_conn if error_count <= 100 else None}