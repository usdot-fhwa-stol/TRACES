"""
Gap Headway interaction mode handler.  All state that previously lived
as _gh_overlay_items / _gh_locked_items / _gh_saved_keys on
TimeSpacePlotWidget now lives here.
"""

import logging
import math
import time

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt

from .interaction_mode import InteractionModeHandler

logger = logging.getLogger(__name__)


class GapHeadwayModeHandler(InteractionModeHandler):

    def __init__(self):
        self._overlay_items = []   # transient hover items
        self._locked_items  = {}   # (veh_id, veh_time) -> list[pg items]
        self._saved_keys    = set() # set of (veh_id, veh_time) tuples

    # ── Lifecycle ────────────────────────────────────────────────────────

    def on_enter(self, widget):
        """Restore locked overlays from saved keys.

        Body is the moved body of TimeSpacePlotWidget._restore_gh_locked.
        Two substitutions throughout:
            self._series          → widget._series
            self._scatter_plot    → widget._scatter_plot
        Also replace all _gh_* attribute access with handler-local names:
            self._gh_saved_keys   → self._saved_keys
            self._gh_locked_items → self._locked_items
        And forward _find_gap_hway / _draw_gh_overlay calls:
            self._find_gap_hway(...)     → self._find_gap_hway(widget, ...)
            self._draw_gh_overlay(...)   → self._draw_gh_overlay(widget, ...)
        """
        for veh_id, veh_time in self._saved_keys:
            series = widget._series.get(veh_id)
            if series is None:
                continue
            mask = series["veh_time"] == veh_time
            dists = series["veh_dist_trav"][mask]
            if len(dists) == 0:
                continue
            veh_dist = float(dists[0])
            gap_f, gap_b, hway_f, hway_b = self._find_gap_hway(
                widget, veh_id, veh_time, veh_dist
            )
            locked_items = []
            self._draw_gh_overlay(
                widget, veh_id, veh_time, veh_dist,
                gap_b, gap_f, hway_b, hway_f,
                target_list=locked_items
            )
            self._locked_items[(veh_id, veh_time)] = locked_items
        logger.debug(f"GH restored {len(self._saved_keys)} locked points")

    def on_exit(self, widget):
        """Save locked point keys, then remove all overlay graphics.

        Body is the moved 'else' branch of
        TimeSpacePlotWidget._on_mode_changed (the mode != 'gap_headway'
        path), with the same substitutions as on_enter above.
        """
        self._clear_overlay(widget)
        self._saved_keys = set(self._locked_items.keys())
        self._clear_all_locked(widget)
        widget._tooltip.hide()
        logger.debug("GH mode exited")

    def on_plot_cleared(self, widget):
        """scatter_plot.clear() already removed the pg items from the scene;
        just drop the stale references so nothing tries to remove them again."""
        self._overlay_items.clear()
        self._locked_items.clear()

    def on_data_reloaded(self, widget):
        """Restore locked overlays after a lane/event data reload."""
        if self._saved_keys:
            self.on_enter(widget)

    # ── CustomViewBox-originated events ─────────────────────────────────

    def on_mouse_move(self, view_box, ev) -> bool:
        """Suppress selection-box drawing in GH mode."""
        ev.accept()
        return True

    def on_mouse_release(self, view_box, ev) -> bool:
        """Lock or unlock a clicked point.

        Body is the moved GH branch of CustomViewBox.mouseReleaseEvent.
        Three substitutions throughout:
            self.parent_widget._gh_locked_items → self._locked_items
            self.parent_widget._gh_saved_keys   → self._saved_keys
            self.parent_widget._find_gap_hway   → self._find_gap_hway(widget, ...)
            self.parent_widget._draw_gh_overlay → self._draw_gh_overlay(widget, ...)
            self.parent_widget._clear_gh_locked_point → self._clear_locked_point(widget, ...)
        'self.parent_widget' references that remain become 'widget'.
        """
        widget = view_box.parent_widget
        move_distance  = (ev.scenePos() - view_box._press_pos).manhattanLength()
        press_duration = time.time() - view_box._press_time

        if move_distance < 5 and press_duration < 0.5:
            mouse_point = view_box.mapSceneToView(ev.scenePos())
            mx, my = mouse_point.x(), mouse_point.y()
            clicked_veh = view_box._find_clicked_vehicle(mx, my)

            if clicked_veh is not None:
                series   = widget._series.get(clicked_veh)
                time_tol = 0.05
                veh_time = veh_dist = None

                if series is not None:
                    mask = ((series["veh_time"] >= mx - time_tol) &
                            (series["veh_time"] <= mx + time_tol))
                    x_f = series["veh_time"][mask]
                    y_f = series["veh_dist_trav"][mask]
                    if len(x_f) > 0:
                        idx      = int(np.argmin(np.abs(y_f - my)))
                        veh_time = float(x_f[idx])
                        veh_dist = float(y_f[idx])

                if veh_time is not None:
                    pt_key = (clicked_veh, veh_time)

                    if pt_key in self._locked_items:
                        self._clear_locked_point(widget, pt_key)
                        widget._last_queried_point = None
                        logger.debug(f"GH unlocked point {pt_key}")
                    else:
                        self._saved_keys.add(pt_key)
                        locked_items_for_pt = []
                        gap_f, gap_b, hway_f, hway_b = self._find_gap_hway(
                            widget, clicked_veh, veh_time, veh_dist
                        )
                        self._draw_gh_overlay(
                            widget, clicked_veh, veh_time, veh_dist,
                            gap_b, gap_f, hway_b, hway_f,
                            target_list=locked_items_for_pt
                        )
                        self._locked_items[pt_key] = locked_items_for_pt
                        widget._last_queried_point = (
                            clicked_veh, veh_time,
                            widget._current_event_id
                        )
                        logger.debug(f"GH locked point {pt_key}")

        # Always clean up press state and consume the event in GH mode,
        # regardless of whether a vehicle was actually clicked.
        view_box._press_pos    = None
        view_box._press_time   = None
        view_box._is_selecting = False
        ev.accept()
        return True

    # ── TimeSpacePlotWidget-originated events ───────────────────────────

    def on_hover(self, widget, pos, closest_data, min_dist) -> bool:
        """Draw / clear transient gap-headway overlay on mouse movement.

        Body is the moved 'if self._gh_mode_enabled:' block from
        TimeSpacePlotWidget._handle_mouse_moved, including the else/hide
        branch that was previously outside the quoted excerpt.
        Substitutions:
            self._gh_locked_items  → self._locked_items
            self._gh_overlay_items → self._overlay_items
            self._clear_gh_overlay() → self._clear_overlay(widget)
            self._find_gap_hway(...) → self._find_gap_hway(widget, ...)
            self._draw_gh_overlay(...) → self._draw_gh_overlay(widget, ...)
        All other 'self.' references become 'widget.'.
        """
        pixel_tolerance_show = 3

        if closest_data and min_dist < pixel_tolerance_show:
            veh_id, _, _, veh_time, veh_dist = closest_data
            current_point = (veh_id, veh_time, widget._current_event_id)
            pt_key = (veh_id, veh_time)
            is_locked = pt_key in self._locked_items

            if is_locked:
                widget._last_queried_point = current_point
            else:
                if widget._last_queried_point != current_point:
                    gap_f, gap_b, hway_f, hway_b = self._find_gap_hway(
                        widget, veh_id, veh_time, veh_dist
                    )
                    self._clear_overlay(widget)
                    self._draw_gh_overlay(
                        widget, veh_id, veh_time, veh_dist,
                        gap_b, gap_f, hway_b, hway_f,
                        target_list=self._overlay_items
                    )
                    widget._last_queried_point = current_point
        else:
            # Mouse not near any point — clear transient overlay.
            self._clear_overlay(widget)
            widget._last_queried_point = None

        widget._tooltip.hide()
        return True

    def on_key_press(self, widget, event) -> bool:
        """Escape clears all locked points.

        Body is the moved GH branch of TimeSpacePlotWidget.keyPressEvent.
        """
        self._clear_all_locked(widget)
        self._saved_keys.clear()
        widget._last_queried_point = None
        logger.debug("Cleared all GH locked points on Escape")
        return True

    def _find_gap_hway(self, widget, veh_id: int,
                        veh_time: float, veh_dist: float) -> tuple:
            """Find gap and headway in a single pass over segments.
    
            For each other vehicle's segment, first attempts a gap measurement
            if the segment brackets veh_time.  When a gap is found the segment
            extents are narrowed:
    
            - Forward gap (vehicle ahead at veh_time): it hasn't reached veh_dist
              yet, so the crossing must be after veh_time.  Narrow the search
              window to [gap_idx, seg_end) by advancing seg_start and updating
              t_min / d_min.
            - Backward gap (vehicle behind at veh_time): it already passed
              veh_dist, so the crossing was before veh_time.  Narrow the search
              window to [seg_start, gap_idx+2) by pulling back seg_end and
              updating t_max / d_max.
    
            After narrowing, forward headway (hway_f) and backward headway
            (hway_b) are each checked independently against the (possibly
            narrowed) extents and scanned in their respective directions.
            Segments where no gap was found still get the full headway scan
            with original extents.
    
            Returns ``float('inf')`` for any direction with no candidate.
            ``_draw_gh_overlay`` already handles this via its ``_finite()``
            helper which returns ``None`` for non-finite values.
    
            Args:
                veh_id:   Vehicle ID of the subject point (skipped).
                veh_time: Time coordinate of the subject point (seconds).
                veh_dist: Distance coordinate of the subject point (metres).
    
            Returns:
                ``(gap_f, gap_b, hway_f, hway_b)`` as positive magnitudes,
                or ``float('inf')`` for any direction with no candidate.
            """
            INF = float('inf')
            gap_f = INF
            gap_b = INF
            hway_f = INF
            hway_b = INF
    
            for sid, series in widget._series.items():
                if sid == veh_id:
                    continue
    
                t_arr = series['veh_time']
                d_arr = series['veh_dist_trav']

                for seg_start, seg_end, t_min, t_max, d_min, d_max, seg_t, seg_d in series.get('segments', []):
    
                    # ── Gap check: does this segment bracket veh_time? ────────
                    if t_min <= veh_time <= t_max:
                        idx = int(np.searchsorted(seg_t, veh_time, side='left'))
                        idx = min(idx, len(seg_t) - 1)
    
                        if seg_t[idx] == veh_time:
                            diff = float(seg_d[idx]) - veh_dist
                            abs_idx = seg_start + idx
                        else:
                            lo, hi = idx - 1, idx
                            pct = (veh_time - seg_t[lo]) / (seg_t[hi] - seg_t[lo])
                            diff = float(seg_d[lo] + pct * (seg_d[hi] - seg_d[lo])) - veh_dist
                            abs_idx = seg_start + lo

                        if diff > 0:
                            if diff < gap_f:
                                gap_f = diff
                            # Vehicle ahead → crossing is after veh_time
                            seg_end = min(abs_idx + 2, seg_end)
                            t_max = float(t_arr[seg_end - 1])
                            d_max = float(d_arr[seg_end - 1])

                        elif diff < 0:
                            abs_diff = -diff
                            if abs_diff < gap_b:
                                gap_b = abs_diff
                            # Vehicle behind → crossing was before veh_time
                            seg_start = abs_idx
                            t_min = float(t_arr[abs_idx])
                            d_min = float(d_arr[abs_idx])
    
                    # ── Forward headway: scan backward for crossing before veh_time
                    if d_min <= veh_dist <= d_max:
                        for i in range(seg_end - 2, seg_start - 1, -1):
                            d0 = float(d_arr[i])
                            d1 = float(d_arr[i + 1])
                            if not ((d0 <= veh_dist <= d1) or (d1 <= veh_dist <= d0)):
                                continue
                            t0 = float(t_arr[i])
                            t1 = float(t_arr[i + 1])
                            span = d1 - d0
                            if span == 0.0:
                                pct = 1.0
                            else:
                                pct = max(0.0, min(1.0, (veh_dist - d0) / span))
                            crossing_time = t0 + pct * (t1 - t0)
                            diff = veh_time - crossing_time
                            if diff > 0.0:
                                if diff < hway_f:
                                    hway_f = diff
                                break
    
                    # ── Backward headway: scan forward for crossing after veh_time
                    if d_min <= veh_dist <= d_max:
                        for i in range(seg_start, seg_end - 1):
                            d0 = float(d_arr[i])
                            d1 = float(d_arr[i + 1])
                            if not ((d0 <= veh_dist <= d1) or (d1 <= veh_dist <= d0)):
                                continue
                            t0 = float(t_arr[i])
                            t1 = float(t_arr[i + 1])
                            span = d1 - d0
                            if span == 0.0:
                                pct = 0.0
                            else:
                                pct = max(0.0, min(1.0, (veh_dist - d0) / span))
                            crossing_time = t0 + pct * (t1 - t0)
                            diff = crossing_time - veh_time
                            if diff > 0.0:
                                if diff < hway_b:
                                    hway_b = diff
                                break
    
            return gap_f, gap_b, hway_f, hway_b

    def _draw_gh_overlay(self, widget, veh_id: int,
                          veh_time: float, veh_dist: float,
                          gap_b, gap_f, hway_b, hway_f, *,
                          target_list: list):
            """Draw gap and headway lines + labels for the given point.
    
            Items are appended to `target_list` (either _gh_overlay_items for
            transient hover lines or _gh_locked_items for persistent locked lines)
            so the two sets can be cleared independently.  The caller is
            responsible for clearing the appropriate list before calling.
    
            Axes convention (same as the plot):
                x-axis → time (s)
                y-axis → distance (m)
    
            gap_b / gap_f  are distances (m) → drawn as vertical segments
                            (same time, ± distance).
            hway_b / hway_f are times   (s) → drawn as horizontal segments
                            (same distance, ± time).
    
            Args:
                veh_id      : vehicle identifier (used only for logging).
                veh_time    : x-coordinate of the source point (s).
                veh_dist    : y-coordinate of the source point (m).
                gap_b, gap_f   : backward/forward spatial gap (m); may be None/NaN.
                hway_b, hway_f : backward/forward time headway (s); may be None/NaN.
                target_list    : list to append created pg items to.
            """
            NUDGE_PX = 10
            LINE_PEN    = pg.mkPen(color=(120, 120, 120), width=3,
                                   style=Qt.PenStyle.SolidLine)
            LABEL_COLOR = (80, 80, 80)
    
            def _add_line(x0, y0, x1, y1):
                item = pg.PlotDataItem([x0, x1], [y0, y1], pen=LINE_PEN)
                widget._scatter_plot.addItem(item)
                target_list.append(item)

            def _add_label(x, y, text, anchor, angle=0, dx_px=0, dy_px=0):
                item = pg.TextItem(text=text, color=LABEL_COLOR, anchor=anchor,
                                   angle=angle)
                item.setFont(pg.QtGui.QFont("Arial", 8))
                if dx_px != 0 or dy_px != 0:
                    px_w, px_h = widget._scatter_plot.getViewBox().viewPixelSize()
                    x = x + dx_px * px_w
                    y = y + dy_px * px_h
                item.setPos(x, y)
                widget._scatter_plot.addItem(item)
                target_list.append(item)
                
            # ── vertical lines (gap — same time, different distance) ─────────────
            #
            # gap_f: other vehicle is at higher dist at same time → line goes UP
            #        label: rotated 90°, right of and above source point
            # gap_b: other vehicle is at lower dist at same time  → line goes DOWN
            #        label: rotated 90°, left of and below source point
    
            if math.isfinite(gap_f):
                y_end = veh_dist + gap_f
                _add_line(veh_time, veh_dist, veh_time, y_end)
                _add_label(veh_time, veh_dist,
                           f"{gap_f:.2f} m",
                           anchor=(0.0, 0.0),
                           angle=90,
                           dy_px=NUDGE_PX)
    
            if math.isfinite(gap_b):
                y_end = veh_dist - gap_b
                _add_line(veh_time, veh_dist, veh_time, y_end)
                _add_label(veh_time, veh_dist,
                           f"{gap_b:.2f} m",
                           anchor=(1.0, 1.0),
                           angle=90,
                           dy_px=-NUDGE_PX)
    
            # ── horizontal lines (headway — same distance, different time) ───────
            #
            # hway_f: other vehicle passed this distance earlier → line goes LEFT
            #         label: left of and above source point
            # hway_b: other vehicle passed this distance later   → line goes RIGHT
            #         label: right of and below source point
    
            if math.isfinite(hway_f):
                x_end = veh_time - hway_f
                _add_line(veh_time, veh_dist, x_end, veh_dist)
                _add_label(veh_time, veh_dist,
                           f"{hway_f:.2f} s",
                           anchor=(1.0, 1.0),
                           dx_px=-NUDGE_PX)
    
            if math.isfinite(hway_b):
                x_end = veh_time + hway_b
                _add_line(veh_time, veh_dist, x_end, veh_dist)
                _add_label(veh_time, veh_dist,
                           f"{hway_b:.2f} s",
                           anchor=(0.0, 0.0),
                           dx_px=NUDGE_PX)
    
            logger.debug(
                f"Drew GH overlay for veh {veh_id} at t={veh_time:.2f}: "
                f"gap_b={gap_b}, gap_f={gap_f}, hway_b={hway_b}, hway_f={hway_f}"
            )


    # ── Item management helpers ──────────────────────────────────────────

    def _clear_overlay(self, widget):
        for item in self._overlay_items:
            widget._scatter_plot.removeItem(item)
        self._overlay_items.clear()

    def _clear_all_locked(self, widget):
        for items in self._locked_items.values():
            for item in items:
                widget._scatter_plot.removeItem(item)
        self._locked_items.clear()

    def _clear_locked_point(self, widget, pt_key: tuple):
        self._saved_keys.discard(pt_key)
        items = self._locked_items.pop(pt_key, [])
        for item in items:
            widget._scatter_plot.removeItem(item)