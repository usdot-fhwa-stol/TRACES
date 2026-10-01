"""Right-click "Filter > Spline Fitting" preview window (Figure 4).

Opens for exactly one (event_id, veh_id) trajectory. Lets the user choose
a fitting base (Position/Speed/Acceleration/Jerk), try different
smoothness (s) / degree (k) values, and see the resulting spline fit
overlaid on the original measured data on all four kinematic plots at
once, each in its own tab, plus a per-plot goodness-of-fit readout shown
as that plot's title. Nothing here is ever written to the database —
this dialog is parameter exploration only, matching the SplineReport's
"no output to be saved" instruction for the right-click pathway.

Fitting base is selectable, not fixed, so a single dialog instance covers
all four bases rather than requiring four separate menu entries. Combobox
entries are disabled (with a tooltip) for any base lacking at least
min(allowed_k_options(base)) + 1 valid measured points on this trajectory.

Base/s/k combo and spin changes have no effect on any plot until "Update
Plot" is clicked -- changing the base combo only refreshes the k-degree
options (which are inherently tied to the base) so a valid choice is
available before the next Update. The currently-viewed tab never changes
as a side effect of any control, including Update Plot -- the user is
judging how new parameters affected whichever plot they're already
looking at. The tab belonging to the base actually used by the last
successful fit is marked with a trailing "*" in its label.

All four plots are persistent widgets (one per kinematic), kept alive for
the life of the dialog rather than rebuilt from a single shared plot.
Their time (x) axes are linked so panning/zooming on one tab is preserved
when switching to another; each plot's y-axis is left independently
auto-ranging since the four kinematics have unrelated scales. Because the
four plots always exist, HTML/SVG export (Save Plots) reads directly from
them -- no separate offscreen rendering step is needed.

Synchronous by design: a single trajectory's worth of points fits well
within the time budget of a direct call, so this dialog does not use
BaseAnalysisDialog / BaseAnalysisWorker at all (no progress bar, no
QThread).

Precondition (enforced by the caller, plot/custom_view_box.py)
----------------------------------------------------------------
The caller is expected to verify, before constructing this dialog, that
the current selection context resolves to exactly one trajectory —
mirroring SplineReport's "Filtering is only available when one trajectory
is selected" rule. This class does not re-check selection context; it
only checks (per fitting base) whether *that one* trajectory has enough
valid data points, closing itself with a message if none of the four
bases are viable at all.

Measured data is read with imputed_status = IMPUTED_SPLINE excluded (where 
that column exists): this window is about testing fit quality against real
measurements, not against rows synthesized by a previous "fill missing
rows" run.

HTML export uses comp.plot_export.build_svg_html / export_plot_to_svg,
which are shared with JerkBoxPlotDialog / InternalConsistencyResultDialog.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
)

from comp.plot_export import export_plot_to_svg, build_svg_html
from .spline_comp import (
    BASE_LABELS, KINEMATICS, FITTING_BASES, _ORDER, allowed_k_options,
    apply_spline_fit, compute_error_metrics, nearest_valid_ic,
)

logger = logging.getLogger(__name__)

_KINEMATIC_LABELS = {
    "position":     "Position (m)",
    "speed":        "Speed (m/s)",
    "acceleration": "Acceleration (m/s²)",
    "jerk":         "Jerk (m/s³)",
}

_NO_VIABLE_BASE_MESSAGE = (
    "Not enough valid data on any kinematic variable (position, speed, "
    "acceleration, jerk) to fit a spline for this trajectory."
)


class SplineTestingDialog(QDialog):
    """Right-click spline fitting preview/testing window — never writes to the DB."""

    def __init__(self, parent, db_connection, event_id: int, veh_id: int,
                 series_color: str = "#1f77b4"):
        super().__init__(parent)
        self._db_connection = db_connection
        self._event_id = event_id
        self._veh_id = veh_id

        self._time: np.ndarray = np.array([], dtype=np.float64)
        self._measured: Dict[str, np.ndarray] = {k: np.array([]) for k in KINEMATICS}

        self._time_valid: np.ndarray = np.array([], dtype=np.float64)
        self._base_valid: np.ndarray = np.array([], dtype=np.float64)

        # "Applied" state — reflects the last successfully computed fit,
        # never the raw combo/spin values. None until Update Plot succeeds
        # for the first time. This drives the dashed fit traces, the
        # per-plot titles, and the tab asterisk; combo/spin changes alone
        # never touch it.
        self._fitting_base: Optional[str] = None
        self._kinematic_result: Optional[Dict[str, np.ndarray]] = None
        self._last_s: float = 1.0

        self._plots: Dict[str, pg.PlotWidget] = {}
        self._legends: Dict[str, pg.LegendItem] = {}

        self.setWindowTitle(f"Spline Fitting Test — Event {event_id}, Vehicle {veh_id}")
        self.setModal(False)

        self._load_data()

        availability = self._base_availability()
        if not any(availability.values()):
            QMessageBox.warning(self.parent(), "Spline Fitting", _NO_VIABLE_BASE_MESSAGE)
            QTimer.singleShot(0, self.close)
            return

        self._setup_ui(availability)

        default_base = next(b for b in FITTING_BASES if availability[b])
        idx = self._base_combo.findData(default_base)
        if idx >= 0:
            self._base_combo.setCurrentIndex(idx)
        # Unconditional: Qt does not re-emit currentIndexChanged if idx was
        # already 0, so the initial k-combo population can't rely solely
        # on the signal firing.
        self._on_base_combo_changed()

        self._size_and_center_dialog()

    # ── Data loading (read-only — never writes anything back) ───────────

    def _load_data(self):
        from comp.base_comp import BaseComp, IMPUTED_SPLINE
        cols = BaseComp._events_columns(self._db_connection)
        has_speed = "veh_speed" in cols
        has_accel = "veh_accel" in cols
        has_jerk  = "veh_jerk"  in cols
        has_flag  = "imputed_status" in cols

        select_cols = ["veh_time", "veh_dist_trav"]
        if has_speed:
            select_cols.append("veh_speed")
        if has_accel:
            select_cols.append("veh_accel")
        if has_jerk:
            select_cols.append("veh_jerk")

        where_extra = (
            f" AND (imputed_status IS NULL OR imputed_status != '{IMPUTED_SPLINE}')"
            if has_flag else ""
        )
        sql = (
            f"SELECT {', '.join(select_cols)} FROM events "
            f"WHERE event_id = ? AND veh_id = ?{where_extra} "
            f"ORDER BY veh_time"
        )
        cursor = self._db_connection.cursor()
        cursor.execute(sql, (self._event_id, self._veh_id))
        rows = cursor.fetchall()
        n = len(rows)

        col_idx = {name: i for i, name in enumerate(select_cols)}

        def column(name: str) -> np.ndarray:
            if name not in col_idx:
                return np.full(n, np.nan)
            i = col_idx[name]
            return np.array(
                [r[i] if r[i] is not None else np.nan for r in rows],
                dtype=np.float64,
            )

        self._time = column("veh_time")
        self._measured = {
            "position":     column("veh_dist_trav"),
            "speed":        column("veh_speed"),
            "acceleration": column("veh_accel"),
            "jerk":         column("veh_jerk"),
        }

    def _base_availability(self) -> Dict[str, bool]:
        """Return {base: True/False} indicating whether each fitting base
        has at least min(allowed_k_options(base)) + 1 valid (non-NaN)
        measured points for this trajectory."""
        availability = {}
        for base in FITTING_BASES:
            valid_count = int(np.sum(~np.isnan(self._measured[base])))
            min_required = min(allowed_k_options(base)) + 1
            availability[base] = valid_count >= min_required
        return availability

    def _recompute_valid_arrays(self, base: str):
        base_array = self._measured[base]
        valid_mask = ~np.isnan(base_array)
        time_valid = self._time[valid_mask]
        base_valid = base_array[valid_mask]

        # Defensive de-dup, matching SplineComp.comp_series.
        if time_valid.size >= 2 and np.any(np.diff(time_valid) <= 0):
            _, uniq_idx = np.unique(time_valid, return_index=True)
            uniq_idx = np.sort(uniq_idx)
            time_valid = time_valid[uniq_idx]
            base_valid = base_valid[uniq_idx]

        self._time_valid = time_valid
        self._base_valid = base_valid

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self, availability: Dict[str, bool]):
            layout = QVBoxLayout(self)
    
            # ── Top: one tab per kinematic, each holding its own persistent plot ──
            self._tabs = QTabWidget()
            self._tabs.setTabPosition(QTabWidget.TabPosition.North)
    
            for key in KINEMATICS:
                pw = pg.PlotWidget()
                pw.setBackground('w')
                pw.setLabel("bottom", "Time (s)")
                pw.setLabel("left", _KINEMATIC_LABELS[key])
                pw.showGrid(x=True, y=True, alpha=0.2)
                # Never guaranteed to be the active tab, so it may never be
                # laid out by the QTabWidget's stack before Save Plots runs —
                # give SVGExporter a real size to work with regardless.
                pw.setMinimumSize(500, 350)
                pw.getViewBox().setMouseEnabled(x=True, y=False)
                pw.enableAutoRange(axis="y", enable=True)
                self._legends[key] = pw.addLegend()
                self._plots[key] = pw
                self._tabs.addTab(pw, _KINEMATIC_LABELS[key])
    
            # Time is the same physical axis on all four plots, so link it —
            # panning/zooming time on any one tab carries over when switching
            # tabs. Y is deliberately left unlinked: each kinematic has its
            # own scale, and continuous auto-range keeps it independently
            # fit to whatever time window is currently visible.
            ref_vb = self._plots[KINEMATICS[0]].getViewBox()
            for key in KINEMATICS:
                vb = self._plots[key].getViewBox()
                if vb is not ref_vb:
                    vb.setXLink(ref_vb)
                # The built-in "A" corner button only resets its own plot by
                # default; this makes it reset all four together.
                self._plots[key].getPlotItem().autoBtn.clicked.connect(self._on_auto_btn_clicked)
    
            layout.addWidget(self._tabs, stretch=1)
    
            # ── Bottom: controls laid out horizontally ──
            control_bar = QHBoxLayout()
    
            control_bar.addWidget(QLabel("Fitting Base:"))
            self._base_combo = QComboBox()
            for key in FITTING_BASES:
                self._base_combo.addItem(BASE_LABELS[key], key)
                idx = self._base_combo.count() - 1
                if not availability[key]:
                    item = self._base_combo.model().item(idx)
                    item.setEnabled(False)
                    item.setToolTip(
                        f"Not enough valid {BASE_LABELS[key].lower()} data "
                        f"points on this trajectory.")
            self._base_combo.currentIndexChanged.connect(self._on_base_combo_changed)
            control_bar.addWidget(self._base_combo)
    
            control_bar.addSpacing(12)
            control_bar.addWidget(QLabel("Smoothness (s):"))
            self._s_spin = QDoubleSpinBox()
            self._s_spin.setRange(0.0, 1_000_000.0)
            self._s_spin.setDecimals(4)
            self._s_spin.setValue(1.0)
            control_bar.addWidget(self._s_spin)
    
            control_bar.addSpacing(12)
            control_bar.addWidget(QLabel("Degree (k):"))
            self._k_combo = QComboBox()
            control_bar.addWidget(self._k_combo)
    
            control_bar.addSpacing(20)
            self._show_original_check = QCheckBox("Show Original Data")
            self._show_original_check.setChecked(True)
            self._show_original_check.stateChanged.connect(self._on_series_visibility_changed)
            control_bar.addWidget(self._show_original_check)
    
            self._show_filtered_check = QCheckBox("Show Filtered Data")
            self._show_filtered_check.setChecked(True)
            self._show_filtered_check.stateChanged.connect(self._on_series_visibility_changed)
            control_bar.addWidget(self._show_filtered_check)
    
            control_bar.addStretch()
    
            self._update_btn = QPushButton("Update Plot")
            self._update_btn.clicked.connect(self._on_update_plot)
            control_bar.addWidget(self._update_btn)
    
            # Left enabled by default — the original-data traces are always
            # exportable even before any fit has been computed.
            self._save_btn = QPushButton("Save Plots")
            self._save_btn.clicked.connect(self._on_save_plots)
            control_bar.addWidget(self._save_btn)
    
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.close)
            control_bar.addWidget(close_btn)
    
            layout.addLayout(control_bar)
    
            # Original-data traces don't depend on fitting parameters at all,
            # so draw them once up front, independent of the base combo.
            for key in KINEMATICS:
                self._redraw_plot(key)
                self._update_plot_title(key)
            self._update_tab_labels()

    def _size_and_center_dialog(self):
        container = self.parent()
        if container is not None and container.parent() is not None:
            container = container.parent()
        if container is not None:
            parent_geom = container.frameGeometry()
            self.resize(int(parent_geom.width() * 0.7), int(parent_geom.height() * 0.7))
            self_geom = self.frameGeometry()
            self_geom.moveCenter(parent_geom.center())
            self.move(self_geom.topLeft())
        else:
            self.resize(900, 650)

    # ── Cross-plot synchronization ──────────────────────────────────────

    def _on_auto_btn_clicked(self, *_args):
        """The corner 'A' button only auto-ranges its own plot by
        default; this makes it reset all four so the tabs stay
        comparable after the user has manually panned/zoomed one."""
        for pw in self._plots.values():
            pw.enableAutoRange()

    # ── Fitting-base combobox ─────────────────────────────────────────────

    def _populate_k_combo(self, base: str):
        self._k_combo.blockSignals(True)
        self._k_combo.clear()
        for k in allowed_k_options(base):
            self._k_combo.addItem(str(k), k)
        self._k_combo.setCurrentIndex(0)
        self._k_combo.blockSignals(False)

    def _on_base_combo_changed(self, _index=None):
        """Refresh the k-degree options and the regression input arrays
        for the newly selected base. Deliberately does *not* touch any
        plot, self._fitting_base, or self._kinematic_result — per the
        "no effect until Update Plot" rule, a base change only stages
        what the *next* Update Plot click will use."""
        base = self._base_combo.currentData()
        if base is None:
            return
        self._recompute_valid_arrays(base)
        self._populate_k_combo(base)

    def _on_series_visibility_changed(self, *_args):
            for key in KINEMATICS:
                self._redraw_plot(key)

    # ── Fit ──────────────────────────────────────────────────────────────

    def _on_update_plot(self):
        base = self._base_combo.currentData()
        s = self._s_spin.value()
        k = self._k_combo.currentData()
        if base is None or k is None:
            return

        self._recompute_valid_arrays(base)   # defensive re-sync with the current combo state

        base_order = _ORDER[base]
        initial_conditions = {
            name: ic
            for name in ("position", "speed", "acceleration")
            if _ORDER[name] < base_order
            for ic in [nearest_valid_ic(
                self._time, self._measured[name], self._time_valid[0]
            )]
            if ic is not None
        }

        result = apply_spline_fit(
            self._time_valid, self._base_valid, base,
            s=s, k=k, target_timestamps=self._time,
            initial_conditions=initial_conditions,
        )
        if result is None:
            QMessageBox.warning(
                self, "Spline Fitting",
                "Spline fit failed for the current parameters (insufficient "
                "valid points, duplicate timestamps, or an internal error). "
                "Try adjusting s or k.")
            return   # previous applied fit / plots / tab labels stay exactly as they were

        self._fitting_base = base
        self._kinematic_result = result
        self._last_s = s

        for key in KINEMATICS:
            self._redraw_plot(key)
            self._update_plot_title(key)
        self._update_tab_labels()
        # self._tabs.setCurrentIndex(...) is never called — the user is
        # judging how the new parameters affected the plot they're
        # already looking at.

    @staticmethod
    def _split_segments(time_array: np.ndarray, value_array: np.ndarray):
        """Yield (t_seg, v_seg) runs of contiguous non-NaN values, so plotted
        lines break at gaps instead of connecting across missing data."""
        valid = ~np.isnan(value_array)
        if not np.any(valid):
            return
        edges = np.diff(valid.astype(np.int8))
        starts = list(np.where(edges == 1)[0] + 1)
        ends = list(np.where(edges == -1)[0] + 1)
        if valid[0]:
            starts.insert(0, 0)
        if valid[-1]:
            ends.append(len(valid))
        for s_idx, e_idx in zip(starts, ends):
            yield time_array[s_idx:e_idx], value_array[s_idx:e_idx]

    # ── Per-plot drawing ─────────────────────────────────────────────────

    def _redraw_plot(self, key: str):
        """Rebuild both the original-data and spline-fit traces for one
        kinematic's plot. Called for all four keys after a checkbox
        toggle or a successful Update Plot."""
        plot = self._plots[key]
        plot.clear()
        self._legends[key].clear()

        if self._show_original_check.isChecked():
            measured = self._measured[key]
            first = True
            for t_seg, v_seg in self._split_segments(self._time, measured):
                item = pg.PlotDataItem(
                    x=t_seg, y=v_seg,
                    pen=pg.mkPen(color='k', width=4),
                )
                plot.addItem(item)
                if first:
                    self._legends[key].addItem(item, "Original Data")
                    first = False

        if self._show_filtered_check.isChecked() and self._kinematic_result is not None:
            pen = pg.mkPen(color='k', width=4)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 4])   # longer dash, longer gap than the old DashLine default

            calc = self._kinematic_result[f"calc_{key}"]
            first = True
            for t_seg, v_seg in self._split_segments(self._time, calc):
                item = pg.PlotDataItem(x=t_seg, y=v_seg, pen=pen)
                plot.addItem(item)
                if first:
                    self._legends[key].addItem(item, f"Spline Fit (s = {self._last_s})")
                    first = False

    # ── Live goodness-of-fit as each plot's itle ───────────

    def _update_plot_title(self, key: str):
        plot = self._plots[key]
        if self._kinematic_result is None:
            plot.setTitle("No spline fit applied yet.", size="9pt")
            return

        measured = self._measured[key]
        calculated = self._kinematic_result[f"calc_{key}"]
        rmse, mae, max_err, min_err, mean_err = compute_error_metrics(measured, calculated)
        role = "fitted base" if key == self._fitting_base else "derived"

        if np.isnan(rmse):
            plot.setTitle(f"({role}) — no overlapping measured points to compare", size="9pt")
            return

        n_compared = int(np.sum(~np.isnan(measured) & ~np.isnan(calculated)))
        base_order = _ORDER[self._fitting_base]
        missing_ic = [
            name for name in ("position", "speed", "acceleration")
            if _ORDER[name] < base_order
            and nearest_valid_ic(self._time, self._measured[name], self._time_valid[0]) is None
        ]
        note = (
            f" — no measured {', '.join(missing_ic)} to anchor initial "
            f"conditions; result may be offset"
            if missing_ic else ""
        )
        plot.setTitle(
            f"({role})  n={n_compared}   RMSE: {rmse:.4f}   MAE: {mae:.4f}   "
            f"Max: {max_err:.4f}   Min: {min_err:.4f}   Mean: {mean_err:.4f}{note}",
            size="9pt",
        )

    # ── Tab labels ───────────────────────────────────────────────────────

    def _update_tab_labels(self):
        """Append '*' to the tab of whichever kinematic was used as the
        fitting base for the currently applied fit, so the user can tell
        which one it is without opening that tab. No tab is marked until
        the first successful Update Plot click."""
        for i, key in enumerate(KINEMATICS):
            label = _KINEMATIC_LABELS[key]
            is_base = key == self._fitting_base
            self._tabs.setTabText(i, f"{label}*" if is_base else label)
            self._tabs.setTabToolTip(
                i, "Used as the spline fitting base" if is_base else "")

    def _build_export_plot(self, key: str) -> pg.PlotWidget:
        """Render kinematic *key* into a standalone PlotWidget for SVG export.
        Independent of self._plot, so all four kinematics can be captured
        regardless of which one the radio buttons currently show."""
        plot = pg.PlotWidget()
        plot.resize(640, 420)   # never shown/laid out, so give SVGExporter a real size
        plot.setBackground('w')
        plot.setLabel("bottom", "Time (s)")
        plot.setLabel("left", _KINEMATIC_LABELS[key])
        plot.showGrid(x=True, y=True, alpha=0.2)
        legend = plot.addLegend()
    
        if self._show_original_check.isChecked():
            measured = self._measured[key]
            first = True
            for t_seg, v_seg in self._split_segments(self._time, measured):
                item = pg.PlotDataItem(x=t_seg, y=v_seg, pen=pg.mkPen(color='k', width=4))
                plot.addItem(item)
                if first:
                    legend.addItem(item, "Original Data")
                    first = False
    
        if self._show_filtered_check.isChecked() and self._kinematic_result is not None:
            pen = pg.mkPen(color='k', width=4)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 4])
            calc = self._kinematic_result[f"calc_{key}"]
            first = True
            for t_seg, v_seg in self._split_segments(self._time, calc):
                item = pg.PlotDataItem(x=t_seg, y=v_seg, pen=pen)
                plot.addItem(item)
                if first:
                    legend.addItem(item, f"Spline Fit (s = {self._last_s})")
                    first = False
    
        plot.autoRange()
        return plot

    # ── Save: HTML with embedded SVG and pan/zoom JS ───────────────────

    def _on_save_plots(self):
        from utils import default_save_stem, last_dialog_dir, remember_dialog_path
        stem = default_save_stem(
            f"spline_fit_test_e{self._event_id}_v{self._veh_id}",
            self._fitting_base or "unfit",
        )
        default = str(Path(last_dialog_dir()) / f"{stem}.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plots", default,
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return
        remember_dialog_path(path)

        original_index = self._tabs.currentIndex()
        try:
            svg_blocks = []
            for i, key in enumerate(KINEMATICS):
                # A tab that's never been the active page has never
                # received a real resize/layout pass, so its PlotItem's
                # axis geometry and auto-range can be stale or partial.
                # Bring it to front and flush the event loop so Qt
                # actually lays it out before we export it.
                self._tabs.setCurrentIndex(i)
                QApplication.processEvents()
                svg_blocks.append(
                    (self._tabs.tabText(i), export_plot_to_svg(self._plots[key]))
                )

            title = f"Spline Fitting Test — Event {self._event_id}, Vehicle {self._veh_id}"
            if self._kinematic_result is not None:
                k = self._k_combo.currentData()
                title += (
                    f" — Base: {BASE_LABELS[self._fitting_base]}, "
                    f"s = {self._last_s}, k = {k}"
                )
            html = build_svg_html(title, svg_blocks, grid_columns=2, cell_height_px=320)
            Path(path).write_text(html, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to save spline testing plots")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")
        finally:
            self._tabs.setCurrentIndex(original_index)