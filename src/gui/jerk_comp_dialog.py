import sqlite3
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QDoubleSpinBox, QComboBox, QPushButton
)

class JerkCompDialog(QDialog):
    """Collects jerk-analysis parameters from the user.

    Signals:
        params_accepted(dict): emitted with keys
            jerk_threshold, missing_gap_seconds, jerk_method
    """
    params_accepted = pyqtSignal(dict)

    def __init__(self, parent, db_connection: sqlite3.Connection):
        super().__init__(parent)
        self._db_connection = db_connection
        self.setWindowTitle("Jerk Analysis")
        self.setModal(True)
        self._setup_ui()
        self._configure_method_options()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.1, 1000.0)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setValue(15.0)
        self._threshold_spin.setSuffix(" m/s³")
        form.addRow("Jerk threshold:", self._threshold_spin)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(0.01, 60.0)
        self._gap_spin.setDecimals(2)
        self._gap_spin.setValue(1.0)
        self._gap_spin.setSuffix(" s")
        form.addRow("Missing value gap threshold:", self._gap_spin)

        self._method_combo = QComboBox()
        # Items are populated/disabled in _configure_method_options.
        form.addRow("Jerk calculation method:", self._method_combo)

        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton("OK")
        self._cancel_btn = QPushButton("Cancel")
        self._ok_btn.clicked.connect(self._on_accept)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._ok_btn)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    def _configure_method_options(self):
        cursor = self._db_connection.cursor()
        cursor.execute("PRAGMA table_info(events)")
        cols = {row[1] for row in cursor.fetchall()}

        entries = [
            ("Use jerk column from dataset", "dataset", "veh_jerk" in cols),
            ("Calculate jerk from acceleration", "from_accel", "veh_accel" in cols),
            ("Calculate acceleration and jerk from speed", "from_speed", "veh_speed" in cols),
        ]
        for label, key, enabled in entries:
            self._method_combo.addItem(label, key)
            idx = self._method_combo.count() - 1
            if not enabled:
                # Disable via the model so it appears grayed out.
                model = self._method_combo.model()
                item = model.item(idx)
                item.setEnabled(False)

        # Default to first enabled item.
        for i in range(self._method_combo.count()):
            if self._method_combo.model().item(i).isEnabled():
                self._method_combo.setCurrentIndex(i)
                break
        else:
            self._method_combo.setEnabled(False)
            self._ok_btn.setEnabled(False)

    def _on_accept(self):
        self.params_accepted.emit({
            "jerk_threshold":      self._threshold_spin.value(),
            "missing_gap_seconds": self._gap_spin.value(),
            "jerk_method":         self._method_combo.currentData(),
        })
        self.accept()