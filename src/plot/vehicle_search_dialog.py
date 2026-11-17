"""
Modeless dialog for finding and selecting a vehicle trajectory on the
time-space plot by vehicle id.
"""

import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIntValidator

# Set up logger
logger = logging.getLogger(__name__)


class VehicleSearchDialog(QDialog):
    """Modeless dialog to find a vehicle trajectory by (partial) vehicle id.

    The user types digits into a search field; the list below filters live
    to vehicle ids whose string form contains the typed digits. Clicking or
    activating a row emits `vehicle_selected`. The dialog holds no plot or
    database state of its own -- it only knows about the event's vehicle id
    list, keeping it decoupled from TimeSpacePlotWidget internals.

    Attributes:
        _all_veh_ids (list[int]): Full sorted list of vehicle ids for the
            current event, supplied by the caller.
        _search_field (QLineEdit): Digits-only search input.
        _result_list (QListWidget): Filtered list of matching vehicle ids.
        _count_label (QLabel): Shows match count / truncation notice.
    """

    # Signal emitted when a vehicle id is chosen from the results list
    vehicle_selected = pyqtSignal(int)

    _MAX_RESULTS = 200  # cap the list so a near-empty filter stays responsive

    def __init__(self, parent=None, veh_ids=None):
        """Initialize the VehicleSearchDialog.

        Args:
            parent: Parent widget (typically MainWindow).
            veh_ids (list[int]): Vehicle ids available for the current event.
        """
        super().__init__(parent)
        self._all_veh_ids = sorted(veh_ids or [])
        self._search_field = None
        self._result_list = None
        self._count_label = None

        self.setWindowTitle("Search Vehicle")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)
        self.setModal(False)
        self.resize(10, 20)

        logger.debug("VehicleSearchDialog initialized")

        self._setup_ui()
        self._populate_results("")
        QTimer.singleShot(0, self._position_dialog)

    def _setup_ui(self):
        """Set up the dialog UI components."""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter vehicle id digits:"))

        self._search_field = QLineEdit()
        self._search_field.setValidator(QIntValidator(0, 999_999_999, self))
        self._search_field.setPlaceholderText("e.g. 42")
        self._search_field.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._search_field)

        self._result_list = QListWidget()
        self._result_list.itemClicked.connect(self._on_item_chosen)
        self._result_list.itemActivated.connect(self._on_item_chosen)
        row_height = self._result_list.fontMetrics().height() + 8  # padding per row
        self._result_list.setMaximumHeight(row_height * 4 + 2 * self._result_list.frameWidth())
        layout.addWidget(self._result_list, stretch=1)

        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(self._count_label)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._search_field.setFocus()

    def _position_dialog(self):
        """Position the dialog near the top left within the parent window."""
        if self.parent():
            parent_rect = self.parent().geometry()
            x = parent_rect.x() + parent_rect.width() // 10
            y = parent_rect.y() + parent_rect.height() // 10
            self.move(x, y)

    def set_vehicle_ids(self, veh_ids):
        """Replace the searchable vehicle id list, e.g. after a new event
        has been set, and re-apply the current filter text.

        Args:
            veh_ids (list[int]): Vehicle ids available for the current event.
        """
        self._all_veh_ids = sorted(veh_ids or [])
        self._populate_results(self._search_field.text() if self._search_field else "")

    def _on_text_changed(self, text: str):
        """Handle changes to the search field.

        Args:
            text (str): Current search field contents.
        """
        self._populate_results(text)

    def _populate_results(self, text: str):
        """Filter `_all_veh_ids` by substring match and refresh the list.

        Args:
            text (str): Digits typed by the user (may be empty, meaning show all).
        """
        self._result_list.clear()

        if text:
            matches = [v for v in self._all_veh_ids if text in str(v)]
        else:
            matches = self._all_veh_ids

        shown = matches[: self._MAX_RESULTS]
        for veh_id in shown:
            self._result_list.addItem(QListWidgetItem(str(veh_id)))

        if len(matches) > len(shown):
            self._count_label.setText(f"Showing {len(shown)} of {len(matches)} matches")
        else:
            self._count_label.setText(f"{len(matches)} vehicle(s)")

    def _on_item_chosen(self, item: QListWidgetItem):
        """Emit the selected vehicle id when a list row is clicked/activated.

        Args:
            item (QListWidgetItem): The chosen row.
        """
        veh_id = int(item.text())
        logger.debug(f"Vehicle {veh_id} selected in search dialog")
        self.vehicle_selected.emit(veh_id)