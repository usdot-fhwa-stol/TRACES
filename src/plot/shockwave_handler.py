import logging
import math
import numpy as np
import time
import pyqtgraph as pg
from PyQt6.QtCore import Qt

from .interaction_mode import InteractionModeHandler
from comp.shockwave import InflectionType, InflectionPoint, ShockwaveResult, ShockwaveComp


class ShockwaveModeHandler(InteractionModeHandler):
    """Shared base for all three shockwave interaction modes.

    Owns everything common to Manual, Semi-Auto, and Automatic:
    inflection cache, result accumulation, slope slider lifecycle,
    inflection marker rendering, and mode-exit save prompt.
    """

    # Marker appearance constants
    _MARKER_SIZE_FULL   = 12
    _MARKER_SIZE_DIMMED = 8
    _COLOR_RELEASE      = (0, 180, 0)     # green
    _COLOR_COMPRESSION  = (200, 0, 0)     # red
    _COLOR_DIMMED       = (180, 180, 180)

    def __init__(self, slope_percentile: float = 80.0, vicinity: float = 0.05):
        self._slope_percentile = slope_percentile
        self._vicinity         = vicinity
        self._inflection_cache: dict = {}   # veh_id → list[InflectionPoint]
        self._fitted_results:   list = []   # list[ShockwaveResult]
        self._inflection_items: dict = {}   # veh_id → pg.ScatterPlotItem
        self._shockwave_items:  list = []   # pg items for committed shockwave lines
        self._result_panel = None           # ShockwaveResultPanel, created lazily

    def on_mouse_move(self, view_box, ev) -> bool:
        """Suppress the default drag-selection box in Shockwave mode."""
        ev.accept()
        return True

    # ── Lifecycle ────────────────────────────────────────────────────────

    def on_enter(self, widget):
        """Build cache, draw markers, show slider controls."""
        self._inflection_cache = ShockwaveComp.rebuild_inflection_cache(
            widget._series, self._slope_percentile
        )
        self._draw_inflection_markers(widget, validity_filter=None)
        widget._shockwave_slider_widget.setVisible(True)

    def on_exit(self, widget):
        """Prompt save if needed, clear all overlays, hide controls."""
        if self._fitted_results:
            self._show_save_prompt(widget)
        self._clear_inflection_markers(widget)
        self._clear_shockwave_items(widget)

        if self._result_panel:
            self._result_panel.clear()
            self._result_panel.deleteLater()
            self._result_panel = None

        widget._shockwave_slider_widget.setVisible(False)

    def on_before_data_reload(self, widget):
        """Warn before a lane/event change invalidates committed results.
    
        A lane or event change reloads widget._series with a different set
        of trajectories, so any already-fitted shockwave results no longer
        correspond to anything on screen -- the same situation on_exit
        already guards against when leaving Shockwave mode entirely. Unlike
        on_exit, though, this handler instance is NOT discarded afterward
        (the user is staying in Shockwave mode, just looking at different
        data), so _fitted_results and the result panel have to be explicitly
        reset here, or they'd silently keep describing a lane that's no
        longer displayed.
        """
        if self._fitted_results:
            self._show_save_prompt(widget)
            self._fitted_results = []
    
        if self._result_panel:
            self._result_panel.clear()
            self._result_panel.deleteLater()
            self._result_panel = None

    def on_plot_cleared(self, widget):
        """scatter_plot.clear() removed all items; drop stale references."""
        self._inflection_items.clear()
        self._shockwave_items.clear()

    def on_data_reloaded(self, widget):
        """Rebuild cache and redraw markers after lane/event change."""
        from comp.shockwave import ShockwaveComp
        self._inflection_cache = ShockwaveComp.rebuild_inflection_cache(
            widget._series, self._slope_percentile
        )
        self._draw_inflection_markers(widget, validity_filter=None)

    # ── Slider hooks (called from TimeSpacePlotWidget) ───────────────────

    def on_slope_changed(self, widget, value: float):
        """Rebuild cache and redraw markers; leave fitted shockwaves alone."""
        from comp.shockwave import ShockwaveComp
        self._slope_percentile = value
        self._inflection_cache = ShockwaveComp.rebuild_inflection_cache(
            widget._series, self._slope_percentile
        )
        self._clear_inflection_markers(widget)
        self._draw_inflection_markers(widget, validity_filter=None)

    def on_vicinity_changed(self, widget, value: float):
        """Store new vicinity; affects future BFS runs only (Automatic)."""
        self._vicinity = value

    # ── Inflection marker helpers ────────────────────────────────────────

    def _draw_inflection_markers(self, widget, validity_filter,
                                  locked_color=None, extra_filter=None):
        """Create one ScatterPlotItem per trajectory with its X markers.

        validity_filter: None  → all markers full size and color
                         set    → only veh_ids in set are full; others dimmed
        locked_color:    None  → no color filter applied
                         InflectionType → points of the opposite type are dimmed
        extra_filter:    None  → no additional filter
                         callable(InflectionPoint) -> bool → points where this
                         returns False are dimmed, regardless of trajectory/color
                         validity. Used for the minimum time-separation check.
        """
        self._clear_inflection_markers(widget)
        for veh_id, pts in self._inflection_cache.items():
            if not pts:
                continue
            traj_dimmed = validity_filter is not None and veh_id not in validity_filter

            times  = np.array([p.time     for p in pts])
            dists  = np.array([p.distance for p in pts])
            colors = []
            sizes  = []
            for p in pts:
                color_dimmed = (locked_color is not None and p.type != locked_color)
                extra_dimmed = (extra_filter is not None and not extra_filter(p))
                if traj_dimmed or color_dimmed or extra_dimmed:
                    colors.append(self._COLOR_DIMMED)
                    sizes.append(self._MARKER_SIZE_DIMMED)
                elif p.type == InflectionType.RELEASE:
                    colors.append(self._COLOR_RELEASE)
                    sizes.append(self._MARKER_SIZE_FULL)
                else:
                    colors.append(self._COLOR_COMPRESSION)
                    sizes.append(self._MARKER_SIZE_FULL)

            item = pg.ScatterPlotItem(
                x=times, y=dists,
                symbol='x',
                pen=[pg.mkPen(color=c, width=4) for c in colors],
                brush=[pg.mkBrush(None)] * len(pts),
                size=sizes,
            )
            widget._scatter_plot.addItem(item)
            self._inflection_items[veh_id] = item

    def _clear_inflection_markers(self, widget):
        for item in self._inflection_items.values():
            widget._scatter_plot.removeItem(item)
        self._inflection_items.clear()

    def _clear_shockwave_items(self, widget):
        for item in self._shockwave_items:
            widget._scatter_plot.removeItem(item)
        self._shockwave_items.clear()

    def _draw_fitted_shockwave(self, widget, result: ShockwaveResult):
            """Draw a dashed regression line spanning the full displayed time range.
    
            The line is computed from the fitted slope/intercept but extended to
            cover the entire time extent of widget._series so the shockwave is
            visible across the whole plot, not just between the selected points.
            """
            # Collect the global time range from all displayed series.
            t_min = float('inf')
            t_max = float('-inf')
            for series in widget._series.values():
                times = series.get("veh_time")
                if times is not None and len(times) > 0:
                    t_min = min(t_min, float(np.min(times)))
                    t_max = max(t_max, float(np.max(times)))
    
            # Fall back to the selected-point range if no series data found.
            if t_min == float('inf'):
                pt_times = [p[0] for p in result.points]
                t_min, t_max = min(pt_times), max(pt_times)
    
            t_range = [t_min, t_max]
            d_range = [result.slope * t + result.intercept for t in t_range]
    
            color_idx = (result.id - 1) % len(widget.series_color)
            pen = pg.mkPen(color=widget.series_color[color_idx], width=2,
                           style=Qt.PenStyle.DashLine)
            line = pg.PlotDataItem(t_range, d_range, pen=pen)
            widget._scatter_plot.addItem(line)
            self._shockwave_items.append(line)

    def _show_save_prompt(self, widget):
        """Warn the user that unsaved shockwave data will be lost."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            widget, "Shockwave Mode",
            "Shockwave data will be lost, save progress?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._open_results_table(widget)

    def _open_results_table(self, widget, warn_on_close=True):
        from comp.shockwave import ShockwaveResultsTableDialog
        dlg = ShockwaveResultsTableDialog(
            widget, self._fitted_results, warn_on_close=warn_on_close
        )
        dlg.exec()

    def _ensure_result_panel(self, widget):
        if self._result_panel is None:
            from plot.shockwave_result_panel import ShockwaveResultPanel
            self._result_panel = ShockwaveResultPanel(
                widget._scatter_plot,
                on_title_clicked=lambda: self._open_results_table(
                    widget, warn_on_close=False
                ),
            )
        return self._result_panel

# Set up logger
logger = logging.getLogger(__name__)

class ShockwaveSemiAutoModeHandler(ShockwaveModeHandler):
    """Semi-Automatic shockwave mode.

    The user clicks pre-marked inflection X markers one by one.
    After the first click, only same-color markers on the immediately
    adjacent trajectory are valid. Press Enter to fit; right-click to undo
    the last point; Escape to abandon the current chain.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._active_chain:         list = []   # list[InflectionPoint]
        self._locked_color          = None      # InflectionType or None
        self._ordered_sequence:     list = []   # veh_ids ordered by crossing time
        self._valid_next_traj_ids         = None  # set[int] or None (None = any)
        self._chain_items:          list = []   # pg items for chain visualization

    # ── Lifecycle ────────────────────────────────────────────────────────

    def on_enter(self, widget):
        super().on_enter(widget)   # builds cache, draws markers, shows sliders
        # All markers valid at entry — no chain in progress.
        logger.debug("Entered Semi-Auto Shockwave mode")

    def on_exit(self, widget):
        self._clear_chain_items(widget)
        self._reset_chain_state()
        super().on_exit(widget)

    def on_plot_cleared(self, widget):
        super().on_plot_cleared(widget)
        self._chain_items.clear()
        self._reset_chain_state()

    def on_data_reloaded(self, widget):
        super().on_data_reloaded(widget)
        self._clear_chain_items(widget)
        self._reset_chain_state()

    # ── CustomViewBox events ─────────────────────────────────────────────

    def on_mouse_release(self, view_box, ev) -> bool:
        """Left-click selects the nearest valid inflection point.
        Ctrl+Left-click removes the nearest point already in the chain."""
        widget = view_box.parent_widget
        move_distance  = (ev.scenePos() - view_box._press_pos).manhattanLength()
        press_duration = time.time() - view_box._press_time

        if move_distance < 5 and press_duration < 0.5:
            mouse_pt = view_box.mapSceneToView(ev.scenePos())

            if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._erase_chain_point(mouse_pt.x(), mouse_pt.y(), widget)
            else:
                best_pt, best_dist = self._find_nearest_valid_point(
                    mouse_pt.x(), mouse_pt.y(), widget
                )
                if best_pt is not None and best_dist < self._CLICK_RADIUS:
                    self._select_point(best_pt, widget)

        view_box._press_pos    = None
        view_box._press_time   = None
        view_box._is_selecting = False
        ev.accept()
        return True

    def _erase_chain_point(self, mx, my, widget):
        """Remove the chain point nearest (mx, my), if within click radius.

        Implements the Ctrl+Left-Click erase command described in
        ShockwaveAnalysisReport_v1.0: removes that specific point from
        active_chain (not necessarily the most recently added one), then
        recomputes valid_next_traj_ids from whatever is now the new last
        point in the chain. If the chain becomes empty, all chain state
        (locked_color, ordered_sequence, valid_next_traj_ids) resets.
        """
        if not self._active_chain:
            return

        best_idx  = None
        best_dist = float('inf')
        for i, pt in enumerate(self._active_chain):
            d = math.hypot(pt.time - mx, pt.distance - my)
            if d < best_dist:
                best_dist = d
                best_idx  = i

        if best_idx is None or best_dist >= self._CLICK_RADIUS:
            return

        removed = self._active_chain.pop(best_idx)

        if not self._active_chain:
            self._reset_chain_state()
        else:
            last = self._active_chain[-1]
            self._valid_next_traj_ids = ShockwaveComp.get_adjacent_trajectories(
                last.veh_id, self._ordered_sequence
            )

        self._refresh_highlights(widget)
        self._redraw_chain(widget)
        logger.debug(
            f"SW semi-auto: erased veh {removed.veh_id} at t={removed.time:.2f} "
            f"via Ctrl+Click"
        )

    def on_right_click(self, view_box, ev) -> bool:
        """Undo the last selected point."""
        widget = view_box.parent_widget
        if self._active_chain:
            self._active_chain.pop()
            if not self._active_chain:
                self._reset_chain_state()
            else:
                last = self._active_chain[-1]
                self._valid_next_traj_ids = ShockwaveComp.get_adjacent_trajectories(
                    last.veh_id, self._ordered_sequence
                )
            self._refresh_highlights(widget)
            self._redraw_chain(widget)
        ev.accept()
        return True   # suppress context menu

    def on_key_press(self, widget, event) -> bool:
        key = event.key()

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit_chain(widget)
            return True

        if key == Qt.Key.Key_Escape:
            if self._active_chain:
                self._clear_chain_items(widget)
                self._reset_chain_state()
                self._refresh_highlights(widget)
                return True   # consumed; don't propagate to Select-mode clear
            return False      # empty chain: let widget handle Escape normally

        return False

    # ── Selection logic ──────────────────────────────────────────────────

    _CLICK_RADIUS = 15   # pixels/data-units (same scale as CustomViewBox tolerances)
    _MIN_TIME_SEPARATION = 0.1  # seconds

    def _passes_time_separation(self, pt: InflectionPoint) -> bool:
        """True if pt is far enough in time from the current chain anchor.

        The anchor is the most recently selected point in active_chain.
        With no active chain (first selection), every point passes —
        there's nothing yet to be degenerate relative to.
        """
        if not self._active_chain:
            return True
        anchor = self._active_chain[-1]
        return abs(pt.time - anchor.time) >= self._MIN_TIME_SEPARATION

    def _find_nearest_valid_point(self, mx, my, widget):
        """Return (InflectionPoint, distance) for the closest valid marker,
        or (None, inf) if none found."""
        best_pt   = None
        best_dist = float('inf')
        for veh_id, pts in self._inflection_cache.items():
            if not pts:
                continue
            if self._valid_next_traj_ids is not None and veh_id not in self._valid_next_traj_ids:
                continue
            for pt in pts:
                if self._locked_color is not None and pt.type != self._locked_color:
                    continue
                if not self._passes_time_separation(pt):
                    continue
                d = math.hypot(pt.time - mx, pt.distance - my)
                if d < best_dist:
                    best_dist = d
                    best_pt   = pt
        return best_pt, best_dist

    def _select_point(self, pt: InflectionPoint, widget):
        """Add pt to active_chain and update all derived state."""
        from comp.shockwave import ShockwaveComp
        if not self._active_chain:
            # First selection: lock color, build trajectory order.
            self._locked_color = pt.type
            self._ordered_sequence = ShockwaveComp.build_trajectory_order(
                pt.distance, widget._series
            )
        self._active_chain.append(pt)
        self._valid_next_traj_ids = ShockwaveComp.get_adjacent_trajectories(
            pt.veh_id, self._ordered_sequence
        )
        self._refresh_highlights(widget)
        self._redraw_chain(widget)
        logger.debug(f"SW semi-auto: selected veh {pt.veh_id} at t={pt.time:.2f}")

    def _commit_chain(self, widget):
        """Fit a line through the current chain on Enter."""
        from comp.shockwave import ShockwaveComp
        if len(self._active_chain) < 2:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                widget, "Shockwave",
                "Select at least 2 inflection points before fitting."
            )
            return

        points = [(p.time, p.distance) for p in self._active_chain]
        result = ShockwaveComp.fit_and_report(
            points, mode="semi_auto",
            shockwave_id=len(self._fitted_results) + 1
        )
        self._fitted_results.append(result)
        self._draw_fitted_shockwave(widget, result)
        panel = self._ensure_result_panel(widget)
        panel.update_result(result)

        self._clear_chain_items(widget)
        self._reset_chain_state()
        self._refresh_highlights(widget)
        logger.debug(f"SW semi-auto: committed shockwave {result.id}, R²={result.r_squared:.4f}")

    # ── Visual helpers ───────────────────────────────────────────────────

    def on_slope_changed(self, widget, value: float):
        """Rebuild cache on new threshold, then re-apply chain highlighting
        (if a chain is in progress) instead of drawing all markers full."""
        self._slope_percentile = value
        self._inflection_cache = ShockwaveComp.rebuild_inflection_cache(
            widget._series, self._slope_percentile
        )
        self._refresh_highlights(widget)

    def _refresh_highlights(self, widget):
        """Redraw X markers: full for valid selections, dimmed for invalid."""
        if not self._active_chain:
            # No chain in progress — all markers full, no filters active.
            self._draw_inflection_markers(widget, validity_filter=None, locked_color=None)
            return

        selected_veh_ids = {p.veh_id for p in self._active_chain}
        if self._valid_next_traj_ids is not None:
            full_set = selected_veh_ids | self._valid_next_traj_ids
        else:
            full_set = None  # all valid (first click just happened)
        self._draw_inflection_markers(
            widget,
            validity_filter=full_set,
            locked_color=self._locked_color,
            extra_filter=self._passes_time_separation,
        )

    def _redraw_chain(self, widget):
        """Draw filled circles at selected points and a line connecting them."""
        self._clear_chain_items(widget)
        if not self._active_chain:
            return

        times = [p.time     for p in self._active_chain]
        dists = [p.distance for p in self._active_chain]

        # Connecting line — only meaningful with 2+ points
        if len(self._active_chain) >= 2:
            pen  = pg.mkPen(color=(80, 80, 200), width=2,
                            style=Qt.PenStyle.SolidLine)
            line = pg.PlotDataItem(times, dists, pen=pen)
            widget._scatter_plot.addItem(line)
            self._chain_items.append(line)

        # Filled circle(s) — drawn for any number of selected points
        dots = pg.ScatterPlotItem(
            x=times, y=dists,
            symbol='o', size=10,
            pen=pg.mkPen(color=(80, 80, 200)),
            brush=pg.mkBrush(color=(80, 80, 200, 160)),
        )
        widget._scatter_plot.addItem(dots)
        self._chain_items.append(dots)

    def _clear_chain_items(self, widget):
        for item in self._chain_items:
            widget._scatter_plot.removeItem(item)
        self._chain_items.clear()

    def _reset_chain_state(self):
        self._active_chain.clear()
        self._locked_color        = None
        self._ordered_sequence    = []
        self._valid_next_traj_ids = None