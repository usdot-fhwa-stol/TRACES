"""Qt half of the Lane-Level Anomaly extension point (see anomaly_registry).

AnomalyTypeUI supplies, for one AnomalyType:

    create_config_row()  its row in Select Anomalies to Identify (Figure 2):
                         a checkbox, plus a right-justified parameter
                         spinbox and unit when the type has one (ConfigRow)
    BUTTON_TEXT          its button in the review panel (Figure 3)
    count_text()         its "N detected (M fixed)" line in the review panel
    title(), vehicle_label()
                         text AnomalyPlot shows for one of its anomalies
    create_fix_panel()   its fix panel (a FixPanel): the controls that
                         build its fix, and the applied fix's description
                         once fixed. Default: VehicleLanesFixPanel

Apply and Undo are the review panel's: Apply passes the panel's
pending_fix() to AnomalyType.apply_fix, Undo calls AnomalyType.undo_fix.

AnomalyPlot
-----------
The review panel's time-space diagram, a scatter plot like the main
TimeSpacePlotWidget:

    color    which vehicle -- the anomaly's first vehicle amber, the second
             green (the Trajectory Stitching preview's Traj 1 / Traj 2
             colors), other traffic grey; the second vehicle is drawn on
             top of the first
    symbol   which lane, by the lane's position in the event's sorted lane
             list: circle, square, triangle, cross, diamond, upside-down
             triangle, x -- filled -- then circle, square, triangle,
             diamond, upside-down triangle outlined (cross and x have no
             outlined form), then the cycle repeats (lane_symbol)

The legend (top left), clicking an entry hides or shows its points:

    Veh N              one per anomaly vehicle, a short line in its color:
                       all of that vehicle's points, in every lane
    Lane N             one per lane of the event, in event order, its
                       symbol in grey: the other traffic in that lane
                       only -- the anomaly's vehicles stay visible

Every entry starts on (its points visible) each time an anomaly is shown.
Hovering a point shows vehicle, time, lane and distance. The anomaly span is shaded light red (green once
fixed); the plot opens framed on it and pans and zooms with the mouse.

Other traffic (traffic_lanes, loaded by the review panel through
lane_correction.anomaly_traffic) is every lane of the event -- which
includes every lane the fix panel can move a vehicle to
(AnomalyType.fix_target_lanes) and the anomaly's own lanes -- within
anomaly_window, at any distance: time is the only bound, so each lane in
the legend has points whenever it carries traffic in that window. Every
lane a fix can choose is therefore drawn from the start; choosing one
changes nothing but the preview.

A fix panel's preview (Preview) shades the segment it would change yellow
and rings each sample that would move with its new lane's symbol in the
proposal color (magenta), outlined. SegmentFixPanel also warns when the
fix would put a moved vehicle closer than the proximity threshold to
another vehicle in its new lane (proximity.spacing_conflicts).
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from utils import event_time_step
from ui_kit.plot_style import PROPOSAL_COLOR, apply_plot_style, region_brush
from .anomaly_registry import anomaly_type
from .lane_anomaly_store import LaneAnomaly
from .lane_correction import LaneMove, event_lanes, move_times
from .proximity import proximity_threshold, spacing_conflicts

# Vehicle colors: the first two are the Trajectory Stitching preview's
# Traj 1 (amber) and Traj 2 (green); the rest (Okabe-Ito) serve a future
# anomaly type with more vehicles.
VEHICLE_COLORS = ("#E69F00", "#009E73", "#56B4E9", "#CC79A7", "#0072B2", "#D55E00")
VEHICLE_COLOR_NAMES = ("amber", "green", "sky blue", "pink", "blue", "vermillion")
TRAFFIC_COLOR = "#A9A9A9"
WARNING_COLOR = "#B00020"   # fix panel's spacing-conflict warning text

# Lane symbols (pyqtgraph names): 't1' points up, 't' points down.
LANE_SYMBOLS = ("o", "s", "t1", "+", "d", "t", "x")
_OUTLINABLE = ("o", "s", "t1", "d", "t")      # cross and x have no outlined form
SYMBOL_NAMES = {"o": "circle", "s": "square", "t1": "triangle", "+": "cross",
                "d": "diamond", "t": "upside-down triangle", "x": "x"}

VEHICLE_SIZE = 10
TRAFFIC_SIZE = 6
PREVIEW_SIZE = 15
_OUTLINE_WIDTH = 1.5
_LEGEND_LINE_WIDTH = 4

# Shading of the selected anomaly's span, and of the segment a fix changes.
SPAN_REGION = "unfilled"   # light red -- "needs attention" in plot_style
FIXED_REGION = "manual"    # green -- a fix is in effect
SELECT_REGION = "auto"     # yellow -- the segment a pending fix changes

_MIN_PAD_S = 3.0


def lane_symbol(index: int) -> Tuple[str, bool]:
    """(pyqtgraph symbol, filled) for the lane at *index* in the event's
    sorted lane list: the seven symbols filled, then the five that have an
    outline outlined, then around again."""
    i = index % (len(LANE_SYMBOLS) + len(_OUTLINABLE))
    if i < len(LANE_SYMBOLS):
        return LANE_SYMBOLS[i], True
    return _OUTLINABLE[i - len(LANE_SYMBOLS)], False


def anomaly_window(anomaly: LaneAnomaly) -> Tuple[float, float]:
    """Time span of data loaded around an anomaly (room to pan out)."""
    pad = max(_MIN_PAD_S, anomaly.duration)
    return anomaly.start_time - pad, anomaly.end_time + pad


def traffic_lanes(conn: sqlite3.Connection, anomaly: LaneAnomaly,
                  rows: Dict[int, Dict[str, np.ndarray]]) -> List[int]:
    """Lanes whose other traffic the review panel draws for *anomaly*:
    every lane of the event, plus any lane its fix can move a vehicle to
    (fix_target_lanes), its own lanes and the lanes its vehicles use in
    *rows* (normally all already among the event's)."""
    lanes = {int(l) for l in event_lanes(conn, anomaly.event_id)}
    lanes |= {int(l) for l in anomaly_type(anomaly.anomaly_type).fix_target_lanes(conn, anomaly)}
    lanes |= {int(l) for l in anomaly.lane_ids}
    for r in rows.values():
        lanes |= {int(l) for l in r["lane"] if l is not None}
    return sorted(lanes)


def _lanes_float(lanes: np.ndarray) -> np.ndarray:
    return np.array([np.nan if l is None else float(l) for l in lanes], dtype=np.float64)


@dataclass
class Preview:
    """What a pending fix would change: the segment, and per vehicle the
    samples that would move and their new lane."""
    segment: Optional[Tuple[float, float]] = None
    moves: List[Tuple[int, np.ndarray, np.ndarray, int]] = field(default_factory=list)
    # (veh_id, times, distances, to_lane)


class ConfigRow(QWidget):
    """One Figure 2 row: [x] Label ............ Param label [spin] unit"""

    def __init__(self, key: str, checkbox_text: str,
                 param_key: Optional[str] = None, param_label: str = "",
                 unit: str = "", spin: Optional[QAbstractSpinBox] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.key = key
        self._param_key = param_key
        self._spin = spin
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.checkbox = QCheckBox(checkbox_text)
        row.addWidget(self.checkbox)
        row.addStretch(1)
        if spin is not None:
            label = QLabel(param_label)
            label.setBuddy(spin)
            row.addWidget(label)
            row.addWidget(spin)
            row.addWidget(QLabel(unit))
            self.checkbox.toggled.connect(spin.setEnabled)
            self.checkbox.toggled.connect(label.setEnabled)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def options(self) -> Dict[str, float]:
        if self._spin is None or self._param_key is None:
            return {}
        return {self._param_key: self._spin.value()}

    def set_controls_enabled(self, enabled: bool) -> None:
        self.checkbox.setEnabled(enabled)
        if self._spin is not None:
            self._spin.setEnabled(enabled and self.checkbox.isChecked())


# ── Plot ────────────────────────────────────────────────────────────────────

class AnomalyPlot(pg.PlotWidget):
    """Scatter time-space diagram of one anomaly (module docstring)."""

    def __init__(self, conn: sqlite3.Connection, event_id: int,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._conn = conn
        self._event_id = event_id
        self._lane_order = {int(l): i for i, l in enumerate(event_lanes(conn, event_id))}
        self._anomaly: Optional[LaneAnomaly] = None
        self._ui: Optional["AnomalyTypeUI"] = None
        self._rows: Dict[int, Dict[str, np.ndarray]] = {}
        self._traffic: Dict[int, Dict[str, np.ndarray]] = {}
        self._items: List[pg.GraphicsObject] = []
        self._points: Dict[Tuple[int, str], pg.ScatterPlotItem] = {}   # (lane, group)
        self._lane_proxies: Dict[int, pg.ScatterPlotItem] = {}         # lane's traffic
        self._vehicle_proxies: Dict[str, pg.PlotDataItem] = {}         # "v0", "v1", ...

        apply_plot_style(self)
        self.setLabel("left", "Distance traveled", units="m")
        self.setLabel("bottom", "Time", units="s")
        self._legend = self.addLegend(offset=(10, 10), labelTextColor="k",
                                      brush=pg.mkBrush(255, 255, 255, 210))

        self._span = pg.LinearRegionItem(movable=False, pen=pg.mkPen(None))
        self._span.setZValue(-10)
        self.addItem(self._span, ignoreBounds=True)
        self._selection = pg.LinearRegionItem(movable=False, pen=pg.mkPen(None),
                                              brush=region_brush(SELECT_REGION))
        self._selection.setZValue(-5)
        self.addItem(self._selection, ignoreBounds=True)
        self._selection.hide()
        self._preview = pg.ScatterPlotItem(size=PREVIEW_SIZE, brush=pg.mkBrush(None),
                                           pen=pg.mkPen(PROPOSAL_COLOR, width=2))
        self._preview.setZValue(10)
        self.addItem(self._preview)

    # ── Public ────────────────────────────────────────────────────────

    def lane_symbol_for(self, lane: int) -> Tuple[str, bool]:
        return lane_symbol(self._lane_order.get(int(lane), int(lane)))

    def show_anomaly(self, anomaly: Optional[LaneAnomaly], ui: Optional["AnomalyTypeUI"],
                     rows: Dict[int, Dict[str, np.ndarray]],
                     traffic: Optional[Dict[int, Dict[str, np.ndarray]]] = None) -> None:
        """Draw *anomaly*'s vehicles (*rows*, loaded over anomaly_window)
        and the other *traffic* around them (module docstring), framed on
        its span. None clears."""
        self._anomaly, self._ui, self._rows = anomaly, ui, rows
        self._traffic = (traffic or {}) if anomaly is not None else {}
        self._clear_preview_items()
        if anomaly is None:
            self._rebuild()
            self.setTitle("No anomaly selected.")
            self._span.hide()
            return
        self._rebuild()
        self.setTitle(ui.title(anomaly))
        self._span.setBrush(region_brush(FIXED_REGION if anomaly.is_fixed else SPAN_REGION))
        self._span.setRegion((anomaly.start_time, anomaly.end_time))
        self._span.show()
        self._frame()

    def set_preview(self, preview: Optional[Preview]) -> None:
        """Show (or with None / an empty Preview, clear) a pending fix."""
        if preview is None or (preview.segment is None and not preview.moves):
            self._clear_preview_items()
            return
        # Every lane a fix can choose is already drawn (module docstring),
        # so the plot isn't rebuilt and legend toggles are kept.
        if preview.segment is not None:
            self._selection.setRegion(preview.segment)
            self._selection.show()
        else:
            self._selection.hide()
        spots = []
        for veh, t, d, lane in preview.moves:
            symbol, _ = self.lane_symbol_for(lane)
            spots += [{"pos": (float(x), float(y)), "symbol": symbol} for x, y in zip(t, d)]
        self._preview.setData(spots)

    # ── Drawing ───────────────────────────────────────────────────────

    def _clear_preview_items(self) -> None:
        self._selection.hide()
        self._preview.setData([])

    def _rebuild(self) -> None:
        for item in self._items:
            self.removeItem(item)
        self._items, self._points = [], {}
        self._lane_proxies, self._vehicle_proxies = {}, {}
        self._legend.clear()
        if self._anomaly is None:
            return

        # Group per (lane, who): "traffic", then "v0", "v1", ... drawn in
        # that order so the anomaly's second vehicle ends up on top. Other
        # traffic has no legend entry of its own: its lanes' entries.
        groups: List[Tuple[str, str, str, int, Dict[int, Dict[str, np.ndarray]]]] = []
        if self._traffic:
            groups.append(("traffic", "", TRAFFIC_COLOR, TRAFFIC_SIZE, self._traffic))
        for i, (veh, rows) in enumerate(self._rows.items()):
            groups.append((f"v{i}", self._ui.vehicle_label(self._anomaly, veh, i),
                           VEHICLE_COLORS[i % len(VEHICLE_COLORS)], VEHICLE_SIZE,
                           {veh: rows}))

        lanes_shown = set()
        for z, (key, _label, color, size, data) in enumerate(groups):
            per_lane: Dict[int, List[Tuple[np.ndarray, np.ndarray, int]]] = {}
            for veh, rows in data.items():
                lanes = _lanes_float(rows["lane"])
                for lane in np.unique(lanes[np.isfinite(lanes)]).astype(int):
                    mask = lanes == lane
                    per_lane.setdefault(int(lane), []).append(
                        (rows["time"][mask], rows["dist"][mask], int(veh)))
            for lane, parts in per_lane.items():
                lanes_shown.add(lane)
                t =np.concatenate([p[0] for p in parts])
                d = np.concatenate([p[1] for p in parts])
                vehs = np.concatenate([np.full(p[0].size, p[2]) for p in parts]).tolist()
                item = self._scatter(lane, color, size, t, d, vehs)
                item.setZValue(z)
                self._add(item)
                self._points[(lane, key)] = item

        # Legend: the vehicles, each a plain line in its color (symbols
        # are kept for lanes) ...
        for key, label, color, _size, _data in groups:
            if key == "traffic":
                continue
            proxy = pg.PlotDataItem(pen=pg.mkPen(color, width=_LEGEND_LINE_WIDTH))
            self._add_proxy(proxy, label)
            self._vehicle_proxies[key] = proxy
        # ... then one entry per lane of the event (and any other lane
        # drawn), in event order, drawn as its traffic is: toggles that
        # lane's other traffic only.
        legend_lanes = set(self._lane_order) | lanes_shown
        for lane in sorted(legend_lanes, key=lambda l: self._lane_order.get(l, l)):
            symbol, filled = self.lane_symbol_for(lane)
            proxy = pg.ScatterPlotItem(
                symbol=symbol, size=VEHICLE_SIZE,
                pen=pg.mkPen(TRAFFIC_COLOR, width=_OUTLINE_WIDTH),
                brush=pg.mkBrush(TRAFFIC_COLOR) if filled else pg.mkBrush(None))
            self._add_proxy(proxy, f"Lane {lane}")
            self._lane_proxies[lane] = proxy
        self._sync_visibility()   # every entry was just made visible

    def _scatter(self, lane: int, color: str, size: int, t: np.ndarray, d: np.ndarray,
                 vehs: list) -> pg.ScatterPlotItem:
        symbol, filled = self.lane_symbol_for(lane)
        if filled:
            pen, brush = pg.mkPen(color), pg.mkBrush(color)
        else:
            pen, brush = pg.mkPen(color, width=_OUTLINE_WIDTH), pg.mkBrush(None)

        def tip(x, y, data, _lane=lane):
            return (f"Vehicle ID: {data}\nTime: {x:.2f} s\nLane ID: {_lane}\n"
                    f"Distance: {y:.2f} m")

        return pg.ScatterPlotItem(x=t, y=d, data=vehs, symbol=symbol, size=size,
                                  pen=pen, brush=brush, hoverable=True, tip=tip)

    def _add(self, item) -> None:
        self.addItem(item)
        self._items.append(item)

    def _add_proxy(self, proxy: pg.GraphicsObject, label: str) -> None:
        """A dataless item standing for a set of points in the legend;
        clicking its legend entry toggles it, and _sync_visibility follows.
        Starts visible, so its points do too."""
        proxy.setVisible(True)
        self._add(proxy)
        self._legend.addItem(proxy, label)
        proxy.visibleChanged.connect(self._sync_visibility)

    def _sync_visibility(self) -> None:
        """A vehicle's points follow its own entry; other traffic in a
        lane follows that lane's entry."""
        for (lane, key), item in self._points.items():
            proxy = (self._lane_proxies.get(lane) if key == "traffic"
                     else self._vehicle_proxies.get(key))
            item.setVisible(proxy is None or proxy.isVisible())

    def _frame(self) -> None:
        """x: the anomaly span with a margin; y: the anomaly vehicles'
        distances in that span (traffic further away is clipped -- pan or
        zoom out to see it)."""
        a = self._anomaly
        margin = max(1.0, 0.1 * a.duration)
        x0, x1 = a.start_time - margin, a.end_time + margin
        self.setXRange(x0, x1, padding=0)
        dists = [r["dist"][(r["time"] >= x0) & (r["time"] <= x1) & np.isfinite(r["dist"])]
                 for r in self._rows.values()]
        dists = np.concatenate(dists) if dists else np.array([])
        if dists.size:
            lo, hi = float(dists.min()), float(dists.max())
            pad = max(10.0, 0.15 * (hi - lo))
            self.setYRange(lo - pad, hi + pad, padding=0)


# ── Fix panels ──────────────────────────────────────────────────────────────

class FixPanel(QGroupBox):
    """The review panel's fix controls for one anomaly type: what its fix
    would be, and, for a fixed anomaly, the fix applied. The panel has no
    Apply button of its own -- the review panel's Apply passes
    pending_fix() to the type's AnomalyType.apply_fix, as its Undo calls
    undo_fix. Emits preview_changed (a Preview, or None) and
    pending_changed (whether there is a fix to apply) as the user's
    choices change."""

    preview_changed = pyqtSignal(object)
    pending_changed = pyqtSignal(bool)

    def __init__(self, ui: "AnomalyTypeUI", conn: sqlite3.Connection, event_id: int,
                 parent: Optional[QWidget] = None):
        super().__init__("Fix", parent)
        self._ui = ui
        self._type = ui.type
        self._conn = conn
        self._event_id = event_id
        self._anomaly: Optional[LaneAnomaly] = None
        self._rows: Dict[int, Dict[str, np.ndarray]] = {}
        self._traffic: Dict[int, Dict[str, np.ndarray]] = {}
        self._threshold: Tuple[float, bool] = proximity_threshold(conn, event_id)
        self._time_step = event_time_step(conn, event_id)

    def set_traffic(self, traffic: Dict[int, Dict[str, np.ndarray]]) -> None:
        """The other traffic the plot draws for the next anomaly shown
        (lane_correction.lane_traffic's form, the anomaly's vehicles left
        out). The review panel calls this just before show_anomaly."""
        self._traffic = traffic or {}

    def show_anomaly(self, anomaly: Optional[LaneAnomaly],
                     rows: Dict[int, Dict[str, np.ndarray]]) -> None:
        """Show *anomaly*; any pending (unapplied) choices are discarded."""
        self._anomaly, self._rows = anomaly, rows

    def conflicts(self, preview: "Preview") -> list:
        """proximity.SpacingConflicts the fix *preview* shows would create:
        moved samples closer than the proximity threshold to another
        vehicle in their new lane -- the traffic set by set_traffic, or
        another of the anomaly's vehicles as the fix would leave it."""
        if not preview.moves:
            return []
        moved = {int(v): (t, l) for v, t, _d, l in preview.moves}
        occupants = dict(self._traffic)
        for veh, r in self._rows.items():
            lanes = r["lane"]
            if int(veh) in moved:
                times, to_lane = moved[int(veh)]
                lanes = lanes.copy()
                lanes[np.isin(r["time"], times)] = int(to_lane)
            occupants[veh] = {"time": r["time"], "dist": r["dist"], "lane": lanes}
        return spacing_conflicts(preview.moves, occupants, self._threshold[0],
                                 self._time_step)

    def conflict_text(self, conflicts: list) -> str:
        """User-facing warning for *conflicts*, "" when there are none."""
        if not conflicts:
            return ""
        threshold, detected = self._threshold
        source = ("the threshold of the last Proximity detection" if detected
                  else "the default Proximity threshold")
        lines = [f"Warning: this fix would bring vehicles closer than {threshold:g} m "
                 f"({source}):"]
        for c in conflicts:
            lines.append(f"Vehicle {c.veh_id} would be {c.spacing_m:.1f} m from vehicle "
                         f"{c.other_veh_id} in lane {c.lane_id} at {c.time:.2f} s "
                         f"({c.samples} sample(s) too close).")
        return "\n".join(lines)

    def has_pending_fix(self) -> bool:
        """Whether the current choices describe a fix to apply (cheap: no
        database read; drives the review panel's Apply button)."""
        return False

    def pending_fix(self):
        """The fix the current choices describe, in the form the type's
        apply_fix takes, or None when there is nothing to apply."""
        return None


class SegmentFixPanel(FixPanel):
    """Base of the built-in fix panels: a fix made of LaneMoves over one
    time segment. Layout, top to bottom: the applied fix's text (fixed
    anomalies only), then the editor -- Start / End spinboxes snapped to
    the vehicles' samples, _build_choices(), a summary line and, when the
    fix would create spacing conflicts, a warning (conflict_text). The
    warning doesn't block Apply. Subclasses implement _build_choices,
    _load_choices and _moves."""

    def __init__(self, ui, conn, event_id, parent=None):
        super().__init__(ui, conn, event_id, parent)
        self._step = event_time_step(conn, event_id)
        self._seg: Tuple[float, float] = (0.0, 0.0)   # exact sample times
        self._samples = np.array([], dtype=np.float64)
        self._loading = False
        self._has_pending = False

        layout = QVBoxLayout(self)
        self._applied = QLabel()
        self._applied.setWordWrap(True)
        layout.addWidget(self._applied)

        self._editor = QWidget()
        editor = QVBoxLayout(self._editor)
        editor.setContentsMargins(0, 0, 0, 0)
        segment = QFormLayout()
        self._start_spin = self._time_spin()
        self._end_spin = self._time_spin()
        segment.addRow("Start:", self._start_spin)
        segment.addRow("End:", self._end_spin)
        editor.addLayout(segment)
        self._build_choices(editor)
        self._info = QLabel()
        self._info.setWordWrap(True)
        editor.addWidget(self._info)
        self._warning = QLabel()
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet(f"color: {WARNING_COLOR};")
        self._warning.hide()
        editor.addWidget(self._warning)

        layout.addWidget(self._editor)
        layout.addStretch(1)
        self._start_spin.valueChanged.connect(self._on_segment_edited)
        self._end_spin.valueChanged.connect(self._on_segment_edited)

    # ── Hooks ─────────────────────────────────────────────────────────

    def _build_choices(self, layout: QVBoxLayout) -> None:
        raise NotImplementedError

    def _load_choices(self, anomaly: LaneAnomaly) -> None:
        """Reset the choices for *anomaly* (signals of the choice widgets
        may fire; _changed ignores them while loading)."""
        raise NotImplementedError

    def _moves(self) -> List[LaneMove]:
        """The fix the current choices describe."""
        raise NotImplementedError

    # ── Showing an anomaly ────────────────────────────────────────────

    def show_anomaly(self, anomaly, rows) -> None:
        super().show_anomaly(anomaly, rows)
        self.setEnabled(anomaly is not None)
        if anomaly is None or anomaly.is_fixed:
            if anomaly is not None:
                self._applied.setText(self._type.describe_fix(self._conn, anomaly)
                                      or "A fix is in effect.")
            self._applied.setVisible(anomaly is not None)
            self._editor.hide()
            self._set_pending(False)
            self.preview_changed.emit(None)
            return
        self._applied.hide()
        self._editor.show()
        self._loading = True
        try:
            vehicles = [v for v in self._type.fix_vehicles(anomaly) if v in rows]
            times = [rows[v]["time"] for v in vehicles if rows[v]["time"].size]
            self._samples = np.unique(np.concatenate(times)) if times else np.array([])
            lo, hi = anomaly_window(anomaly)
            for spin in (self._start_spin, self._end_spin):
                spin.blockSignals(True)
                spin.setRange(lo, hi)
                spin.blockSignals(False)
            self._set_segment(anomaly.start_time, anomaly.end_time)
            self._load_choices(anomaly)
        finally:
            self._loading = False
        self._changed()

    # ── Segment ───────────────────────────────────────────────────────

    def _time_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(max(2, int(np.ceil(-np.log10(self._step))) + 1) if self._step > 0 else 2)
        spin.setSingleStep(self._step if self._step > 0 else 0.1)
        spin.setSuffix(" s")
        spin.setKeyboardTracking(False)
        return spin

    def _snap(self, value: float) -> float:
        s = self._samples
        return float(s[int(np.argmin(np.abs(s - value)))]) if s.size else float(value)

    def _set_segment(self, lo: float, hi: float) -> None:
        self._seg = (self._snap(lo), self._snap(hi))
        for spin, value in zip((self._start_spin, self._end_spin), self._seg):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_segment_edited(self, *_args) -> None:
        self._set_segment(self._start_spin.value(), self._end_spin.value())
        self._changed()

    # ── Pending fix ───────────────────────────────────────────────────

    def _set_pending(self, pending: bool) -> None:
        self._has_pending = pending
        self.pending_changed.emit(pending)

    def _changed(self, *_args) -> None:
        """Recompute the preview and summary after any change."""
        if self._loading or self._anomaly is None or self._anomaly.is_fixed:
            return
        lo, hi = self._seg
        if hi < lo:
            self._info.setText("Start must be before End.")
            self._show_warning("")
            self._set_pending(False)
            self.preview_changed.emit(Preview())
            return
        preview = Preview(segment=(lo, hi))
        lines = []
        for m in self._moves():
            rows = self._rows.get(m.veh_id)
            if rows is None:
                continue
            move = np.array([(lo <= t <= hi) and (l != m.to_lane)
                             for t, l in zip(rows["time"], rows["lane"])], dtype=bool)
            if move.any():
                preview.moves.append((m.veh_id, rows["time"][move], rows["dist"][move],
                                      m.to_lane))
                lines.append(f"Vehicle {m.veh_id}: {int(move.sum())} sample(s) to "
                             f"lane {m.to_lane}.")
            else:
                lines.append(f"Vehicle {m.veh_id} is already in lane {m.to_lane} "
                             f"for the whole segment.")
        self._info.setText("\n".join(lines) or "Choose a lane to move a vehicle to.")
        self._show_warning(self.conflict_text(self.conflicts(preview)))
        self._set_pending(bool(preview.moves))
        self.preview_changed.emit(preview)

    def _show_warning(self, text: str) -> None:
        self._warning.setText(text)
        self._warning.setVisible(bool(text))

    def has_pending_fix(self) -> bool:
        return self._has_pending

    def pending_fix(self) -> Optional[List[LaneMove]]:
        """The moves that change something, checked against the database
        again (the preview was built from rows read when the anomaly was
        shown); None when there are none."""
        a = self._anomaly
        if a is None or a.is_fixed or not self._has_pending:
            return None
        lo, hi = self._seg
        moves = [LaneMove(m.veh_id, lo, hi, m.to_lane) for m in self._moves()]
        moves = [m for m in moves if move_times(self._conn, a.event_id, m).size]
        return moves or None


class VehicleLanesFixPanel(SegmentFixPanel):
    """One row per vehicle the type lets the user move (fix_vehicles), each
    with a "Move to" combo: Keep (default) or any target lane."""

    KEEP = "Keep"

    def _build_choices(self, layout: QVBoxLayout) -> None:
        self._rows_form = QFormLayout()
        layout.addLayout(self._rows_form)
        self._combos: Dict[int, QComboBox] = {}

    def _load_choices(self, anomaly: LaneAnomaly) -> None:
        while self._rows_form.rowCount():
            self._rows_form.removeRow(0)
        self._combos = {}
        targets = self._type.fix_target_lanes(self._conn, anomaly)
        for i, veh in enumerate(self._type.fix_vehicles(anomaly)):
            combo = QComboBox()
            combo.addItem(self.KEEP, None)
            for lane in targets:
                combo.addItem(f"Lane {lane}", int(lane))
            default = self._type.default_target_lane(self._conn, anomaly, veh)
            combo.setCurrentIndex(max(combo.findData(default), 0) if default is not None else 0)
            combo.currentIndexChanged.connect(self._changed)
            color = VEHICLE_COLOR_NAMES[i % len(VEHICLE_COLOR_NAMES)]
            self._rows_form.addRow(f"Vehicle {veh} ({color}):", combo)
            self._combos[veh] = combo

    def _moves(self) -> List[LaneMove]:
        lo, hi = self._seg
        return [LaneMove(veh, lo, hi, int(c.currentData()))
                for veh, c in self._combos.items() if c.currentData() is not None]


# ── Type UI ─────────────────────────────────────────────────────────────────

class AnomalyTypeUI:
    """Qt side of one anomaly type. Subclasses set BUTTON_TEXT and implement
    create_config_row(); the rest has working defaults."""

    BUTTON_TEXT = ""

    def __init__(self, type_key: str):
        self._type_key = type_key

    @property
    def type(self):
        return anomaly_type(self._type_key)

    def count_text(self, n: int, n_fixed: int = 0) -> str:
        return f"{self.type.LABEL}: {n} detected ({n_fixed} fixed)"

    def create_config_row(self, parent: QWidget) -> ConfigRow:
        raise NotImplementedError

    def create_fix_panel(self, parent: QWidget, conn, event_id: int) -> FixPanel:
        return VehicleLanesFixPanel(self, conn, event_id, parent)

    def title(self, anomaly: LaneAnomaly) -> str:
        lanes = " and ".join(str(l) for l in anomaly.lane_ids)
        return (f"Anomaly #{anomaly.anomaly_id} -- lane {lanes}, "
                f"{anomaly.start_time:.1f}–{anomaly.end_time:.1f} s"
                + (" -- fixed" if anomaly.is_fixed else ""))

    def vehicle_label(self, anomaly: LaneAnomaly, veh_id: int, index: int) -> str:
        return f"Veh {veh_id}"