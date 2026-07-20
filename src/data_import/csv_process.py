"""CSV import processor.

Implements the four-stage BaseProcess interface for CSV source files driven
by a JSON ``col_defs`` configuration. The configuration defines the database
columns to create (schema is config-driven — there is no fixed events table
layout) and how each column is populated from the CSV rows.

Key rules enforced here:
    * Column keys: lowercase letters with optional internal underscores
      (no digits, no leading/trailing underscore).
    * Internal reserved names (computed by later analysis steps) are
      rejected as configuration keys.
    * Required columns per definition set: event_id, veh_id, veh_time,
      lane_id, veh_dist_trav — each must have a populating function.
    * Recognized reserved columns have their storage type forced.
    * ``src_col`` is deprecated: source columns are declared through
      ``parms`` and each import function publishes which of its parms are
      CSV column indices via metadata attached in csv_algorithms.
    * A definition with no ``func`` is a placeholder: the column is created
      but no data is imported into it.
    * Array-form col_defs: keys may repeat across sets (with consistent
      type/units); the events schema is the union of keys in first-seen
      order; each set independently satisfies the required columns.
"""

import csv
import sqlite3
import io
import os
import re
from typing import Any, Callable, Dict, List

from .base_process import BaseProcess, LogFn
from .csv_algorithms import CsvAlgorithms
from .units import Units

_units = Units()

# Lowercase words separated by single underscores; no digits.
_KEY_RE = re.compile(r"^[a-z]+(_[a-z]+)*$")

# Must be present, with a populating function, in every definition set.
_REQUIRED_COLS = ("event_id", "veh_id", "veh_time", "lane_id", "veh_dist_trav")

# Created internally by analysis modules; may never appear in a configuration.
_INTERNAL_RESERVED = {
    "time_diff", "veh_speed_cal", "veh_accel_cal", "spacing_cal",
    "veh_jerk_cal", "is_spline_imputed", "summary_json",
}
_INTERNAL_RESERVED_PREFIXES = ("calc_",)

# Recognized reserved columns whose storage type is forced regardless of the
# configured "type". Any reserved column not listed as int/text is float.
_FORCED_INT_COLS = {
    "event_id", "veh_id", "lane_id", "orig_lane_id", "veh_automation",
    "osm_way_id", "osm_speed_limit", "osm_traffic_control",
    "preceding_veh_id",
}
_FORCED_TEXT_COLS = {"event_name"}
_FORCED_FLOAT_COLS = {
    "veh_time", "veh_dist_trav", "veh_speed", "veh_accel", "veh_jerk",
    "veh_length", "veh_width", "veh_spacing", "veh_time_headway",
    "x_map_loc", "y_map_loc", "x_frenet_loc", "y_frenet_loc",
    "x_map_origin", "y_map_origin", "veh_lat", "veh_lon",
}

_SQL_AFFINITY = {"int": "INTEGER", "float": "REAL", "text": "TEXT"}
_VALID_TYPES = frozenset(_SQL_AFFINITY)


def _forced_type(key: str) -> str:
    """Return the forced JSON-level type for a reserved column, or "" if the
    key is user-defined (no forcing)."""
    if key in _FORCED_INT_COLS:
        return "int"
    if key in _FORCED_TEXT_COLS:
        return "text"
    if key in _FORCED_FLOAT_COLS:
        return "float"
    return ""


def _default_source_cols(parms: List[Any]) -> List[Any]:
    """Fallback source-column extractor for functions without metadata:
    every integer parameter is treated as a CSV column index."""
    return [p for p in parms if isinstance(p, int)]


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
    """Processor for importing CSV files into SQLite based on a JSON
    configuration. One instance runs one import; create a fresh instance
    for each run."""

    def __init__(self):
        super().__init__()
        self._func_dict: Dict[str, Callable] = {}
        self._has_header = False
        self._min_cols = 1
        self._col_defs: List[Dict[str, Any]] = []
        self._columns: List[str] = []          # union of keys, first-seen order
        self._col_types: Dict[str, str] = {}   # key -> "int" | "float" | "text"
        self._validated = False
        self._algorithms = CsvAlgorithms()
        self._initialize_func_dict()

    def _initialize_func_dict(self):
        """Dynamically populate _func_dict with public methods from
        CsvAlgorithms."""
        for method_name in dir(self._algorithms):
            if not method_name.startswith('_'):
                method = getattr(self._algorithms, method_name)
                if callable(method):
                    self._func_dict[method_name] = method

    # ── Stage 1: configuration ────────────────────────────────────────────

    def check_configuration(self, config: Dict[str, Any], log_fn: LogFn) -> int:
        """Validate the JSON configuration and retain column definitions,
        the union schema, and per-column source metadata."""
        log_fn("Info: Checking configured column definitions", True)
        error_count = 0

        col_defs = config.get("col_defs", {})
        if isinstance(col_defs, dict):
            self._col_defs = [col_defs]
        elif isinstance(col_defs, list):
            self._col_defs = col_defs
        else:
            log_fn("Error: 'col_defs' must be an object or array.", False)
            return 1

        self._columns = []
        self._col_types = {}
        self._min_cols = 1

        for i, col_def_set in enumerate(self._col_defs):
            if not isinstance(col_def_set, dict):
                log_fn(f"Error: col_defs[{i}] is not a dictionary.", False)
                error_count += 1
                continue

            for key, col_def in col_def_set.items():
                if not isinstance(col_def, dict):
                    log_fn(f"Error: col_defs[{i}]['{key}'] is not a dictionary.", False)
                    error_count += 1
                    continue

                # ── Key naming rules ──────────────────────────────────────
                if not _KEY_RE.match(key):
                    log_fn(
                        f"Error: Invalid column key '{key}' in col_defs[{i}]. "
                        "Keys must be lowercase letters with optional internal "
                        "underscores.", False
                    )
                    error_count += 1
                    continue

                if key in _INTERNAL_RESERVED or key.startswith(_INTERNAL_RESERVED_PREFIXES):
                    log_fn(
                        f"Error: Column key '{key}' in col_defs[{i}] is reserved "
                        "for internal computation and may not be configured.", False
                    )
                    error_count += 1
                    continue

                # ── Deprecated key ────────────────────────────────────────
                if "src_col" in col_def:
                    log_fn(
                        f"Error: 'src_col' in col_defs[{i}]['{key}'] is deprecated. "
                        "Declare the source column as parms[0] of a 'func' "
                        "(e.g. {\"func\": \"copy\", \"parms\": [0]}).", False
                    )
                    error_count += 1
                    continue

                # ── Defaults ──────────────────────────────────────────────
                col_def.setdefault("type", "float")
                col_def.setdefault("units", "")
                col_def.setdefault("parms", [])
                # NOTE: no default for "func" — its absence means placeholder.

                if col_def["type"] not in _VALID_TYPES:
                    log_fn(
                        f"Error: Invalid type '{col_def['type']}' in "
                        f"col_defs[{i}]['{key}']. Must be one of "
                        f"{sorted(_VALID_TYPES)}.", False
                    )
                    error_count += 1
                    continue

                if not isinstance(col_def["parms"], list):
                    log_fn(f"Error: 'parms' in col_defs[{i}]['{key}'] must be an array.", False)
                    error_count += 1
                    continue

                # ── Forced types for reserved columns ─────────────────────
                forced = _forced_type(key)
                if forced and col_def["type"] != forced:
                    log_fn(
                        f"Warning: Column '{key}' in col_defs[{i}] declared type "
                        f"'{col_def['type']}' but reserved columns are stored as "
                        f"'{forced}'. Overriding.", False
                    )
                    col_def["type"] = forced

                # ── Cross-set consistency and union schema ────────────────
                if key in self._col_types:
                    if self._col_types[key] != col_def["type"]:
                        log_fn(
                            f"Error: Column '{key}' in col_defs[{i}] has type "
                            f"'{col_def['type']}' but was previously defined as "
                            f"'{self._col_types[key]}'. Types must be consistent "
                            "across definition sets.", False
                        )
                        error_count += 1
                        continue
                else:
                    self._col_types[key] = col_def["type"]
                    self._columns.append(key)

                # ── Function, parms, and source columns ───────────────────
                func_name = col_def.get("func")
                if func_name is None:
                    if col_def["parms"]:
                        log_fn(
                            f"Error: col_defs[{i}]['{key}'] provides 'parms' "
                            "without 'func'. Omit both for a placeholder column, "
                            "or add the intended function.", False
                        )
                        error_count += 1
                    col_def["_src_cols"] = []
                elif func_name not in self._func_dict:
                    log_fn(
                        f"Error: Invalid function '{func_name}' in "
                        f"col_defs[{i}] for '{key}'.", False
                    )
                    error_count += 1
                    continue
                else:
                    extractor = getattr(
                        self._func_dict[func_name], "_source_cols",
                        _default_source_cols
                    )
                    try:
                        src_cols = extractor(col_def["parms"])
                    except IndexError:
                        log_fn(
                            f"Error: 'parms' in col_defs[{i}]['{key}'] has too "
                            f"few entries for function '{func_name}'.", False
                        )
                        error_count += 1
                        continue

                    bad = [c for c in src_cols
                           if not isinstance(c, int) or isinstance(c, bool) or c < 0]
                    if bad:
                        log_fn(
                            f"Error: col_defs[{i}]['{key}'] source column "
                            f"indices {bad} must be non-negative integers.", False
                        )
                        error_count += 1
                        continue

                    col_def["_src_cols"] = src_cols
                    for c in src_cols:
                        self._min_cols = max(self._min_cols, c + 1)

                # ── Units ─────────────────────────────────────────────────
                src_u = col_def["units"]
                if src_u and key not in Units.SYSTEM_UNITS:
                    log_fn(
                        f"Warning: 'units' on user-defined column '{key}' in "
                        f"col_defs[{i}] has no effect; value stored without "
                        "conversion.", False
                    )
                    col_def["_factor"] = 1.0
                elif src_u:
                    dst_u = _units.system_units(key)
                    if not _units.is_known(src_u):
                        log_fn(
                            f"Error: Unknown units '{src_u}' for column '{key}' "
                            f"in col_defs[{i}].", False
                        )
                        error_count += 1
                    else:
                        try:
                            col_def["_factor"] = _units.factor(src_u, dst_u)
                        except KeyError:
                            log_fn(
                                f"Error: No conversion from '{src_u}' to "
                                f"'{dst_u}' for column '{key}' in "
                                f"col_defs[{i}].", False
                            )
                            error_count += 1
                else:
                    col_def["_factor"] = 1.0

        # ── Required columns per definition set ───────────────────────────
        for i, col_def_set in enumerate(self._col_defs):
            if not isinstance(col_def_set, dict):
                continue
            missing = [c for c in _REQUIRED_COLS if c not in col_def_set]
            if missing:
                log_fn(f"Error: Missing required columns {missing} in col_defs[{i}].", False)
                error_count += 1
            for c in _REQUIRED_COLS:
                if c in col_def_set and isinstance(col_def_set[c], dict) \
                        and col_def_set[c].get("func") is None:
                    log_fn(
                        f"Error: Required column '{c}' in col_defs[{i}] must "
                        "have a populating 'func' — it cannot be a placeholder.",
                        False
                    )
                    error_count += 1

        # ── Summary ───────────────────────────────────────────────────────
        if error_count == 0:
            self._validated = True
            log_fn(f"Info: Found {len(self._col_defs)} column definition set(s).", True)
            log_fn(f"Info: Events table will contain {len(self._columns)} columns.", True)
            for i, col_def_set in enumerate(self._col_defs):
                log_fn(f"Info: col_defs[{i}] contains {len(col_def_set)} columns:", True)
                for key, col_def in col_def_set.items():
                    func_name = col_def.get("func")
                    if func_name is None:
                        log_fn(f"Info: {key} is a placeholder (no data imported)", True)
                    elif col_def["_src_cols"]:
                        log_fn(
                            f"Info: {key} reads column(s) "
                            f"{col_def['_src_cols']} using {func_name}", True
                        )
                    else:
                        log_fn(f"Info: {key} is using {func_name}", True)

        return error_count

    # ── Stage 2: schema ───────────────────────────────────────────────────

    def column_defs(self) -> Dict[str, str]:
        """Return the ordered column-name to SQLite-affinity mapping."""
        if not self._validated:
            raise RuntimeError("column_defs() called before successful check_configuration()")
        return {name: _SQL_AFFINITY[self._col_types[name]] for name in self._columns}

    # ── Stage 3: file checking ────────────────────────────────────────────

    def check_file(self, input_path: str, worker: Any) -> int:
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

            error_count += self._check_row(first_data_row, 1, worker)
            if len(first_data_row) < self._min_cols:
                return error_count  # row too short is already fatal enough

            # Check remaining rows in batches with byte-based progress
            total_error_count = error_count
            batch_size = 1000
            row_count = 1
            batch_rows = []

            for row in reader:
                batch_rows.append(row)
                row_count += 1

                if len(batch_rows) >= batch_size:
                    for r_idx, r in enumerate(batch_rows, start=row_count - len(batch_rows) + 1):
                        total_error_count += self._check_row(r, r_idx, worker)

                    progress = int((tracker.bytes_read / file_size) * 100)
                    worker.progress_signal.emit(progress)

                    if worker.interrupt_flag or total_error_count > 100:
                        break
                    batch_rows = []

            if batch_rows and not (worker.interrupt_flag or total_error_count > 100):
                for r_idx, r in enumerate(batch_rows, start=row_count - len(batch_rows) + 1):
                    total_error_count += self._check_row(r, r_idx, worker)

                progress = int((tracker.bytes_read / file_size) * 100)
                worker.progress_signal.emit(progress)

        return total_error_count

    def _check_row(self, row: List[str], row_num: int, worker: Any) -> int:
        """Validate one CSV row: width, source-column bounds, and copy-value
        types. Returns the number of errors found in the row."""
        errors = 0

        if len(row) < self._min_cols:
            worker.log_signal.emit(
                f"Error: Row {row_num} contains fewer than {self._min_cols} columns.", False
            )
            return 1

        for j, col_def_set in enumerate(self._col_defs):
            for key, col_def in col_def_set.items():
                for c in col_def.get("_src_cols", []):
                    if c >= len(row):
                        worker.log_signal.emit(
                            f"Error: col_defs[{j}]['{key}'] source column {c} "
                            f"exceeds row {row_num} length {len(row)}.", False
                        )
                        errors += 1

                # Value type check applies to copy only; text accepts anything.
                if col_def.get("func") == "copy" and col_def["type"] in ("int", "float"):
                    src = col_def["parms"][0]
                    if src < len(row) and row[src]:
                        try:
                            if col_def["type"] == "int":
                                int(float(row[src]))
                            else:
                                float(row[src])
                        except (ValueError, TypeError):
                            worker.log_signal.emit(
                                f"Error: col_defs[{j}]['{key}'] column {src} "
                                f"has invalid {col_def['type']} value "
                                f"'{row[src]}' in row {row_num}.", False
                            )
                            errors += 1

        return errors

    # ── Stage 4: data import ──────────────────────────────────────────────

    def import_data(self, input_path: str, db_connection: sqlite3.Connection,
                    worker: Any) -> int:
        """Import the CSV file into the events table."""
        file_size = os.path.getsize(input_path)
        worker.log_signal.emit(f"Info: Processing file of size {file_size:,} bytes", True)

        columns = self._columns
        insert_query = (
            f"INSERT OR IGNORE INTO events ({', '.join(columns)}) "
            f"VALUES ({', '.join(['?'] * len(columns))})"
        )
        default_record = {col: None for col in columns}

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
                        func_name = col_def.get("func")
                        if func_name is None:
                            continue  # placeholder column stays None
                        try:
                            value = self._func_dict[func_name](row, col_def)
                            if value is not None:
                                factor = col_def["_factor"]
                                if factor != 1.0:
                                    value *= factor
                            record[key] = value
                        except Exception as e:
                            worker.log_signal.emit(
                                f"Error: Failed to process column '{key}' in "
                                f"row {row_count + 1}: {str(e)}", False
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
                    progress = int((tracker.bytes_read / file_size) * 100)
                    worker.progress_signal.emit(progress)

            if batch_records and not worker.interrupt_flag:
                try:
                    db_connection.executemany(insert_query, batch_records)
                    db_connection.commit()
                except sqlite3.Error:
                    pass  # Ignore duplicate key errors

                progress = int((tracker.bytes_read / file_size) * 100)
                worker.progress_signal.emit(progress)

        if not worker.interrupt_flag:
            worker.log_signal.emit(f"Info: {row_count} rows successfully imported", True)

        return 0