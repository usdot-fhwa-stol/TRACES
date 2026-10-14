import csv
import logging
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
)

logger = logging.getLogger(__name__)


class ShockwaveResultsTableDialog(QDialog):
    """Tabular view of shockwave results, one row per ShockwaveResult.

    Shown when the user requests a summary during or at the end of a
    shockwave analysis session, or when exiting with unsaved results.
    Provides a Save button to write the table to CSV.
    """

    # (header_label, attr, fmt, alignment)
    # 'num_points' resolves via the num_points property on ShockwaveResult.
    _COLUMNS = [
        ("Shockwave ID",      "id",         "{:d}",   Qt.AlignmentFlag.AlignRight),
        ("Mode",              "mode",       "{}",     Qt.AlignmentFlag.AlignLeft),
        ("Equation",          "equation",   "{}",     Qt.AlignmentFlag.AlignLeft),
        ("Speed (m/s)", "slope",      "{:.4f}", Qt.AlignmentFlag.AlignRight),
        ("R²",                "r_squared",  "{:.4f}", Qt.AlignmentFlag.AlignRight),
        ("Points Used",       "num_points", "{:d}",   Qt.AlignmentFlag.AlignRight),
    ]

    def __init__(self, parent, results, warn_on_close=True):
        """
        Args:
            parent:        parent widget (TimeSpacePlotWidget or MainWindow)
            results:       list[ShockwaveResult]
            warn_on_close: if True (default), closing without saving prompts
                "data will be lost" — used when the dialog is opened because
                the session is actually ending (mode switch, app exit).
                If False, the dialog is being opened on-demand purely to
                view current results (e.g. clicking the result panel title)
                while the shockwave session remains active; nothing is at
                risk of being lost, so closeEvent should not warn.
        """
        super().__init__(parent)
        self._results = list(results)
        self._unsaved = True
        self._warn_on_close = warn_on_close
        self.setWindowTitle("Shockwave Analysis Results")
        self.setModal(True)

        parent_height = parent.height() if parent else 600
        self.resize(820, int(parent_height * 0.6))

        self._setup_ui()
        self._populate_table()
        QTimer.singleShot(0, self._center_dialog)

    # ── UI ────────────────────────────────────────────────────────────────

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
        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save table to CSV")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _populate_table(self):
        rows = sorted(self._results, key=lambda r: r.id)
        self._table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            for col_idx, (_label, attr, fmt, align) in enumerate(self._COLUMNS):
                val = getattr(r, attr)
                try:
                    text = fmt.format(val)
                except (ValueError, TypeError):
                    text = str(val)
                item = QTableWidgetItem(text)
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row_idx, col_idx, item)

    # ── Save ─────────────────────────────────────────────────────────────

    def _on_save(self):
        from utils import default_save_stem, last_dialog_dir, remember_dialog_path

        n     = len(self._results)
        modes = "_".join(sorted({r.mode for r in self._results})) if self._results else "unknown"
        default = str(
            Path(last_dialog_dir()) / f"{default_save_stem(f'shockwave_results_{modes}', f'n{n}')}.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Shockwave Results", default,
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        remember_dialog_path(path)

        try:
            with Path(path).open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([c[0] for c in self._COLUMNS])
                for r in sorted(self._results, key=lambda r: r.id):
                    row_out = []
                    for _label, attr, _fmt, _align in self._COLUMNS:
                        row_out.append(getattr(r, attr))
                    writer.writerow(row_out)
            self._unsaved = False
        except OSError as e:
            logger.exception("Failed to save shockwave results CSV")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{e}")

    # ── Close guard ───────────────────────────────────────────────────────

    def closeEvent(self, event):
        """Prompt before discarding unsaved results."""
        if self._warn_on_close and self._unsaved and self._results:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "Shockwave Results",
                "Shockwave data will be lost, save progress?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._on_save()
        super().closeEvent(event)

    # ── Positioning ───────────────────────────────────────────────────────

    def _center_dialog(self):
        parent = self.parent()
        if parent:
            pr = parent.geometry()
            dr = self.geometry()
            self.move(
                pr.x() + (pr.width()  - dr.width())  // 2,
                pr.y() + (pr.height() - dr.height()) // 2,
            )