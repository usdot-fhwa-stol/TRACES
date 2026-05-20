import json
import logging
import sqlite3

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QMessageBox,
)

logger = logging.getLogger(__name__)


class EventSelectionDialog(QDialog):
    """Multi-select dialog that lists all events (id + name) from the ranges table.

    The caller retrieves the chosen event ids via :attr:`selected_event_ids`
    after the dialog is accepted.
    """

    def __init__(self, parent, db_connection: sqlite3.Connection):
        super().__init__(parent)
        self._db_connection = db_connection
        self.selected_event_ids: list[int] = []

        self.setWindowTitle("Select Events")
        self.setModal(True)
        self.resize(360, 420)
        self._setup_ui()
        self._populate_list()
        QTimer.singleShot(0, self._center_dialog)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select one or more events to include in the export:"))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        layout.addWidget(self._list)

        # Select-all / clear helpers
        helper_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._list.selectAll)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._list.clearSelection)
        helper_row.addWidget(select_all_btn)
        helper_row.addWidget(clear_btn)
        helper_row.addStretch()
        layout.addLayout(helper_row)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------

    def _populate_list(self):
        """Load event ids and names from the ranges table."""
        try:
            cursor = self._db_connection.cursor()
            cursor.execute("SELECT event_id, summary_json FROM ranges ORDER BY event_id")
            rows = cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"EventSelectionDialog: error reading ranges: {e}")
            return

        for event_id, summary_json in rows:
            try:
                summary = json.loads(summary_json)
                event_name = summary.get("event_name", "")
            except (json.JSONDecodeError, TypeError):
                event_name = ""

            label = f"Event {event_id}" + (f" — {event_name}" if event_name else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, event_id)
            self._list.addItem(item)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_accept(self):
        selected = self._list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one event.")
            return
        self.selected_event_ids = [
            item.data(Qt.ItemDataRole.UserRole) for item in selected
        ]
        self.accept()

    def _center_dialog(self):
        if self.parent():
            pr = self.parent().geometry()
            dr = self.geometry()
            self.move(
                pr.x() + (pr.width()  - dr.width())  // 2,
                pr.y() + (pr.height() - dr.height()) // 2,
            )