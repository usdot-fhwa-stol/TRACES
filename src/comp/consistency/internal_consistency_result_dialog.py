"""Right-click result dialog for Module 4 — Internal Consistency.

Layout (two horizontal sections in a QSplitter):
  Left   — three vertically stacked pyqtgraph subplots (speed, accel, jerk)
  Right  — vertical QSplitter:
               Top    — single-row statistics summary table (current trajectory)
               Bottom — scrollable raw data table (Figure 12 layout)

Footer spanning both sections:
  [← Prev]  [Trajectory dropdown]  [Next →]  [Save Plots]  [Close]

Changing trajectory via dropdown or arrows reloads all sections and
resets each plot's view range.

Plot colours are fixed:
  Speed        — blue  (#1f77b4)
  Acceleration — red   (#d62728)
  Jerk         — green (#2ca02c)

Cross-plot synchronization
---------------------------
  - The crosshair (vertical line) moves together on all three plots,
    regardless of which plot's scene generated the mouse-move event.
  - The crosshair is added with ignoreBounds=True so that moving it does
    not perturb continuous auto-range -- without this, the view would
    appear to "auto-pan" whenever the line approached the view's edges.
  - Time (x) is linked across all three ViewBoxes via setXLink, so
    panning/zooming time on any one plot moves all three.
  - Y is intentionally NOT linked -- speed/accel/jerk have unrelated
    scales.  Y-axis mouse interaction is disabled and each plot's y-axis
    is kept in continuous auto-range, so each plot independently
    rescales to fit whatever is visible in the (shared) time window.
  - The corner "A" auto-range button on any one plot resets all three.
  - Hovering near a curve in any plot shows THREE small floating labels
    (one per plot, parented to that plot), each positioned next to that
    plot's own curve and containing only that plot's own time/value/
    calculated-value rows.  QToolTip can't be used for this because it's
    a single OS-level widget -- only one can ever be visible at a time,
    which is why a single combined tooltip was the earlier approach.

HTML export uses comp.plot_export.build_svg_html / export_plot_to_svg,
which are shared with JerkBoxPlotDialog.
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QWidget, QLabel,
)

from .internal_consistency_comp import InternalConsistencyResult
from .internal_consistency_stats_dialog import (
    _compute_stats, _StatRow, _COLUMNS as _STAT_COLUMNS, _isnan, NAN,
)
from comp.plot_export import export_plot_to_svg, build_svg_html

logger = logging.getLogger(__name__)

# ── Column definitions ─────────────────────────────────────────────────────

# Raw data table columns: (header_abbrev, full_name, attribute, format)
_RAW_COLUMNS = [
    ("Event ID",   "Event ID",                              "event_id",      "{:d}"),
    ("Veh ID",     "Vehicle ID",                            "veh_id",        "{:d}"),
    ("Time",   "Time (s)",                              "veh_time",      "{:.4f}"),
    ("Dist",   "Distance Traveled (m)",                 "veh_dist_trav", "{:.4f}"),
    ("Speed",  "Speed (m/s)",                           "veh_speed",     "{:.4f}"),
    ("Speed Cal",    "Calculated Speed (m/s)",             "veh_speed_cal", "{:.4f}"),
    ("Speed Err",    "Speed Error (m/s)", "speed_error",   "{:.4f}"),
    ("Accel", "Acceleration (m/s²)",                 "veh_accel",     "{:.4f}"),
    ("Accel Cal",  "Calculated Acceleration (m/s²)",     "veh_accel_cal", "{:.4f}"),
    ("Accel Err",  "Acceleration Error (m/s²)", "accel_error",   "{:.4f}"),
    ("Jerk", "Jerk (m/s³)",                         "veh_jerk",      "{:.4f}"),
    ("Jerk Cal",   "Calculated Jerk (m/s³)",             "veh_jerk_cal",  "{:.4f}"),
    ("Jerk Err",   "Jerk Error (m/s³)", "jerk_error",    "{:.4f}"),
]

# Plot configuration: (title, orig_attr, cal_attr, y_label, hex_color)
_PLOTS = [
    ("Speed",        "veh_speed", "veh_speed_cal", "Speed (m/s)",   "#1f77b4"),
    ("Acceleration", "veh_accel", "veh_accel_cal", "Accel (m/s²)",  "#d62728"),
    ("Jerk",         "veh_jerk",  "veh_jerk_cal",  "Jerk (m/s³)",   "#2ca02c"),
]

_TOOLTIP_LABEL_STYLE = (
    "QLabel {"
    "  background-color: #ffffe1;"
    "  border: 1px solid #767676;"
    "  padding: 2px 5px;"
    "  font-size: 11px;"
    "  color: #000000;"
    "}"
)


class InternalConsistencyResultDialog(QDialog):
    """Combined right-click output dialog for Module 4."""

    # Snap tolerance in data units (seconds along the x/time axis).
    _TOOLTIP_T_TOL: float = 0.1
    # Snap tolerance in screen pixels for deciding a tooltip should appear.
    _TOOLTIP_PIXEL_TOL: float = 12.0

    def __init__(self, parent, results: List[InternalConsistencyResult]):
        super().__init__(parent)
        self.setWindowTitle("Internal Consistency — Results")
        self.setModal(True)
        self.resize(1400, 700)

        # Group results by (event_id, veh_id) preserving insertion order.
        self._traj_keys: List[Tuple[int, int]] = []
        self._traj_data: Dict[Tuple[int, int], List[InternalConsistencyResult]] = {}
        for r in results:
            key = (r.event_id, r.veh_id)
            if key not in self._traj_data:
                self._traj_keys.append(key)
                self._traj_data[key] = []
            self._traj_data[key].append(r)

        # Pre-compute stats for the stats panel.
        self._stats: Dict[Tuple[int, int], _StatRow] = {
            (s.event_id, s.veh_id): s
            for s in _compute_stats(results)
        }

        self._current_idx: int = 0
        self._plot_widgets: List[pg.PlotWidget] = []
        # Parallel lists populated in _build_plot_panel; one entry per plot.
        self._plot_curves:  List[List[pg.PlotDataItem]] = []  # [plot_idx][curve_idx]
        self._vlines:       List[pg.InfiniteLine] = []        # vertical crosshair per plot
        self._tooltip_labels: List[QLabel] = []                # floating per-plot tooltip

        self._setup_ui()
        self._load_trajectory(0)
        QTimer.singleShot(0, self._center)

    # ── UI construction ───────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)

        # Outer horizontal splitter: plots (left) | tables (right)
        outer = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(outer, stretch=1)

        outer.addWidget(self._build_plot_panel())
        outer.addWidget(self._build_right_panel())
        outer.setSizes([520, 880])

        root.addLayout(self._build_footer())

    def _build_plot_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._plot_widgets   = []
        self._plot_curves    = []
        self._vlines         = []
        self._tooltip_labels = []
        for title, _orig, _cal, ylabel, _color in _PLOTS:
            pw = pg.PlotWidget()
            pw.setBackground("w")
            pw.setTitle(title)
            pw.setLabel("left", ylabel)
            pw.setLabel("bottom", "Time (s)")
            pw.showGrid(x=True, y=True, alpha=0.3)
            pw.addLegend(offset=(5, 5))

            # Vertical crosshair — hidden until the mouse enters a plot.
            # ignoreBounds=True keeps the line's x-position out of the
            # auto-range bounding box; without it, moving the line near
            # the view's edges makes continuous auto-range "chase" it and
            # the plot appears to pan on its own.
            vline = pg.InfiniteLine(angle=90, movable=False,
                                    pen=pg.mkPen("#888888", width=1,
                                                 style=Qt.PenStyle.DashLine))
            vline.setVisible(False)
            pw.addItem(vline, ignoreBounds=True)
            self._vlines.append(vline)

            # Floating per-plot tooltip. A real child QWidget (not the OS
            # QToolTip), so up to three can be visible simultaneously,
            # each positioned next to its own plot's curve.
            # WA_TransparentForMouseEvents lets mouse-move events pass
            # through to the GraphicsView underneath, so the label sitting
            # on top of a plot never interrupts the hover/crosshair sync.
            lbl = QLabel(pw)
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lbl.setStyleSheet(_TOOLTIP_LABEL_STYLE)
            lbl.hide()
            self._tooltip_labels.append(lbl)

            # Wire mouse-move on every plot scene to the shared handler.
            pw.scene().sigMouseMoved.connect(
                lambda pos, _pw=pw: self._on_mouse_moved(pos, _pw)
            )

            self._plot_widgets.append(pw)
            self._plot_curves.append([])   # filled in _refresh_plots
            layout.addWidget(pw)

        # ── Cross-plot synchronization ───────────────────────────────────
        # Time is the same physical axis on all three plots, so link it:
        # panning/zooming time on any one plot moves all three. Y is left
        # unlinked -- speed/accel/jerk have unrelated scales, so each
        # plot's y-axis stays in continuous auto-range instead, rescaling
        # on its own to fit whatever time window is currently visible.
        ref_vb = self._plot_widgets[0].getViewBox()
        for pw in self._plot_widgets:
            vb = pw.getViewBox()
            vb.setMouseEnabled(x=True, y=False)
            pw.enableAutoRange(axis="y", enable=True)
            if vb is not ref_vb:
                vb.setXLink(ref_vb)

            # The built-in "A" corner button only resets its own plot by
            # default; add our own handler so it resets all three.
            pw.getPlotItem().autoBtn.clicked.connect(self._on_auto_btn_clicked)

        return container

    def _build_right_panel(self) -> QWidget:
        """Vertical splitter: single-row stats table (top) + raw data table (bottom)."""
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_stats_table())
        splitter.addWidget(self._build_raw_table())
        # Give the stats table a compact fixed share; raw table gets the rest.
        splitter.setSizes([80, 600])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        return splitter

    def _build_stats_table(self) -> QTableWidget:
        self._stats_table = QTableWidget()
        self._stats_table.setColumnCount(len(_STAT_COLUMNS))
        self._stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._stats_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._stats_table.setAlternatingRowColors(True)
        self._stats_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._stats_table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self._stats_table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        for ci, (abbrev, full, _, _) in enumerate(_STAT_COLUMNS):
            item = QTableWidgetItem(abbrev)
            item.setToolTip(full)
            self._stats_table.setHorizontalHeaderItem(ci, item)
        return self._stats_table

    def _build_raw_table(self) -> QTableWidget:
        self._raw_table = QTableWidget()
        self._raw_table.setColumnCount(len(_RAW_COLUMNS))
        self._raw_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._raw_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._raw_table.setAlternatingRowColors(True)
        self._raw_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._raw_table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self._raw_table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        for ci, (abbrev, full, _, _fmt) in enumerate(_RAW_COLUMNS):
            item = QTableWidgetItem(abbrev)
            item.setToolTip(full)
            self._raw_table.setHorizontalHeaderItem(ci, item)

        self._raw_table.cellClicked.connect(self._on_raw_row_clicked)
        return self._raw_table

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.clicked.connect(self._on_prev)
        row.addWidget(self._prev_btn)

        self._traj_combo = QComboBox()
        for eid, vid in self._traj_keys:
            self._traj_combo.addItem(f"Event {eid} — Veh {vid}")
        self._traj_combo.currentIndexChanged.connect(self._on_combo_changed)
        row.addWidget(self._traj_combo)

        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._on_next)
        row.addWidget(self._next_btn)

        save_btn = QPushButton("Save Plots")
        save_btn.clicked.connect(self._on_save_plots)
        row.addWidget(save_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)

        row.addStretch()

        return row

    # ── Trajectory loading ────────────────────────────────────────────────

    def _load_trajectory(self, idx: int):
        self._current_idx = idx
        key  = self._traj_keys[idx]
        rows = self._traj_data[key]

        self._refresh_plots(rows)
        self._refresh_raw_table(rows)
        self._refresh_stats_table(key)

        self._traj_combo.blockSignals(True)
        self._traj_combo.setCurrentIndex(idx)
        self._traj_combo.blockSignals(False)

        n = len(self._traj_keys)
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < n - 1)

    def _refresh_plots(self, rows: List[InternalConsistencyResult]):
        # Hide any tooltip left over from the previous trajectory before
        # the underlying curve data changes out from under it.
        for lbl in self._tooltip_labels:
            lbl.hide()

        t = np.asarray([r.veh_time for r in rows], dtype=np.float64)

        for pi, (pw, (_title, orig_attr, cal_attr, _ylabel, color)) in enumerate(
                zip(self._plot_widgets, _PLOTS)):
            pw.clear()
            pw.addLegend(offset=(5, 5))
            # Re-add the crosshair (clear() removes all items including
            # it). ignoreBounds=True for the same reason as on initial
            # construction -- see _build_plot_panel. Note: the floating
            # tooltip QLabel is a native Qt child widget, not a scene
            # item, so it survives pw.clear() untouched and needs no
            # re-adding here.
            vline = self._vlines[pi]
            vline.setVisible(False)
            pw.addItem(vline, ignoreBounds=True)

            curves: List[pg.PlotDataItem] = []

            orig = np.asarray([getattr(r, orig_attr) for r in rows], dtype=np.float64)
            cal  = np.asarray([getattr(r, cal_attr)  for r in rows], dtype=np.float64)

            # Original — solid line
            if not np.all(np.isnan(orig)):
                c = pw.plot(t, orig,
                            pen=pg.mkPen(color=color, width=4),
                            name=orig_attr.replace("_", " "))
                curves.append(c)

            # Reconstructed — dashed, same colour
            if not np.all(np.isnan(cal)):
                pen = pg.mkPen(color=color, width=4)
                pen.setStyle(Qt.PenStyle.CustomDashLine)
                pen.setDashPattern([4, 4])   # longer dash, longer gap than the old DashLine default
                c = pw.plot(t, cal,
                            pen=pen,
                            name=cal_attr.replace("_", " "))
                curves.append(c)

            self._plot_curves[pi] = curves

            # Reset view so the new trajectory's data fills the viewport.
            # Y stays in continuous auto-range afterward (set once in
            # _build_plot_panel); X will too, until the user manually
            # pans/zooms it, at which point pyqtgraph respects that and
            # stops auto-fitting x for this trajectory.
            pw.enableAutoRange()

    def _refresh_raw_table(self, rows):
        self._raw_table.setRowCount(len(rows))
        last_idx = len(rows) - 1
        for ri, r in enumerate(rows):
            for ci, (abbrev, full, attr, fmt) in enumerate(_RAW_COLUMNS):
                val = getattr(r, attr)
                text = "" if _isnan(val) else fmt.format(val)
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if ri == last_idx and attr.endswith("_cal"):
                    item.setToolTip("No following record to derive this value")
                    item.setBackground(QColor("#f0f0f0"))
                self._raw_table.setItem(ri, ci, item)

    def _refresh_stats_table(self, key: Tuple[int, int]):
        """Show only the stats row for the currently selected trajectory."""
        stat = self._stats.get(key)
        self._stats_table.setRowCount(1 if stat else 0)
        if not stat:
            return
        for ci, (abbrev, full, attr, fmt) in enumerate(_STAT_COLUMNS):
            val = getattr(stat, attr, NAN)
            if attr in ("event_id", "veh_id"):
                text = str(val)
            else:
                text = "" if _isnan(val) else fmt.format(val)
            item = QTableWidgetItem(text)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._stats_table.setItem(0, ci, item)

    def _on_raw_row_clicked(self, row: int, _col: int):
        rows = self._traj_data[self._traj_keys[self._current_idx]]
        t_val = rows[row].veh_time
        for vline in self._vlines:
            vline.setValue(t_val)
            vline.setVisible(True)

    # ── Auto-range button ────────────────────────────────────────────────

    def _on_auto_btn_clicked(self, *_args):
        """The corner 'A' button only auto-ranges its own plot by
        default; this makes it reset all three so the views stay
        comparable after the user has manually panned/zoomed.
        """
        for pw in self._plot_widgets:
            pw.enableAutoRange()

    # ── Tooltip / crosshair ───────────────────────────────────────────────

    def _on_mouse_moved(self, pos, source_pw: pg.PlotWidget):
        """Move the crosshair on all three plots together, and show one
        small floating label per plot -- each positioned next to that
        plot's own curve, containing only that plot's own time/value/
        calculated-value rows -- rather than a single combined tooltip
        anchored to whichever plot the mouse happens to be over.

        ``pos`` is in scene coordinates of ``source_pw``'s scene.
        """
        pi = self._plot_widgets.index(source_pw)
        vb = source_pw.getViewBox()

        if not source_pw.sceneBoundingRect().contains(pos):
            # Mouse has left this plot — hide all crosshairs and labels.
            for vline in self._vlines:
                vline.setVisible(False)
            for lbl in self._tooltip_labels:
                lbl.hide()
            return

        data_pt = vb.mapSceneToView(pos)
        mx = data_pt.x()

        # All three crosshairs move together, regardless of which plot's
        # scene actually generated this event.
        for vline in self._vlines:
            vline.setValue(mx)
            vline.setVisible(True)

        # Whether any tooltip appears at all is decided by proximity to a
        # curve in the plot under the mouse.
        hit = self._closest_point(pi, mx, data_pt.y(), vb)
        if hit is None:
            for lbl in self._tooltip_labels:
                lbl.hide()
            return
        hit_x, _hit_y, _hit_label, _hit_dist = hit

        # Each plot gets its own label, built only from its own values at
        # hit_x, and positioned next to its own curve -- not the cursor.
        for qi, pw in enumerate(self._plot_widgets):
            lbl = self._tooltip_labels[qi]
            vals = self._values_at_time(qi, hit_x)
            if not vals:
                lbl.hide()
                continue

            lines = [f"t = {hit_x:.4f} s"]
            ys = []
            for _x, y, name in vals:
                lines.append(f"{name}: {y:.4f}")
                ys.append(y)

            lbl.setText("\n".join(lines))
            lbl.adjustSize()
            self._place_tooltip_label(lbl, pw, pw.getViewBox(),
                                      hit_x, sum(ys) / len(ys))

    def _values_at_time(self, qi: int, hit_x: float) -> List[Tuple[float, float, str]]:
        """(x, y, label) for every curve in plot `qi` with a sample within
        _TOOLTIP_T_TOL of hit_x -- i.e. that plot's own original and/or
        calculated value at this instant.
        """
        out: List[Tuple[float, float, str]] = []
        for curve in self._plot_curves[qi]:
            xdata, ydata = curve.getData()
            if xdata is None or len(xdata) == 0:
                continue
            idx = int(np.argmin(np.abs(xdata - hit_x)))
            if abs(xdata[idx] - hit_x) <= self._TOOLTIP_T_TOL:
                out.append((float(xdata[idx]), float(ydata[idx]), curve.name() or ""))
        return out

    def _place_tooltip_label(self, label: QLabel, pw: pg.PlotWidget, vb,
                             x: float, y: float) -> None:
        """Position `label` (a child widget of `pw`) at the pixel location
        of data point (x, y) in `pw`'s own view, offset slightly so it
        doesn't sit directly on top of the curve point, and clamped so it
        never extends past that plot's own edges.
        """
        scene_pt  = vb.mapViewToScene(pg.Point(x, y))
        widget_pt = pw.mapFromScene(scene_pt)
        px = widget_pt.x() + 10
        py = widget_pt.y() - label.height() - 8
        px = max(0, min(int(px), pw.width()  - label.width()))
        py = max(0, min(int(py), pw.height() - label.height()))
        label.move(px, py)
        label.show()
        label.raise_()

    def _closest_point(
        self, pi: int, mx: float, my: float, vb
    ) -> Optional[Tuple[float, float, str, float]]:
        """Closest curve point to (mx, my) in plot `pi`, by on-screen
        pixel distance.

        Returns (x, y, label, pixel_dist), or None if nothing in that
        plot is within _TOOLTIP_PIXEL_TOL pixels of the cursor.
        """
        best_dist = float("inf")
        best: Optional[Tuple[float, float, str, float]] = None
        for curve in self._plot_curves[pi]:
            xdata, ydata = curve.getData()
            if xdata is None or len(xdata) == 0:
                continue
            mask = np.abs(xdata - mx) <= self._TOOLTIP_T_TOL
            if not mask.any():
                continue
            xs, ys = xdata[mask], ydata[mask]
            idx = int(np.argmin(np.abs(xs - mx)))
            # Pixel distance, so the threshold is display-size independent.
            pt_scene  = vb.mapViewToScene(pg.Point(xs[idx], ys[idx]))
            cur_scene = vb.mapViewToScene(pg.Point(mx, my))
            dist = (pt_scene - cur_scene).manhattanLength()
            if dist < best_dist:
                best_dist = dist
                best = (float(xs[idx]), float(ys[idx]), curve.name() or "", dist)
        if best is not None and best[3] <= self._TOOLTIP_PIXEL_TOL:
            return best
        return None

    # ── Footer slots ──────────────────────────────────────────────────────

    def _on_prev(self):
        if self._current_idx > 0:
            self._load_trajectory(self._current_idx - 1)

    def _on_next(self):
        if self._current_idx < len(self._traj_keys) - 1:
            self._load_trajectory(self._current_idx + 1)

    def _on_combo_changed(self, idx: int):
        if idx != self._current_idx:
            self._load_trajectory(idx)

    # ── Save plots ────────────────────────────────────────────────────────

    def _on_save_plots(self):
        from utils import default_save_stem, last_dialog_dir, remember_dialog_path
        key     = self._traj_keys[self._current_idx]
        stem    = default_save_stem(f"internal_consistency_e{key[0]}_v{key[1]}")
        default = str(Path(last_dialog_dir()) / f"{stem}.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plots", default,
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return
        remember_dialog_path(path)
        try:
            svg_blocks = [
                (title, export_plot_to_svg(pw))
                for pw, (title, _o, _c, _y, _col) in zip(self._plot_widgets, _PLOTS)
            ]
            html = build_svg_html(
                "Internal Consistency — Plots",
                svg_blocks,
                grid_columns=1,
                cell_height_px=300,
            )
            Path(path).write_text(html, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to save plots")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")

    # ── Centering ─────────────────────────────────────────────────────────

    def _center(self):
        parent = self.parent()
        if parent:
            pg_ = parent.geometry()
            dg  = self.geometry()
            self.move(
                pg_.x() + (pg_.width()  - dg.width())  // 2,
                pg_.y() + (pg_.height() - dg.height()) // 2,
            )