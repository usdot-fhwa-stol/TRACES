"""Advanced Statistics results table (Task 9.4, Figures 2 and 3).

One row per reported vehicle, ordered by vehicle ID, then a bold summary
row "All" (stat_registry.summarize). The columns come from
stat_registry.ADVANCED_STATISTICS: header text is the statistic and the
quantity on two lines, and each header's tooltip gives the full name and
its definition. Values that can't be computed -- no leader, no lane
change, no veh_speed column -- show as N/A.

Buttons under the table:

    Generate Plots  opens PlotVariableSelectionDialog (Figure 4); Confirm
                    opens one box plot per variable checked, each built
                    from the samples of every vehicle in the table, with
                    outliers labelled by vehicle and time
    Save            writes the table, summary row included, to CSV
                    (blank where the table shows N/A)
    Close
"""

import csv
import logging
import math
from pathlib import Path
from typing import Any, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .stat_registry import (
    ADVANCED_STATISTICS, Column, plot_groups, summarize, table_columns,
)

logger = logging.getLogger(__name__)

NA = "N/A"


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and not math.isfinite(value))


def format_value(value: Any, column: Column) -> str:
    """Table text of one value: the column's format, N/A when missing. An
    integer column's summary may be a mean, shown with 2 decimals."""
    if _missing(value):
        return NA
    if column.fmt == "{:d}" and isinstance(value, float):
        return f"{value:.2f}"
    try:
        return column.fmt.format(value)
    except (ValueError, TypeError):
        return str(value)


class AdvancedStatisticsResultsDialog(QDialog):

    def __init__(self, parent, results: list, event_id: Optional[int], mode: str = "All",
                 params: Optional[dict] = None, stats=ADVANCED_STATISTICS):
        super().__init__(parent)
        self._results = sorted(results, key=lambda r: r.veh_id)
        self._event_id = event_id
        self._stats = stats
        self._columns = table_columns(stats)
        self._summary = summarize(self._results, stats) if self._results else None
        self._params = params or {}
        title = "Advanced Statistics"
        if event_id is not None:
            title += f" -- Event {event_id}"
        self.setWindowTitle(f"{title} ({mode})")
        self.setModal(True)
        if parent is not None:
            geom = parent.frameGeometry()
            self.resize(int(geom.width() * 0.85), int(geom.height() * 0.7))
        else:
            self.resize(1200, 600)
        self._setup_ui()
        self._populate()

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        dwell = self._params.get("min_dwell_s")
        max_gap = self._params.get("max_gap_m")
        notes = [f"{len(self._results)} vehicle(s). Leaders and followers are found "
                 f"among every active vehicle of the event; gaps are center to center."]
        if dwell is not None:
            notes.append(f"Lane changes: at least {dwell:g} s in the new lane.")
        if max_gap:
            notes.append(f"Leaders / followers within {max_gap:g} m.")
        info = QLabel("  ".join(notes))
        info.setWordWrap(True)
        info.setStyleSheet("color: #555;")
        layout.addWidget(info)

        self._table = QTableWidget()
        self._table.setColumnCount(len(self._columns))
        for i, col in enumerate(self._columns):
            item = QTableWidgetItem(col.header)
            item.setToolTip(f"{col.name}\n{col.description}")
            self._table.setHorizontalHeaderItem(i, item)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._plots_btn = QPushButton("Generate Plots")
        self._plots_btn.setToolTip("Box plots of the variables with a full statistic package")
        self._plots_btn.clicked.connect(self._on_generate_plots)
        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save the table to CSV")
        self._save_btn.clicked.connect(self._on_save)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        for b in (self._plots_btn, self._save_btn, self._close_btn):
            b.setAutoDefault(False)
            buttons.addWidget(b)
        layout.addLayout(buttons)
        enabled = bool(self._results)
        self._plots_btn.setEnabled(enabled)
        self._save_btn.setEnabled(enabled)

    def _rows(self) -> List[dict]:
        rows = [r.values for r in self._results]
        if self._summary is not None:
            rows.append(self._summary)
        return rows

    def _populate(self) -> None:
        rows = self._rows()
        self._table.setRowCount(len(rows))
        bold = QFont(self._table.font())
        bold.setBold(True)
        for r, values in enumerate(rows):
            is_summary = values is self._summary
            for c, col in enumerate(self._columns):
                text = format_value(values.get(col.key), col)
                item = QTableWidgetItem(text)
                align = (Qt.AlignmentFlag.AlignCenter if text == NA
                         else Qt.AlignmentFlag.AlignRight)
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                if is_summary:
                    item.setFont(bold)
                self._table.setItem(r, c, item)

    # ── Generate Plots ────────────────────────────────────────────────

    def _on_generate_plots(self) -> None:
        from .plot_selection_dialog import PlotVariableSelectionDialog
        groups = plot_groups(self._stats)
        picker = PlotVariableSelectionDialog(self, groups)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [g for g in groups if g.key in picker.selected_keys()]
        if not chosen:
            return
        from ui_kit.box_plot_grid_dialog import BoxPlotGridDialog, BoxPlotPanel
        panels = []
        for g in chosen:
            pairs = []
            for r in self._results:
                values, times = r.samples.get(g.key, ((), ()))
                pairs.extend((f"{r.veh_id} @ {t:.1f} s", v) for v, t in zip(values, times))
            unit = f" ({g.unit})" if g.unit else ""
            panels.append(BoxPlotPanel(g.key, f"{g.name}{unit}", pairs))
        event = f" -- Event {self._event_id}" if self._event_id is not None else ""
        dlg = BoxPlotGridDialog(self, f"Advanced Statistics Box Plots{event}", panels,
                                columns=min(3, len(panels)),
                                note=f"Each plot pools the samples of the "
                                     f"{len(self._results)} vehicle(s) in the table.",
                                save_stem="advanced_statistics_box_plots")
        dlg.exec()

    # ── Save ──────────────────────────────────────────────────────────

    def _default_save_path(self) -> str:
        from utils import default_save_stem, last_dialog_dir
        ids = [r.veh_id for r in self._results]
        vids = (f"v{ids[0]}" if len(ids) == 1 else f"v{ids[0]}-v{ids[-1]}") if ids else "vall"
        event = f"e{self._event_id}" if self._event_id is not None else "e"
        return str(Path(last_dialog_dir()) /
                   f"{default_save_stem(f'advanced_statistics_{event}', vids)}.csv")

    def _on_save(self) -> None:
        from utils import remember_dialog_path
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Advanced Statistics", self._default_save_path(),
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        remember_dialog_path(path)
        try:
            write_csv(path, self._rows(), self._columns, self._event_id)
        except OSError as exc:
            logger.exception("Failed to save advanced statistics CSV")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")


def write_csv(path: str, rows: List[dict], columns: List[Column],
              event_id: Optional[int]) -> None:
    """The table as CSV: an Event ID column, then the table's columns
    under their full names; missing values blank."""
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Event ID"] + [c.name for c in columns])
        for values in rows:
            out: List[Any] = [event_id if event_id is not None else ""]
            for c in columns:
                v = values.get(c.key)
                out.append("" if _missing(v) else
                           (round(v, 6) if isinstance(v, float) else v))
            writer.writerow(out)
