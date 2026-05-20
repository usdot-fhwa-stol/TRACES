import csv
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QDoubleSpinBox, QComboBox, QPushButton,
    QFileDialog, QMessageBox,
)

logger = logging.getLogger(__name__)


class JerkCompDialog(QDialog):
    """Collects jerk-analysis parameters from the user.

    Normal mode (event_ids is None / single event from top-level menu or
    right-click): shows an **OK** button that emits :attr:`params_accepted`.

    Export mode (event_ids is a non-empty list): replaces OK with a
    **Save…** button. Clicking Save runs JerkComp over every supplied
    event, opens a file-save dialog, and writes the combined results to
    CSV. :attr:`params_accepted` is *not* emitted in this path.

    Signals:
        params_accepted(dict): emitted (normal mode only) with keys
            jerk_threshold, missing_gap_seconds, jerk_method
    """

    params_accepted = pyqtSignal(dict)

    def __init__(
        self,
        parent,
        db_connection: sqlite3.Connection,
        event_ids: Optional[list[int]] = None,
    ):
        super().__init__(parent)
        self._db_connection = db_connection
        # export mode when a list of event ids is supplied
        self._export_event_ids: Optional[list[int]] = event_ids if event_ids else None
        self._is_export_mode = bool(self._export_event_ids)

        self.setWindowTitle("Jerk Analysis — Export" if self._is_export_mode else "Jerk Analysis")
        self.setModal(True)
        self._setup_ui()
        self._configure_method_options()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.1, 1000.0)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setValue(15.0)
        self._threshold_spin.setSuffix(" m/s³")
        form.addRow("Jerk threshold:", self._threshold_spin)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(0.01, 60.0)
        self._gap_spin.setDecimals(2)
        self._gap_spin.setValue(1.0)
        self._gap_spin.setSuffix(" s")
        form.addRow("Missing value gap threshold:", self._gap_spin)

        self._method_combo = QComboBox()
        form.addRow("Jerk calculation method:", self._method_combo)

        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if self._is_export_mode:
            self._primary_btn = QPushButton("Save…")
            self._primary_btn.clicked.connect(self._on_save)
        else:
            self._primary_btn = QPushButton("OK")
            self._primary_btn.clicked.connect(self._on_accept)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._primary_btn)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    def _configure_method_options(self):
        cursor = self._db_connection.cursor()
        cursor.execute("PRAGMA table_info(events)")
        cols = {row[1] for row in cursor.fetchall()}

        entries = [
            ("Use jerk column from dataset",             "dataset",    "veh_jerk"  in cols),
            ("Calculate jerk from acceleration",          "from_accel", "veh_accel" in cols),
            ("Calculate acceleration and jerk from speed","from_speed", "veh_speed" in cols),
        ]
        for label, key, enabled in entries:
            self._method_combo.addItem(label, key)
            idx = self._method_combo.count() - 1
            if not enabled:
                model = self._method_combo.model()
                item = model.item(idx)
                item.setEnabled(False)

        # Default to first enabled item.
        for i in range(self._method_combo.count()):
            if self._method_combo.model().item(i).isEnabled():
                self._method_combo.setCurrentIndex(i)
                break
        else:
            self._method_combo.setEnabled(False)
            self._primary_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_params(self) -> dict:
        return {
            "jerk_threshold":      self._threshold_spin.value(),
            "missing_gap_seconds": self._gap_spin.value(),
            "jerk_method":         self._method_combo.currentData(),
        }

    # ------------------------------------------------------------------
    # Slots — normal mode
    # ------------------------------------------------------------------

    def _on_accept(self):
        self.params_accepted.emit(self._current_params())
        self.accept()

    # ------------------------------------------------------------------
    # Slots — export mode
    # ------------------------------------------------------------------

    def _on_save(self):
        """Run JerkComp over all selected events and write results to CSV."""
        params = self._current_params()

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save Jerk Analysis Export",
            str(Path.home()),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not dest:
            return  # user cancelled file dialog

        try:
            self._run_export(params, dest)
        except Exception as e:
            logger.exception("Jerk analysis export failed")
            QMessageBox.critical(self, "Export Error", f"Export failed:\n{e}")
            return

        QMessageBox.information(
            self,
            "Export Complete",
            f"Jerk analysis exported successfully to:\n{dest}",
        )
        self.accept()

    def _run_export(self, params: dict, dest: str):
        """Execute JerkComp for every event id and write a CSV.

        Each row corresponds to one (event_id, veh_id) trajectory.
        """
        from comp.jerk_comp import JerkComp

        comp = JerkComp()
        fieldnames = [
            "event_id",
            "veh_id",
            "jerk_violation_pct",
            "max_jerk",
            "min_jerk",
            "sign_inversion_excess_pct",
        ]

        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()

            for event_id in self._export_event_ids:
                logger.debug(f"Exporting jerk analysis for event {event_id}")
                results = comp.comp_event(
                    self._db_connection, event_id, options=params
                )
                for r in results:
                    writer.writerow({
                        "event_id":                   r.event_id,
                        "veh_id":                     r.veh_id,
                        "jerk_violation_pct":          "" if _isnan(r.jerk_violation_pct)         else f"{r.jerk_violation_pct:.4f}",
                        "max_jerk":                    "" if _isnan(r.max_jerk)                   else f"{r.max_jerk:.4f}",
                        "min_jerk":                    "" if _isnan(r.min_jerk)                   else f"{r.min_jerk:.4f}",
                        "sign_inversion_excess_pct":   "" if _isnan(r.sign_inversion_excess_pct)  else f"{r.sign_inversion_excess_pct:.4f}",
                    })


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _isnan(v) -> bool:
    import math
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return True