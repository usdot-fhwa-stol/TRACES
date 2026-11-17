"""Jerk Analysis box plots: a 3x2 grid summarizing per-trajectory metrics.

The plotting, hover and HTML save live in ui_kit.box_plot_grid_dialog;
this class only lays out the jerk metrics.
"""

from ui_kit.box_plot_grid_dialog import BoxPlotGridDialog, BoxPlotPanel


class JerkBoxPlotDialog(BoxPlotGridDialog):
    """Args:
        parent: parent widget
        title (str): dialog title
        metrics (dict[str, list[tuple[int, float]]]): metric key -> (veh_id,
            value) list. Expected keys: 'min_jerk', 'mean_jerk', 'max_jerk',
            'jerk_violation_pct', 'sign_inversion_excess_pct'.
        dropped (int): number of trajectories excluded due to missing data.
    """

    # Row-major grid, 3 columns; None = empty cell.
    _LAYOUT = [
        ("min_jerk",                  "Minimum Jerk (m/s³)"),
        ("mean_jerk",                 "Mean Jerk (m/s³)"),
        ("max_jerk",                  "Maximum Jerk (m/s³)"),
        ("jerk_violation_pct",        "Jerk Violation (%)"),
        None,
        ("sign_inversion_excess_pct", "Sign Inversion Excess (%)"),
    ]
    _COLUMNS = 3

    def __init__(self, parent, title, metrics, dropped=0):
        panels = [None if entry is None else
                  BoxPlotPanel(entry[0], entry[1], metrics.get(entry[0], []))
                  for entry in self._LAYOUT]
        note = (f"{dropped} trajector{'y' if dropped == 1 else 'ies'} excluded due to "
                f"missing data." if dropped > 0 else "")
        super().__init__(parent, title, panels, columns=self._COLUMNS, note=note,
                         html_title="Jerk Analysis Box Plots")
        self.resize(1350, 700)
