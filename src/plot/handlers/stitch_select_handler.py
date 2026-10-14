"""Trajectory Stitching selection mode (Task 9.2, "Operation").

Entered from the right-click menu with one trajectory already chosen.
Shows "Select trajectory to stitch", highlights the first trajectory, and
waits for a click on a second one, which opens the preview dialog.

If the first trajectory is itself the result of a stitch that can still be
undone, the banner also says "press Enter to edit", and Enter opens the
preview dialog on that stitch (review state, Undo enabled).

Cancel with Escape, right-click, or Mode > Select Trajectory. Cancelling
the preview dialog stays in this mode so another second trajectory can be
picked -- unless the dialog undid a stitch, which changes the trajectories
on the plot, so the mode ends and the plot refreshes. The lane may be
changed while waiting -- segments of one vehicle often break at a lane
change.
"""

import logging
import sqlite3

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QMessageBox

from .interaction_mode import InteractionModeHandler
from .plot_overlay_label import PlotOverlayLabel

logger = logging.getLogger(__name__)

_CLICK_MAX_MOVE = 5   # px; CustomViewBox starts a drag box beyond this
_TITLE = "Trajectory Stitching"


class StitchSelectModeHandler(InteractionModeHandler):

    def __init__(self, first_veh_id: int):
        self._first = first_veh_id
        self._banner = None
        self._highlight = None
        self._saved_selection = set()
        self._busy = False       # preview dialog open
        self._editable = False   # first trajectory is an undoable stitch

    # ── Lifecycle ──────────────────────────────────────────────────────

    def on_enter(self, widget):
        # Selection dimming would grey out everything on a lane where the
        # first trajectory isn't present; use a halo instead.
        self._saved_selection = set(widget._selected_series)
        widget._selected_series.clear()
        widget._update_plot_colors()
        self._editable = self._is_editable(widget)
        self._banner = PlotOverlayLabel(widget._scatter_plot)
        self._update_banner(widget)
        self._draw_highlight(widget)
        logger.debug("Entered stitch selection with vehicle %s (editable: %s)",
                     self._first, self._editable)

    def on_exit(self, widget):
        self._clear_highlight(widget)
        if self._banner is not None:
            self._banner.dispose()
            self._banner = None
        widget._selected_series = {v for v in self._saved_selection if v in widget._series}
        widget._update_plot_colors()

    def on_plot_cleared(self, widget):
        self._highlight = None   # scatter_plot.clear() already removed it

    def on_data_reloaded(self, widget):
        self._draw_highlight(widget)
        self._update_banner(widget)

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
        if veh_id == self._first:
            hint = " or press <b>Enter</b> to edit its stitch" if self._editable else ""
            self._banner.flash(f"Choose a <b>different</b> trajectory to stitch with{hint}.")
            return True

        self._busy = True
        QTimer.singleShot(0, lambda: self._open_preview(widget, veh_id))
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
            widget.end_stitch_selection()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._editable:
            self._busy = True
            QTimer.singleShot(0, lambda: self._open_edit(widget))
            return True
        return False

    # ── Preview ────────────────────────────────────────────────────────

    def _is_editable(self, widget) -> bool:
        from analysis.manual_stitching import ManualStitchingComp
        try:
            return ManualStitchingComp.stitch_record_for(
                widget._db_connection, widget._current_event_id, self._first) is not None
        except (sqlite3.Error, ValueError):
            logger.exception("Could not look up the stitch of vehicle %s", self._first)
            return False

    def _open_preview(self, widget, second_veh_id: int):
        """New stitch of the first trajectory with *second_veh_id*."""
        from analysis.manual_stitching import ManualStitchingComp, StitchingPreviewDialog
        try:
            inputs = ManualStitchingComp.prepare(
                widget._db_connection, widget._current_event_id,
                self._first, second_veh_id)
        except (ValueError, sqlite3.Error) as exc:
            self._busy = False
            QMessageBox.warning(widget, _TITLE, str(exc))
            return

        dlg = StitchingPreviewDialog(widget, widget._db_connection, inputs,
                                     resolve_target=widget.stitch_target_resolver())
        self._finish(widget, dlg, dlg.exec() == QDialog.DialogCode.Accepted)

    def _open_edit(self, widget):
        """Review (and possibly undo) the stitch that created the first
        trajectory."""
        from analysis.manual_stitching import ManualStitchingComp, StitchingPreviewDialog
        try:
            inputs, record = ManualStitchingComp.prepare_existing(
                widget._db_connection, widget._current_event_id, self._first)
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
        Nothing changed: stay in selection mode."""
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

    def _on_lane(self, widget) -> bool:
        series = widget._series.get(self._first) if widget._series else None
        return series is not None and series["plot_item"] is not None

    def _update_banner(self, widget):
        if self._banner is None:
            return
        where = "" if self._on_lane(widget) else " (not on this lane)"
        edit = (f"<br>Vehicle {self._first} is a stitched trajectory — "
                f"press <b>Enter</b> to edit" if self._editable else "")
        self._banner.show_message(
            f"<b>Select trajectory to stitch</b><br>"
            f"with vehicle {self._first}{where} — Esc or right-click to cancel"
            f"{edit}")

    def _draw_highlight(self, widget):
        self._clear_highlight(widget)
        if not self._on_lane(widget):
            return
        series = widget._series[self._first]
        self._highlight = pg.PlotDataItem(
            series["veh_time"], series["veh_dist_trav"],
            pen=pg.mkPen(color=(255, 165, 0, 150), width=18))
        self._highlight.setZValue(-10)   # halo beneath the scatter points
        widget._scatter_plot.addItem(self._highlight)

    def _clear_highlight(self, widget):
        if self._highlight is not None:
            widget._scatter_plot.removeItem(self._highlight)
            self._highlight = None
