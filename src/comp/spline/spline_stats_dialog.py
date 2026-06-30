"""Goodness-of-fit results table for Spline Fitting (top-level path only).

One row per (event_id, veh_id). For whichever kinematic was the spline's
fitting base, the five metrics in that group are the fit's own residual
(spl_base(time_valid) vs. base_valid) — these columns are bolded in the
header. For the other three kinematics, the same five metrics instead
compare the spline-derived calc_* column against that kinematic's
*measured* column wherever a measurement exists — diagnostic information
about how well the derived quantities track reality, not part of the fit
itself (per the SplineReport, only the base-variable residual is strictly
required; the other three groups are an addition).

status="skipped" rows (insufficient valid points for the chosen k) show
blank metric cells; the skip reason is in the Status column's tooltip and
in the saved CSV's message column.

The Save button here is the *only* CSV export path for Spline Fitting —
there's no separate File > Export > Export Analysis entry, since the new
calc_* / is_spline_imputed columns already flow through the existing
Export All / Export Selected paths once present on the events table.
"""

import csv
import logging
import math
from typing import Any, Dict, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .spline_comp import BASE_LABELS, SplineFitResult

logger = logging.getLogger(__name__)

_ID_COLUMNS = [
    ("Event ID", "Event ID",                     "event_id",  "{:d}"),
    ("Veh ID",   "Vehicle ID",                    "veh_id",    "{:d}"),
    ("Status",   "Status",                        "status",    "{}"),
    ("N Pts",    "Measured Points Used in Fit",    "n_points",  "{:d}"),
    ("N Imp",    "Imputed Rows Created",           "n_imputed", "{:d}"),
]

# (field_prefix, full_label, header_abbrev, unit)
_METRIC_GROUPS = [
    ("position",     "Position",     "Pos", "m"),
    ("speed",        "Speed",        "Spd", "m/s"),
    ("acceleration", "Acceleration", "Acc", "m/s²"),
    ("jerk",         "Jerk",         "Jrk", "m/s³"),
]
_METRIC_SUFFIXES = [
    ("rmse",     "RMSE"),
    ("mae",      "MAE"),
    ("max_err",  "Max"),
    ("min_err",  "Min"),
    ("mean_err", "Mean"),
]


def _build_columns():
    cols = list(_ID_COLUMNS)
    for prefix, full_label, abbrev, unit in _METRIC_GROUPS:
        for suffix, suffix_abbrev in _METRIC_SUFFIXES:
            attr = f"{prefix}_{suffix}"
            header = f"{abbrev} {suffix_abbrev}"
            full = f"{full_label} {suffix_abbrev} Error ({unit})" if suffix != "rmse" and suffix != "mae" \
                else f"{full_label} {suffix_abbrev} ({unit})"
            cols.append((header, full, attr, "{:.4f}"))
    return cols


_COLUMNS = _build_columns()


class SplineStatsDialog(QDialog):
    """Top-level menu result: spline fitting goodness-of-fit table."""

    def __init__(self, parent, results: List[SplineFitResult], params: Dict[str, Any]):
        super().__init__(parent)
        self._results = results
        self._params = params
        self.setWindowTitle("Spline Fitting — Goodness of Fit")
        self.setModal(False)
        self._setup_ui()
        self.resize(1300, 700)

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        n_ok = sum(1 for r in self._results if r.status == "ok")
        n_skipped = sum(1 for r in self._results if r.status == "skipped")
        n_imputed_total = sum(r.n_imputed for r in self._results if r.status == "ok")
        base_label = BASE_LABELS.get(self._params.get("fitting_base"), "?")
        fill_label = "on" if self._params.get("fill_missing_rows") else "off"

        header = QLabel(
            f"Fitting base: {base_label}    s = {self._params.get('s')}    "
            f"k = {self._params.get('k')}    Fill missing rows: {fill_label}\n"
            f"{n_ok} trajectory(ies) fitted, {n_imputed_total} row(s) imputed, "
            f"{n_skipped} skipped. Four columns written: calc_position, "
            f"calc_speed, calc_acceleration, calc_jerk."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        fitting_base = self._params.get("fitting_base")
        bold_attrs = (
            {f"{fitting_base}_{suffix}" for suffix, _ in _METRIC_SUFFIXES}
            if fitting_base else set()
        )

        for col_idx, (_, full_name, attr, _) in enumerate(_COLUMNS):
            item = self._table.horizontalHeaderItem(col_idx)
            if attr in bold_attrs:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setToolTip(full_name + "  (the fitted base variable's own residual)")
            else:
                item.setToolTip(full_name)

        self._populate_table()
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self._on_save)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _populate_table(self):
        self._table.setRowCount(len(self._results))
        for row_idx, r in enumerate(self._results):
            for col_idx, (_, _, attr, fmt) in enumerate(_COLUMNS):
                value = getattr(r, attr, None)
                text = self._format_value(value, fmt)
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if r.status == "skipped" and r.message:
                    item.setToolTip(r.message)
                self._table.setItem(row_idx, col_idx, item)

    @staticmethod
    def _format_value(value, fmt: str) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and math.isnan(value):
            return ""
        try:
            return fmt.format(value)
        except (TypeError, ValueError):
            return str(value)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self):
        from utils import default_save_stem
        eids = sorted({r.event_id for r in self._results})
        eid_part = "_".join(str(e) for e in eids) if eids else "eall"
        base = self._params.get("fitting_base", "base")
        default_name = default_save_stem(f"spline_fit_e{eid_part}", base)

        dest, _ = QFileDialog.getSaveFileName(
            self, "Save Spline Fitting Results", default_name, "CSV Files (*.csv)")
        if not dest:
            return
        try:
            self._write_csv(self._results, dest)
        except OSError as exc:
            logger.exception("SplineStatsDialog: failed to write CSV")
            QMessageBox.critical(self, "Error", f"Failed to save file: {exc}")
            return
        QMessageBox.information(self, "Saved", f"Results saved to:\n{dest}")

    @staticmethod
    def _write_csv(results: List[SplineFitResult], dest: str):
        fieldnames = ["fitting_base", "s", "k"] + [c[2] for c in _COLUMNS] + ["message"]
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                row = {"fitting_base": r.fitting_base, "s": r.s, "k": r.k,
                       "message": r.message}
                for _, _, attr, _ in _COLUMNS:
                    value = getattr(r, attr, None)
                    row[attr] = "" if (isinstance(value, float) and math.isnan(value)) else value
                writer.writerow(row)