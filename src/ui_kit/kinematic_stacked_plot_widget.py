"""Kinematic plots (distance / speed / acceleration / jerk) stacked
vertically on one linked time axis.

Lives in ui_kit; deprecates plot/kinematic_plot_stack.py. Dialogs register 
*series* with the widget instead of drawing curves themselves, so pens, 
z-order, legends and cross-plot visibility are handled in one place.

Plots
-----
Any ordered subset of KINEMATICS, e.g. ("dist", "speed", "accel") for
Trajectory Stitching or ("speed", "accel", "jerk") for Internal
Consistency. A plot whose column is absent from the dataset is still
created (so the layout doesn't jump) but is titled "not available" and
receives no curves.

Every plot shows its own time axis and label: when only some plots are in
view (scrolling, Add Points), the plots on screen must still be readable on
their own. Left axes share a fixed width so the time axes line up
vertically across the stack.

Time (x) is linked across all plots. Each plot's y-axis is independent and
auto-ranges either to the data in the visible time window (y_fit="visible")
or to all of its data (y_fit="all"). Mouse interaction is left at
pyqtgraph's defaults, so the wheel over an axis zooms only that axis. The
corner "A" button on any plot resets every plot.

Series and legends
------------------
add_series() creates one curve per target plot, styled by its plot_style
Role. A plot shows a legend only when it has two or more legend series.
Clicking a legend entry hides/shows that curve (pyqtgraph >= 0.12.2); for a
series created with linked=True the change is copied to the same series on
every other plot, while linked=False series toggle per plot. Markers
(vertical lines, regions, labels) never appear in a legend.

Colors follow plot_style's design language without the dialog naming
them: each plot draws its series in its kinematic color, proposals in
purple, and outline=True marks a second object of the same kind.
Lines break at time gaps (utils.gap_indices) as well as at NaNs.

Hover
-----
With hover_values=True (default), moving the mouse over any plot's data
area shows a thin vertical cursor at that time on every plot. When the
mouse is within a few pixels of a visible legend series, every plot also
shows a small label next to its own curves listing the time and that
plot's visible series values at that instant. The labels ignore the mouse,
so they never block clicks, legend toggles or point editing. set_cursor()
places the cursor from code (e.g. a table row click).

Typical use
-----------
    w = KinematicStackedPlotWidget(kinematics=("dist", "speed", "accel"),
                                   available={"accel": has_accel})
    s = w.add_series("Traj 1", Role.MEASURED, linked=False)   # plot colors
    w.set_data(s, time, {"dist": d, "speed": v, "accel": a})
    w.add_vlines([t0, t1], Role.BOUNDARY)
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractSlider, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from pyqtgraph.graphicsItems.LegendItem import ItemSample

from .plot_export import curve_item, pen_spec, plot_spec, vline_item
from .plot_style import (
    OUTLINE_COLOR, ROLE_STYLES, TITLE_SIZE, Role, apply_plot_style,
    outline_pen, resolve_color, role_in_legend, role_pen, role_z,
)
from utils import estimate_time_step, gap_indices


@dataclass(frozen=True)
class KinematicSpec:
    label: str
    unit: str
    column: str


KINEMATICS: Dict[str, KinematicSpec] = {
    "dist":  KinematicSpec("Distance traveled", "m",    "veh_dist_trav"),
    "speed": KinematicSpec("Speed",             "m/s",  "veh_speed"),
    "accel": KinematicSpec("Acceleration",      "m/s²", "veh_accel"),
    "jerk":  KinematicSpec("Jerk",              "m/s³", "veh_jerk"),
}

_LEGEND_TOOLTIP = "Click an entry to show or hide that series."
_SCROLL_TOOLTIP = ("Keys 1–9: number of plots in view, 0: all.\n"
                   "Page Up / Page Down, or Shift+wheel over a plot: scroll one plot.")
_QWIDGETSIZE_MAX = 16777215
_SNAP_DELAY_MS = 150
_HOVER_PIXEL_TOL = 12.0          # cursor-to-curve distance that shows values
_HOVER_LABEL_STYLE = (
    "QLabel {"
    "  background-color: #ffffe1;"
    "  border: 1px solid #767676;"
    "  padding: 2px 5px;"
    "  font-size: 11px;"
    "  color: #000000;"
    "}"
)


class _OutlineSample(ItemSample):
    """Legend sample that also draws a curve's shadowPen (its outline)
    under the line, so an outlined series is recognisable in the legend.
    The default ItemSample draws only the main pen."""

    def paint(self, p, *args):
        opts = getattr(self.item, "opts", None)
        shadow = opts.get("shadowPen") if isinstance(opts, dict) else None
        if shadow is not None and self.item.isVisible():
            p.setPen(pg.mkPen(shadow))
            p.drawLine(0, 11, 20, 11)   # same line ItemSample draws
        super().paint(p, *args)


class Series:
    """One named data series, drawn as one curve on each of its plots.
    Returned by add_series(); pass it back to set_data() etc."""

    def __init__(self, name: str, role: Role, linked: bool, in_legend: bool,
                 break_gaps: bool = True):
        self.name = name
        self.role = role
        self.linked = linked
        self.in_legend = in_legend
        self.items: Dict[str, pg.PlotDataItem] = {}
        self.break_gaps = break_gaps

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(self.items)

    def item(self, key: str) -> Optional[pg.PlotDataItem]:
        return self.items.get(key)


class KinematicStackedPlotWidget(QWidget):

    def __init__(self, parent=None,
                 kinematics: Sequence[str] = ("dist", "speed", "accel"),
                 available: Optional[Mapping[str, bool]] = None,
                 y_fit: str = "visible",
                 left_axis_width: int = 72,
                 legend_offset: Tuple[int, int] = (10, 10),
                 visible_count: int = 0,
                 hover_values: bool = True,
                 time_step: Optional[float] = None):
        """
        Args:
            kinematics: plot keys from KINEMATICS, top to bottom.
            available: {key: False} for columns absent from the dataset;
                keys not listed are treated as available.
            y_fit: "visible" -- y fits the data inside the current time
                window; "all" -- y fits all of the plot's data, so zooming
                time never rescales y.
            left_axis_width: fixed pixel width of every left axis so the
                time axes line up. Raise it if large y tick labels clip.
            legend_offset: legend position from the plot's top-left
                (negative values anchor to the right / bottom).
            visible_count: plots fitted in the view at once; the rest are
                reached by scrolling. 0 (default) fits all plots.
            hover_values: show the hover cursor and per-plot value labels.
            time_step: dataset sampling interval for gap breaks; None estimates per series.
        """
        super().__init__(parent)
        unknown = [k for k in kinematics if k not in KINEMATICS]
        if unknown:
            raise ValueError(f"Unknown kinematic key(s): {unknown}")
        if y_fit not in ("visible", "all"):
            raise ValueError("y_fit must be 'visible' or 'all'")

        self._keys: Tuple[str, ...] = tuple(kinematics)
        avail = dict(available or {})
        self._available = {k: bool(avail.get(k, True)) for k in self._keys}
        self._y_fit = y_fit
        self._left_axis_width = left_axis_width
        self._legend_offset = legend_offset
        self._hover_values = hover_values
        self._time_step = time_step

        self.plots: Dict[str, pg.PlotWidget] = {}
        self._legends: Dict[str, pg.LegendItem] = {}
        self._cursors: Dict[str, pg.InfiniteLine] = {}
        self._hover_labels: Dict[str, QLabel] = {}
        self._label_prefix: Dict[str, str] = {}
        self._titles: Dict[str, str] = {}
        self._vlines: List[Tuple[str, pg.InfiniteLine]] = []   # for export_spec
        self._series: List[Series] = []
        self._propagating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._plot_container = QWidget()
        column = QVBoxLayout(self._plot_container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(self._plot_container)
        outer.addWidget(self._scroll)

        ref_vb = None
        for key in self._keys:
            pw = self._build_plot(key)
            column.addWidget(pw, stretch=1)
            self.plots[key] = pw
            vb = pw.getViewBox()
            if ref_vb is None:
                ref_vb = vb
            else:
                vb.setXLink(ref_vb)

        self._visible_count = 0
        self._top_index = 0
        self._pitch = 1                  # one plot's height + spacing, in px
        self._wheel_accum = 0            # partial wheel deltas (touchpads)
        self._locked = False
        self._pre_lock: Optional[Tuple[int, int]] = None   # (visible_count, top_index)
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(_SNAP_DELAY_MS)
        self._snap_timer.timeout.connect(self._snap_to_nearest)
        self._install_scroll_controls()
        self.set_visible_count(visible_count)

    # ── Construction ───────────────────────────────────────────────────

    def set_time_step(self, step: Optional[float]) -> None:
        """Sampling interval used to break lines at gaps (utils.gap_indices).
        Applies to the next set_data() call."""
        self._time_step = step

    def _build_plot(self, key: str) -> pg.PlotWidget:
        spec = KINEMATICS[key]
        pw = pg.PlotWidget()
        apply_plot_style(pw)
        pw.setLabel("left", spec.label, units=spec.unit)
        pw.setLabel("bottom", "Time", units="s")     # on every plot, see module doc
        pw.getAxis("left").setWidth(self._left_axis_width)
        if not self._available[key]:
            pw.setTitle(f"{spec.label} ({spec.column} not available in this dataset)",
                        size=TITLE_SIZE)

        vb = pw.getViewBox()
        vb.setAutoVisible(y=(self._y_fit == "visible"))
        vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)

        legend = pw.addLegend(
            offset=self._legend_offset,
            brush=pg.mkBrush(255, 255, 255, 210),
            pen=pg.mkPen("#999999"),
            labelTextColor="k",
            sampleType=_OutlineSample,
        )
        legend.setToolTip(_LEGEND_TOOLTIP)
        legend.setVisible(False)          # shown once the plot has 2+ legend series
        self._legends[key] = legend

        pw.getPlotItem().autoBtn.clicked.connect(self._on_auto_btn_clicked)

        # Hover cursor: ignoreBounds keeps it out of auto-range, otherwise
        # the view would chase the line near the edges.
        cursor = pg.InfiniteLine(angle=90, movable=False, pen=role_pen(Role.CROSSHAIR))
        cursor.setZValue(role_z(Role.CROSSHAIR))
        cursor.setVisible(False)
        pw.addItem(cursor, ignoreBounds=True)
        self._cursors[key] = cursor

        # Value label: a child widget of the plot (not QToolTip, of which
        # only one can show at a time), transparent to the mouse so it never
        # takes clicks or hover away from the plot underneath.
        label = QLabel(pw)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setStyleSheet(_HOVER_LABEL_STYLE)
        label.hide()
        self._hover_labels[key] = label

        pw.scene().sigMouseMoved.connect(
            lambda pos, k=key: self._on_mouse_moved(k, pos))
        return pw

    # ── Plots in view / scrolling ──────────────────────────────────────

    def _install_scroll_controls(self) -> None:
        vbar = self._scroll.verticalScrollBar()
        vbar.setToolTip(_SCROLL_TOOLTIP)
        vbar.valueChanged.connect(self._on_scroll_value_changed)
        vbar.sliderReleased.connect(self._snap_to_nearest)
        vbar.actionTriggered.connect(self._on_scroll_action)
        vbar.installEventFilter(self)                       # wheel: one plot per notch

        # Plot viewports: Shift+wheel scrolls the stack (a plain wheel zooms).
        # StrongFocus so clicking a plot gives the stack keyboard focus.
        self._wheel_targets = set()
        for pw in self.plots.values():
            pw.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            pw.viewport().installEventFilter(self)
            self._wheel_targets.add(pw.viewport())
        self._scroll.viewport().installEventFilter(self)   # re-fit on resize

        # WidgetWithChildrenShortcut: active only while focus is inside this
        # widget, so digits typed into the dialog's spin boxes are unaffected.
        def shortcut(seq: str, slot) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        for digit in range(10):
            shortcut(str(digit), lambda n=digit: self.set_visible_count(n))
        shortcut("PgDown", lambda: self.scroll_by(1))
        shortcut("PgUp", lambda: self.scroll_by(-1))

    def visible_count(self) -> int:
        """Plots fitted in the view at once; 0 means all of them."""
        return self._visible_count

    def set_visible_count(self, n: int) -> None:
        """Fit *n* plots in the view and scroll to reach the others.
        0, or any n >= the number of plots, fits all and hides the
        scrollbar. The current top plot stays on top where possible.
        Ignored while locked (see lock_to)."""
        if self._locked:
            return
        count = len(self._keys)
        self._visible_count = 0 if n <= 0 or n >= count else int(n)
        self._apply_plot_heights()

    def _apply_plot_heights(self) -> None:
        container = self._plot_container
        n = self._visible_count
        if n == 0:
            container.setMinimumHeight(0)
            container.setMaximumHeight(_QWIDGETSIZE_MAX)
            self._top_index = 0
            return
        spacing = container.layout().spacing()
        view_h = self._scroll.viewport().height()
        plot_h = max((view_h - spacing * (n - 1)) // n, 1)
        count = len(self._keys)
        self._pitch = plot_h + spacing
        container.setFixedHeight(plot_h * count + spacing * (count - 1))
        # The scroll range updates after the layout pass, so scroll then.
        QTimer.singleShot(0, lambda: self.scroll_to_plot(self._top_index))

    def scroll_to_plot(self, index) -> None:
        """Scroll so plot *index* (position or key) is at the top. Clamped
        so the view is always full of plots."""
        if isinstance(index, str):
            index = self._keys.index(index)
        if self._visible_count == 0:
            return
        last_top = len(self._keys) - self._visible_count
        self._top_index = max(0, min(int(index), last_top))
        self._scroll.verticalScrollBar().setValue(self._top_index * self._pitch)

    def scroll_by(self, plots: int) -> None:
        if not self._locked:
            self.scroll_to_plot(self._top_index + plots)

    def lock_to(self, keys: Iterable[str]) -> None:
        """Fill the view with exactly *keys* -- adjacent plots, in any
        order -- and freeze scrolling and the number keys until unlock().
        For focused editing, e.g. GapReview's Add Points (fitting-base plot
        plus the derived plot beneath it). Calling it again while locked
        switches to the new keys; unlock() still restores the view from
        before the first call."""
        idx = sorted({self._keys.index(k) for k in keys})
        if not idx:
            raise ValueError("lock_to needs at least one plot key")
        if idx != list(range(idx[0], idx[-1] + 1)):
            raise ValueError(f"lock_to needs adjacent plots, got {list(keys)}")
        if not self._locked:
            self._pre_lock = (self._visible_count, self._top_index)
        self._locked = True
        n = len(idx)
        self._visible_count = 0 if n >= len(self._keys) else n
        self._top_index = idx[0]
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._apply_plot_heights()

    def unlock(self) -> None:
        """Undo lock_to(): restore the plots-in-view count and scroll
        position from before it, and re-enable scrolling."""
        if not self._locked:
            return
        self._locked = False
        self._visible_count, self._top_index = self._pre_lock
        self._pre_lock = None
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._apply_plot_heights()

    def is_locked(self) -> bool:
        return self._locked

    def _on_scroll_value_changed(self, _value: int) -> None:
        # Scrollbar arrows, wheel and track clicks land anywhere; settle on
        # a plot boundary shortly after they stop. A drag snaps on release.
        if self._visible_count and not self._scroll.verticalScrollBar().isSliderDown():
            self._snap_timer.start()

    def _on_scroll_action(self, action: int) -> None:
        """Scrollbar arrows step one plot; track clicks step a page (the
        number of plots in view). Qt applies the action after this slot,
        so moving the slider position here replaces its pixel step."""
        if not self._visible_count or self._locked:
            return
        a = QAbstractSlider.SliderAction
        steps = {
            a.SliderSingleStepAdd: 1,
            a.SliderSingleStepSub: -1,
            a.SliderPageStepAdd: self._visible_count,
            a.SliderPageStepSub: -self._visible_count,
        }.get(a(action))
        if steps is None:
            return                       # drags snap on release
        last_top = len(self._keys) - self._visible_count
        self._top_index = max(0, min(self._top_index + steps, last_top))
        self._scroll.verticalScrollBar().setSliderPosition(self._top_index * self._pitch)

    def _wheel_scroll(self, event) -> None:
        """One plot per wheel notch (120 units). Partial deltas from
        high-resolution wheels and touchpads accumulate until a notch."""
        delta = event.angleDelta().y() or event.angleDelta().x()
        self._wheel_accum += delta
        while abs(self._wheel_accum) >= 120:
            step = -1 if self._wheel_accum > 0 else 1   # wheel up scrolls up
            self._wheel_accum += 120 * step
            self.scroll_by(step)

    def _snap_to_nearest(self) -> None:
        if self._visible_count:
            value = self._scroll.verticalScrollBar().value()
            self.scroll_to_plot(round(value / self._pitch))

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.Type.Leave and obj in self._wheel_targets:
            self.hide_cursor()          # mouse left a plot
        elif etype == QEvent.Type.Resize and obj is self._scroll.viewport():
            if self._visible_count:
                self._apply_plot_heights()
        elif etype == QEvent.Type.Wheel and self._visible_count and not self._locked:
            # Over the scrollbar or the gaps between plots: plain wheel.
            # Over a plot: Shift+wheel only, since a plain wheel zooms.
            # (macOS turns Shift+wheel into a horizontal delta;
            # _wheel_scroll accepts either.)
            on_bar = obj is self._scroll.verticalScrollBar() or obj is self._scroll.viewport()
            on_plot_shift = (obj in self._wheel_targets
                             and event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if on_bar or on_plot_shift:            
                self._wheel_scroll(event)
                return True
        return super().eventFilter(obj, event)

    # ── Plot access ────────────────────────────────────────────────────

    @property
    def keys(self) -> Tuple[str, ...]:
        return self._keys

    def available(self, key: str) -> bool:
        return self._available.get(key, False)

    def plot(self, key: str) -> pg.PlotWidget:
        return self.plots[key]

    def set_title(self, key: str, text: str) -> None:
        """Per-plot title, e.g. fit statistics. Empty text removes it."""
        self._titles[key] = text or ""
        self.plots[key].setTitle(text or None, size=TITLE_SIZE)

    def set_label_prefix(self, key: str, prefix: str = "",
                         tooltip: str = "") -> None:
        """Put *prefix* before plot *key*'s y-axis label -- e.g. "*" to mark
        the spline fitting base: "*Speed (m/s)" -- with an optional axis
        tooltip explaining it. Empty prefix restores the plain label. The
        prefix also appears in export_spec() labels."""
        spec = KINEMATICS[key]
        self._label_prefix[key] = prefix
        pw = self.plots[key]
        pw.setLabel("left", f"{prefix}{spec.label}", units=spec.unit)
        pw.getAxis("left").setToolTip(tooltip)

    def plot_label(self, key: str) -> str:
        """Display label for plot *key*, e.g. "*Speed (m/s)"."""
        spec = KINEMATICS[key]
        return f"{self._label_prefix.get(key, '')}{spec.label} ({spec.unit})"

    def _target_keys(self, plots: Optional[Iterable[str]]) -> List[str]:
        keys = self._keys if plots is None else [k for k in plots if k in self.plots]
        return [k for k in keys if self._available[k]]

    # ── Series ─────────────────────────────────────────────────────────

    def add_series(self, name: str, role: Role = Role.MEASURED,
                   color=None, width: Optional[float] = None,
                   linked: bool = True,
                   plots: Optional[Iterable[str]] = None,
                   in_legend: Optional[bool] = None,
                   z: Optional[float] = None,
                   outline=False,
                   break_gaps: bool = True) -> Series:
        """Create an empty curve for *name* on each target plot (all
        available plots by default). Fill it with set_data().

        Args:
            role: plot_style Role -- sets width, dash, z and legend default.
            color: normally omitted -- each plot then uses its kinematic
                color, or the role's own color (proposal purple). Pass one
                color for every plot, or {plot key: color} per plot, only
                to override that design language.
            width, z: override the role's defaults.
            outline: True draws a black outline under the line -- marks a
                second object of the same kind (e.g. Traj 2) in the same
                color. A color (e.g. plot_style.HALO_COLOR, white) draws
                the outline in that color instead -- a contrast halo for
                lines crossing shaded regions. Dashed lines get dashed
                outlines that line up with their dashes.
            linked: legend clicks on one plot show/hide this series on
                every plot (True) or only on the clicked plot (False).
            plots: restrict the series to these plot keys.
            in_legend: override the role's legend default.
            break_gaps: False draws straight through gaps, for deliberate connectors.
        """
        series = Series(name, role, linked,
                        role_in_legend(role) if in_legend is None else in_legend,
                        break_gaps)
        z_value = role_z(role) if z is None else z

        for key in self._target_keys(plots):
            given = color.get(key) if isinstance(color, Mapping) else color
            pen = role_pen(role, resolve_color(role, key, given), width)
            extra = {}
            if outline is not False and outline is not None:
                outline_color = OUTLINE_COLOR if outline is True else outline
                extra["shadowPen"] = outline_pen(
                    pen.widthF(), outline_color, ROLE_STYLES[role].dash_px)
            # No name= on the item, so PlotItem.addItem doesn't auto-add it
            # to the legend; legend membership is decided here instead.
            item = pg.PlotDataItem(pen=pen, **extra)
            item.setZValue(z_value)
            self.plots[key].addItem(item)
            if series.in_legend:
                self._legends[key].addItem(item, name)
            item.visibleChanged.connect(
                lambda s=series, k=key: self._on_item_visibility_changed(s, k))
            series.items[key] = item

        self._series.append(series)
        self._refresh_legends(series.keys)
        return series

    def set_data(self, series: Series, time,
                 values: Mapping[str, Optional[np.ndarray]]) -> None:
        """Set *series* data per plot key. A key that is missing or None
        clears that plot's curve. NaNs break the line (connect="finite"),
        and so does every time gap (utils.gap_indices) unless the series
        was created with break_gaps=False."""
        if time is not None and series.break_gaps:
            time, values = self._insert_gap_breaks(time, values)
        for key, item in series.items.items():
            y = values.get(key)
            if y is None or time is None:
                item.setData([], [])
            else:
                item.setData(time, y, connect="finite")

    def _insert_gap_breaks(self, time, values):
        """Insert one separator per gap: x at the gap's midpoint, NaN for
        every y. Stored in the item's own data, so export_spec() and
        plot_view.js break there too, and hover ignores it."""
        t = np.asarray(time, dtype=np.float64)
        step = self._time_step or estimate_time_step([t])
        idx = gap_indices(t, step) + 1
        if idx.size == 0:
            return t, values
        t = np.insert(t, idx, 0.5 * (t[idx - 1] + t[idx]))
        return t, {k: (None if y is None
                       else np.insert(np.asarray(y, dtype=np.float64), idx, np.nan))
                   for k, y in values.items()}

    def rename_series(self, series: Series, name: str) -> None:
        """Change *series*' name in every legend and hover label, e.g. to
        show the parameters of the latest fit."""
        series.name = name
        if not series.in_legend:
            return
        for key, item in series.items.items():
            legend = self._legends[key]
            for sample, label in legend.items:
                if sample.item is item:
                    label.setText(name)       # keeps the legend's color/size
            if hasattr(legend, "updateSize"):  # older pyqtgraph sizes manually
                legend.updateSize()

    @staticmethod
    def clear_data(series: Series) -> None:
        for item in series.items.values():
            item.setData([], [])

    def set_series_visible(self, series: Series, visible: bool,
                           key: Optional[str] = None) -> None:
        """Show/hide *series* on one plot, or on all of its plots when
        *key* is None. Legends repaint to match."""
        keys = series.keys if key is None else [key]
        self._propagating = True        # explicit request: no link copying
        try:
            for k in keys:
                item = series.items.get(k)
                if item is not None and item.isVisible() != visible:
                    item.setVisible(visible)
                    self._repaint_legend(k)
        finally:
            self._propagating = False

    def remove_series(self, series: Series) -> None:
        for key, item in series.items.items():
            try:
                item.visibleChanged.disconnect()
            except TypeError:
                pass
            if series.in_legend:
                self._legends[key].removeItem(item)
            self.plots[key].removeItem(item)
        keys = series.keys
        series.items.clear()
        if series in self._series:
            self._series.remove(series)
        self._refresh_legends(keys)

    @property
    def series(self) -> Tuple[Series, ...]:
        return tuple(self._series)

    # ── Legends / linked visibility ────────────────────────────────────

    def _refresh_legends(self, keys: Iterable[str]) -> None:
        for key in keys:
            n = sum(1 for s in self._series if s.in_legend and key in s.items)
            self._legends[key].setVisible(n >= 2)

    def _repaint_legend(self, key: str) -> None:
        """Legend samples read their item's visibility when painted, so
        they must be repainted after a programmatic setVisible()."""
        for sample, _label in self._legends[key].items:
            sample.update()

    def _on_item_visibility_changed(self, series: Series, key: str) -> None:
        if self._propagating or not series.linked:
            return
        visible = series.items[key].isVisible()
        self._propagating = True
        try:
            for other_key, item in series.items.items():
                if other_key != key and item.isVisible() != visible:
                    item.setVisible(visible)
                    self._repaint_legend(other_key)
        finally:
            self._propagating = False

    # ── Markers ────────────────────────────────────────────────────────

    def add_vlines(self, xs: Iterable[float], role: Role = Role.BOUNDARY,
                   color=None, width: Optional[float] = None,
                   plots: Optional[Iterable[str]] = None) -> List[pg.InfiniteLine]:
        """A vertical line at each x on every target plot (unavailable
        plots included, so the time reference stays visible). Returns all
        lines; move them together with line.setPos()."""
        pen = role_pen(role, color, width)
        keys = self._keys if plots is None else [k for k in plots if k in self.plots]
        lines = []
        for x in xs:
            for key in keys:
                line = pg.InfiniteLine(pos=x, angle=90, pen=pen, movable=False)
                line.setZValue(role_z(role))
                self.plots[key].addItem(line, ignoreBounds=True)
                lines.append(line)
                self._vlines.append((key, line))
        return lines

    def add_item(self, key: str, item, ignore_bounds: bool = True) -> None:
        """Add any other graphics item (region, label) to one plot. Items
        added here are never put in a legend."""
        self.plots[key].addItem(item, ignoreBounds=ignore_bounds)

    def remove_item(self, key: str, item) -> None:
        self.plots[key].removeItem(item)

    # ── View ───────────────────────────────────────────────────────────

    def set_x_range(self, lo: float, hi: float) -> None:
        # Set on every plot rather than relying on XLink alone: before the
        # widget is shown, XLink maps ranges through not-yet-valid geometry.
        for pw in self.plots.values():
            pw.setXRange(lo, hi, padding=0)

    def reset_all(self) -> None:
        """Auto-range every plot (what the corner "A" button does)."""
        for pw in self.plots.values():
            pw.enableAutoRange()

    def _on_auto_btn_clicked(self, *_args) -> None:
        self.reset_all()

    # ── Export ─────────────────────────────────────────────────────────

    def export_spec(self, x_link: str = "time") -> List[Dict]:
        """Plot specs for ui_kit.plot_export.build_plot_html(), one per
        available plot, top to bottom. Each series is exported with its own
        pen, outline, z-order, legend membership and current visibility, so
        the HTML matches the dialog, and linked series stay linked. The
        page opens on the current time range (double-click resets), with
        the same y_fit, titles and axis labels (including any prefix such
        as the spline fitting base's "*"). The hover cursor is not exported."""
        (x0, x1), _ = self.plots[self._keys[0]].viewRange()
        specs = []
        for key in self._keys:
            if not self._available[key]:
                continue
            items = []
            for index, s in enumerate(self._series):
                item = s.items.get(key)
                if item is None or item.xData is None or len(item.xData) == 0:
                    continue
                shadow = item.opts.get("shadowPen")
                items.append(curve_item(
                    s.name, item.xData, item.yData,
                    pen_spec(pg.mkPen(item.opts["pen"])),
                    shadow=pen_spec(pg.mkPen(shadow)) if shadow is not None else None,
                    z=item.zValue(), legend=s.in_legend, visible=item.isVisible(),
                    link=f"{index}:{s.name}" if s.linked else None))
            for line_key, line in self._vlines:
                if line_key == key:
                    items.append(vline_item(line.value(), pen_spec(line.pen), line.zValue()))
            spec = KINEMATICS[key]
            specs.append(plot_spec(
                items, title=self._titles.get(key, ""),
                x_label="Time (s)", y_label=self.plot_label(key),
                x_name="t", x_unit="s", y_unit=spec.unit,
                x_link=x_link, y_fit=self._y_fit, x_range=(x0, x1)))
        return specs

    # ── Hover cursor and values ────────────────────────────────────────

    def set_cursor(self, x: float) -> None:
        """Show the vertical cursor at time *x* on every plot, e.g. for a
        table row click. Moving the mouse over a plot replaces it."""
        for label in self._hover_labels.values():
            label.hide()
        for cursor in self._cursors.values():
            cursor.setValue(x)
            cursor.setVisible(True)

    def hide_cursor(self) -> None:
        for cursor in self._cursors.values():
            cursor.setVisible(False)
        for label in self._hover_labels.values():
            label.hide()

    def _hover_items(self, key: str):
        """(series, item) for each visible legend series on plot *key* --
        the curves whose values the hover labels report."""
        for s in self._series:
            item = s.items.get(key)
            if item is not None and s.in_legend and item.isVisible():
                yield s, item

    def _on_mouse_moved(self, key: str, scene_pos) -> None:
        if not self._hover_values:
            return
        vb = self.plots[key].getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            self.hide_cursor()          # over an axis, title or legend margin
            return
        pt = vb.mapSceneToView(scene_pos)
        mx, my = pt.x(), pt.y()
        for cursor in self._cursors.values():
            cursor.setValue(mx)
            cursor.setVisible(True)

        px_w, px_h = vb.viewPixelSize()
        if not px_w or not px_h:
            return
        hit_x = self._nearest_curve_x(key, mx, my, px_w, px_h)
        if hit_x is None:
            for label in self._hover_labels.values():
                label.hide()
            return
        x_tol = _HOVER_PIXEL_TOL * px_w      # x is linked, so the same on every plot
        for k in self._keys:
            self._show_values(k, hit_x, x_tol)

    def _nearest_curve_x(self, key: str, mx: float, my: float,
                         px_w: float, px_h: float) -> Optional[float]:
        """Time of the curve point on plot *key* nearest the mouse, if it
        is within _HOVER_PIXEL_TOL screen pixels; otherwise None."""
        best_d, best_x = _HOVER_PIXEL_TOL, None
        for _s, item in self._hover_items(key):
            x, y = item.getData()
            if x is None or len(x) == 0:
                continue
            d = np.hypot((np.asarray(x) - mx) / px_w, (np.asarray(y) - my) / px_h)
            finite = np.isfinite(d)
            if not finite.any():
                continue
            i = int(np.nanargmin(np.where(finite, d, np.nan)))
            if d[i] <= best_d:
                best_d, best_x = d[i], float(x[i])
        return best_x

    def _show_values(self, key: str, hit_x: float, x_tol: float) -> None:
        """Label on plot *key* with each visible series' value at the
        sample nearest *hit_x* (within *x_tol*), placed beside the curves."""
        label = self._hover_labels[key]
        unit = KINEMATICS[key].unit
        lines = [f"t = {hit_x:.3f} s"]
        ys = []
        for s, item in self._hover_items(key):
            x, y = item.getData()
            if x is None or len(x) == 0:
                continue
            x = np.asarray(x)
            y = np.asarray(y)
            dx = np.where(np.isfinite(y), np.abs(x - hit_x), np.inf)
            i = int(np.argmin(dx))
            if dx[i] <= x_tol:
                lines.append(f"{s.name}: {y[i]:.4f} {unit}")
                ys.append(float(y[i]))
        if not ys:
            label.hide()
            return
        label.setText("\n".join(lines))
        label.adjustSize()
        self._place_label(key, label, hit_x, sum(ys) / len(ys))

    def _place_label(self, key: str, label: QLabel, x: float, y: float) -> None:
        """Put *label* just above-right of data point (x, y), kept inside
        its plot."""
        pw = self.plots[key]
        scene_pt = pw.getViewBox().mapViewToScene(pg.Point(x, y))
        widget_pt = pw.mapFromScene(scene_pt)
        px = widget_pt.x() + 10
        py = widget_pt.y() - label.height() - 8
        px = max(0, min(int(px), pw.width() - label.width()))
        py = max(0, min(int(py), pw.height() - label.height()))
        label.move(px, py)
        label.show()
        label.raise_()
