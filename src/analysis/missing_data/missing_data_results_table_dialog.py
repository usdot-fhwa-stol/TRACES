import csv
import logging
import math
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
)
from utils import last_dialog_dir, remember_dialog_path

logger = logging.getLogger(__name__)

class MissingDataResultsTableDialog(QDialog):
    """Tabular view of MissingDataComp results, one row per (event_id, veh_id).

    Used by the right-click invocation of Missing Data analysis. Provides a
    'Save…' button to write the table to CSV.
    """

    _COLUMNS = [
        ("Event ID",         "event_id",           "{:d}",   Qt.AlignmentFlag.AlignRight),
        ("Vehicle ID",       "veh_id",             "{:d}",   Qt.AlignmentFlag.AlignRight),
        ("Gap ID",           "gap_id",             "{}",     Qt.AlignmentFlag.AlignRight), # {} handles integers or "N/A" strings
        ("Missing Record %", "missing_record_pct", "{:.2f}", Qt.AlignmentFlag.AlignRight),
        ("Number of Gaps",   "num_gaps",           "{:d}",   Qt.AlignmentFlag.AlignRight),
        ("Gap Length",       "gap_length",         "{}",     Qt.AlignmentFlag.AlignRight), # {} handles integers or "N/A" strings
    ]

    def __init__(self, parent, results):
        """
        Args:
            parent: parent widget
            results (list[MissingDataResult]): output from MissingDataComp.comp_series
        """
        super().__init__(parent)
        self.setWindowTitle("Missing Data Analysis Results")
        self.setModal(True)

        parent_height = parent.height() if parent else 600
        self.resize(780, int(parent_height * 0.6))

        # Flatten the trajectory results into individual gap rows
        self._flat_rows = self._flatten_results(results)

        self._setup_ui()
        self._populate_table()
        QTimer.singleShot(0, self._center_dialog)

    def _flatten_results(self, results):
        """
        Transforms trajectory-level results into row-level dictionaries.
        If a vehicle has 3 gaps, it generates 3 rows and calculates the Gap ID.
        """
        flat = []
        # Sort to ensure vehicle blocks are kept together in ascending order
        sorted_results = sorted(results, key=lambda r: (r.event_id, r.veh_id))
        
        for r in sorted_results:
            if r.num_gaps == 0:
                # If no gaps exist, show the trajectory with N/A for gap-specific columns
                flat.append({
                    "event_id":           r.event_id,
                    "veh_id":             r.veh_id,
                    "gap_id":             "N/A",
                    "missing_record_pct": r.missing_record_pct,
                    "num_gaps":           0,
                    "gap_length":         "N/A"
                })
            else:
                # If gaps exist, loop through the lengths array and use enumeration for Gap ID
                for gap_index, length in enumerate(r.gap_lengths, start=1):
                    flat.append({
                        "event_id":           r.event_id,
                        "veh_id":             r.veh_id,
                        "gap_id":             gap_index,
                        "missing_record_pct": r.missing_record_pct,
                        "num_gaps":           r.num_gaps,
                        "gap_length":         length
                    })
        return flat

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._table = QTableWidget()
        self._table.setColumnCount(len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in self._COLUMNS])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton("Save…")
        self._save_btn.setToolTip("Save table to CSV")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _populate_table(self):
        self._table.setRowCount(len(self._flat_rows))

        for row_idx, row_data in enumerate(self._flat_rows):
            for col_idx, (_label, key, fmt, align) in enumerate(self._COLUMNS):
                val = row_data[key]
                
                if isinstance(val, float) and math.isnan(val):
                    text = "N/A"
                    item_align = Qt.AlignmentFlag.AlignCenter
                else:
                    try:
                        text = fmt.format(val)
                    except (ValueError, TypeError):
                        text = str(val)
                    item_align = align
                    
                item = QTableWidgetItem(text)
                item.setTextAlignment(item_align | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row_idx, col_idx, item)

    def _on_save(self):
        default = str(Path(last_dialog_dir()) / "missing_data_results.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Missing Data Results", default,
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        remember_dialog_path(path)
            
        try:
            with Path(path).open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # Write Headers
                writer.writerow([c[0] for c in self._COLUMNS])
                
                # Write Data
                for row_data in self._flat_rows:
                    row_out = []
                    for _label, key, _fmt, _align in self._COLUMNS:
                        val = row_data[key]
                        if isinstance(val, float) and math.isnan(val):
                            row_out.append("")
                        else:
                            row_out.append(val)
                    writer.writerow(row_out)
        except OSError as e:
            logger.exception("Failed to save missing data results CSV")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{e}")

    def _center_dialog(self):
        parent = self.parent()
        if parent:
            pr = parent.geometry()
            dr = self.geometry()
            self.move(
                pr.x() + (pr.width() - dr.width()) // 2,
                pr.y() + (pr.height() - dr.height()) // 2,
            )