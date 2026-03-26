import json
import sqlite3
import logging
import math
import numpy as np
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar, QTextEdit, QFileDialog, QMessageBox, QLabel
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from data_import.base_process import BaseProcess
from utils import get_user_data_path
import importlib

# Set up logger
logger = logging.getLogger(__name__)

class _ImportWorkerThread(QThread):
    """Worker thread for handling import operations."""

    _SEGMENT_GAP_FACTOR = 3     # multiple of median step that marks a break
    _MIN_SEGMENT_PTS    = 2     # minimum points required per segment
    _DB_BATCH           = 2000  # rows per executemany UPDATE call

    log_signal = pyqtSignal(str, bool)
    progress_signal = pyqtSignal(int)

    def __init__(self, config: dict, input_path: str, output_path: str):
        super().__init__()
        self.config = config
        self.input_path = input_path
        self.output_path = output_path
        self.db_connection = None
        self.interrupt_flag = False
        self._continue_to_plot = False
        logger.debug(f"ImportWorkerThread initialized with input: {input_path}, output: {output_path}")

    def run(self):
        """Execute the import process."""
        logger.debug("ImportWorkerThread.run() started")
        try:
            # CRITICAL FIX: Close any existing database connection first
            # and ensure the database file is not locked before deletion
            logger.debug("Checking and cleaning up existing output file")
            
            if Path(self.output_path).exists():
                logger.debug(f"Output file exists, attempting to remove: {self.output_path}")
                
                # Try to close any existing connections to this database
                try:
                    # Create a temporary connection to ensure file is accessible
                    temp_conn = sqlite3.connect(self.output_path)
                    temp_conn.close()
                    logger.debug("Temporary connection test successful")
                except Exception as e:
                    logger.warning(f"Could not test database connection: {e}")
                
                # Add a small delay to ensure file handles are released
                import time
                time.sleep(0.1)
                
                # Now try to remove the file
                try:
                    Path(self.output_path).unlink()
                    logger.debug(f"Successfully removed existing output file: {self.output_path}")
                except PermissionError as e:
                    logger.error(f"Permission error removing file: {e}")
                    # Try to rename instead of delete (sometimes works when delete fails)
                    backup_path = Path(self.output_path).with_suffix(".sqlite.backup")
                    try:
                        Path(self.output_path).rename(backup_path)
                        logger.debug(f"Renamed existing file to: {backup_path}")
                    except Exception as rename_error:
                        logger.error(f"Could not rename file either: {rename_error}")
                        raise Exception(f"Cannot access output file {self.output_path}. File may be locked by another process.")

            # Now create the new database connection
            logger.debug(f"Creating new database connection to: {self.output_path}")
            # Create fresh connection
            self.db_connection = sqlite3.connect(self.output_path)
            
            # Set up database
            logger.debug("Setting up database")
            self.db_connection.execute("PRAGMA auto_vacuum=INCREMENTAL;")
            self.db_connection.execute("VACUUM;")
            self.db_connection.execute("""
                CREATE TABLE events (
                    event_id INTEGER NOT NULL,
                    veh_id INTEGER NOT NULL,
                    veh_time REAL NOT NULL,
                    lane_id INTEGER,
                    orig_lane_id INTEGER,
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
                    osm_speed_limit INTEGER,
                    osm_traffic_control INTEGER,
                    preceding_veh_id INTEGER,
                    veh_dist_trav REAL,
                    gap_b REAL,
                    gap_f REAL,
                    hway_b REAL,
                    hway_f REAL,
                    event_name TEXT,
                    PRIMARY KEY (event_id, veh_id, veh_time)
                ) WITHOUT ROWID;
            """)
            
            # Create ranges table for event summaries
            self.db_connection.execute("""
                CREATE TABLE ranges (
                    event_id INTEGER PRIMARY KEY,
                    summary_json TEXT NOT NULL
                );
            """)
            
            self.db_connection.commit()
            logger.debug("Database setup completed")

            # Instantiate processor
            logger.debug(f"Instantiating processor: {self.config.get('class', 'data_import.csv_process.CsvProcess')}")
            module_name, class_name = self.config.get("class", "data_import.csv_process.CsvProcess").rsplit(".", 1)
            module = importlib.import_module(module_name)
            processor_class = getattr(module, class_name)
            
            if not issubclass(processor_class, BaseProcess):
                logger.error("Processor class is not a subclass of BaseProcess")
                self.log_signal.emit("Error: Processor class is not a subclass of BaseProcess.", False)
                return

            processor = processor_class()
            logger.debug("Starting data processing")
            error_count = processor.process(self.config, self.input_path, self.db_connection, self)
            logger.debug(f"Data processing completed with {error_count} errors")

            if error_count == 0 and not self.interrupt_flag:
                if not self._compute_gap_headway():
                    self.log_signal.emit("Import interrupted", False)
                    error_count += 1
                
            if error_count == 0 and not self.interrupt_flag:
                cursor = self.db_connection.cursor()
                logger.debug("Creating event summaries")
                self.log_signal.emit("Creating event summaries", True)
                
                # Get distinct event IDs
                cursor.execute("SELECT DISTINCT event_id FROM events ORDER BY event_id")
                event_ids = [row[0] for row in cursor.fetchall()]
                
                logger.debug(f"Creating summaries for {len(event_ids)} events")
                self.log_signal.emit(f"Creating summaries for {len(event_ids)} events", True)
                
                for i, event_id in enumerate(event_ids):
                    if self.interrupt_flag:
                        logger.debug("Interrupt flag set during event summary creation")
                        break
                    
                    # Get event name
                    cursor.execute("""
                        SELECT DISTINCT event_name FROM events
                        WHERE event_id = ?
                    """, (event_id,))
                    event_name, = cursor.fetchone()
                    
                    # Get time and distance ranges
                    cursor.execute("""
                        SELECT MIN(veh_time), MAX(veh_time), MIN(veh_dist_trav), MAX(veh_dist_trav)
                        FROM events
                        WHERE event_id = ?
                    """, (event_id,))
                    time_min, time_max, dist_min, dist_max = cursor.fetchone()
                    
                    # Get unique lane IDs
                    cursor.execute("""
                        SELECT DISTINCT lane_id, orig_lane_id FROM events
                        WHERE event_id = ?
                        ORDER BY lane_id
                    """, (event_id,))
                    lane_ids = [[row[0], row[1]] for row in cursor.fetchall() if row[0] is not None]
                    
                    # Get unique vehicle IDs
                    cursor.execute("""
                        SELECT DISTINCT veh_id FROM events
                        WHERE event_id = ?
                        ORDER BY veh_id
                    """, (event_id,))
                    veh_ids = [row[0] for row in cursor.fetchall()]
                    
                    # Create summary JSON
                    summary = {
                        "event_name": event_name,
                        "veh_time": [math.floor(time_min) if time_min is not None else 0, 
                                    math.ceil(time_max) if time_max is not None else 0],
                        "veh_dist_trav": [math.floor(dist_min) if dist_min is not None else 0, 
                                        math.ceil(dist_max) if dist_max is not None else 0],
                        "lane_id": lane_ids,
                        "veh_id": veh_ids
                    }
                    
                    # Insert summary into ranges table
                    cursor.execute("""
                        INSERT INTO ranges (event_id, summary_json)
                        VALUES (?, ?)
                    """, (event_id, json.dumps(summary)))
                    
                    # Update progress bar based on event processing
                    if len(event_ids) > 0:
                        progress = int(((i + 1) / len(event_ids)) * 100)
                        self.progress_signal.emit(progress)
                        logger.debug(f"Processed event summary {i + 1}/{len(event_ids)}")
                    
                    # Check for interrupt after each event
                    if self.interrupt_flag:
                        logger.debug("Interrupt flag set after event summary processing")
                        break
                
                logger.debug("Committing final database changes")
                self.db_connection.commit()
                self.log_signal.emit(f"{self.input_path} successfully imported", True)
                logger.info(f"Import completed successfully: {self.input_path}")
            else:
                logger.debug("Import was interrupted, cleaning up")
                self.log_signal.emit("Import interrupted", False)
                if Path(self.output_path).exists():
                    Path(self.output_path).unlink()

        except Exception as e:
            logger.exception(f"Exception in ImportWorkerThread.run(): {e}")
            self.log_signal.emit(f"Error during import: {str(e)}", False)
        finally:
            logger.debug("ImportWorkerThread.run() finishing, closing database connection")
            try:
                if self.db_connection:
                    self.db_connection.close()
            except Exception as e:
                logger.error(f"Error closing database connection: {e}")

    @staticmethod
    def _build_segment_meta(
        segments: list[tuple[int, int]],
        arr: np.ndarray,
    ) -> list[tuple[int, int, float, float]]:
        """Return (start, end, min_val, max_val) for each segment.

        Pre-computing the range of each segment enables O(1) early-out checks
        in _interp_at without re-scanning the array on every query.
        """
        return [
            (s, e, float(arr[s]), float(arr[e - 1]))   # arr is sorted, so [s] is min
            for s, e in segments
        ]

    def _build_segments(self, time: np.ndarray) -> list[tuple[int, int]]:
        """Return (start, end_exclusive) index pairs for contiguous segments.

        A new segment begins wherever the gap between consecutive time values
        exceeds _SEGMENT_GAP_FACTOR times the median step for this vehicle.
        Segments with fewer than _MIN_SEGMENT_PTS points are discarded.
        """
        if len(time) < self._MIN_SEGMENT_PTS:
            return []

        diffs = np.diff(time)
        median_step = np.median(diffs[diffs > 0]) if np.any(diffs > 0) else 1.0
        threshold = self._SEGMENT_GAP_FACTOR * median_step

        breaks = np.where(diffs > threshold)[0] + 1
        starts = np.concatenate(([0], breaks))
        ends   = np.concatenate((breaks, [len(time)]))

        return [
            (int(s), int(e))
            for s, e in zip(starts, ends)
            if e - s >= self._MIN_SEGMENT_PTS
        ]

    @staticmethod
    def _interp_at(
        seg_meta:  list[tuple[int, int, float, float]],
        x_arr:     np.ndarray,
        y_arr:     np.ndarray,
        x_query:   float,
        cursor:    list[int],          # cursor[0] = last segment index used
    ) -> float:
        """Linearly interpolate y at x_query using pre-built segment metadata.

        Optimizations over the original:
        1. Segment early-out  – each segment's [min, max] is checked with a
           scalar compare before touching any numpy slice.  Because segments
           are in ascending order a break is issued as soon as seg_min >
           x_query, so later segments are never visited.
        2. Cursor (last-index)– the caller passes a single-element list that
           remembers the last intra-segment index used.  On the next call the
           search starts there rather than at the segment start, exploiting the
           fact that query values are non-decreasing across the outer loop.
        3. Linear index estimate – when a forward search is required the
           fractional position of x_query in [x0, x_end] is used to compute a
           cheap starting index, reducing the searchsorted window to ~1 step
           for uniformly-spaced data.

        Returns NaN if x_query falls outside every segment.
        """
        n_segs = len(seg_meta)
        for si in range(n_segs):
            s, e, seg_min, seg_max = seg_meta[si]

            # Early-out: query is below this segment's range.
            if x_query < seg_min:
                break                       # segments are ordered; none later will match

            # Early-out: query is above this segment's range.
            if x_query > seg_max:
                continue

            # x_query is within [seg_min, seg_max].
            seg_len = e - s                 # number of points in segment
            lo = cursor[0]                  # last index used (relative to segment start)

            # Clamp cursor to this segment's valid range.
            if lo >= seg_len - 1:
                lo = 0

            # Linear estimate of the intra-segment index.
            span = seg_max - seg_min
            if span > 0.0:
                frac   = (x_query - seg_min) / span
                est    = int(frac * (seg_len - 2))
                lo     = max(lo, max(0, est - 1))   # never go backward

            # Narrow searchsorted to [lo, seg_len-1].
            xs     = x_arr[s:e]
            lo     = min(lo, seg_len - 2)
            idx    = int(np.searchsorted(xs[lo:], x_query, side="right")) - 1 + lo
            idx    = max(0, min(idx, seg_len - 2))

            # Persist cursor for the next call.
            cursor[0] = idx

            x0, x1 = xs[idx], xs[idx + 1]
            dx = x1 - x0
            if dx == 0.0:
                return float(y_arr[s + idx])
            t = (x_query - x0) / dx
            return float(y_arr[s + idx] * (1.0 - t) + y_arr[s + idx + 1] * t)

        return float("nan")

    def _compute_gap_headway(self) -> bool:
        """Compute gap and time-headway for every (event_id, lane_id) pair."""
        try:
            cursor = self.db_connection.cursor()

            cursor.execute("""
                SELECT DISTINCT event_id, lane_id
                FROM   events
                WHERE  lane_id IS NOT NULL
                ORDER  BY event_id, lane_id
            """)
            pairs = cursor.fetchall()

            if not pairs:
                self.log_signal.emit("Info: No event/lane pairs found; skipping gap/headway.", True)
                return True

            total = len(pairs)
            self.log_signal.emit("Info: Computing gap and headway values", True)
            self.log_signal.emit(
                f"Info: {total} unique event_id/lane_id combination(s) to process", True
            )

            for idx, (event_id, lane_id) in enumerate(pairs):
                if self.interrupt_flag:
                    return False

                # ── load ──────────────────────────────────────────────────────
                cursor.execute("""
                    SELECT veh_id, veh_time, veh_dist_trav
                    FROM   events
                    WHERE  event_id = ? AND lane_id = ?
                    ORDER  BY veh_id, veh_time
                """, (event_id, lane_id))

                raw: dict[int, dict] = {}
                for veh_id, veh_time, veh_dist in cursor:
                    if veh_dist is None:
                        continue
                    if veh_id not in raw:
                        raw[veh_id] = {"times": [], "dists": []}
                    raw[veh_id]["times"].append(veh_time)
                    raw[veh_id]["dists"].append(veh_dist)

                if len(raw) < 2:
                    self.progress_signal.emit(int(((idx + 1) / total) * 100))
                    continue

                # ── build arrays + segment metadata ───────────────────────────
                veh_data: dict[int, dict] = {}
                for veh_id, d in raw.items():
                    t = np.array(d["times"], dtype=np.float64)
                    x = np.array(d["dists"], dtype=np.float64)
                    order = np.argsort(t, kind="stable")
                    t, x = t[order], x[order]
                    segs_t = self._build_segments(t)
                    segs_d = self._build_segments(x)
                    veh_data[veh_id] = {
                        "time":       t,
                        "dist":       x,
                        "meta_t":     self._build_segment_meta(segs_t, t),
                        "meta_d":     self._build_segment_meta(segs_d, x),
                    }

                # ── compute ───────────────────────────────────────────────────
                veh_ids = list(veh_data.keys())
                rows: list[tuple] = []

                _cursors: dict[tuple, list[int]] = {}
                for veh_id in veh_ids:
                    if self.interrupt_flag:
                        return False

                    v      = veh_data[veh_id]
                    n_pts  = len(v["time"])

                    gap_f  = np.full(n_pts, np.nan)
                    gap_b  = np.full(n_pts, np.nan)
                    hway_f = np.full(n_pts, np.nan)
                    hway_b = np.full(n_pts, np.nan)

                    for i in range(n_pts):
                        t_i = v["time"][i]
                        d_i = v["dist"][i]

                        best_f_gap  = np.inf
                        best_b_gap  = np.inf
                        best_f_hway = np.inf
                        best_b_hway = np.inf

                        g_f = g_b = h_f = h_b = np.nan

                        for other_id in veh_ids:
                            if other_id == veh_id:
                                continue

                            o = veh_data[other_id]

                            # Each cursor is per-(subject, other, query_type) and
                            # persists across the i-loop because t_i / d_i are
                            # non-decreasing.  Stored as a 1-element list so the
                            # static method can mutate it without a nonlocal.
                            cur_t_key = (veh_id, other_id, "t")
                            cur_d_key = (veh_id, other_id, "d")
                            if cur_t_key not in _cursors:
                                _cursors[cur_t_key] = [0]
                                _cursors[cur_d_key] = [0]

                            # Gap: interpolate other's dist at our time
                            d_other = self._interp_at(
                                o["meta_t"], o["time"], o["dist"],
                                t_i, _cursors[cur_t_key]
                            )
                            if np.isfinite(d_other):
                                delta = d_other - d_i
                                if delta >= 0 and delta < best_f_gap:
                                    best_f_gap = delta
                                    g_f = delta
                                elif delta < 0 and (-delta) < best_b_gap:
                                    best_b_gap = -delta
                                    g_b = -delta

                            # Headway: interpolate other's time at our dist
                            t_other = self._interp_at(
                                o["meta_d"], o["dist"], o["time"],
                                d_i, _cursors[cur_d_key]
                            )
                            if np.isfinite(t_other):
                                delta_t = t_i - t_other
                                if delta_t >= 0 and delta_t < best_f_hway:
                                    best_f_hway = delta_t
                                    h_f = delta_t
                                elif delta_t < 0 and (-delta_t) < best_b_hway:
                                    best_b_hway = -delta_t
                                    h_b = -delta_t

                        gap_f[i]  = g_f
                        gap_b[i]  = g_b
                        hway_f[i] = h_f
                        hway_b[i] = h_b

                    for i, t in enumerate(v["time"]):
                        rows.append((
                            float(gap_f[i]),
                            float(gap_b[i]),
                            float(hway_f[i]),
                            float(hway_b[i]),
                            event_id,
                            veh_id,
                            float(t),
                        ))

                # ── flush ─────────────────────────────────────────────────────
                for batch_start in range(0, len(rows), self._DB_BATCH):
                    if self.interrupt_flag:
                        return False
                    self.db_connection.executemany("""
                        UPDATE events
                        SET    gap_f  = ?,
                               gap_b  = ?,
                               hway_f = ?,
                               hway_b = ?
                        WHERE  event_id = ? AND veh_id = ? AND veh_time = ?
                    """, rows[batch_start : batch_start + self._DB_BATCH])
                    self.db_connection.commit()

                self.progress_signal.emit(int(((idx + 1) / total) * 100))

            return True

        except sqlite3.Error as e:
            self.log_signal.emit(f"Error: Database error during gap/headway: {e}", False)
            logger.exception("Gap/headway sqlite error")
            return False
        except Exception as e:
            self.log_signal.emit(f"Error: Unexpected error during gap/headway: {e}", False)
            logger.exception("Gap/headway unexpected error")
            return False

class ImportDialog(QDialog):
    """Modal dialog for importing trajectory data into SQLite databases.

    Signals:
        import_finished (str): Emitted when the dialog closes with the most recently imported database file path.
    """

    import_finished = pyqtSignal(str)  # Signal to emit output path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Trajectory Data")
        self.setFixedSize(800, 600)
        self._input_path = ""
        self._output_path = ""
        self._configs = {}
        self._selected_file_type = ""
        self._worker_thread = None
        # Use get_user_data_path for log file to work in both development and PyInstaller environments
        self._log_file = get_user_data_path("trajectory_tools.log")
        self._import_completed = False  # Track if import completed successfully
        self._successful_import_path = ""  # Store path of successful import
        logger.debug("ImportDialog initialized")
        self._setup_ui()
        self._center_dialog()
        self._initialize()

    def __del__(self):
        logger.debug("ImportDialog.__del__ called")

    def _setup_ui(self):
        """Set up the dialog layout and widgets."""
        logger.debug("Setting up UI")
        layout = QVBoxLayout(self)
        
        # Text edit
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        layout.addWidget(self._text_edit, stretch=1)
        
        # Progress bar (moved between text area and buttons)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        layout.addWidget(self._progress_bar)
        
        # Button row
        button_layout = QHBoxLayout()
        self._input_button = QPushButton("Input")
        self._output_button = QPushButton("Output")
        self._import_button = QPushButton("Import")
        self._interrupt_button = QPushButton("Interrupt")

        self._input_button.setToolTip("Select the input file to import")
        self._output_button.setToolTip("Set the database output path")
        self._import_button.setToolTip("Begin importing the selected file")
        self._interrupt_button.setToolTip("Stop the running import process")
        
        self._input_button.setEnabled(False)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(False)

        button_layout.addStretch()
        button_layout.addWidget(self._input_button)
        button_layout.addStretch()
        button_layout.addWidget(self._output_button)
        button_layout.addStretch()
        button_layout.addWidget(self._import_button)
        button_layout.addStretch()
        button_layout.addWidget(self._interrupt_button)
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        # Connect signals
        self._input_button.clicked.connect(self._select_input)
        self._output_button.clicked.connect(self._select_output)
        self._import_button.clicked.connect(self._on_import)
        self._interrupt_button.clicked.connect(self._interrupt_import)

    def _center_dialog(self):
        """Center the dialog within the parent window."""
        parent = self.parent()
        if parent:
            parent_rect = parent.geometry()
            dialog_rect = self.geometry()
            x = parent_rect.x() + (parent_rect.width() - dialog_rect.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - dialog_rect.height()) // 2
            self.move(x, y)

    def _initialize(self):
        """Initialize the dialog by checking configuration files."""
        logger.debug("Initializing dialog")
        # Keep close button enabled throughout
        
        # Clear log file
        if self._log_file.exists():
            self._log_file.unlink()
        
        # Use get_user_data_path for config directory to work in both development and PyInstaller environments
        config_dir = get_user_data_path("config")
        config_dir.mkdir(exist_ok=True)
        
        # Check JSON configuration files
        config_files = list(config_dir.glob("*.json"))
        if not config_files:
            self._log_message("Error: No JSON configuration files found in 'config' directory.", False)
            return
        
        for config_file in config_files:
            try:
                with config_file.open('r') as f:
                    config = json.load(f)
                
                if not isinstance(config, dict):
                    self._log_message(f"Error: {config_file} does not contain a JSON object.", False)
                    continue
                
                if "col_defs" not in config:
                    self._log_message(f"Error: {config_file} missing 'col_defs' key.", False)
                    continue
                
                config.setdefault("file_ext", "csv")
                config.setdefault("class", "data_import.csv_process.CsvProcess")
                self._configs[Path(config_file).stem] = config

            except json.JSONDecodeError:
                self._log_message(f"Error: {config_file} is not a valid JSON file.", False)
        
        if not self._configs:
            self._log_message("Error: No valid JSON configuration files found. Import cannot continue.", False)
        else:
            file_types = ", ".join(self._configs.keys())
            self._log_message(f"Info: Available file types for import: {file_types}", True)
            self._log_message("Info: Please select an input file.", True)
            self._input_button.setEnabled(True)

    def _log_message(self, message: str, is_html: bool = False):
        """Log a message to the text edit and log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        display_message = f"[{timestamp}] {message}"
        
        if is_html:
            self._text_edit.append(display_message)
        else:
            self._text_edit.append(display_message.replace("<", "<").replace(">", ">"))
        
        # Write plain text to log file
        plain_message = display_message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        with self._log_file.open('a') as f:
            f.write(plain_message + "\n")
        
        self._text_edit.verticalScrollBar().setValue(self._text_edit.verticalScrollBar().maximum())

    def _select_input(self):
        """Handle input file selection."""
        logger.debug("Selecting input file")
        file_types = ";;".join([f"{ft} (*.{config['file_ext']})" for ft, config in self._configs.items()])
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select Input File",
            "",
            file_types
        )
        
        if file_path:
            self._input_path = file_path
            self._selected_file_type = selected_filter.split(" (")[0]
            self._output_path = str(Path(file_path).with_suffix(".sqlite"))
            logger.debug(f"Input file selected: {file_path}, type: {self._selected_file_type}")
            self._log_message(f"Info: Input file selected: {file_path}", True)
            self._log_message(f"Info: Output file set to: {self._output_path}", True)
            if Path(self._output_path).exists():
                self._log_message(f"Warning: Output file {self._output_path} will be overwritten.", False)
            self._log_message("Info: You can change the output file.", True)
            self._output_button.setEnabled(True)
            self._import_button.setEnabled(True)

    def _select_output(self):
        """Handle output file selection."""
        logger.debug("Selecting output file")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output SQLite Database",
            self._output_path,
            "SQLite Database (*.sqlite)"
        )
        
        if file_path:
            self._output_path = file_path
            logger.debug(f"Output file changed to: {file_path}")
            self._log_message(f"Info: Output file changed to: {file_path}", True)

    def _on_import(self):
        QTimer.singleShot(200, self._start_import) # delay for button repaint

    def _start_import(self):
        """Start the import process in a separate thread."""
        logger.debug("Starting import process")
        
        # Keep close button enabled, handle close logic in closeEvent
        self._input_button.clearFocus()
        self._input_button.setEnabled(False)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(True)
        self._import_completed = False  # Reset completion flag
        
        if not self._selected_file_type or self._selected_file_type not in self._configs:
            logger.error(f"Invalid file type: {self._selected_file_type}")
            self._log_message(f"Error: Invalid or missing file type '{self._selected_file_type}'.", False)
            self._input_button.setEnabled(True)
            return
        
        config = self._configs[self._selected_file_type]
        
        logger.debug("Creating worker thread")
        self._worker_thread = _ImportWorkerThread(config, self._input_path, self._output_path)
        
        # Connect signals directly to the thread
        self._worker_thread.log_signal.connect(self._log_message)
        self._worker_thread.progress_signal.connect(self._progress_bar.setValue)
        self._worker_thread.finished.connect(self._finish_import)
        
        logger.debug("Starting worker thread")
        self._worker_thread.start()

    def _interrupt_import(self):
        """Handle interrupt button click."""
        logger.debug("Interrupt requested")
        if self._worker_thread:
            self._worker_thread.interrupt_flag = True
        self._interrupt_button.setEnabled(False)
        self._log_message("Warning: Interrupt requested", False)

    def _show_completion_popup(self, success: bool):
        """Show a completion popup with context-appropriate buttons.
        
        Args:
            success (bool): Whether the import completed successfully.
        """
        popup = QDialog(self)
        popup.setWindowTitle("Import Complete" if success else "Import Unsuccessful")
        popup.setFixedSize(400, 150)
        popup.setWindowFlags(
            popup.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
    
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
    
        # Message label
        if success:
            msg = QLabel(
                "The file was imported successfully.\n\n"
                "Click Continue to open the plot settings.\n"
                "Click Back to import another file."
            )
        else:
            msg = QLabel(
                "The import did not complete successfully.\n\n"
                "Click Back to review the import log for details."
            )
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg)
    
        # Button row
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
    
        back_btn = QPushButton("Back")
        back_btn.setMinimumWidth(80)
        back_btn.clicked.connect(popup.reject)
        btn_layout.addWidget(back_btn)
    
        if success:
            continue_btn = QPushButton("Continue")
            continue_btn.setMinimumWidth(80)
            continue_btn.setDefault(True)
            continue_btn.clicked.connect(popup.accept)
            btn_layout.addWidget(continue_btn)
    
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
    
        result = popup.exec()
    
        if success and result == QDialog.DialogCode.Accepted:
            self._continue_to_plot = True
            self.close()  # triggers closeEvent which emits import_finished
    
    def _finish_import(self):
        """Handle import completion without closing dialog."""
        logger.debug("Import process completed")
    
        self._progress_bar.setValue(0)
    
        if (not getattr(self._worker_thread, 'interrupt_flag', False) and
                self._output_path and
                Path(self._output_path).exists()):
            self._import_completed = True
            self._successful_import_path = self._output_path
            logger.debug("Import completed successfully")
        else:
            self._import_completed = False
            self._successful_import_path = ""
            logger.debug("Import was interrupted or failed")
    
        if self._worker_thread:
            self._worker_thread.wait()
            self._worker_thread.deleteLater()
            self._worker_thread = None
    
        self._input_path = ""
        self._output_path = ""
        self._selected_file_type = ""
    
        self._input_button.setEnabled(True)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(False)
    
        logger.debug("Import process finished, dialog ready for next import")
    
        # Show completion popup after UI is fully reset
        self._show_completion_popup(self._import_completed)

    def closeEvent(self, event):
        """Handle dialog close event and emit the output path."""
        logger.debug("Dialog close event triggered")
        
        # If import is running, ask user for confirmation
        if self._worker_thread and self._worker_thread.isRunning():
            logger.debug("Import is running, asking user for confirmation")
            
            reply = QMessageBox.question(
                self,
                "Import In Progress",
                "An import process is currently running. Do you want to interrupt it and close the dialog?\n\n"
                "Warning: Interrupting the import will cancel the process and any partial results will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel  # Default to Cancel for safety
            )
            
            if reply == QMessageBox.StandardButton.Cancel:
                logger.debug("User chose to cancel dialog close")
                event.ignore()  # Prevent the dialog from closing
                return
            
            # User chose Yes - interrupt the import
            logger.debug("User confirmed to interrupt import and close dialog")
            self._worker_thread.interrupt_flag = True
            self._log_message("Warning: Import interrupted by user", False)
            
            # Wait for thread to finish
            self._worker_thread.quit()
            success = self._worker_thread.wait(3000)
            if not success:
                logger.warning("Forcing thread termination")
                self._worker_thread.terminate()
                self._worker_thread.wait()
            
            # Mark import as not completed since it was interrupted
            self._import_completed = False
        
        # Only emit signal if import was completed successfully
        if self._import_completed and self._successful_import_path and Path(self._successful_import_path).exists():
            logger.debug(f"Emitting import_finished signal with path: {self._successful_import_path}")
            self.import_finished.emit(self._successful_import_path)
        else:
            logger.debug("No valid output path to emit or import was not completed")
            self.import_finished.emit("")
        
        event.accept()