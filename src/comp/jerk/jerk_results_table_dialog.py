import csv
import logging
import math
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QLabel,
)

logger = logging.getLogger(__name__)


class JerkResultsTableDialog(QDialog):
    """Tabular view of JerkComp results, one row per (event_id, veh_id).

    Used by the right-click invocation of jerk analysis. Provides a
    'Save' button to write the table to CSV.
    """

    _COLUMNS = [
        ("Event ID",                "event_id",                  "{:d}",   Qt.AlignmentFlag.AlignRight),
        ("Vehicle ID",              "veh_id",                    "{:d}",   Qt.AlignmentFlag.AlignRight),
        ("Jerk Violation %",        "jerk_violation_pct",        "{:.2f}", Qt.AlignmentFlag.AlignRight),
        ("Max Jerk (m/s³)",         "max_jerk",                  "{:.3f}", Qt.AlignmentFlag.AlignRight),
        ("Min Jerk (m/s³)",         "min_jerk",                  "{:.3f}", Qt.AlignmentFlag.AlignRight),
        ("Sign Inversion Excess %", "sign_inversion_excess_pct", "{:.2f}", Qt.AlignmentFlag.AlignRight),
    ]

    def __init__(self, parent, results):
        """
        Args:
            parent: parent widget
            results (list[JerkResult]): output from JerkComp.comp_series
        """
        super().__init__(parent)
        self._results = list(results)
        self.setWindowTitle("Jerk Analysis Results")
        self.setModal(True)

        parent_height = parent.height() if parent else 600
        self.resize(780, int(parent_height * 0.6))

        self._setup_ui()
        self._populate_table()
        QTimer.singleShot(0, self._center_dialog)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        unavailable = sum(1 for r in self._results if r.jerk_source == "unavailable")
        if unavailable:
            note = QLabel(
                f"{unavailable} trajector{'y' if unavailable == 1 else 'ies'} "
                f"could not be computed (missing source data)."
            )
            note.setStyleSheet("color: #555; font-style: italic;")
            layout.addWidget(note)

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
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _populate_table(self):
        # Sort by (event_id, veh_id) so rows are predictable.
        rows = sorted(self._results, key=lambda r: (r.event_id, r.veh_id))
        self._table.setRowCount(len(rows))

        for row_idx, r in enumerate(rows):
            for col_idx, (_label, attr, fmt, align) in enumerate(self._COLUMNS):
                val = getattr(r, attr)
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
        from utils import default_save_stem
        event_ids = sorted({r.event_id for r in self._results})
        veh_ids   = sorted({r.veh_id   for r in self._results})
        eids = "_".join(str(e) for e in event_ids)  if event_ids else "e?"
        vids = f"v{veh_ids[0]}-v{veh_ids[-1]}" if len(veh_ids) > 1 else f"v{veh_ids[0]}" if veh_ids else "vall"
        default = str(Path.home() / f"{default_save_stem(f'jerk_results_e{eids}', vids)}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Jerk Results", default,
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        try:
            with Path(path).open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([c[0] for c in self._COLUMNS])
                rows = sorted(self._results, key=lambda r: (r.event_id, r.veh_id))
                for r in rows:
                    row_out = []
                    for _label, attr, _fmt, _align in self._COLUMNS:
                        val = getattr(r, attr)
                        if isinstance(val, float) and math.isnan(val):
                            row_out.append("")
                        else:
                            row_out.append(val)
                    writer.writerow(row_out)
        except OSError as e:
            logger.exception("Failed to save jerk results CSV")
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