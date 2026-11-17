"""Generate Plots window (Task 9.4, Figure 4).

"Select Variables Used to Generate Plots", centered, over two columns of
checkboxes -- one per statistic with the full statistic package
(stat_registry.plot_groups), filled top to bottom, left column first --
and Confirm at the bottom right, enabled while at least one box is
checked. With the built-in statistics the columns read:

    [ ] Speed              [ ] Accepted Gap Space Before Lane Change
    [ ] Leader Gap         [ ] Front Gap After Lane Change
    [ ] Follower Gap       [ ] Rear Gap After Lane Change
"""

from typing import Dict, List, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from .stat_registry import StatGroup


class PlotVariableSelectionDialog(QDialog):

    COLUMNS = 2

    def __init__(self, parent, groups: Sequence[StatGroup]):
        super().__init__(parent)
        self.setWindowTitle("Generate Plots")
        self.setModal(True)
        layout = QVBoxLayout(self)

        title = QLabel("Select Variables Used to Generate Plots")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont(title.font())
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        rows = -(-len(groups) // self.COLUMNS) if groups else 0
        self._boxes: Dict[str, QCheckBox] = {}
        for i, group in enumerate(groups):
            box = QCheckBox(group.plot_label or group.name)
            box.toggled.connect(self._update_confirm)
            grid.addWidget(box, i % rows, i // rows)
            self._boxes[group.key] = box
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._confirm_btn = QPushButton("Confirm")
        self._confirm_btn.setDefault(True)
        self._confirm_btn.clicked.connect(self.accept)
        buttons.addWidget(self._confirm_btn)
        layout.addLayout(buttons)
        self._update_confirm()

    def _update_confirm(self, *_args) -> None:
        self._confirm_btn.setEnabled(any(b.isChecked() for b in self._boxes.values()))

    def selected_keys(self) -> List[str]:
        return [key for key, box in self._boxes.items() if box.isChecked()]
