import csv
import logging
import math
import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal, QThread, QObject
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QDoubleSpinBox, QComboBox, QPushButton, QProgressBar,
    QFileDialog, QMessageBox,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _JerkWorker(QObject):
    """Runs JerkComp on a background thread.

    Signals:
        progress(int):          emitted after each event finishes (1-based count).
        finished(list):         emitted with the combined JerkResult list on success.
        error(str):             emitted with a message string on failure.
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, db_path: str, event_ids: list[int],
                 veh_ids: Optional[list[int]], params: dict,
                 dest_path: Optional[str]):
        super().__init__()
        self._db_path   = db_path
        self._event_ids = event_ids
        self._veh_ids   = veh_ids   # None → all vehicles (comp_event path)
        self._params    = params
        self._dest_path = dest_path  # None → normal mode, str → export mode

    def run(self):
        from comp.jerk_comp import JerkComp
        try:
            # Each thread needs its own connection.
            conn = sqlite3.connect(self._db_path)
            comp = JerkComp()
            all_results = []

            for i, event_id in enumerate(self._event_ids):
                if self._veh_ids is not None:
                    # right-click path: specific vehicles, single event
                    results = comp.comp_series(
                        conn, event_id, self._veh_ids, options=self._params)
                else:
                    results = comp.comp_event(
                        conn, event_id, options=self._params)
                all_results.extend(results)
                self.progress.emit(i + 1)

            if self._dest_path is not None:
                _write_csv(all_results, self._dest_path)

            conn.close()
            self.finished.emit(all_results)

        except Exception as exc:
            logger.exception("JerkWorker failed")
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class JerkCompDialog(QDialog):
    """Collects jerk-analysis parameters from the user, then runs the analysis
    on a background QThread with a progress bar.

    Invocation paths
    ----------------
    Normal — top-level menu (event_ids=[single id], veh_ids=None):
        OK button emits ``params_accepted`` after the thread finishes.

    Normal — right-click (event_ids=[single id], veh_ids=list):
        Same as above; ``params_accepted`` carries the results via the same
        signal.

    Export — Export Analysis menu (event_ids=multiple ids, veh_ids=None,
             export mode enabled by passing dest_path via _on_save):
        Save… button opens a file dialog, then the thread writes the CSV
        directly. ``params_accepted`` is NOT emitted.

    Signals
    -------
    params_accepted(dict, list):
        Emitted in normal mode with the parameter dict and the JerkResult list.
        Callers that previously connected only to ``params_accepted(dict)``
        must update their slot signature.
    """

    params_accepted = pyqtSignal(dict, list)

    def __init__(
        self,
        parent,
        db_connection: sqlite3.Connection,
        event_ids:   Optional[list[int]] = None,
        veh_ids:     Optional[list[int]] = None,
        export_mode: bool = False,
    ):
        super().__init__(parent)
        self._db_connection    = db_connection
        self._db_path          = db_connection.execute("PRAGMA database_list").fetchone()[2]
        self._export_event_ids = list(event_ids) if event_ids else None
        self._veh_ids          = list(veh_ids) if veh_ids else None
        self._is_export_mode   = export_mode

        self._thread: Optional[QThread] = None
        self._worker: Optional[_JerkWorker] = None

        self.setWindowTitle(
            "Jerk Analysis — Export" if self._is_export_mode else "Jerk Analysis"
        )
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

        # Progress bar — always visible, starts at zero.
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if self._is_export_mode:
            self._primary_btn = QPushButton("Save…")
            self._primary_btn.clicked.connect(self._on_save)
        else:
            self._primary_btn = QPushButton("OK")
            self._primary_btn.clicked.connect(self._on_ok)

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
            ("Use jerk column from dataset",              "dataset",    "veh_jerk"  in cols),
            ("Calculate jerk from acceleration",           "from_accel", "veh_accel" in cols),
            ("Calculate acceleration and jerk from speed", "from_speed", "veh_speed" in cols),
        ]
        for label, key, enabled in entries:
            self._method_combo.addItem(label, key)
            idx = self._method_combo.count() - 1
            if not enabled:
                model = self._method_combo.model()
                model.item(idx).setEnabled(False)

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

    def _set_controls_enabled(self, enabled: bool):
        self._primary_btn.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)
        self._threshold_spin.setEnabled(enabled)
        self._gap_spin.setEnabled(enabled)
        self._method_combo.setEnabled(enabled)

    def _start_worker(self, event_ids: list[int], veh_ids: Optional[list[int]],
                      params: dict, dest_path: Optional[str]):
        n = len(event_ids)
        self._progress_bar.setMaximum(n)
        self._progress_bar.setValue(0)
        self._set_controls_enabled(False)

        self._worker = _JerkWorker(self._db_path, event_ids, veh_ids, params, dest_path)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._progress_bar.setValue)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        # Clean up thread when worker is done.
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    # ------------------------------------------------------------------
    # Primary button slots
    # ------------------------------------------------------------------

    def _on_ok(self):
        """Normal mode: top-level menu or right-click."""
        # event_ids and veh_ids were supplied at construction.
        if not self._export_event_ids:
            logger.error("JerkCompDialog._on_ok called with no event_ids")
            return
        self._start_worker(
            self._export_event_ids,
            self._veh_ids,
            self._current_params(),
            dest_path=None,
        )

    def _on_save(self):
        """Export mode: ask for a destination file then start the worker."""
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save Jerk Analysis Export",
            str(Path.home()),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not dest:
            return
        self._dest_path = dest
        self._start_worker(
            self._export_event_ids,
            None,
            self._current_params(),
            dest_path=dest,
        )

    # ------------------------------------------------------------------
    # Worker result slots
    # ------------------------------------------------------------------

    def _on_worker_finished(self, results: list):
        self._progress_bar.setValue(self._progress_bar.maximum())

        if self._is_export_mode:
            QMessageBox.information(
                self,
                "Export Complete",
                f"Jerk analysis exported successfully to:\n{self._dest_path}",
            )
            self.accept()
        else:
            # Normal mode: emit signal so caller can open box-plot or table dialog.
            self.params_accepted.emit(self._current_params(), results)
            self.accept()

    def _on_worker_error(self, message: str):
        self._set_controls_enabled(True)
        self._progress_bar.setValue(0)
        QMessageBox.critical(self, "Jerk Analysis Error", f"Analysis failed:\n{message}")

    # ------------------------------------------------------------------
    # Prevent closing while the worker is running
    # ------------------------------------------------------------------

    def reject(self):
        if self._thread and self._thread.isRunning():
            return  # silently ignore; buttons are disabled anyway
        super().reject()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


# ---------------------------------------------------------------------------
# CSV writer (used by the worker, called on the worker thread)
# ---------------------------------------------------------------------------

def _write_csv(results, dest: str):
    fieldnames = [
        "event_id", "veh_id",
        "jerk_violation_pct", "max_jerk", "min_jerk",
        "sign_inversion_excess_pct",
    ]
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "event_id":                  r.event_id,
                "veh_id":                    r.veh_id,
                "jerk_violation_pct":         _fmt(r.jerk_violation_pct),
                "max_jerk":                   _fmt(r.max_jerk),
                "min_jerk":                   _fmt(r.min_jerk),
                "sign_inversion_excess_pct":  _fmt(r.sign_inversion_excess_pct),
            })


def _fmt(v) -> str:
    try:
        return "" if math.isnan(v) else f"{v:.4f}"
    except (TypeError, ValueError):
        return ""