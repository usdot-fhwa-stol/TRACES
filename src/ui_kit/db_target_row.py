"""Write-target row for dialogs that modify the open database.

A "Write changes to: [path] Browse…" row plus the resolver contract the
dialog calls before its first write:

    resolver(parent, requested_path) -> sqlite3.Connection or None

normally DatabaseSession.ensure_write_target (first-write warning per
opened database, "Save to a Copy…", copy-and-switch when another path is
chosen). in_place_resolver() is the fallback when a dialog is opened
without one: it only accepts the open database.

The row itself never decides anything -- Browse only fills in the path
(overwrite confirmation is the resolver's job, hence DontConfirmOverwrite).
After resolving, the dialog calls set_connection() so the row shows the
file actually written to (e.g. the copy the session switched to).

Used by StitchingPreviewDialog and GapConfigDialog.
"""

import os
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget,
)

from utils import last_dialog_dir, remember_dialog_path

TargetResolver = Callable[[QWidget, str], Optional[sqlite3.Connection]]

DB_FILE_FILTER = "SQLite Database (*.sqlite);;SQLite Database (*.db);;All Files (*)"


def db_file_path(conn: sqlite3.Connection) -> str:
    """File path of *conn*'s main database."""
    return conn.execute("PRAGMA database_list").fetchone()[2]


def same_file(a: Optional[str], b: Optional[str]) -> bool:
    """True if both paths name the same file. Case-insensitive where the
    platform is, and works whether or not the files exist yet."""
    if not a or not b:
        return False
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
        norm = lambda p: os.path.normcase(str(Path(p).expanduser().resolve()))
        return norm(a) == norm(b)
    except (OSError, ValueError):
        return False


def in_place_resolver(conn: sqlite3.Connection, title: str = "Write Changes") -> TargetResolver:
    """Fallback resolver: write only to the open database, without the
    session's first-write warning or copy support."""
    current = db_file_path(conn)

    def resolve(parent: QWidget, requested: str) -> Optional[sqlite3.Connection]:
        if same_file(requested, current):
            return conn
        QMessageBox.information(
            parent, title,
            "Writing to a different database file is not available here.\n"
            f"Choose the current database:\n{current}")
        return None

    return resolve


class DbTargetRow(QWidget):
    """Label, path field and Browse button for a dialog's write target."""

    def __init__(self, conn: sqlite3.Connection,
                 browse_title: str = "Write Changes To",
                 label: str = "Write changes to:",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._browse_title = browse_title

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(label))

        self._edit = QLineEdit(db_file_path(conn))
        self._edit.setMinimumWidth(300)
        lay.addWidget(self._edit, stretch=1)

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setAutoDefault(False)
        self._browse_btn.clicked.connect(self._on_browse)
        lay.addWidget(self._browse_btn)

    def path(self) -> str:
        return self._edit.text().strip()

    def set_path(self, path: str) -> None:
        self._edit.setText(path)

    def set_connection(self, conn: sqlite3.Connection) -> None:
        """Show the file *conn* writes to -- call after resolving."""
        self._edit.setText(db_file_path(conn))

    def _on_browse(self):
        start = self.path() or last_dialog_dir()
        path, _ = QFileDialog.getSaveFileName(
            self, self._browse_title, start, DB_FILE_FILTER,
            options=QFileDialog.Option.DontConfirmOverwrite)   # resolver owns warnings
        if path:
            remember_dialog_path(path)
            self._edit.setText(path)
