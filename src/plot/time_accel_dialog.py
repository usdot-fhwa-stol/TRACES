from .time_speed_dialog import TimeSpeedDialog

class TimeAccelDialog(TimeSpeedDialog):
    """Dialog for displaying time-acceleration plots for multiple vehicles.
    
    This class inherits from TimeSpeedDialog and overrides the necessary
    attributes to display acceleration data instead of speed data.
    Drag panning, wheel zoom and per-axis zoom are inherited unchanged.
    """
    
    # Override class attributes for acceleration plotting
    Y_COLUMN = "veh_accel"
    Y_LABEL = "Accel (m/s²)"
    PLOT_TITLE_SUFFIX = "Acceleration vs Time"
    TOLERANCE = 1.0  # Acceleration tolerance in m/s²
    MIN_Y_SPAN = 0.2  # Acceleration zoom floor in m/s² (range is only a few m/s²)