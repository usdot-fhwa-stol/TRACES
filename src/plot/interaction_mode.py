"""
Defines InteractionMode and the InteractionModeHandler base class.
Both TimeSpacePlotWidget and CustomViewBox import from here so neither
file owns the shared type.
"""

from enum import Enum


class InteractionMode(Enum):
    SELECT             = "select"
    GAP_HEADWAY        = "gap_headway"
    SHOCKWAVE_MANUAL   = "shockwave_manual"
    SHOCKWAVE_SEMI_AUTO = "shockwave_semi_auto"
    SHOCKWAVE_AUTOMATIC = "shockwave_automatic"


class InteractionModeHandler:
    """Base class for time-space plot interaction mode handlers.

    All hooks default to "not handled" (return False / no-op), so a mode
    that doesn't need a hook simply inherits the no-op.  Select mode
    requires no subclass — InteractionModeHandler() is sufficient.

    Return-value convention
    -----------------------
    Hooks that return bool should return True to consume the event and
    suppress the default behavior, False to fall through.  The hook is
    responsible for calling ev.accept() before returning True.
    """

    # ── Lifecycle ────────────────────────────────────────────────────────

    def on_enter(self, widget):
        """Called once when this mode becomes active."""
        pass

    def on_exit(self, widget):
        """Called once when switching away from this mode."""
        pass

    def on_plot_cleared(self, widget):
        """Called immediately after scatter_plot.clear() in
        _update_plot_data.  Handlers that cache pg item references must
        clear those caches here (items are already gone from the scene)."""
        pass

    def on_data_reloaded(self, widget):
        """Called after _update_plot_data finishes loading new data.
        Handlers that restore visual state from saved keys do so here."""
        pass

    def on_before_data_reload(self, widget):
        """Called at the very start of _update_plot_data, before
        scatter_plot.clear() runs and before any new data is loaded for the
        new lane/event. Handlers holding state that becomes stale or
        inapplicable after such a reload -- and that the user might want to
        save first -- hook in here. Default is a no-op."""
        pass

    # ── CustomViewBox-originated events ─────────────────────────────────

    def on_mouse_press(self, view_box, ev) -> bool:
        return False

    def on_mouse_move(self, view_box, ev) -> bool:
        """Return True to suppress the default selection-box drag."""
        return False

    def on_mouse_release(self, view_box, ev) -> bool:
        """Return True to suppress the default click-to-select / box-select."""
        return False

    def on_right_click(self, view_box, ev) -> bool:
        """Return True to suppress the context menu. Reserved for
        Shockwave's undo-on-right-click; no current mode overrides this."""
        return False

    # ── TimeSpacePlotWidget-originated events ───────────────────────────

    def on_hover(self, widget, pos, closest_data, min_dist) -> bool:
        """Called from _handle_mouse_moved after the nearest-point search.
        Return True to suppress the default tooltip behavior."""
        return False

    def on_key_press(self, widget, event) -> bool:
        """Called from keyPressEvent for Key_Escape, Key_Return, and
        Key_Enter. Return True to consume the event and suppress default
        behavior. GapHeadwayModeHandler handles Escape only; shockwave
        handlers handle both Enter and Escape."""
        return False

    def on_slope_changed(self, widget, value: float) -> None:
        """Called when the Min Slope Change slider is released.
        Only meaningful for shockwave modes; base no-op is correct for
        Select and Gap Headway."""
        pass
    
    def on_vicinity_changed(self, widget, value: float) -> None:
        """Called when the Vicinity slider is released.
        Only meaningful for Automatic mode; base no-op is correct for all
        Phase 1 and Phase 2 handlers."""
        pass