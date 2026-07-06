"""Right-click "Filter > Spline Fitting" preview window (Figure 4).

Opens for exactly one (event_id, veh_id) trajectory. Lets the user choose
a fitting base (Position/Speed/Acceleration/Jerk) via a combobox, try
different smoothness (s) / degree (k) values, and see the resulting
spline fit overlaid on the original measured data, plus a live
goodness-of-fit readout for whichever kinematic is currently displayed.
Nothing here is ever written to the database — this dialog is parameter
exploration only, matching the SplineReport's "no output to be saved"
instruction for the right-click pathway.

Fitting base is selectable, not fixed, so a single dialog instance covers
all four bases rather than requiring four separate menu entries. Combobox
entries are disabled (with a tooltip) for any base lacking at least
min(allowed_k_options(base)) + 1 valid measured points on this trajectory.
Switching the fitting base discards any existing fit (a fit against one
base's data doesn't carry over to another) and resets the k options to
that base's allowed set.

Synchronous by design: a single trajectory's worth of points fits well
within the time budget of a direct call, so this dialog does not use
BaseAnalysisDialog / BaseAnalysisWorker at all (no progress bar, no
QThread). Re-fitting only happens when the user clicks "Update Plot" —
not on every keystroke in the s/k controls, and not merely from switching
the display-variable radio.

Precondition (enforced by the caller, plot/custom_view_box.py)
----------------------------------------------------------------
The caller is expected to verify, before constructing this dialog, that
the current selection context resolves to exactly one trajectory —
mirroring SplineReport's "Filtering is only available when one trajectory
is selected" rule. This class does not re-check selection context; it
only checks (per fitting base) whether *that one* trajectory has enough
valid data points, closing itself with a message if none of the four
bases are viable at all.

Measured data is read with is_spline_imputed excluded (where that column
exists): this window is about testing fit quality against real
measurements, not against rows synthesized by a previous "fill missing
rows" run.
"""

import logging
from typing import Dict, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QRadioButton, QVBoxLayout,
)

from .spline_comp import (
    BASE_LABELS, KINEMATICS, FITTING_BASES, allowed_k_options,
    apply_spline_fit, compute_error_metrics,
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

        self._fitting_base: str = "position"
        self._time_valid: np.ndarray = np.array([], dtype=np.float64)
        self._base_valid: np.ndarray = np.array([], dtype=np.float64)

        self._kinematic_result: Optional[Dict[str, np.ndarray]] = None
        self._last_s: float = 0.0

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
        # already 0 (the case when "position" is both the default and the
        # first combo entry), so the initial render can't rely on the signal.
        self._on_fitting_base_changed()

        self._size_and_center_dialog()

    # ── Data loading (read-only — never writes anything back) ───────────

    def _load_data(self):
        from comp.base_comp import BaseComp
        cols = BaseComp._events_columns(self._db_connection)
        has_speed = "veh_speed" in cols
        has_accel = "veh_accel" in cols
        has_jerk  = "veh_jerk"  in cols
        has_flag  = "is_spline_imputed" in cols

        select_cols = ["veh_time", "veh_dist_trav"]
        if has_speed:
            select_cols.append("veh_speed")
        if has_accel:
            select_cols.append("veh_accel")
        if has_jerk:
            select_cols.append("veh_jerk")

        where_extra = (
            " AND (is_spline_imputed = 0 OR is_spline_imputed IS NULL)"
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
        body = QHBoxLayout()

        # ── Left: plot ──
        plot_col = QVBoxLayout()
        self._plot = pg.PlotWidget()
        self._plot.setBackground('w')
        self._plot.setLabel("bottom", "Time (s)")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._legend = self._plot.addLegend()
        plot_col.addWidget(self._plot, stretch=1)

        body.addLayout(plot_col, stretch=3)

        # ── Right: controls ──
        control_col = QVBoxLayout()

        param_form = QFormLayout()

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
        self._base_combo.currentIndexChanged.connect(self._on_fitting_base_changed)
        param_form.addRow("Fitting Base:", self._base_combo)

        self._s_spin = QDoubleSpinBox()
        self._s_spin.setRange(0.0, 1_000_000.0)
        self._s_spin.setDecimals(4)
        self._s_spin.setValue(0.0)
        param_form.addRow("Smoothness (s):", self._s_spin)

        self._k_combo = QComboBox()
        param_form.addRow("Degree (k):", self._k_combo)
        control_col.addLayout(param_form)

        display_group = QGroupBox("Display Variable")
        display_form = QVBoxLayout(display_group)
        self._display_buttons = QButtonGroup(self)
        for key in KINEMATICS:
            rb = QRadioButton(BASE_LABELS[key])
            rb.setProperty("kinematic_key", key)
            self._display_buttons.addButton(rb)
            display_form.addWidget(rb)
        self._display_buttons.buttonClicked.connect(self._on_display_variable_changed)
        control_col.addWidget(display_group)

        self._show_original_check = QCheckBox("Show Original Data")
        self._show_original_check.setChecked(True)
        self._show_original_check.stateChanged.connect(self._draw_plot)
        control_col.addWidget(self._show_original_check)

        self._show_filtered_check = QCheckBox("Show Filtered Data")
        self._show_filtered_check.setChecked(True)
        self._show_filtered_check.stateChanged.connect(self._draw_plot)
        control_col.addWidget(self._show_filtered_check)

        self._update_btn = QPushButton("Update Plot")
        self._update_btn.clicked.connect(self._on_update_plot)
        control_col.addWidget(self._update_btn)

        self._fit_status_label = QLabel("No spline fit applied yet.")
        self._fit_status_label.setWordWrap(True)
        control_col.addWidget(self._fit_status_label)

        control_col.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        control_col.addWidget(close_btn)

        body.addLayout(control_col, stretch=1)
        layout.addLayout(body)

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

    # ── Fitting-base combobox ─────────────────────────────────────────────

    def _populate_k_combo(self, base: str):
        self._k_combo.blockSignals(True)
        self._k_combo.clear()
        for k in allowed_k_options(base):
            self._k_combo.addItem(str(k), k)
        self._k_combo.setCurrentIndex(0)
        self._k_combo.blockSignals(False)

    def _on_fitting_base_changed(self, _index=None):
        base = self._base_combo.currentData()
        if base is None:
            return

        self._fitting_base = base
        self._recompute_valid_arrays(base)
        self._kinematic_result = None   # a fit against the old base doesn't apply here
        self._populate_k_combo(base)

        # Display-variable radios follow the new fitting base by default,
        # mirroring the right-click pathway's original "default = fitting_base".
        for btn in self._display_buttons.buttons():
            if btn.property("kinematic_key") == base:
                btn.setChecked(True)
                break

        self._draw_plot(auto_range=True)
        self._update_fit_status_label()

    # ── Display-variable / slider helpers ────────────────────────────────

    def _current_display_key(self) -> str:
        btn = self._display_buttons.checkedButton()
        return btn.property("kinematic_key") if btn else self._fitting_base

    def _on_display_variable_changed(self, _btn):
        self._draw_plot(auto_range=True)
        self._update_fit_status_label()

    # ── Fit / plot ────────────────────────────────────────────────────────

    def _on_update_plot(self):
        s = self._s_spin.value()
        k = self._k_combo.currentData()
        if k is None:
            return

        result = apply_spline_fit(
            self._time_valid, self._base_valid, self._fitting_base,
            s=s, k=k, target_timestamps=self._time,
        )
        if result is None:
            QMessageBox.warning(
                self, "Spline Fitting",
                "Spline fit failed for the current parameters (insufficient "
                "valid points, duplicate timestamps, or an internal error). "
                "Try adjusting s or k.")
            return   # keep previous kinematic_result / plot, per SplineReport

        self._kinematic_result = result
        self._last_s = s
        self._draw_plot(auto_range=True)
        self._update_fit_status_label()

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

    def _draw_plot(self, *_, auto_range: bool = False):
        self._plot.clear()
        self._legend.clear()

        display_key = self._current_display_key()
        self._plot.setLabel("left", _KINEMATIC_LABELS[display_key])

        if self._show_original_check.isChecked():
            measured = self._measured[display_key]
            first = True
            for t_seg, v_seg in self._split_segments(self._time, measured):
                item = pg.PlotDataItem(
                    x=t_seg, y=v_seg,
                    pen=pg.mkPen(color='k', width=4),
                )
                self._plot.addItem(item)
                if first:
                    self._legend.addItem(item, "Original Data")
                    first = False

        if self._show_filtered_check.isChecked() and self._kinematic_result is not None:
            pen = pg.mkPen(color='k', width=4)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 4])   # longer dash, longer gap than the old DashLine default

            calc = self._kinematic_result[f"calc_{display_key}"]
            first = True
            for t_seg, v_seg in self._split_segments(self._time, calc):
                item = pg.PlotDataItem(
                    x=t_seg, y=v_seg,
                    pen=pen,
                )
                self._plot.addItem(item)
                if first:
                    self._legend.addItem(item, f"Spline Fit (s = {self._last_s})")
                    first = False

        if auto_range:
            self._plot.autoRange()

    # ── Live goodness-of-fit readout ─────────────────────────────────────

    def _update_fit_status_label(self):
        display_key = self._current_display_key()
        if self._kinematic_result is None:
            self._fit_status_label.setText("No spline fit applied yet.")
            return

        measured = self._measured[display_key]
        calculated = self._kinematic_result[f"calc_{display_key}"]
        rmse, mae, max_err, min_err, mean_err = compute_error_metrics(measured, calculated)

        label = BASE_LABELS[display_key]
        role = "fitted base" if display_key == self._fitting_base else "derived"

        if np.isnan(rmse):
            self._fit_status_label.setText(
                f"{label} ({role}): no overlapping measured points to compare.")
            return

        n_compared = int(np.sum(~np.isnan(measured) & ~np.isnan(calculated)))
        self._fit_status_label.setText(
            f"{label} ({role}) vs. measured — n={n_compared}\n"
            f"RMSE: {rmse:.4f}   MAE: {mae:.4f}\n"
            f"Max: {max_err:.4f}   Min: {min_err:.4f}   Mean: {mean_err:.4f}"
        )