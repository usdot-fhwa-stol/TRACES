"""Missing Data box plots: one plot per metric passed in, two per row.

The plotting, hover and HTML save live in ui_kit.box_plot_grid_dialog;
this class only names the missing-data metrics.
"""

from ui_kit.box_plot_grid_dialog import BoxPlotGridDialog, BoxPlotPanel


class MissingDataBoxPlotDialog(BoxPlotGridDialog):
    """Args:
        parent: parent widget
        title (str): dialog title
        metrics (dict[str, list[tuple[int, float]]]): metric key -> (veh_id,
            value) list, in display order. Known keys: 'missing_record_pct',
            'num_gaps', 'gap_lengths'.
        dropped (int): number of trajectories excluded due to missing data.
    """

    _TITLES = {
        "missing_record_pct": "Missing Record (%)",
        "num_gaps":           "Number of Gaps",
        "gap_lengths":        "Gap Length (points)",
    }

    def __init__(self, parent, title, metrics, dropped=0):
        panels = [BoxPlotPanel(key, self._TITLES.get(key, key), values)
                  for key, values in metrics.items()]
        note = (f"{dropped} trajector{'y' if dropped == 1 else 'ies'} excluded due to "
                f"missing or invalid data." if dropped > 0 else "")
        super().__init__(parent, title, panels, columns=2, note=note,
                         html_title="Missing Data Analysis Box Plots",
                         save_stem="missing_data_box_plots")
        self.resize(900, 500)
