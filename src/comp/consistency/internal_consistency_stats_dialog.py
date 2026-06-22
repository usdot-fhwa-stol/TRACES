"""Statistics summary table for Module 4 — Internal Consistency (top-level menu path).

One row per (event_id, veh_id) pair, 10 metric columns with abbreviated
headers and full names as header tooltips.  Includes a Save button that
exports the table to CSV, consistent with the other noise-analysis result
dialogs (e.g. JerkResultsTableDialog, InternalConsistencyResultDialog).
"""

import csv
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import List

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QMessageBox,
)

from .internal_consistency_comp import InternalConsistencyResult

logger = logging.getLogger(__name__)

NAN = float("nan")

# (header_abbrev, full_name, attribute, format_spec)
_COLUMNS = [
    ("Event ID",       "Event ID",                        "event_id",      "{:d}"),
    ("Veh ID",         "Vehicle ID",                      "veh_id",        "{:d}"),
    ("Min Speed Err",    "Minimum Speed Error (m/s)",        "_spd_min",      "{:.4f}"),
    ("Max Speed Err",    "Maximum Speed Error (m/s)",        "_spd_max",      "{:.4f}"),
    ("Mean Speed Err",   "Mean Speed Error (m/s)",           "_spd_mean",     "{:.4f}"),
    ("Min Accel Err",  "Minimum Acceleration Error (m/s²)","_accel_min",    "{:.4f}"),
    ("Max Accel Err",  "Maximum Acceleration Error (m/s²)","_accel_max",    "{:.4f}"),
    ("Mean Accel Err", "Mean Acceleration Error (m/s²)",   "_accel_mean",   "{:.4f}"),
    ("Min Jerk Err",   "Minimum Jerk Error (m/s³)",        "_jerk_min",     "{:.4f}"),
    ("Max Jerk Err",   "Maximum Jerk Error (m/s³)",        "_jerk_max",     "{:.4f}"),
    ("Mean Jerk Err",  "Mean Jerk Error (m/s³)",           "_jerk_mean",    "{:.4f}"),
]


class InternalConsistencyStatsDialog(QDialog):
    """Top-level menu result: statistics summary table.

    One row per (event_id, veh_id) pair.  10 metric columns with
    abbreviated headers and full names as header tooltips.
    """

    def __init__(self, parent, results: List[InternalConsistencyResult]):
        super().__init__(parent)
        self._stats = _compute_stats(results)
        self.setWindowTitle("Internal Consistency — Statistics Summary")
        self.setModal(True)
        self.resize(1000, min(80 + 30 * len(self._stats), 600))
        self._setup_ui()
        self._populate()
        QTimer.singleShot(0, self._center)

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)

        # Set abbreviated headers with full names as tooltips.
        for ci, (abbrev, full, _, _fmt) in enumerate(_COLUMNS):
            item = QTableWidgetItem(abbrev)
            item.setToolTip(full)
            self._table.setHorizontalHeaderItem(ci, item)

        layout.addWidget(self._table)

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

    def _populate(self):
        self._table.setRowCount(len(self._stats))
        for ri, row in enumerate(self._stats):
            for ci, (abbrev, full, attr, fmt) in enumerate(_COLUMNS):
                val = getattr(row, attr, NAN)
                if attr in ("event_id", "veh_id"):
                    text = str(val)
                else:
                    text = "" if _isnan(val) else fmt.format(val)
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._table.setItem(ri, ci, item)

    # ── Save ─────────────────────────────────────────────────────────────

    def _on_save(self):
        from utils import default_save_stem

        event_ids = sorted({r.event_id for r in self._stats})
        veh_ids   = sorted({r.veh_id   for r in self._stats})
        eids = "_".join(str(e) for e in event_ids) if event_ids else "eall"
        vids = (
            f"v{veh_ids[0]}-v{veh_ids[-1]}" if len(veh_ids) > 1
            else f"v{veh_ids[0]}" if veh_ids else "vall"
        )
        default = str(
            Path.home() / f"{default_save_stem(f'internal_consistency_stats_e{eids}', vids)}.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Statistics Summary", default,
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return

        try:
            with Path(path).open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([full for _abbrev, full, _attr, _fmt in _COLUMNS])
                rows = sorted(self._stats, key=lambda r: (r.event_id, r.veh_id))
                for row in rows:
                    row_out = []
                    for _abbrev, _full, attr, _fmt in _COLUMNS:
                        val = getattr(row, attr, NAN)
                        if attr in ("event_id", "veh_id"):
                            row_out.append(val)
                        else:
                            row_out.append("" if _isnan(val) else val)
                    writer.writerow(row_out)
        except OSError as e:
            logger.exception("Failed to save internal consistency stats CSV")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{e}")

    def _center(self):
        parent = self.parent()
        if parent:
            pg = parent.geometry()
            dg = self.geometry()
            self.move(
                pg.x() + (pg.width()  - dg.width())  // 2,
                pg.y() + (pg.height() - dg.height()) // 2,
            )


# ── Stats aggregation ─────────────────────────────────────────────────────

class _StatRow:
    __slots__ = [
        "event_id", "veh_id",
        "_spd_min",  "_spd_max",  "_spd_mean",
        "_accel_min","_accel_max","_accel_mean",
        "_jerk_min", "_jerk_max", "_jerk_mean",
    ]

    def __init__(self, event_id, veh_id,
                 spd, accel, jerk):
        self.event_id   = event_id
        self.veh_id     = veh_id
        self._spd_min   = _nanmin(spd)
        self._spd_max   = _nanmax(spd)
        self._spd_mean  = _nanmean(spd)
        self._accel_min  = _nanmin(accel)
        self._accel_max  = _nanmax(accel)
        self._accel_mean = _nanmean(accel)
        self._jerk_min  = _nanmin(jerk)
        self._jerk_max  = _nanmax(jerk)
        self._jerk_mean = _nanmean(jerk)


def _compute_stats(results: List[InternalConsistencyResult]) -> List[_StatRow]:
    """Aggregate row-level results into one _StatRow per (event_id, veh_id)."""
    buckets: dict = defaultdict(lambda: {"spd": [], "accel": [], "jerk": []})
    order = []
    for r in results:
        key = (r.event_id, r.veh_id)
        if key not in buckets or not buckets[key]["spd"]:
            order.append(key)
        b = buckets[key]
        b["spd"].append(r.speed_error)
        b["accel"].append(r.accel_error)
        b["jerk"].append(r.jerk_error)

    rows = []
    seen = set()
    for key in order:
        if key in seen:
            continue
        seen.add(key)
        b = buckets[key]
        rows.append(_StatRow(key[0], key[1], b["spd"], b["accel"], b["jerk"]))
    return rows


def _nanmin(vals):
    a = np.asarray([v for v in vals if not _isnan(v)], dtype=np.float64)
    return float(np.min(a)) if a.size else NAN


def _nanmax(vals):
    a = np.asarray([v for v in vals if not _isnan(v)], dtype=np.float64)
    return float(np.max(a)) if a.size else NAN


def _nanmean(vals):
    a = np.asarray([v for v in vals if not _isnan(v)], dtype=np.float64)
    return float(np.mean(a)) if a.size else NAN


def _isnan(v) -> bool:
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return True