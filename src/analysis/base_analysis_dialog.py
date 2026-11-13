import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QProgressBar, QFileDialog, QMessageBox,
)
from utils import last_dialog_dir, remember_dialog_path

logger = logging.getLogger(__name__)


class BaseAnalysisDialog(QDialog):
    """Orchestration base for all noise-analysis parameter dialogs.

    Subclasses must override
    ────────────────────────
    _build_parameter_form() -> QFormLayout
        Return a populated QFormLayout with the module's widgets.
        Store widget references on *self* so _current_params() can read them.

    _current_params() -> dict
        Return the current widget values as a plain dict.

    _create_worker(db_path, event_ids, veh_ids, params, dest_path)
        -> BaseAnalysisWorker
        Instantiate and return the module-specific worker (do NOT move it to a
        thread here; the base class does that).

    _csv_export_label() -> str   (optional, default "Analysis")
        Human-readable module name used in the Save dialog title and the
        completion message box.

    _show_results(self, results: list) -> None
        Invokes module-specific dialog to render analysis results

    Class-level flags
    -----------------
    RIGHT_CLICK_ENABLED : bool = True
        Set to False on modules that are top-level only (e.g. Module 5).

    Invocation paths
    ----------------
    Normal — top-level menu or right-click
        OK is shown; on finish.

    Export
        Save is shown; on finish, writes CSV and shows a completion dialog.
        params_accepted is NOT emitted.
    """

    RIGHT_CLICK_ENABLED: bool = True   # Module 5 sets this to False

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(self, parent, db_connection,
                 event_ids: Optional[list] = None,
                 veh_ids:   Optional[list] = None,
                 export_mode: bool = False):
        super().__init__(parent)
        self._db_connection  = db_connection
        self._db_path        = db_connection.execute(
                                   "PRAGMA database_list").fetchone()[2]
        self._export_event_ids = list(event_ids) if event_ids else []
        self._veh_ids          = list(veh_ids) if veh_ids else None
        self._is_export_mode   = export_mode
        self._dest_path: Optional[str] = None

        self._thread: Optional[QThread] = None
        self._worker = None

        self.setWindowTitle(self._dialog_title())
        self.setModal(True)
        self._setup_ui()

    def _dialog_title(self) -> str:
        label = self._csv_export_label()
        return f"{label} — Export" if self._is_export_mode else label

    # ── Abstract / hook methods ────────────────────────────────────────────

    def _build_parameter_form(self) -> QFormLayout:
        """Return a QFormLayout populated with module-specific widgets."""
        raise NotImplementedError

    def _current_params(self) -> dict:
        """Return current widget values as a dict."""
        raise NotImplementedError

    def _create_worker(self, db_path: str, event_ids: list,
                       veh_ids, params: dict, dest_path):
        """Instantiate and return the module-specific BaseAnalysisWorker."""
        raise NotImplementedError

    def _csv_export_label(self) -> str:
        return "Analysis"

    def _show_results(self, results: list) -> None:
        pass

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_parameter_form())

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if self._is_export_mode:
            self._primary_btn = QPushButton("Save")
            self._primary_btn.clicked.connect(self._on_save)
        else:
            self._primary_btn = QPushButton("OK")
            self._primary_btn.clicked.connect(self._on_ok)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._primary_btn)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    # ── Control enable/disable ────────────────────────────────────────────

    def _set_controls_enabled(self, enabled: bool):
        """Enable or disable all interactive controls during worker execution.

        Subclasses may override to include additional widgets, but should call
        super()._set_controls_enabled(enabled) to handle the buttons.
        """
        self._primary_btn.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)

    # ── Thread lifecycle ──────────────────────────────────────────────────

    def _progress_maximum(self, event_ids: list, veh_ids, params: dict) -> int:
        """Return the progress bar's maximum — the total number of
        progress_callback ticks _run_for_event is expected to make.

        Default: one tick per event (matches the default _run_for_event
        pattern of ticking once after each event finishes). Override this
        when a module ticks more granularly, e.g. once per vehicle as well
        (see SplineCompDialog).
        """
        return len(event_ids)

    def _start_worker(self, event_ids: list, veh_ids,
                      params: dict, dest_path):
        self._progress_bar.setMaximum(self._progress_maximum(event_ids, veh_ids, params))
        self._progress_bar.setValue(0)
        self._set_controls_enabled(False)

        self._worker = self._create_worker(
            self._db_path, event_ids, veh_ids, params, dest_path)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._progress_bar.setValue)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    # ── Button slots ──────────────────────────────────────────────────────

    def _on_ok(self):
        if not self._export_event_ids:
            logger.error("%s._on_ok called with no event_ids",
                         type(self).__name__)
            return
        self._start_worker(
            self._export_event_ids, self._veh_ids,
            self._current_params(), dest_path=None)

    def _default_export_stem(self) -> str:
        """Return a filename stem for the Save dialog. Subclasses may override."""
        from utils import default_save_stem
        label = self._csv_export_label().lower().replace(" ", "_")
        return default_save_stem(label)
    
    def _on_save(self):
        default = str(Path(last_dialog_dir()) / f"{self._default_export_stem()}.csv")
        dest, _ = QFileDialog.getSaveFileName(
            self,
            f"Save {self._csv_export_label()} Export",
            default,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not dest:
            return
        remember_dialog_path(dest)
        self._dest_path = dest
        self._start_worker(
            self._export_event_ids, None,
            self._current_params(), dest_path=dest)

    # ── Worker result slots ───────────────────────────────────────────────

    def _on_worker_finished(self, results: list):
        self._progress_bar.setValue(self._progress_bar.maximum())
        if self._is_export_mode:
            QMessageBox.information(
                self, "Export Complete",
                f"{self._csv_export_label()} exported successfully to:\n{self._dest_path}",
            )
            self.accept()
        else:
            self._show_results(results)
            self.accept()

    def _on_worker_error(self, message: str):
        self._set_controls_enabled(True)
        self._progress_bar.setValue(0)
        QMessageBox.critical(
            self, f"{self._csv_export_label()} Error",
            f"Analysis failed:\n{message}")

    # ── Close guards ─────────────────────────────────────────────────────

    def reject(self):
        if self._thread and self._thread.isRunning():
            return
        super().reject()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)