import csv
import sqlite3
import io
import os
from typing import Dict, Any, Callable
from .base_process import BaseProcess
from .csv_algorithms import CsvAlgorithms

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
        self._func_dict: Dict[str, Callable] = {}
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

    def _count_csv_rows(self, input_path: str, has_header: bool) -> int:
        """Count total rows in CSV file for progress tracking."""
        # This method is no longer needed with byte counting approach
        pass

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

        # Log configuration summary
        if error_count == 0:
            worker.log_signal.emit(f"Info: Found {len(self._col_defs)} column definition set(s).", True)
            for i, col_def_set in enumerate(self._col_defs):
                worker.log_signal.emit(f"Info: col_defs[{i}] contains {len(col_def_set)} columns:", True)
                for key, col_def in col_def_set.items():
                    if col_def["src_col"] >= 0:
                        worker.log_signal.emit(
                            f"Info: {key} reads column {col_def['src_col']} using {col_def['func']}", True
                        )
                    else:
                        worker.log_signal.emit(f"Info: {key} is using {col_def['func']}", True)

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

            # Check data types in first row
            for i, col_def_set in enumerate(self._col_defs):
                for key, col_def in col_def_set.items():
                    if col_def["src_col"] >= 0:
                        if col_def["src_col"] >= len(first_data_row):
                            worker.log_signal.emit(
                                f"Error: col_defs[{i}]['{key}'] src_col {col_def['src_col']} "
                                f"exceeds row length {len(first_data_row)}.", False
                            )
                            error_count += 1
                        elif first_data_row[col_def["src_col"]] and col_def["func"] == "copy":
                            try:
                                if col_def["type"] == "int":
                                    int(float(first_data_row[col_def["src_col"]]))
                                elif col_def["type"] == "float":
                                    float(first_data_row[col_def["src_col"]])
                            except (ValueError, TypeError):
                                worker.log_signal.emit(
                                    f"Error: col_defs[{i}]['{key}'] column {col_def['src_col']} "
                                    f"has invalid {col_def['type']} value '{first_data_row[col_def['src_col']]}'.", False
                                )
                                error_count += 1

            # Check remaining rows in batches with byte-based progress
            total_error_count = error_count
            batch_size = 1000
            row_count = 1  # Already processed first row
            batch_rows = []

            for row in reader:
                batch_rows.append(row)
                row_count += 1

                if len(batch_rows) >= batch_size:
                    # Process batch
                    for i, row in enumerate(batch_rows, start=row_count - len(batch_rows)):
                        if len(row) < self._min_cols:
                            worker.log_signal.emit(
                                f"Error: Row {i} contains fewer than {self._min_cols} columns.", False
                            )
                            total_error_count += 1
                        else:
                            for j, col_def_set in enumerate(self._col_defs):
                                for key, col_def in col_def_set.items():
                                    if col_def["src_col"] >= 0:
                                        if col_def["src_col"] >= len(row):
                                            worker.log_signal.emit(
                                                f"Error: col_defs[{j}]['{key}'] src_col {col_def['src_col']} "
                                                f"exceeds row {i} length {len(row)}.", False
                                            )
                                            total_error_count += 1
                                        elif row[col_def["src_col"]] and col_def["func"] == "copy":
                                            try:
                                                if col_def["type"] == "int":
                                                    int(float(row[col_def["src_col"]]))
                                                elif col_def["type"] == "float":
                                                    float(row[col_def["src_col"]])
                                            except (ValueError, TypeError):
                                                worker.log_signal.emit(
                                                    f"Error: col_defs[{j}]['{key}'] column {col_def['src_col']} "
                                                    f"has invalid {col_def['type']} value '{row[col_def['src_col']]}' in row {i}.", False
                                                )
                                                total_error_count += 1

                    # Update progress based on bytes read
                    progress = int((tracker.bytes_read / file_size) * 100)
                    worker.progress_signal.emit(progress)
                    
                    if worker.interrupt_flag or total_error_count > 100:
                        break
                    batch_rows = []

            # Process remaining rows
            if batch_rows and not (worker.interrupt_flag or total_error_count > 100):
                for i, row in enumerate(batch_rows, start=row_count - len(batch_rows)):
                    if len(row) < self._min_cols:
                        worker.log_signal.emit(
                            f"Error: Row {i} contains fewer than {self._min_cols} columns.", False
                        )
                        total_error_count += 1
                    else:
                        for j, col_def_set in enumerate(self._col_defs):
                            for key, col_def in col_def_set.items():
                                if col_def["src_col"] >= 0:
                                    if col_def["src_col"] >= len(row):
                                        worker.log_signal.emit(
                                            f"Error: col_defs[{j}]['{key}'] src_col {col_def['src_col']} "
                                            f"exceeds row {i} length {len(row)}.", False
                                        )
                                        total_error_count += 1
                                    elif row[col_def["src_col"]] and col_def["func"] == "copy":
                                        try:
                                            if col_def["type"] == "int":
                                                int(float(row[col_def["src_col"]]))
                                            elif col_def["type"] == "float":
                                                float(row[col_def["src_col"]])
                                        except (ValueError, TypeError):
                                            worker.log_signal.emit(
                                                f"Error: col_defs[{j}]['{key}'] column {col_def['src_col']} "
                                                f"has invalid {col_def['type']} value '{row[col_def['src_col']]}' in row {i}.", False
                                            )
                                            total_error_count += 1

                # Final progress update
                progress = int((tracker.bytes_read / file_size) * 100)
                worker.progress_signal.emit(progress)

        return total_error_count

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

        # Prepare insert query
        columns = [
            "event_id", "veh_id", "veh_time", "lane_id", "x_map_loc", "y_map_loc",
            "x_frenet_loc", "y_frenet_loc", "x_map_origin", "y_map_origin", "veh_lat", "veh_lon",
            "veh_speed", "veh_accel", "veh_length", "veh_width", "veh_automation",
            "osm_way_id", "osm_speed_limit", "osm_traffic_control", "preceding_veh_id", "veh_dist_trav", "event_name"
        ]
        insert_query = f"INSERT OR IGNORE INTO events ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        default_record = {col: None for col in columns}

        # Process CSV rows with byte counting
        with open(input_path, 'rb') as f_raw:
            tracker = ByteCountingStream(f_raw)
            text_stream = io.TextIOWrapper(tracker, encoding='utf-8', newline='')
            reader = csv.reader(text_stream)
            
            if self._has_header:
                next(reader)  # Skip header

            row_count = 0
            batch_size = 1000
            batch_records = []

            for row in reader:
                if worker.interrupt_flag:
                    break

                for col_def_set in self._col_defs:
                    record = default_record.copy()
                    for key, col_def in col_def_set.items():
                        try:
                            value = self._func_dict[col_def["func"]](row, col_def)
                            record[key] = value
                        except Exception as e:
                            worker.log_signal.emit(
                                f"Error: Failed to process column '{key}' in row {row_count + 1}: {str(e)}", False
                            )
                            return 1

                    batch_records.append([record[col] for col in columns])

                row_count += 1

                if len(batch_records) >= batch_size:
                    try:
                        db_connection.executemany(insert_query, batch_records)
                        db_connection.commit()
                    except sqlite3.Error as e:
                        worker.log_signal.emit(f"Error: Database insert failed: {str(e)}", False)
                        # Continue processing, ignore duplicate key errors
                    
                    batch_records = []
                    # Update progress based on bytes read
                    progress = int((tracker.bytes_read / file_size) * 100)
                    worker.progress_signal.emit(progress)

            # Process final batch
            if batch_records and not worker.interrupt_flag:
                try:
                    db_connection.executemany(insert_query, batch_records)
                    db_connection.commit()
                except sqlite3.Error:
                    pass  # Ignore duplicate key errors
                
                # Final progress update
                progress = int((tracker.bytes_read / file_size) * 100)
                worker.progress_signal.emit(progress)

        if not worker.interrupt_flag:
            worker.log_signal.emit(f"Info: {row_count} rows successfully imported", True)

        return 0