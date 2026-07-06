"""
Custom ViewBox for TimeSpacePlotWidget with selection and context menu functionality.

This module provides a CustomViewBox class that extends pyqtgraph's ViewBox to add:
- Shift+drag selection box functionality
- Custom right-click context menus with dynamic registry system
- Vehicle selection and manipulation features
"""

import logging
import time
import numpy as np
from PyQt6.QtWidgets import QMenu
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
import pyqtgraph as pg

# Set up logger
logger = logging.getLogger(__name__)
_NOT_IMPLEMENTED_TOOLTIP = "Not yet implemented."
_NO_SELECTION_TOOLTIP    = "Select one or more vehicles to enable."
_SINGLE_SELECTION_TOOLTIP = "Filtering is only available when one trajectory is selected."

class CustomViewBox(pg.ViewBox):
    """Custom ViewBox that handles selection box drawing when Shift is held and custom context menus.
    
    This class extends pyqtgraph's ViewBox to provide:
    - Shift+drag selection box for selecting multiple vehicles
    - Right-click context menus with dynamic content based on current state
    - Integration with parent TimeSpacePlotWidget for vehicle selection management
    
    Attributes:
        parent_widget: Reference to the parent TimeSpacePlotWidget
        _selection_start (QPointF): Starting point of selection box in view coordinates
        _is_selecting (bool): Flag indicating if selection box is active
        _selection_box (pg.RectROI): Selection box item for group selection
        _context_menu_registry (list): Registry of context menu items
        _current_context_vehicle (int): Vehicle ID for series-specific context menu actions
    """
    
    def __init__(self, parent_widget, *args, **kwargs):
        """Initialize the CustomViewBox.
        
        Args:
            parent_widget: Reference to the parent TimeSpacePlotWidget
            *args: Additional positional arguments passed to parent ViewBox
            **kwargs: Additional keyword arguments passed to parent ViewBox
        """
        super().__init__(*args, **kwargs)
        self.parent_widget = parent_widget
        self._selection_start = None
        self._is_selecting = False
        self._selection_box = None
        self._current_context_vehicle = None

        # Context menu registry — list of dicts with keys:
        #   filter   : string of context codes ('A', 'S', 'O') that show this item
        #   label    : display text
        #   callback : callable to invoke, or None to render as a disabled placeholder
        #   submenu  : list of sub-item dicts (same keys minus filter), optional
        #
        # A callback of None in a sub-item produces a disabled action in the submenu.
        self._context_menu_registry = [
            {'filter': 'ASO', 'label': 'Statistics',         'callback': self._generate_statistics},
            {'filter': 'ASO', 'label': 'Plots', 'submenu': [
                {'filter': 'SO', 'label': 'Time-Speed Plot', 'callback': self._show_time_speed_dialog},
                {'filter': 'SO', 'label': 'Time-Accel Plot', 'callback': self._show_time_accel_dialog},
            ]},
            {'filter': 'ASO', 'label': 'Filter', 'submenu': [
                {'single_only': True, 'label': 'Spline Fitting', 'callback': self._open_spline_testing_dialog},
            ]},
            {'filter': 'ASO', 'label': 'Noise Analysis',     'submenu': [
                {'label': 'Jerk Analysis',          'callback': self._run_jerk_analysis_rightclick},
                {'label': 'Missing Data',           'callback': self._run_missing_data_analysis_rightclick},
                {'label': 'Internal Consistency',   'callback': self._run_ic_rightclick},
                {'label': 'Platoon Consistency',    'callback': None},
            ]},
        ]

    def mousePressEvent(self, ev):
        """Handle mouse press events for mode handlers."""
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos    = ev.scenePos()
            self._press_time   = time.time()
            self._selection_start = self.mapSceneToView(ev.scenePos())
            self._is_selecting = False
            self.parent_widget._active_handler.on_mouse_press(self, ev)
            ev.accept()
            return

        # For non-left clicks, use default behavior
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        """Handle mouse move events for selection box resizing."""
        if hasattr(self, '_press_pos') and self._press_pos is not None:
            if self.parent_widget._active_handler.on_mouse_move(self, ev):
                return

            # Calculate distance moved since press
            move_distance = (ev.scenePos() - self._press_pos).manhattanLength()
            
            # If we've moved enough distance, start selection box
            if move_distance > 5 and not self._is_selecting:  # 5 pixel threshold
                self._is_selecting = True
                
                # Create selection box
                self._selection_box = pg.RectROI(
                    pos=[self._selection_start.x(), self._selection_start.y()],
                    size=[0, 0],
                    pen=pg.mkPen(color='red', width=2, style=Qt.PenStyle.DashLine),
                    removable=False
                )
                
                # Disable handles
                for handle in self._selection_box.getHandles():
                    handle.hide()
                
                # Add to parent plot
                self.parent_widget._scatter_plot.addItem(self._selection_box)
            
            # Update selection box if we're selecting
            if self._is_selecting and self._selection_box:
                current_pos = self.mapSceneToView(ev.scenePos())
                x0, y0 = self._selection_start.x(), self._selection_start.y()
                x1, y1 = current_pos.x(), current_pos.y()
    
                # Update selection box position and size
                self._selection_box.setPos(min(x0, x1), min(y0, y1))
                self._selection_box.setSize([abs(x1 - x0), abs(y1 - y0)])
            
            ev.accept()
            return
    
        # For non-selection moves, use default behavior
        super().mouseMoveEvent(ev)
    
    def mouseReleaseEvent(self, ev):
        """Handle mouse release events for selection box completion or single clicks."""
        if ev.button() == Qt.MouseButton.LeftButton and hasattr(self, '_press_pos') and self._press_pos is not None:
            # Calculate how much we moved and how long the press was
            move_distance = (ev.scenePos() - self._press_pos).manhattanLength()
            press_duration = time.time() - self._press_time

            if self.parent_widget._active_handler.on_mouse_release(self, ev):
                return
            
            if self._is_selecting:
                # Complete selection box
                end_pos = self.mapSceneToView(ev.scenePos())
                
                x0, y0 = self._selection_start.x(), self._selection_start.y()
                x1, y1 = end_pos.x(), end_pos.y()
                
                # Get selection box bounds
                x_min, x_max = min(x0, x1), max(x0, x1)
                y_min, y_max = min(y0, y1), max(y0, y1)
                
                # Find vehicles with points in the selection box
                selected_vehicles = set()
                for series in self.parent_widget._series.values():
                    if series['veh_time'] is not None and series['veh_dist_trav'] is not None:
                        mask = (series['veh_time'] >= x_min) & (series['veh_time'] <= x_max) & \
                               (series['veh_dist_trav'] >= y_min) & (series['veh_dist_trav'] <= y_max)
                        if np.any(mask):
                            selected_vehicles.add(series['veh_id'])
                
                # Update selected_series: add or remove based on Shift key
                if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.parent_widget._selected_series -= selected_vehicles
                else:
                    self.parent_widget._selected_series.update(selected_vehicles)
                    # Clear selected_series if all vehicles are selected
                    if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
                        self.parent_widget._selected_series.clear()

                # Update plot colors
                self.parent_widget._update_plot_colors()
                
                # Clear selection box
                self._clear_selection_box()
                
            elif move_distance < 5 and press_duration < 0.5:  # Short click with minimal movement
                # Handle single click on series
                mouse_point = self.mapSceneToView(ev.scenePos())
                mx, my = mouse_point.x(), mouse_point.y()
                
                # Find closest point
                min_dist = float('inf')
                closest_veh_id = None
                time_tolerance = 0.05  # Time tolerance in seconds
                pixel_tolerance = 3   # Pixel distance to register click
                
                for series in self.parent_widget._series.values():
                    if series["plot_item"] and len(series["veh_time"]) > 0:
                        mask = (series["veh_time"] >= mx - time_tolerance) & (series["veh_time"] <= mx + time_tolerance)
                        x_filtered = series["veh_time"][mask]
                        y_filtered = series["veh_dist_trav"][mask]
                        
                        if len(x_filtered) > 0:
                            distances = np.abs(y_filtered - my)
                            min_idx = np.argmin(distances)
                            if distances[min_idx] < pixel_tolerance and distances[min_idx] < min_dist:
                                min_dist = distances[min_idx]
                                closest_veh_id = series["veh_id"]
                
                if closest_veh_id is not None:
                    # Toggle veh_id in selected_series
                    if closest_veh_id in self.parent_widget._selected_series:
                        self.parent_widget._selected_series.remove(closest_veh_id)
                    else:
                        self.parent_widget._selected_series.add(closest_veh_id)
                        # Clear selected_series if it contains all vehicles
                        if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
                            self.parent_widget._selected_series.clear()
                    
                    # Update plot colors
                    self.parent_widget._update_plot_colors()
                    logger.debug(f"Toggled veh_id {closest_veh_id}, selected_series: {self.parent_widget._selected_series}")
            
            # Clean up
            self._press_pos = None
            self._press_time = None
            self._is_selecting = False
            ev.accept()
            return
        
        # For non-left releases, use default behavior
        super().mouseReleaseEvent(ev)

    def mouseClickEvent(self, ev):
        """Override to handle right-click context menu.
        
        Args:
            ev: Mouse event containing button information
        """
        if ev.button() == Qt.MouseButton.RightButton:
            if self.parent_widget._active_handler.on_right_click(self, ev):
                ev.accept()
                return
            self._show_context_menu(ev)
            ev.accept()
            return
        
        # For other clicks, use default behavior
        super().mouseClickEvent(ev)

    def wheelEvent(self, ev, axis=None):
        """Zoom the view on scroll-wheel, preserving aspect ratio.

        Zooms toward the cursor position.  Minimum spans and slider hard
        limits are read from the parent widget — no local recalculation.

        Args:
            ev:   QGraphicsSceneWheelEvent.
            axis: Unused; kept for signature compatibility.
        """
        _ZOOM_FACTOR = 0.9          # <1 → scroll-up zooms in

        delta = ev.delta()
        if delta == 0:
            ev.accept()
            return

        pw = self.parent_widget      # shorthand
        scale = _ZOOM_FACTOR if delta > 0 else 1.0 / _ZOOM_FACTOR

        # Current window from sliders (source of truth)
        t_lo, t_hi = pw._time_slider.value()
        d_lo, d_hi = pw._dist_slider.value()
        t_span = t_hi - t_lo
        d_span = d_hi - d_lo

        # Precomputed minimum spans
        min_t = pw._min_time_span
        min_d = pw._min_dist_span

        # Cursor in view coords — zoom centres here
        mouse_point = self.mapSceneToView(ev.scenePos())
        mx, my = mouse_point.x(), mouse_point.y()

        fx = max(0.0, min(1.0, (mx - t_lo) / t_span if t_span else 0.5))
        fy = max(0.0, min(1.0, (my - d_lo) / d_span if d_span else 0.5))

        # New spans, clamped to minimums
        new_t_span = max(t_span * scale, min_t)
        new_d_span = max(d_span * scale, min_d)

        # New edges, keeping cursor at the same fractional position
        new_t_lo = mx - fx * new_t_span
        new_t_hi = new_t_lo + new_t_span
        new_d_lo = my - fy * new_d_span
        new_d_hi = new_d_lo + new_d_span

        # Clamp to slider hard limits
        s_t_min = pw._time_slider.minimum()
        s_t_max = pw._time_slider.maximum()
        s_d_min = pw._dist_slider.minimum()
        s_d_max = pw._dist_slider.maximum()

        if new_t_lo < s_t_min:
            new_t_lo = s_t_min
            new_t_hi = new_t_lo + new_t_span
        if new_t_hi > s_t_max:
            new_t_hi = s_t_max
            new_t_lo = new_t_hi - new_t_span
        new_t_lo = max(new_t_lo, s_t_min)

        if new_d_lo < s_d_min:
            new_d_lo = s_d_min
            new_d_hi = new_d_lo + new_d_span
        if new_d_hi > s_d_max:
            new_d_hi = s_d_max
            new_d_lo = new_d_hi - new_d_span
        new_d_lo = max(new_d_lo, s_d_min)

        # Push through sliders → triggers _on_slider_changed → updates view
        pw._time_slider.setValue((new_t_lo, new_t_hi))
        pw._dist_slider.setValue((new_d_lo, new_d_hi))

        ev.accept()

    def _show_context_menu(self, ev):
        """Show custom context menu based on click location and current state.
        
        Args:
            ev: Mouse event containing position information
        """
        if not self.parent_widget._series:
            return
            
        # Get click position
        pos = ev.scenePos()
        mouse_point = self.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()
        
        # Check if we clicked on a plot item
        clicked_vehicle_id = self._find_clicked_vehicle(mx, my)
        
        # Determine context menu type and title
        if clicked_vehicle_id is not None:
            menu_type = 'O'  # series (O for "One vehicle")
            title = 'Series'
            self._current_context_vehicle = clicked_vehicle_id
        elif len(self.parent_widget._selected_series) > 0:
            menu_type = 'S'  # selected
            title = 'Selected'
            self._current_context_vehicle = None
        else:
            menu_type = 'A'  # all
            title = 'All'
            self._current_context_vehicle = None
        
        # Create and populate context menu
        context_menu = QMenu(self.parent_widget)
        context_menu.setToolTipsVisible(True)

        # Add disabled pseudo-title
        title_action = QAction(title, context_menu)
        title_action.setEnabled(False)
        context_menu.addAction(title_action)
        context_menu.addSeparator()

        # Add menu items based on registry and current context.
        vehicle_count = len(self._get_target_vehicle_ids())
        for item in self._context_menu_registry:
                    if menu_type not in item['filter']:
                        continue
                    if 'submenu' in item:
                        sub = QMenu(item['label'], context_menu)
                        sub.setToolTipsVisible(True)
                        for sub_item in item['submenu']:
                            sub_action = QAction(sub_item['label'], sub)
                            sub_filter = sub_item.get('filter')
                            context_ok = sub_filter is None or menu_type in sub_filter
                            single_ok = not sub_item.get('single_only') or vehicle_count == 1
                            if sub_item.get('callback') is None:
                                sub_action.setEnabled(False)
                                sub_action.setToolTip(sub_item.get('disabled_tooltip', _NOT_IMPLEMENTED_TOOLTIP))
                            elif not context_ok:
                                sub_action.setEnabled(False)
                                sub_action.setToolTip(sub_item.get('disabled_tooltip', _NO_SELECTION_TOOLTIP))
                            elif not single_ok:
                                sub_action.setEnabled(False)
                                sub_action.setToolTip(sub_item.get('disabled_tooltip', _SINGLE_SELECTION_TOOLTIP))
                            else:
                                sub_action.triggered.connect(
                                    lambda checked, cb=sub_item['callback']: cb()
                                )
                            sub.addAction(sub_action)
        
                        context_menu.addMenu(sub)
                    else:
                        action = QAction(item['label'], context_menu)
                        if item.get('callback') is None:
                            action.setEnabled(False)
                        else:
                            action.triggered.connect(
                                lambda checked, cb=item['callback']: cb()
                            )
                        context_menu.addAction(action)
        
        # Show context menu at cursor position
        global_pos = self.parent_widget._scatter_plot.mapToGlobal(
            self.parent_widget._scatter_plot.mapFromScene(pos)
        )
        context_menu.exec(global_pos)
    
    def _find_clicked_vehicle(self, mx, my):
        """Find vehicle ID at the given coordinates, if any.
        
        Args:
            mx (float): Mouse x coordinate in view space
            my (float): Mouse y coordinate in view space
            
        Returns:
            int or None: Vehicle ID if found, None otherwise
        """
        time_tolerance = 0.05  # Time tolerance in seconds
        pixel_tolerance = 3   # Pixel distance to register click
        
        min_dist = float('inf')
        closest_veh_id = None
        
        for series in self.parent_widget._series.values():
            if series["plot_item"] and len(series["veh_time"]) > 0:
                mask = (series["veh_time"] >= mx - time_tolerance) & (series["veh_time"] <= mx + time_tolerance)
                x_filtered = series["veh_time"][mask]
                y_filtered = series["veh_dist_trav"][mask]
                
                if len(x_filtered) > 0:
                    distances = np.abs(y_filtered - my)
                    min_idx = np.argmin(distances)
                    if distances[min_idx] < pixel_tolerance and distances[min_idx] < min_dist:
                        min_dist = distances[min_idx]
                        closest_veh_id = series["veh_id"]
        
        return closest_veh_id
    
    def _clear_selection_box(self):
        """Remove the selection box from the plot."""
        if self._selection_box:
            self.parent_widget._scatter_plot.removeItem(self._selection_box)
            self._selection_box = None
            self._is_selecting = False
            self._selection_start = None

    def _get_vehicle_data_for_context(self):
        """Get vehicle data lists based on current context.
        
        Returns:
            tuple: (veh_ids, series_colors, text_colors) for the current context
        """
        if self._current_context_vehicle is not None:
            # Series context - only the clicked vehicle
            veh_ids = [self._current_context_vehicle]
        elif len(self.parent_widget._selected_series) > 0:
            # Selected context - vehicles in selected series
            veh_ids = list(self.parent_widget._selected_series)
        else:
            # All context - vehicles with plot items
            veh_ids = [veh_id for veh_id, series in self.parent_widget._series.items() 
                      if series['plot_item'] is not None]
        
        # Get corresponding colors for these vehicles
        series_colors = []
        text_colors = []
        for veh_id in veh_ids:
            if veh_id in self.parent_widget._series:
                series_colors.append(self.parent_widget._series[veh_id]['color'])
                text_colors.append(self.parent_widget._series[veh_id]['text_color'])
            else:
                series_colors.append('#000000')  # Default black
                text_colors.append('#ffffff')    # Default white
        
        return veh_ids, series_colors, text_colors

    # Context menu callback methods
    def _do_nothing(self):
        pass

    def _select_all_vehicles(self):
        """Select all vehicles."""
        self.parent_widget._selected_series = set(self.parent_widget._series.keys())
        if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
            self.parent_widget._selected_series.clear()
        self.parent_widget._update_plot_colors()
        logger.debug("Selected all vehicles")
    
    def _clear_selection(self):
        """Clear vehicle selection."""
        self.parent_widget._selected_series.clear()
        self.parent_widget._update_plot_colors()
        logger.debug("Cleared vehicle selection")
    
    def _invert_selection(self):
        """Invert current vehicle selection."""
        all_vehicles = set(self.parent_widget._series.keys())
        self.parent_widget._selected_series = all_vehicles - self.parent_widget._selected_series
        if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
            self.parent_widget._selected_series.clear()
        self.parent_widget._update_plot_colors()
        logger.debug("Inverted vehicle selection")
    
    def _select_vehicle_only(self):
        """Select only the clicked vehicle."""
        if self._current_context_vehicle is not None:
            self.parent_widget._selected_series = {self._current_context_vehicle}
            self.parent_widget._update_plot_colors()
            logger.debug(f"Selected only vehicle {self._current_context_vehicle}")
    
    def _toggle_vehicle_selection(self):
        """Toggle selection of the clicked vehicle."""
        if self._current_context_vehicle is not None:
            veh_id = self._current_context_vehicle
            if veh_id in self.parent_widget._selected_series:
                self.parent_widget._selected_series.remove(veh_id)
            else:
                self.parent_widget._selected_series.add(veh_id)
                # Clear selection if all vehicles are selected
                if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
                    self.parent_widget._selected_series.clear()
            self.parent_widget._update_plot_colors()
            logger.debug(f"Toggled selection for vehicle {veh_id}")
    
    def _reset_view(self):
        """Reset the plot view to show all data."""
        self.autoRange()
        logger.debug("Reset plot view")

    def _show_time_speed_dialog(self):
        """Show time-speed dialog for vehicles based on current context."""
        if not self.parent_widget._db_connection or self.parent_widget._current_event_id is None:
            logger.warning("No database connection or event ID available for time-speed dialog")
            return
        
        # Get vehicle data for current context
        veh_ids, series_colors, text_colors = self._get_vehicle_data_for_context()
        
        if not veh_ids:
            logger.warning("No vehicles available for time-speed dialog")
            return

        # Collect lane ids
        lane_ids = []
        for i in range(self.parent_widget._lane_combobox.count()):
            lane_ids.append(self.parent_widget._lane_combobox.itemData(i)['lane_id'])

        # Create and show time-speed dialog
        time_min, time_max = self.parent_widget._time_slider.value()
        from .time_speed_dialog import TimeSpeedDialog
        dialog = TimeSpeedDialog(
            self.parent_widget,
            self.parent_widget._db_connection,
            self.parent_widget._current_event_id,
            veh_ids,
            lane_ids,
            time_min,
            time_max,
            series_colors,
            text_colors
        )
        dialog.show()
        logger.debug(f"Opened time-speed dialog for {len(veh_ids)} vehicles")
    
    def _show_time_accel_dialog(self):
        """Show time-acceleration dialog for vehicles based on current context."""
        if not self.parent_widget._db_connection or self.parent_widget._current_event_id is None:
            logger.warning("No database connection or event ID available for time-acceleration dialog")
            return
        
        # Get vehicle data for current context
        veh_ids, series_colors, text_colors = self._get_vehicle_data_for_context()
        
        if not veh_ids:
            logger.warning("No vehicles available for time-acceleration dialog")
            return

        # Collect lane ids
        lane_ids = []
        for i in range(self.parent_widget._lane_combobox.count()):
            lane_ids.append(self.parent_widget._lane_combobox.itemData(i)['lane_id'])

        # Create and show time-acceleration dialog
        time_min, time_max = self.parent_widget._time_slider.value()
        from .time_accel_dialog import TimeAccelDialog
        dialog = TimeAccelDialog(
            self.parent_widget,
            self.parent_widget._db_connection,
            self.parent_widget._current_event_id,
            veh_ids,
            lane_ids,
            time_min,
            time_max,
            series_colors,
            text_colors
        )
        dialog.show()
        logger.debug(f"Opened time-acceleration dialog for {len(veh_ids)} vehicles")
    
    def _generate_statistics(self):
        """Generate and display statistics for vehicles based on current context."""
        if not self.parent_widget._db_connection or self.parent_widget._current_event_id is None:
            logger.warning("No database connection or event ID available for statistics")
            return
        
        # Determine which vehicle IDs to include based on context
        target_veh_ids = self._get_target_vehicle_ids()
        if not target_veh_ids:
            logger.warning("No vehicles available for statistics")
            return
        
        # Determine the mode for the dialog title
        mode = self._get_statistics_mode()
        
        # Create and show statistics dialog
        from .statistics_dialog import StatisticsDialog
        dialog = StatisticsDialog(self.parent_widget, target_veh_ids, 
                                self.parent_widget._db_connection, 
                                self.parent_widget._current_event_id,
                                mode)
        dialog.exec()
    
    def _get_target_vehicle_ids(self):
        """Get the list of vehicle IDs based on current context.
        
        Returns:
            list: List of vehicle IDs to include in statistics
        """
        if self._current_context_vehicle is not None:
            # Series context - only the clicked vehicle
            return [self._current_context_vehicle]
        elif len(self.parent_widget._selected_series) > 0:
            # Selected context - vehicles in selected series
            return list(self.parent_widget._selected_series)
        else:
            # All context - vehicles with plot items
            return [veh_id for veh_id, series in self.parent_widget._series.items() 
                   if series['plot_item'] is not None]
    
    def _get_statistics_mode(self):
        """Get the mode string for the statistics dialog title.
        
        Returns:
            str: Mode string (All, Selected, or Series)
        """
        if self._current_context_vehicle is not None:
            return "Series"
        elif len(self.parent_widget._selected_series) > 0:
            return "Selected"
        else:
            return "All"

    def _run_jerk_analysis_rightclick(self):
        if not self.parent_widget._db_connection or self.parent_widget._current_event_id is None:
            return
        veh_ids = self._get_target_vehicle_ids()
        if not veh_ids:
            return

        event_id = self.parent_widget._current_event_id
        from comp.jerk import JerkCompDialog
        dlg = JerkCompDialog(
            self.parent_widget,
            self.parent_widget._db_connection,
            event_ids=[event_id],
            veh_ids=veh_ids,
        )
        dlg.exec()

    def _run_missing_data_analysis_rightclick(self):
        if not self.parent_widget._db_connection or self.parent_widget._current_event_id is None:
            return
            
        veh_ids = self._get_target_vehicle_ids()
        if not veh_ids:
            return

        event_id = self.parent_widget._current_event_id
        
        from comp.missing_data import MissingDataCompDialog
        # The dialog now manages the entire lifecycle
        dlg = MissingDataCompDialog(
            self.parent_widget,
            self.parent_widget._db_connection,
            event_ids=[event_id],
            veh_ids=veh_ids,
        )
        dlg.exec()

    def _run_ic_rightclick(self):
        if (not self.parent_widget._db_connection
                or self.parent_widget._current_event_id is None):
            return
    
        # Gate on veh_dist_trav presence.
        from comp.base_comp import BaseComp
        cols = BaseComp._events_columns(self.parent_widget._db_connection)
        if "veh_dist_trav" not in cols:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self.parent_widget, "Internal Consistency",
                "The veh_dist_trav column is required but absent from this dataset.")
            return
    
        veh_ids  = self._get_target_vehicle_ids()
        event_id = self.parent_widget._current_event_id
    
        from comp.consistency import InternalConsistencyDialog
        dlg = InternalConsistencyDialog(
            self.parent_widget,
            self.parent_widget._db_connection,
            event_ids=[event_id],
            veh_ids=veh_ids,
        )
        dlg.exec()

    def _open_spline_testing_dialog(self):
        """Open the right-click spline fitting preview window (Filter > Spline
        Fitting). Fitting base, s, and k are all chosen inside the dialog via
        its own controls — nothing here depends on which menu path was used.
        """
        if not self.parent_widget._db_connection or self.parent_widget._current_event_id is None:
            logger.warning("No database connection or event ID available for spline fitting")
            return
    
        target_veh_ids = self._get_target_vehicle_ids()
        if len(target_veh_ids) != 1:
            logger.warning("Spline Fitting requested without exactly one trajectory in context")
            return
        veh_id = target_veh_ids[0]
    
        veh_ids, series_colors, _ = self._get_vehicle_data_for_context()
        try:
            color = series_colors[veh_ids.index(veh_id)]
        except (ValueError, IndexError):
            color = '#1f77b4'
    
        from comp.spline import SplineTestingDialog
        dialog = SplineTestingDialog(
            self.parent_widget,
            self.parent_widget._db_connection,
            self.parent_widget._current_event_id,
            veh_id,
        )
        dialog.show()
        logger.debug(f"Opened spline testing dialog for vehicle {veh_id}")