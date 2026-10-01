"""Trajectory Stitching preview window (Task 9.2, Figures 2 and 4).

One dialog, two states: the overlap slider group (Figure 4) is shown only
when the pair overlaps. Left: distance/speed/acceleration on a shared time
axis. Right: transposed info table (Traj 1 / Traj 2 / Stitched) with the
editable new ID, relationship and seam summary, display checkboxes, the
overlap slider, and the red inline safety warning. Bottom: output database
path, Cancel, Stitch.

Traj 1 / Traj 2 are chronological order (not click order), drawn amber /
green as in Figure 4; the stitched preview is dashed dark blue on top.
Overlap bounds are black dashed lines and the split is red dashed (gap
bounds are black dashed too), on all three plots.

Writing goes through a resolver callable(parent, requested_path) -> connection 
or None, normally DatabaseSession.ensure_write_target; in_place_resolver() is 
the fallback when none is supplied.
"""

import logging
import math
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from plot.kinematic_plot_stack import KinematicPlotStack
from utils import last_dialog_dir, remember_dialog_path
from .safety_rules import SafetyContext, evaluate_safety
from .stitching_comp import ManualStitchingComp, StitchInputs, StitchPlan, TemporalCase

logger = logging.getLogger(__name__)

TargetResolver = Callable[[QWidget, str], Optional[sqlite3.Connection]]

FIRST_COLOR = "#E69F00"      # amber
SECOND_COLOR = "#009E73"     # green
STITCHED_COLOR = "#1F3A93"   # dark blue, dashed

_ROWS = [
    "Vehicle ID", "Start (s)", "End (s)", "Duration (s)", "Observations",
    "Initial position (m)", "Final position (m)",
    "Initial speed (m/s)", "Final speed (m/s)",
]
_COL_FIRST, _COL_SECOND, _COL_STITCHED = 0, 1, 2


@dataclass
class StitchResult:
    stitch_id: int
    new_veh_id: int
    source_veh_ids: Tuple[int, int]
    connection: sqlite3.Connection
    db_path: str


def db_file_path(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def _same_file(a: str, b: str) -> bool:
    try:
        norm = lambda p: os.path.normcase(str(Path(p).resolve()))
        return norm(a) == norm(b)
    except (OSError, ValueError):
        return False


def in_place_resolver(conn: sqlite3.Connection) -> TargetResolver:
    """Interim resolver: write only to the open database. Replaced in
    step 4 by DatabaseSession (first-write warning, copy-and-switch)."""
    current = db_file_path(conn)

    def resolve(parent: QWidget, requested: str) -> Optional[sqlite3.Connection]:
        if _same_file(requested, current):
            return conn
        QMessageBox.information(
            parent, "Trajectory Stitching",
            "Writing to a different database file is not available yet.\n"
            f"Choose the current database:\n{current}")
        return None

    return resolve


# ── Formatting helpers ─────────────────────────────────────────────────────

def _fmt(x: Optional[float], digits: int = 2) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:.{digits}f}"


def _first_finite(a: Optional[np.ndarray]) -> Optional[float]:
    if a is None:
        return None
    idx = np.flatnonzero(np.isfinite(a))
    return float(a[idx[0]]) if idx.size else None


def _last_finite(a: Optional[np.ndarray]) -> Optional[float]:
    if a is None:
        return None
    idx = np.flatnonzero(np.isfinite(a))
    return float(a[idx[-1]]) if idx.size else None


def _traj_values(tr) -> list:
    """Table rows 1.. (everything but Vehicle ID) for one trajectory."""
    speed_missing = tr.speed is None
    return [
        _fmt(tr.start_time), _fmt(tr.end_time), _fmt(tr.duration), str(tr.n),
        _fmt(_first_finite(tr.dist)), _fmt(_last_finite(tr.dist)),
        "n/a" if speed_missing else _fmt(_first_finite(tr.speed)),
        "n/a" if speed_missing else _fmt(_last_finite(tr.speed)),
    ]


def _swatch_icon(color: str, dashed: bool = False) -> QIcon:
    pm = QPixmap(24, 12)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    pen = QPen(QColor(color), 3)
    if dashed:
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([2, 2])
    painter.setPen(pen)
    painter.drawLine(1, 6, 23, 6)
    painter.end()
    return QIcon(pm)


def _dashed_pen(color, width: float):
    """Same dash style as the other plot dialogs (Internal Consistency
    results, Spline Testing, shockwave lines): CustomDashLine with a
    [4, 4] pattern, which reads more clearly than Qt's default DashLine."""
    pen = pg.mkPen(color, width=width)
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern([4, 4])
    return pen


# ── Dialog ─────────────────────────────────────────────────────────────────

class StitchingPreviewDialog(QDialog):

    def __init__(self, parent, db_connection: sqlite3.Connection,
                 inputs: StitchInputs, resolve_target: Optional[TargetResolver] = None):
        super().__init__(parent)
        self._conn = db_connection
        self._inputs = inputs
        self._resolve_target = resolve_target or in_place_resolver(db_connection)
        self._plan: Optional[StitchPlan] = None
        self._id_ok = True
        self._curves: Dict[str, dict] = {}
        self._split_lines = []
        self._split_slider: Optional[QSlider] = None
        self._safety = evaluate_safety(SafetyContext(inputs.first, inputs.second, inputs.seam))
        self.result_info: Optional[StitchResult] = None

        self.setWindowTitle(f"Trajectory Stitching — Event {inputs.event_id}")
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
            & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setModal(True)

        self._setup_ui()
        self._draw_sources()
        self._draw_markers()
        self._on_id_changed(self._id_spin.value())   # validates, builds plan
        self._fit_table_width()
        self._set_initial_view()
        self._size_and_center()

    # ── UI construction ────────────────────────────────────────────────

    def _setup_ui(self):
        is_overlap = self._inputs.seam.case is TemporalCase.OVERLAP

        self._plots = KinematicPlotStack(
            has_speed=self._inputs.first.speed is not None,
            has_accel=self._inputs.first.accel is not None)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._plots)
        splitter.addWidget(self._build_right_panel(is_overlap))
        splitter.setStretchFactor(0, 1)   # plots take all spare width
        splitter.setStretchFactor(1, 0)
        splitter.setChildrenCollapsible(False)

        root = QVBoxLayout(self)
        root.addWidget(splitter, stretch=1)
        root.addLayout(self._build_bottom_row())

    def _build_right_panel(self, is_overlap: bool) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)

        self._table = self._build_table()
        lay.addWidget(self._table, stretch=1)     # only the table grows / scrolls

        self._id_label = self._fixed_label()
        self._id_label.setStyleSheet("color: #C00000;")
        self._id_label.hide()
        lay.addWidget(self._id_label)

        self._relation_label = self._fixed_label()
        self._relation_label.setText(self._relationship_text())
        lay.addWidget(self._relation_label)

        self._seam_label = self._fixed_label()
        lay.addWidget(self._seam_label)

        controls = QHBoxLayout()
        controls.addLayout(self._build_display_group())
        if is_overlap:
            controls.addSpacing(20)
            controls.addLayout(self._build_split_group())
        controls.addStretch()
        lay.addLayout(controls)

        self._safety_label = self._fixed_label()
        self._safety_label.setStyleSheet("color: #D00000; font-weight: bold;")
        self._safety_label.setText("<br>".join(f"⚠ {m}" for m in self._safety))
        self._safety_label.setVisible(bool(self._safety))
        lay.addWidget(self._safety_label)

        return panel

    @staticmethod
    def _fixed_label(wrap: bool = False) -> QLabel:
        lbl = QLabel()
        lbl.setWordWrap(wrap)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return lbl

    def _compact_spin_width(self) -> int:
        """Width for the proposed ID plus two more digits, instead of the
        10 digits QSpinBox reserves for its 2,147,483,647 maximum."""
        s = self._id_spin
        fm = s.fontMetrics()
        digits = len(str(self._inputs.default_new_veh_id))
        return (s.sizeHint().width()
                - fm.horizontalAdvance(str(s.maximum()))
                + fm.horizontalAdvance("9" * digits))

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(len(_ROWS), 3)
        t.setVerticalHeaderLabels(_ROWS)

        t.setHorizontalHeaderLabels(["Traj 1", "Traj 2", "Stitched"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        t.setMinimumHeight(110)

        self._id_spin = QSpinBox()
        self._id_spin.setRange(0, 2_147_483_647)
        self._id_spin.setValue(self._inputs.default_new_veh_id)
        self._id_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._id_spin.setToolTip("Proposed vehicle ID for the stitched trajectory")
        self._id_spin.setFixedWidth(self._compact_spin_width())
        self._id_spin.valueChanged.connect(self._on_id_changed)

        # Container so the table stretches the cell, not the spinbox;
        # right-aligned to match the other columns' numbers.
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 2, 0)
        holder_layout.addStretch()
        holder_layout.addWidget(self._id_spin)
        t.setCellWidget(0, _COL_STITCHED, holder)

        self._table = t
        self._set_column(_COL_FIRST, self._inputs.first, include_id=True)
        self._set_column(_COL_SECOND, self._inputs.second, include_id=True)
        return t

    def _build_display_group(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.addWidget(QLabel("<b>Display</b>"))
        self._show_checks = {}
        for role, text, color, dashed in (
                ("first", " First trajectory", FIRST_COLOR, False),
                ("second", " Second trajectory", SECOND_COLOR, False),
                ("stitched", " Stitched trajectory", STITCHED_COLOR, True)):
            cb = QCheckBox(text)
            cb.setIcon(_swatch_icon(color, dashed))
            cb.setChecked(True)
            cb.toggled.connect(lambda on, r=role: self._plots.set_visible(self._curves[r], on))
            box.addWidget(cb)
            self._show_checks[role] = cb
        box.addStretch()
        return box

    def _build_split_group(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.addWidget(QLabel("Select percentage of trajectories to be kept:"))
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 100)
        s.setValue(50)
        s.setTickPosition(QSlider.TickPosition.TicksBelow)
        s.setTickInterval(10)
        s.setMinimumWidth(220)
        s.valueChanged.connect(self._on_split_changed)
        box.addWidget(s)
        self._split_slider = s

        row = QHBoxLayout()
        self._pct_first = QLabel()
        self._pct_first.setStyleSheet(f"color: {FIRST_COLOR}; font-weight: bold;")
        self._pct_second = QLabel()
        self._pct_second.setStyleSheet(f"color: {SECOND_COLOR}; font-weight: bold;")
        row.addWidget(self._pct_first)
        row.addStretch()
        row.addWidget(self._pct_second)
        box.addLayout(row)
        box.addStretch()
        self._update_pct_labels(50)
        return box

    def _build_bottom_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Write changes to:"))
        self._path_edit = QLineEdit(db_file_path(self._conn))
        self._path_edit.setMinimumWidth(300)
        row.addWidget(self._path_edit, stretch=1)
        browse = QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)
        row.addSpacing(24)

        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self._stitch_btn = QPushButton("Stitch")
        self._stitch_btn.setAutoDefault(False)   # Enter must not stitch
        self._stitch_btn.clicked.connect(self._on_stitch)
        row.addWidget(self._stitch_btn)
        row.addWidget(cancel)
        return row

    # ── Drawing ────────────────────────────────────────────────────────

    def _traj_series(self, tr) -> dict:
        return {"dist": tr.dist, "speed": tr.speed, "accel": tr.accel}

    def _draw_sources(self):
        for role, tr, color in (("first", self._inputs.first, FIRST_COLOR),
                                ("second", self._inputs.second, SECOND_COLOR)):
            self._curves[role] = self._plots.add_curves(pg.mkPen(color, width=4))
            self._plots.set_curves(self._curves[role], tr.time, self._traj_series(tr))
        self._curves["stitched"] = self._plots.add_curves(
            _dashed_pen(STITCHED_COLOR, 2), z=10)

    def _draw_markers(self):
        seam = self._inputs.seam
        bound_pen = _dashed_pen("k", 3)
        if seam.case is TemporalCase.OVERLAP:
            self._plots.add_vlines([seam.overlap_start, seam.overlap_end], bound_pen)
            self._split_lines = self._plots.add_vlines(
                [seam.overlap_start], _dashed_pen("r", 3), z=6)
        elif seam.case is TemporalCase.GAP:
            self._plots.add_vlines([seam.first_end, seam.second_start], bound_pen)

    def _set_initial_view(self):
        """Center the seam region so it fills about half the width (like
        GapReviewDialog); y auto-fits the visible data."""
        seam, dt = self._inputs.seam, self._inputs.time_step
        if seam.case is TemporalCase.OVERLAP:
            lo, hi = seam.overlap_start, seam.overlap_end
        else:
            lo, hi = seam.first_end, seam.second_start
        half = max(hi - lo, 10 * dt)
        mid = 0.5 * (lo + hi)
        self._plots.set_x_range(mid - half, mid + half)

    # ── Plan / state updates ──────────────────────────────────────────

    def _split_fraction(self) -> float:
        return self._split_slider.value() / 100.0 if self._split_slider else 0.5

    def _rebuild_plan(self):
        try:
            plan = self._inputs.plan(self._id_spin.value(), self._split_fraction())
        except ValueError as exc:
            self._plan = None
            self._plots.set_curves(self._curves["stitched"], np.array([]), {})
            self._seam_label.setText(f"<span style='color:#C00000'>{exc}</span>")
            self._update_stitch_enabled()
            return

        self._plan = plan
        tr = plan.preview.trajectory
        self._plots.set_curves(self._curves["stitched"], tr.time, self._traj_series(tr))
        if plan.split_time is not None:
            for line in self._split_lines:
                line.setPos(plan.split_time)
        self._set_column(_COL_STITCHED, tr, include_id=False)
        self._seam_label.setText(self._seam_text(plan))
        self._update_stitch_enabled()

    def _on_split_changed(self, value: int):
        self._update_pct_labels(value)
        self._rebuild_plan()

    def _update_pct_labels(self, value: int):
        self._pct_first.setText(f"Traj 1: {value}%")
        self._pct_second.setText(f"Traj 2: {100 - value}%")

    def _on_id_changed(self, value: int):
        self._id_ok = ManualStitchingComp.is_veh_id_available(
            self._conn, self._inputs.event_id, value)
        self._id_spin.setStyleSheet("" if self._id_ok else "QSpinBox { color: #C00000; }")
        self._id_label.setText(
            "" if self._id_ok else f"Vehicle ID {value} is already used in this event.")
        self._id_label.setVisible(not self._id_ok)
        self._rebuild_plan()

    def _update_stitch_enabled(self):
        ok = self._plan is not None and self._id_ok
        self._stitch_btn.setEnabled(ok)
        self._stitch_btn.setToolTip("" if ok else "Resolve the issue shown on the right first.")

    # ── Table / text ──────────────────────────────────────────────────

    def _set_column(self, col: int, tr, include_id: bool):
        values = [str(tr.veh_id)] + _traj_values(tr)
        for row in range(0 if include_id else 1, len(_ROWS)):
            item = QTableWidgetItem(values[row])
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, col, item)

    def _fit_table_width(self):
        """Minimum width = row labels + three equal columns, each wide
        enough for the widest content in any column (including the ID
        spinbox). The splitter gives every other pixel to the plots."""
        t = self._table
        header = t.horizontalHeader()
        col_w = max(max(t.sizeHintForColumn(c), header.sectionSizeHint(c))
                    for c in range(t.columnCount()))
        col_w = max(col_w, self._id_spin.minimumWidth() + 2)
        width = (t.verticalHeader().sizeHint().width()
                 + 3 * col_w
                 + 2 * t.frameWidth()
                 + t.verticalScrollBar().sizeHint().width())
        t.setMinimumWidth(width)

    def _relationship_text(self) -> str:
        s = self._inputs.seam
        if s.case is TemporalCase.OVERLAP:
            txt = (f"<b>Overlap:</b> {s.overlap_duration:.2f} s "
                   f"(Traj 1: {s.n_overlap_first} pts, Traj 2: {s.n_overlap_second} pts)")
            if s.contained:
                txt += "<br>Traj 2 lies entirely within Traj 1's time span."
            return txt
        if s.case is TemporalCase.GAP:
            return (f"<b>Gap:</b> {s.gap_duration:.2f} s "
                    f"({s.missing_samples} sample(s) missing at {s.time_step:g} s)")
        return (f"<b>Continuous:</b> Traj 2 begins {s.gap_duration:.2f} s after "
                f"Traj 1 ends (sampling interval {s.time_step:g} s)")

    def _seam_text(self, plan: StitchPlan) -> str:
        if not plan.joins:
            return "No seam: the stitched trajectory is Traj 1 unchanged."
        lines = []
        for i, j in enumerate(plan.joins, 1):
            dv = f", Δv {j.d_speed:+.2f} m/s" if math.isfinite(j.d_speed) else ""
            v = f", implied {j.implied_speed:.1f} m/s" if math.isfinite(j.implied_speed) else ""
            lines.append(f"Seam {i} at {j.t_after:.2f} s: Δt {j.dt:.2f} s, "
                         f"Δd {j.d_dist:+.2f} m{dv}{v}")
        return "<br>".join(lines)

    # ── Actions ────────────────────────────────────────────────────────

    def _on_browse(self):
        start = self._path_edit.text().strip() or last_dialog_dir()
        path, _ = QFileDialog.getSaveFileName(
            self, "Write Stitching Changes To", start,
            "SQLite Database (*.sqlite);;SQLite Database (*.db);;All Files (*)",
            options=QFileDialog.Option.DontConfirmOverwrite)   # resolver owns warnings
        if path:
            remember_dialog_path(path)
            self._path_edit.setText(path)

    def _on_stitch(self):
        if self._plan is None or not self._id_ok:
            return
        target = self._resolve_target(self, self._path_edit.text().strip())
        if target is None:
            return

        self._conn = target
        self._path_edit.setText(db_file_path(target))

        try:
            stitch_id = ManualStitchingComp.apply(target, self._plan, self._safety)
        except (ValueError, RuntimeError, sqlite3.Error) as exc:
            QMessageBox.critical(self, "Trajectory Stitching",
                                 f"The stitch was not saved:\n{exc}")
            self._on_id_changed(self._id_spin.value())   # e.g. ID taken meanwhile
            return

        self.result_info = StitchResult(
            stitch_id=stitch_id, new_veh_id=self._plan.new_veh_id,
            source_veh_ids=self._plan.source_veh_ids,
            connection=target, db_path=db_file_path(target))
        self.accept()

    # ── Geometry ───────────────────────────────────────────────────────

    def _size_and_center(self):
        top = self.parent().window() if self.parent() else None
        if top is not None:
            w = min(max(int(top.width() * 0.85), 900), 1500)
            h = min(max(int(top.height() * 0.85), 600), 950)
        else:
            w, h = 1200, 800
        self.resize(w, h)
        if top is not None:
            g = top.geometry()
            self.move(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2)