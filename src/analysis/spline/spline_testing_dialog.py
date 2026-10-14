"""Right-click "Filter > Spline Fitting" preview window (Figure 4).

Opens for exactly one (event_id, veh_id) trajectory. Lets the user choose
a fitting base (Position/Speed/Acceleration/Jerk), try different
smoothness (s) / degree (k) values, and see the resulting spline fit
overlaid on the original measured data on all four kinematic plots at
once, stacked in a KinematicStackedPlotWidget, plus a per-plot
goodness-of-fit readout shown as that plot's title. Nothing here is ever
written to the database -- this dialog is parameter exploration only,
matching the SplineReport's "no output to be saved" instruction for the
right-click pathway.

Fitting base is selectable, not fixed, so a single dialog instance covers
all four bases rather than requiring four separate menu entries. Combobox
entries are disabled (with a tooltip) for any base lacking at least
min(allowed_k_options(base)) + 1 valid measured points on this trajectory.

Base/s/k combo and spin changes have no effect on any plot until "Update
Plot" is clicked -- changing the base combo only refreshes the k-degree
options (which are inherently tied to the base) so a valid choice is
available before the next Update. The plot whose kinematic was the
fitting base of the last successful fit is marked with a leading "*" on
its y-axis label (e.g. "*Speed (m/s)").

Two linked series span all four plots: "Original Data" (solid) and
"Spline Fit (s = ...)" (dashed, a derived value), each in the plot's
kinematic color. Clicking either in any legend shows/hides
it on every plot. Number keys set how many plots are in view (1 shows one
plot at a time, like the former tabs); the time axis is linked, each
plot's y fits the visible time window, and hovering shows the cursor and
per-plot values -- all from KinematicStackedPlotWidget. Plots scrolled out
of view are still laid out, so Save Plots exports all four directly.

Synchronous by design: a single trajectory's worth of points fits well
within the time budget of a direct call, so this dialog does not use
BaseAnalysisDialog / BaseAnalysisWorker at all (no progress bar, no
QThread).

Precondition (enforced by the caller, plot/custom_view_box.py)
----------------------------------------------------------------
The caller is expected to verify, before constructing this dialog, that
the current selection context resolves to exactly one trajectory --
mirroring SplineReport's "Filtering is only available when one trajectory
is selected" rule. This class does not re-check selection context; it
only checks (per fitting base) whether *that one* trajectory has enough
valid data points, closing itself with a message if none of the four
bases are viable at all.

Measured data is read with SplineComp synthetic rows excluded
(utils.measured_rows_clause, the same filter every analysis uses):
this window is about testing fit quality against real
measurements, not against rows synthesized by a previous "fill missing
rows" run.

Save Plots writes an interactive HTML page (ui_kit.plot_export.build_plot_html
with the widget's export_spec()): canvas plots with the same series,
legend toggles, linked time axis, zoom, hover values and fit titles.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout,
)

from ui_kit.plot_export import build_plot_html
from ui_kit.kinematic_stacked_plot_widget import KinematicStackedPlotWidget
from ui_kit.plot_style import Role
from .spline_comp import (
    BASE_LABELS, KINEMATICS, FITTING_BASES, _ORDER, allowed_k_options,
    apply_spline_fit, compute_error_metrics, nearest_valid_ic,
)

logger = logging.getLogger(__name__)

# spline_comp kinematic name -> plot key
_PLOT_KEY: Dict[str, str] = {
    "position":     "dist",
    "speed":        "speed",
    "acceleration": "accel",
    "jerk":         "jerk",
}

_BASE_MARK = "*"
_BASE_MARK_TOOLTIP = "Used as the spline fitting base"

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
        # for the first time. This drives the fit series, the per-plot
        # titles, and the axis-label asterisk; combo/spin changes alone
        # never touch it.
        self._fitting_base: Optional[str] = None
        self._kinematic_result: Optional[Dict[str, np.ndarray]] = None
        self._last_s: float = 1.0
        self._last_k: Optional[int] = None

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
        from ..base_comp import BaseComp
        from utils import measured_rows_clause
        cols = BaseComp._events_columns(self._db_connection)
        has_speed = "veh_speed" in cols
        has_accel = "veh_accel" in cols
        has_jerk  = "veh_jerk"  in cols

        select_cols = ["veh_time", "veh_dist_trav"]
        if has_speed:
            select_cols.append("veh_speed")
        if has_accel:
            select_cols.append("veh_accel")
        if has_jerk:
            select_cols.append("veh_jerk")

        # Same synthetic-row filter as every other analysis (handles the
        # legacy is_spline_imputed column too). "" when neither exists,
        # otherwise it starts with " AND".
        where_extra = measured_rows_clause(cols)
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

        # ── Top: all four kinematics stacked on one linked time axis ──
        # Every plot is kept available even without a measured column: a
        # fit on another base still derives that kinematic.
        from utils import event_time_step
        self._plots = KinematicStackedPlotWidget(
            kinematics=tuple(_PLOT_KEY[k] for k in KINEMATICS), y_fit="visible",
            time_step=event_time_step(self._db_connection, self._event_id))
        self._original = self._plots.add_series(
            "Original Data", Role.MEASURED, linked=True)
        self._fit = self._plots.add_series(
            "Spline Fit", Role.DERIVED, linked=True)
        layout.addWidget(self._plots, stretch=1)

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

        control_bar.addStretch()

        self._update_btn = QPushButton("Update Plot")
        self._update_btn.clicked.connect(self._on_update_plot)
        control_bar.addWidget(self._update_btn)

        # Enabled from the start: the original-data traces are always
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
        self._plots.set_data(
            self._original, self._time,
            {_PLOT_KEY[k]: self._measured[k] for k in KINEMATICS})
        for key in KINEMATICS:
            self._update_plot_title(key)

    def _size_and_center_dialog(self):
        """80% of the main window's width and height, centered over it."""
        top = self.parent().window() if self.parent() else None
        if top is None:
            self.resize(900, 650)
            return
        g = top.frameGeometry()
        self.resize(int(g.width() * 0.8), int(g.height() * 0.8))
        own = self.frameGeometry()
        own.moveCenter(g.center())
        self.move(own.topLeft())

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
            return   # previous applied fit / plots / labels stay exactly as they were

        self._fitting_base = base
        self._kinematic_result = result
        self._last_s = s
        self._last_k = k

        self._plots.set_data(
            self._fit, self._time,
            {_PLOT_KEY[key]: result[f"calc_{key}"] for key in KINEMATICS})
        self._plots.rename_series(self._fit, f"Spline Fit (s = {s:g})")
        for key in KINEMATICS:
            self._update_plot_title(key)
        self._update_base_marker()
        # The view is never moved here -- the user is judging how the new
        # parameters affected the region they're already looking at.

    # ── Live goodness-of-fit as each plot's title ───────────

    def _update_plot_title(self, key: str):
        plot_key = _PLOT_KEY[key]
        if self._kinematic_result is None:
            self._plots.set_title(plot_key, "No spline fit applied yet.")
            return

        measured = self._measured[key]
        calculated = self._kinematic_result[f"calc_{key}"]
        rmse, mae, max_err, min_err, mean_err = compute_error_metrics(measured, calculated)
        role = "fitted base" if key == self._fitting_base else "derived"

        if np.isnan(rmse):
            self._plots.set_title(
                plot_key, f"({role}) — no overlapping measured points to compare")
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
        self._plots.set_title(
            plot_key,
            f"({role})  n={n_compared}   RMSE: {rmse:.4f}   MAE: {mae:.4f}   "
            f"Max: {max_err:.4f}   Min: {min_err:.4f}   Mean: {mean_err:.4f}{note}",
        )

    # ── Fitting-base marker ──────────────────────────────────────────────

    def _update_base_marker(self):
        """Mark the y-axis label of whichever kinematic was the fitting
        base for the currently applied fit, e.g. "*Speed (m/s)". No plot is
        marked until the first successful Update Plot click."""
        for key in KINEMATICS:
            is_base = key == self._fitting_base
            self._plots.set_label_prefix(
                _PLOT_KEY[key],
                _BASE_MARK if is_base else "",
                _BASE_MARK_TOOLTIP if is_base else "")

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

        try:
            title = f"Spline Fitting Test — Event {self._event_id}, Vehicle {self._veh_id}"
            if self._kinematic_result is not None:
                title += (
                    f" — Base: {BASE_LABELS[self._fitting_base]}, "
                    f"s = {self._last_s}, k = {self._last_k}"
                )
            html = build_plot_html(title, self._plots.export_spec(),
                                   columns=1, cell_height_px=320)
            Path(path).write_text(html, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to save spline testing plots")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")
