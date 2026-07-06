import logging
import math
import numpy as np
import time
import pyqtgraph as pg
from PyQt6.QtCore import Qt

from .interaction_mode import InteractionModeHandler
from comp.shockwave import InflectionType, InflectionPoint, ShockwaveResult, ShockwaveComp

logger = logging.getLogger(__name__)


class ShockwaveModeHandler(InteractionModeHandler):
    """Shared base for all three shockwave interaction modes.

    Owns everything common to Manual, Semi-Auto, and Automatic:
    inflection cache, result accumulation, slope slider lifecycle,
    inflection marker rendering, mode-exit save prompt, the active
    chain (new-shockwave construction or re-opened-for-edit), and the
    Ctrl+Left-click / Ctrl+Right-click edit-and-delete machinery.

    Mode subclasses only need to supply the rules for which point is a
    valid next selection (see ShockwaveSemiAutoModeHandler) via the hooks
    marked "Hook for subclasses" below.
    """

    # Marker appearance constants
    _MARKER_SIZE_FULL   = 12
    _MARKER_SIZE_DIMMED = 8
    _COLOR_RELEASE      = (0, 180, 0)     # green
    _COLOR_COMPRESSION  = (200, 0, 0)     # red
    _COLOR_NEUTRAL      = (80, 80, 200)   # Manual mode / unknown point_type
    _COLOR_DIMMED       = (180, 180, 180)

    _CLICK_RADIUS = 15   # pixels/data-units (same scale as CustomViewBox tolerances)

    def __init__(self, slope_percentile: float = 80.0, vicinity: float = 0.05):
        self._slope_percentile = slope_percentile
        self._vicinity         = vicinity
        self._inflection_cache: dict = {}   # veh_id → list[InflectionPoint]
        self._fitted_results:   list = []   # list[ShockwaveResult]
        self._inflection_items: dict = {}   # veh_id → pg.ScatterPlotItem
        self._shockwave_items:  dict = {}   # shockwave id → [pg items]
        self._result_panel = None           # ShockwaveResultPanel, created lazily
        self._panel_result_id = None   # id currently shown in the floating panel, or None
        self._next_shockwave_id = 1         # monotonic; never reused after a delete

        # Active chain: points being added for a new shockwave, or the
        # points of a committed shockwave re-opened for editing.
        self._active_chain:    list = []    # list[InflectionPoint]
        self._chain_items:     list = []    # pg items for chain visualization
        self._is_editing       = False
        self._editing_id       = None       # id being edited, preserved on re-commit
        self._editing_index    = None       # position in _fitted_results to restore to
        self._editing_backup   = None       # the ShockwaveResult removed while editing

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
        self._clear_chain_items(widget)
        if self._is_editing and self._editing_backup is not None:
            # Don't drop an in-progress edit's original silently -- put it
            # back so the save prompt below (and the table it opens)
            # reflects it like any other committed shockwave.
            self._fitted_results.insert(self._editing_index, self._editing_backup)
        self._reset_chain_state()

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
        if self._is_editing and self._editing_backup is not None:
            self._fitted_results.insert(self._editing_index, self._editing_backup)
            self._reset_chain_state()

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
        self._chain_items.clear()
        if self._is_editing and self._editing_backup is not None:
            self._fitted_results.insert(self._editing_index, self._editing_backup)
        self._reset_chain_state()

    def on_data_reloaded(self, widget):
        """Rebuild cache and redraw markers after lane/event change."""
        self._inflection_cache = ShockwaveComp.rebuild_inflection_cache(
            widget._series, self._slope_percentile
        )
        self._draw_inflection_markers(widget, validity_filter=None)
        self._clear_chain_items(widget)
        if self._is_editing and self._editing_backup is not None:
            self._fitted_results.insert(self._editing_index, self._editing_backup)
        self._reset_chain_state()

    # ── Slider hooks (called from TimeSpacePlotWidget) ───────────────────

    def on_slope_changed(self, widget, value: float):
        """Rebuild cache and redraw markers; leave fitted shockwaves alone."""
        self._slope_percentile = value
        self._inflection_cache = ShockwaveComp.rebuild_inflection_cache(
            widget._series, self._slope_percentile
        )
        self._clear_inflection_markers(widget)
        self._draw_inflection_markers(widget, validity_filter=None)

    def on_vicinity_changed(self, widget, value: float):
        """Store new vicinity; affects future BFS runs only (Automatic)."""
        self._vicinity = value

    # ── Ctrl+Right-click: delete a chain point or a committed shockwave ──

    def on_right_click(self, view_box, ev) -> bool:
        """Ctrl+Right-click deletes: the nearest point in the active chain
        if one is in progress (new construction or an edit), otherwise the
        nearest committed shockwave. A plain right-click (no Ctrl) is not
        handled here -- it falls through to the default context menu."""
        if not (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return False

        widget   = view_box.parent_widget
        mouse_pt = view_box.mapSceneToView(ev.scenePos())
        mx, my   = mouse_pt.x(), mouse_pt.y()

        if self._active_chain:
            self._delete_chain_point(mx, my, widget)
        else:
            result = self._hit_test_committed_shockwave(mx, my)
            if result is not None:
                self._delete_shockwave(result, widget)

        ev.accept()
        return True

    def _delete_chain_point(self, mx, my, widget):
        """Remove the active-chain point nearest (mx, my), if within
        click radius. Works identically whether the chain is a brand new
        shockwave under construction or a committed one re-opened for
        editing."""
        if not self._active_chain:
            return

        best_idx, best_dist = None, float('inf')
        for i, pt in enumerate(self._active_chain):
            d = math.hypot(pt.time - mx, pt.distance - my)
            if d < best_dist:
                best_dist, best_idx = d, i

        if best_idx is None or best_dist >= self._CLICK_RADIUS:
            return

        removed = self._active_chain.pop(best_idx)
        if not self._active_chain:
            self._reset_mode_state()
        else:
            self._sync_derived_state()

        self._refresh_highlights(widget)
        self._redraw_chain(widget)
        logger.debug(
            f"SW: ctrl+right-click erased veh {removed.veh_id} at t={removed.time:.2f}"
        )

    def _hit_test_committed_shockwave(self, mx, my):
        """Return the committed ShockwaveResult nearest (mx, my) if any of
        its points fall within click radius, else None."""
        best_result, best_dist = None, float('inf')
        for result in self._fitted_results:
            for (t, d) in result.points:
                dist = math.hypot(t - mx, d - my)
                if dist < best_dist:
                    best_dist, best_result = dist, result
        if best_result is not None and best_dist < self._CLICK_RADIUS:
            return best_result
        return None

    def _delete_shockwave(self, result, widget):
        self._fitted_results.remove(result)
        self._remove_shockwave_visual(widget, result.id)
        if self._panel_result_id == result.id:
            panel = self._ensure_result_panel(widget)
            panel.clear()
            self._panel_result_id = None
        logger.debug(f"SW: deleted shockwave {result.id} via ctrl+right-click")

    # ── Ctrl+Left-click (while idle): re-open a committed shockwave ──────

    def _try_enter_edit_mode(self, mx, my, widget, ctrl_held) -> bool:
        """Ctrl+Left-click, only while no chain is in progress, tries to
        grab a committed shockwave for editing. Returns True if edit mode
        was entered -- the caller should stop and skip normal point
        selection. Returns False otherwise, including a Ctrl+click that
        missed every shockwave, so an incidentally-held Ctrl key falls
        back to ordinary point selection instead of doing nothing."""
        if not ctrl_held or self._active_chain:
            return False
        result = self._hit_test_committed_shockwave(mx, my)
        if result is None:
            return False
        self._enter_edit_mode(result, widget)
        return True

    def _enter_edit_mode(self, result, widget):
        self._editing_index  = self._fitted_results.index(result)
        self._editing_backup = result
        self._editing_id     = result.id
        self._is_editing      = True

        self._fitted_results.remove(result)
        self._remove_shockwave_visual(widget, result.id)

        self._active_chain = list(result.source_points)
        self._seed_mode_state_from_chain(widget)

        panel = self._ensure_result_panel(widget)
        panel.enter_edit_mode(result.id)
        self._panel_result_id = result.id

        self._refresh_highlights(widget)
        self._redraw_chain(widget)
        logger.debug(f"SW: entered edit mode on shockwave {result.id}")

    def _revert_edit(self, widget):
        """Escape while editing: restore the original shockwave and panel."""
        result = self._editing_backup
        self._clear_chain_items(widget)
        self._fitted_results.insert(self._editing_index, result)
        self._draw_fitted_shockwave(widget, result)
        self._reset_chain_state()

        panel = self._ensure_result_panel(widget)
        panel.update_result(result)
        self._panel_result_id = result.id
        self._refresh_highlights(widget)
        logger.debug(f"SW: reverted edit on shockwave {result.id}")

    # ── Commit (Enter): shared by new construction and editing ──────────

    def _commit_active_chain(self, widget, mode):
        """Fit the active chain and store/redraw the result. A new chain
        gets the next stable id; an edit reuses and reinserts at its
        original id/position."""
        if len(self._active_chain) < 2:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                widget, "Shockwave", "Select at least 2 points before fitting."
            )
            return

        if self._is_editing:
            shockwave_id = self._editing_id
            insert_at    = self._editing_index
        else:
            shockwave_id = self._next_shockwave_id
            self._next_shockwave_id += 1
            insert_at = None

        points = [(p.time, p.distance) for p in self._active_chain]
        result = ShockwaveComp.fit_and_report(points, mode=mode, shockwave_id=shockwave_id)
        result.source_points = list(self._active_chain)
        result.point_type    = self._chain_point_type()

        if insert_at is not None:
            self._fitted_results.insert(insert_at, result)
        else:
            self._fitted_results.append(result)

        self._draw_fitted_shockwave(widget, result)
        panel = self._ensure_result_panel(widget)
        panel.update_result(result)
        self._panel_result_id = result.id

        self._clear_chain_items(widget)
        self._reset_chain_state()
        self._refresh_highlights(widget)
        logger.debug(f"SW: committed shockwave {result.id} ({mode}), R²={result.r_squared:.4f}")

    # ── Hooks for subclasses (base no-ops; see ShockwaveSemiAutoModeHandler) ─

    def _reset_mode_state(self):
        """Reset mode-specific derived state (locked color, adjacency,
        etc.) when the active chain becomes empty."""
        pass

    def _sync_derived_state(self):
        """Recompute mode-specific derived state from the new last point
        after a single point is removed from a non-empty active chain."""
        pass

    def _seed_mode_state_from_chain(self, widget):
        """Recompute mode-specific derived state after _active_chain is
        seeded from a re-opened shockwave's source_points."""
        pass

    def _chain_point_type(self):
        """The InflectionType shared by every point in the active chain,
        or None (Manual mode has no inherent color)."""
        return None

    def _refresh_highlights(self, widget):
        """Redraw inflection markers reflecting current chain state. Base
        implementation shows everything at full visibility; modes with
        adjacency/color locking override this to dim invalid points."""
        self._draw_inflection_markers(widget, validity_filter=None)

    # ── Chain state / visuals (shared by all modes) ──────────────────────

    def _reset_chain_state(self):
        self._active_chain.clear()
        self._is_editing     = False
        self._editing_id     = None
        self._editing_index  = None
        self._editing_backup = None
        self._reset_mode_state()

    def _redraw_chain(self, widget):
        """Draw filled circles at selected points and a line connecting them."""
        self._clear_chain_items(widget)
        if not self._active_chain:
            return

        times = [p.time     for p in self._active_chain]
        dists = [p.distance for p in self._active_chain]

        if len(self._active_chain) >= 2:
            pen  = pg.mkPen(color=self._COLOR_NEUTRAL, width=2, style=Qt.PenStyle.SolidLine)
            line = pg.PlotDataItem(times, dists, pen=pen)
            widget._scatter_plot.addItem(line)
            self._chain_items.append(line)

        dots = pg.ScatterPlotItem(
            x=times, y=dists,
            symbol='o', size=10,
            pen=pg.mkPen(color=self._COLOR_NEUTRAL),
            brush=pg.mkBrush(color=(*self._COLOR_NEUTRAL, 160)),
        )
        widget._scatter_plot.addItem(dots)
        self._chain_items.append(dots)

    def _clear_chain_items(self, widget):
        for item in self._chain_items:
            widget._scatter_plot.removeItem(item)
        self._chain_items.clear()

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
        for items in self._shockwave_items.values():
            for item in items:
                widget._scatter_plot.removeItem(item)
        self._shockwave_items.clear()

    def _remove_shockwave_visual(self, widget, shockwave_id):
        items = self._shockwave_items.pop(shockwave_id, [])
        for item in items:
            widget._scatter_plot.removeItem(item)

    def _draw_fitted_shockwave(self, widget, result: ShockwaveResult):
        """Draw the fitted line spanning only the time range of the points
        actually used, a dot at each of those points, and the shockwave's id
        labeled at the line's midpoint. Color follows the locked inflection
        type; Manual-mode results (point_type is None) fall back to neutral."""
        pt_times = [p[0] for p in result.points]
        pt_dists = [p[1] for p in result.points]
        t_range  = [min(pt_times), max(pt_times)]
        d_range  = [result.slope * t + result.intercept for t in t_range]
    
        if result.point_type == InflectionType.RELEASE:
            color = self._COLOR_RELEASE
        elif result.point_type == InflectionType.COMPRESSION:
            color = self._COLOR_COMPRESSION
        else:
            color = self._COLOR_NEUTRAL
    
        pen = pg.mkPen(color=color, width=3)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4, 4])   # longer dash, longer gap than the old DashLine default
        line = pg.PlotDataItem(t_range, d_range, pen=pen)
        widget._scatter_plot.addItem(line)
    
        dots = pg.ScatterPlotItem(
            x=pt_times, y=pt_dists,
            symbol='o', size=10,
            pen=pg.mkPen(color=color),
            brush=pg.mkBrush(color=(*color, 160)),
        )
        widget._scatter_plot.addItem(dots)
    
        mid_t = (t_range[0] + t_range[1]) / 2.0
        mid_d = (d_range[0] + d_range[1]) / 2.0
        label = pg.TextItem(text=f"SW {result.id}", color=color, anchor=(0.5, 1.0))
        label.setPos(mid_t, mid_d)
        widget._scatter_plot.addItem(label)
    
        self._shockwave_items[result.id] = [line, dots, label]

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


class ShockwaveSemiAutoModeHandler(ShockwaveModeHandler):
    """Semi-Automatic shockwave mode.

    The user clicks pre-marked inflection X markers one by one. After the
    first click, only same-color markers on the immediately adjacent
    trajectory are valid. Press Enter to fit; Escape to abandon a new
    chain or revert an edit; Ctrl+Left-click (while idle) re-opens a
    committed shockwave for editing; Ctrl+Right-click deletes a chain
    point (while constructing/editing) or a committed shockwave (while
    idle).
    """

    _MIN_TIME_SEPARATION = 0.1  # seconds

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._locked_color      = None   # InflectionType or None
        self._ordered_sequence: list = []   # veh_ids ordered by crossing time
        self._valid_next_traj_ids    = None  # set[int] or None (None = any)

    def on_enter(self, widget):
        super().on_enter(widget)   # builds cache, draws markers, shows sliders
        logger.debug("Entered Semi-Auto Shockwave mode")

    # ── CustomViewBox events ─────────────────────────────────────────────

    def on_mouse_release(self, view_box, ev) -> bool:
        """Left-click selects the nearest valid inflection point and adds
        it to the chain. Ctrl+Left-click, only while idle, instead tries
        to re-open a committed shockwave for editing; on a miss it falls
        back to normal point selection."""
        widget = view_box.parent_widget
        move_distance  = (ev.scenePos() - view_box._press_pos).manhattanLength()
        press_duration = time.time() - view_box._press_time

        if move_distance < 5 and press_duration < 0.5:
            mouse_pt  = view_box.mapSceneToView(ev.scenePos())
            mx, my    = mouse_pt.x(), mouse_pt.y()
            ctrl_held = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)

            if not self._try_enter_edit_mode(mx, my, widget, ctrl_held):
                best_pt, best_dist = self._find_nearest_valid_point(mx, my, widget)
                if best_pt is not None and best_dist < self._CLICK_RADIUS:
                    self._select_point(best_pt, widget)

        view_box._press_pos    = None
        view_box._press_time   = None
        view_box._is_selecting = False
        ev.accept()
        return True

    def on_key_press(self, widget, event) -> bool:
        key = event.key()

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit_active_chain(widget, mode="semi_auto")
            return True

        if key == Qt.Key.Key_Escape:
            if self._is_editing:
                self._revert_edit(widget)
                return True
            if self._active_chain:
                self._clear_chain_items(widget)
                self._reset_chain_state()
                self._refresh_highlights(widget)
                return True   # consumed; don't propagate to Select-mode clear
            return False      # empty chain: let widget handle Escape normally

        return False

    # ── Selection logic ──────────────────────────────────────────────────

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
        if not self._active_chain:
            self._locked_color = pt.type
            self._ordered_sequence = ShockwaveComp.build_trajectory_order(
                pt.distance, widget._series
            )
        self._active_chain.append(pt)
        used = {p.veh_id for p in self._active_chain}
        adjacent = ShockwaveComp.get_adjacent_trajectories(pt.veh_id, self._ordered_sequence)
        self._valid_next_traj_ids = adjacent - used
        self._refresh_highlights(widget)
        self._redraw_chain(widget)
        logger.debug(f"SW semi-auto: selected veh {pt.veh_id} at t={pt.time:.2f}")

    # ── Hooks back to the base class ─────────────────────────────────────

    def _reset_mode_state(self):
        self._locked_color        = None
        self._ordered_sequence    = []
        self._valid_next_traj_ids = None

    def _sync_derived_state(self):
        last = self._active_chain[-1]
        used = {p.veh_id for p in self._active_chain}
        adjacent = ShockwaveComp.get_adjacent_trajectories(last.veh_id, self._ordered_sequence)
        self._valid_next_traj_ids = adjacent - used
    
    def _seed_mode_state_from_chain(self, widget):
        if not self._active_chain:
            return
        first = self._active_chain[0]
        last  = self._active_chain[-1]
        self._locked_color     = first.type
        self._ordered_sequence = ShockwaveComp.build_trajectory_order(
            first.distance, widget._series
        )
        used = {p.veh_id for p in self._active_chain}
        adjacent = ShockwaveComp.get_adjacent_trajectories(last.veh_id, self._ordered_sequence)
        self._valid_next_traj_ids = adjacent - used

    def _chain_point_type(self):
        return self._locked_color

    def _refresh_highlights(self, widget):
        """Redraw X markers: full for valid selections, dimmed for invalid."""
        if not self._active_chain:
            self._draw_inflection_markers(widget, validity_filter=None, locked_color=None)
            return

        # Fix: only the trajectories adjacent to the chain's current end
        # are "available next" -- trajectories already used in the chain
        # must NOT stay full-color, or their other points look selectable
        # when clicking them would actually be rejected.
        full_set = self._valid_next_traj_ids
        self._draw_inflection_markers(
            widget,
            validity_filter=full_set,
            locked_color=self._locked_color,
            extra_filter=self._passes_time_separation,
        )