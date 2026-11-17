"""Trajectory Stitching selection mode (Task 9.2, "Operation").

Entered from the right-click menu (Stitching > Basic Stitching) with one
trajectory already chosen. Every picked trajectory gets an orange halo
drawn along its whole trajectory -- every lane, not just the lane on
show -- since segments of one vehicle often break at a lane change.

Picks
-----
The handler tracks the two most recently picked trajectories, oldest
first. Clicking a trajectory adds it as the most recent pick; with two
already picked, the oldest is dropped (3050, then 120, then 402 leaves
120 and 402; then 7170 leaves 402 and 7170). Clicking a picked
trajectory again makes it the most recent pick. Nothing opens on a
click.

Keys
----
Enter   Two picks: open the preview dialog on the pair.
        One pick that is itself the result of a stitch that can still be
        undone: open the preview dialog on that stitch (review state,
        Undo enabled).
Escape  Remove the most recent pick. Removing the only pick leaves the
        mode.
Right-click, or Mode > Select Trajectory, leaves the mode.

Cancelling the preview dialog stays in this mode with the same picks, so
either can be swapped before trying again -- unless the dialog undid a
stitch, which changes the trajectories on the plot, so the mode ends and
the plot refreshes. The lane may be changed while picking.
"""

import logging
import sqlite3
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QMessageBox

from utils import events_columns, measured_rows_clause, segment_bounds
from .interaction_mode import InteractionModeHandler
from .plot_overlay_label import PlotOverlayLabel

logger = logging.getLogger(__name__)

_CLICK_MAX_MOVE = 5   # px; CustomViewBox starts a drag box beyond this
_TITLE = "Trajectory Stitching"
_MAX_PICKS = 2
_HALO_PEN = pg.mkPen(color=(255, 165, 0, 150), width=18)


class StitchSelectModeHandler(InteractionModeHandler):

    def __init__(self, first_veh_id: int):
        self._picks: List[int] = [first_veh_id]   # oldest first, at most _MAX_PICKS
        self._banner = None
        self._halos: Dict[int, pg.PlotDataItem] = {}
        # veh_id -> (times, distances) over all lanes, NaN-separated at
        # time gaps. Trajectories don't change while this mode is active
        # (a stitch or undo ends it), so they're loaded once per vehicle.
        self._trajectories: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        self._editable: Dict[int, bool] = {}
        self._saved_selection = set()
        self._busy = False       # preview dialog open

    # ── Lifecycle ──────────────────────────────────────────────────────

    def on_enter(self, widget):
        # Selection dimming would grey out everything on a lane where a
        # picked trajectory isn't present; use halos instead.
        self._saved_selection = set(widget._selected_series)
        widget._selected_series.clear()
        widget._update_plot_colors()
        self._banner = PlotOverlayLabel(widget._scatter_plot)
        self._refresh(widget)
        logger.debug("Entered stitch selection with vehicle %s", self._picks[0])

    def on_exit(self, widget):
        self._clear_halos(widget)
        if self._banner is not None:
            self._banner.dispose()
            self._banner = None
        widget._selected_series = {v for v in self._saved_selection if v in widget._series}
        widget._update_plot_colors()

    def on_plot_cleared(self, widget):
        self._halos.clear()   # scatter_plot.clear() already removed them

    def on_data_reloaded(self, widget):
        self._refresh(widget)

    # ── Mouse / keys ──────────────────────────────────────────────────

    def on_mouse_move(self, view_box, ev) -> bool:
        ev.accept()          # no drag-selection box in this mode
        return True

    def on_mouse_release(self, view_box, ev) -> bool:
        widget = view_box.parent_widget
        press_pos = view_box._press_pos
        view_box._press_pos = None
        view_box._press_time = None
        view_box._is_selecting = False
        ev.accept()

        if self._busy or press_pos is None:
            return True
        if (ev.scenePos() - press_pos).manhattanLength() > _CLICK_MAX_MOVE:
            return True      # a drag, not a click

        pt = view_box.mapSceneToView(ev.scenePos())
        veh_id = widget.find_vehicle_at(pt.x(), pt.y())
        if veh_id is None:
            return True
        if self._picks == [veh_id]:
            hint = (" or press <b>Enter</b> to edit its stitch"
                    if self._is_editable(widget, veh_id) else "")
            self._banner.flash(f"Choose a <b>different</b> trajectory to stitch with{hint}.")
            return True

        self._pick(veh_id)
        self._refresh(widget)
        return True

    def on_right_click(self, view_box, ev) -> bool:
        if not self._busy:
            QTimer.singleShot(0, view_box.parent_widget.end_stitch_selection)
        return True          # suppress the context menu in this mode

    def on_key_press(self, widget, event) -> bool:
        if self._busy:
            return False
        key = event.key()
        if key == Qt.Key.Key_Escape:
            dropped = self._picks.pop()
            logger.debug("Stitch selection: removed vehicle %s", dropped)
            if not self._picks:
                widget.end_stitch_selection()
            else:
                self._refresh(widget)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if len(self._picks) == _MAX_PICKS:
                first, second = self._picks
                self._busy = True
                QTimer.singleShot(0, lambda: self._open_preview(widget, first, second))
            elif self._is_editable(widget, self._picks[0]):
                veh_id = self._picks[0]
                self._busy = True
                QTimer.singleShot(0, lambda: self._open_edit(widget, veh_id))
            else:
                self._banner.flash("Select a <b>second</b> trajectory first.")
            return True
        return False

    # ── Picks ─────────────────────────────────────────────────────────

    def _pick(self, veh_id: int):
        """Make *veh_id* the most recent pick, dropping the oldest beyond
        _MAX_PICKS."""
        if veh_id in self._picks:
            self._picks.remove(veh_id)
        self._picks.append(veh_id)
        del self._picks[:-_MAX_PICKS]
        logger.debug("Stitch selection picks: %s", self._picks)

    def _is_editable(self, widget, veh_id: int) -> bool:
        """True if *veh_id* is the result of a stitch that can still be
        undone. Cached per vehicle for the life of the mode."""
        if veh_id not in self._editable:
            from analysis.manual_stitching import ManualStitchingComp
            try:
                self._editable[veh_id] = ManualStitchingComp.stitch_record_for(
                    widget._db_connection, widget._current_event_id, veh_id) is not None
            except (sqlite3.Error, ValueError):
                logger.exception("Could not look up the stitch of vehicle %s", veh_id)
                self._editable[veh_id] = False
        return self._editable[veh_id]

    # ── Preview ────────────────────────────────────────────────────────

    def _open_preview(self, widget, first_veh_id: int, second_veh_id: int):
        """New stitch of the two picked trajectories."""
        from analysis.manual_stitching import ManualStitchingComp, StitchingPreviewDialog
        try:
            inputs = ManualStitchingComp.prepare(
                widget._db_connection, widget._current_event_id,
                first_veh_id, second_veh_id)
        except (ValueError, sqlite3.Error) as exc:
            self._busy = False
            QMessageBox.warning(widget, _TITLE, str(exc))
            return

        dlg = StitchingPreviewDialog(widget, widget._db_connection, inputs,
                                     resolve_target=widget.stitch_target_resolver())
        self._finish(widget, dlg, dlg.exec() == QDialog.DialogCode.Accepted)

    def _open_edit(self, widget, veh_id: int):
        """Review (and possibly undo) the stitch that created *veh_id*."""
        from analysis.manual_stitching import ManualStitchingComp, StitchingPreviewDialog
        try:
            inputs, record = ManualStitchingComp.prepare_existing(
                widget._db_connection, widget._current_event_id, veh_id)
        except (ValueError, sqlite3.Error) as exc:
            self._busy = False
            QMessageBox.warning(widget, _TITLE, str(exc))
            return

        dlg = StitchingPreviewDialog(widget, widget._db_connection, inputs,
                                     resolve_target=widget.stitch_target_resolver(),
                                     stitch_record=record)
        self._finish(widget, dlg, dlg.exec() == QDialog.DialogCode.Accepted)

    def _finish(self, widget, dlg, accepted: bool):
        """Apply what the dialog changed -- an undo, a stitch, or both (an
        undo followed by a new stitch of the restored pair) -- to the plot.
        Nothing changed: stay in selection mode with the same picks."""
        self._busy = False
        undo = dlg.undo_info
        result = dlg.result_info if accepted else None
        if undo is None and result is None:
            return

        added, removed, select = set(), set(), []
        if undo is not None:
            removed.add(undo.removed_veh_id)
            added.update(undo.restored_veh_ids)
            select = list(undo.restored_veh_ids)
        if result is not None:
            removed.update(result.source_veh_ids)
            added.difference_update(result.source_veh_ids)
            added.add(result.new_veh_id)
            select = [result.new_veh_id]

        widget.end_stitch_selection()
        widget.refresh_after_stitch_change(added, removed, select)

    # ── Visuals ────────────────────────────────────────────────────────

    def _refresh(self, widget):
        self._draw_halos(widget)
        self._update_banner(widget)

    def _update_banner(self, widget):
        if self._banner is None:
            return
        if len(self._picks) == _MAX_PICKS:
            first, recent = self._picks
            self._banner.show_message(
                f"<b>Vehicles {first} and {recent} selected</b> — press "
                f"<b>Enter</b> to open the stitching preview<br>"
                f"Click another trajectory to replace vehicle {first} — "
                f"Esc removes vehicle {recent} — right-click to cancel")
            return
        only = self._picks[0]
        edit = (f"<br>Vehicle {only} is a stitched trajectory — "
                f"press <b>Enter</b> to edit" if self._is_editable(widget, only) else "")
        self._banner.show_message(
            f"<b>Select trajectory to stitch</b><br>"
            f"with vehicle {only} — Esc or right-click to cancel{edit}")

    def _trajectory(self, widget, veh_id: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """*veh_id*'s points on every lane, ordered by time, with a NaN
        between segments separated by a time gap so the halo breaks there
        instead of bridging it. Same rows the plot shows (SplineComp
        synthetic rows excluded)."""
        if veh_id not in self._trajectories:
            cols = events_columns(widget._db_connection)
            try:
                rows = widget._db_connection.execute(
                    f"SELECT veh_time, veh_dist_trav FROM events "
                    f"WHERE event_id = ? AND veh_id = ? AND veh_dist_trav IS NOT NULL"
                    f"{measured_rows_clause(cols)} ORDER BY veh_time",
                    (widget._current_event_id, veh_id)).fetchall()
            except sqlite3.Error:
                logger.exception("Could not load the trajectory of vehicle %s", veh_id)
                rows = []
            if not rows:
                self._trajectories[veh_id] = None
            else:
                t = np.array([r[0] for r in rows], dtype=np.float64)
                d = np.array([r[1] for r in rows], dtype=np.float64)
                xs, ys = [], []
                for start, end in segment_bounds(t, widget._time_step):
                    if xs:
                        xs.append(np.array([np.nan]))
                        ys.append(np.array([np.nan]))
                    xs.append(t[start:end])
                    ys.append(d[start:end])
                self._trajectories[veh_id] = (np.concatenate(xs), np.concatenate(ys))
        return self._trajectories[veh_id]

    def _draw_halos(self, widget):
        self._clear_halos(widget)
        for veh_id in self._picks:
            data = self._trajectory(widget, veh_id)
            if data is None:
                continue
            halo = pg.PlotDataItem(data[0], data[1], pen=_HALO_PEN, connect="finite")
            halo.setZValue(-10)   # beneath the scatter points
            widget._scatter_plot.addItem(halo)
            self._halos[veh_id] = halo

    def _clear_halos(self, widget):
        for halo in self._halos.values():
            widget._scatter_plot.removeItem(halo)
        self._halos.clear()
