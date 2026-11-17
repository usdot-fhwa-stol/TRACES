from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QFormLayout,
    QPushButton, QComboBox, QDoubleSpinBox
)


class ShockwaveConfigDialog(QDialog):
    """Initial configuration prompt shown when Shockwave mode is selected.

    Presents:
      - Generation Method combo (disabled items use the established
        disabled-combo-item pattern from JerkCompDialog._configure_method_options)
      - Min Slope Change spinbox (default 80, range 0–95)
      - Vicinity spinbox (default 0.05, range 0.01–0.50; enabled only for
        Automatic — shown greyed when Manual or Semi-Auto is chosen)
      - Data Selection combo (disabled until spline feature, following the
        same disabled-item pattern as 'Filtered data from spline fitting'
        in the jerk method combo)

    Phase 1: only 'Semi-Automatic' is enabled in the method combo.
             Manual and Automatic are present but disabled.
    """

    _METHOD_ENTRIES = [
        # (label, key, enabled_in_phase_1)
        ("Manual",          "manual",    False),
        ("Semi-Automatic",  "semi_auto", True),
        ("Automatic",       "automatic", False),
    ]

    def __init__(self, parent, db_connection=None, event_id=None):
        super().__init__(parent)
        self._db_connection = db_connection
        self._event_id      = event_id
        self.setWindowTitle("Shockwave Analysis — Configuration")
        self.setModal(True)
        self._setup_ui()
        self._on_method_changed()   # initialise Vicinity enabled state

    def _spline_data_available(self) -> bool:
        """True if calc_position exists and has at least one non-null
        value for the event currently shown in the time-space plot."""
        if self._db_connection is None or self._event_id is None:
            return False
        cols = {row[1] for row in
                self._db_connection.execute("PRAGMA table_info(events)").fetchall()}
        if "calc_position" not in cols:
            return False
        row = self._db_connection.execute(
            "SELECT EXISTS(SELECT 1 FROM events "
            "WHERE event_id = ? AND calc_position IS NOT NULL)",
            (self._event_id,),
        ).fetchone()
        return bool(row[0])

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Generation Method
        self._method_combo = QComboBox()
        for label, key, enabled in self._METHOD_ENTRIES:
            self._method_combo.addItem(label, key)
            if not enabled:
                idx = self._method_combo.count() - 1
                self._method_combo.model().item(idx).setEnabled(False)
        # Auto-select first enabled entry
        for i in range(self._method_combo.count()):
            if self._method_combo.model().item(i).isEnabled():
                self._method_combo.setCurrentIndex(i)
                break
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        layout.addRow("Generation method:", self._method_combo)

        # Min Slope Change
        self._slope_spin = QDoubleSpinBox()
        self._slope_spin.setRange(0.0, 95.0)
        self._slope_spin.setDecimals(1)
        self._slope_spin.setValue(80.0)
        self._slope_spin.setSuffix(" %")
        layout.addRow("Min slope change:", self._slope_spin)

        # Vicinity (enabled only for Automatic)
        self._vicinity_spin = QDoubleSpinBox()
        self._vicinity_spin.setRange(0.01, 0.50)
        self._vicinity_spin.setDecimals(2)
        self._vicinity_spin.setValue(0.05)
        self._vicinity_spin.setSuffix("  (normalised)")
        layout.addRow("Vicinity parameter:", self._vicinity_spin)

        # Data Selection (disabled until spline feature)
        self._data_combo = QComboBox()
        self._data_combo.addItem("Original data", "original")
        self._data_combo.addItem("Filtered data from spline fitting", "spline")
        self._data_combo.model().item(1).setEnabled(False)
        spline_ok = self._spline_data_available()
        self._data_combo.model().item(1).setEnabled(spline_ok)
        if not spline_ok:
            self._data_combo.setItemData(
                1,
                "No spline-fit position data are available for this event",
                Qt.ItemDataRole.ToolTipRole,
            )
        layout.addRow("Data selection:", self._data_combo)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addRow(btn_row)

    def _on_method_changed(self):
        is_auto = self._method_combo.currentData() == "automatic"
        self._vicinity_spin.setEnabled(is_auto)

    def selected_method(self) -> str:
        """Return the chosen generation method key: "manual", "semi_auto"
        or "automatic". The time-space plot maps it to its interaction mode,
        so this dialog doesn't depend on the plot's mode system."""
        return self._method_combo.currentData()

    def selected_slope_percentile(self) -> float:
        return self._slope_spin.value()

    def selected_vicinity(self) -> float:
        return self._vicinity_spin.value()

    def selected_data_mode(self) -> str:
        return self._data_combo.currentData()