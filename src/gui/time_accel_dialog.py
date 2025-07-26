from .time_speed_dialog import TimeSpeedDialog

class TimeAccelDialog(TimeSpeedDialog):
    """Dialog for displaying time-acceleration plots for multiple vehicles.
    
    This class inherits from TimeSpeedDialog and overrides the necessary
    attributes to display acceleration data instead of speed data.
    """
    
    # Override class attributes for acceleration plotting
    Y_COLUMN = "veh_accel"
    Y_LABEL = "Accel (m/s²)"
    PLOT_TITLE_SUFFIX = "Acceleration vs Time"
    TOLERANCE = 1.0  # Acceleration tolerance in m/s²