"""Application-level ownership of the open trajectory database.

MainWindow's _db_connection is a read-only view of DatabaseSession.connection,
so every existing reader keeps working, while opening, closing, and
switching happen in one place.

Write targets (Trajectory Stitching, and any future module that should
ask before modifying the file):

    ensure_write_target(parent, requested_path) -> connection or None

  * requested == open file, first time this session:
        warn; offer Write to This File / Save to a Copy… / Cancel
  * requested == open file, already confirmed: no prompt
  * requested == another path: confirm replace if it exists, then
        copy_and_switch()

The confirmation is per opened database: open() and close() re-arm it.

copy_and_switch() copies with sqlite3's backup API (safe on an open
connection, unlike a file copy) into a temporary file beside the
destination, then atomically replaces the destination. A cancelled or
failed copy never touches an existing destination file. On success it
emits database_switched(new_conn, old_conn, new_path) BEFORE closing
old_conn, so listeners can release anything still holding it.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QMessageBox, QProgressDialog, QWidget,
)

from data_import.db_utils import validate_trajectory_db
from utils import remember_dialog_path

logger = logging.getLogger(__name__)

_TITLE = "Write Changes"
_DB_FILTER = "SQLite Database (*.sqlite);;SQLite Database (*.db);;All Files (*)"
_BACKUP_PAGES = 2048          # pages per backup step (progress granularity)


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


class _CopyCancelled(Exception):
    pass


class DatabaseSession(QObject):

    database_switched = pyqtSignal(object, object, str)   # new_conn, old_conn, new_path

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._conn: Optional[sqlite3.Connection] = None
        self._path: Optional[str] = None
        self._write_confirmed = False

    @property
    def connection(self) -> Optional[sqlite3.Connection]:
        return self._conn

    @property
    def path(self) -> Optional[str]:
        return self._path

    # ── Open / close ──────────────────────────────────────────────────

    def open(self, file_path: str) -> Tuple[bool, Optional[str]]:
        """Validate and open *file_path*, replacing any open database only
        on success. Returns (ok, error_message)."""
        conn, error = validate_trajectory_db(file_path)
        if conn is None:
            return False, error
        self.close()
        self._conn = conn
        self._path = str(Path(file_path).absolute())
        self._write_confirmed = False
        return True, None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                logger.exception("Error closing database")
        self._conn = None
        self._path = None
        self._write_confirmed = False

    # ── Write targets ─────────────────────────────────────────────────

    def ensure_write_target(self, parent: QWidget,
                            requested_path: str) -> Optional[sqlite3.Connection]:
        """Connection to write to, or None if the user cancelled or the
        target couldn't be prepared (the user has already been told why)."""
        if self._conn is None:
            return None
        requested = (requested_path or "").strip()
        if not requested:
            QMessageBox.warning(parent, _TITLE,
                                "Enter the database file to write the changes to.")
            return None
        if same_file(requested, self._path):
            return self._conn if self._write_confirmed else self._confirm_in_place(parent)
        return self.copy_and_switch(parent, requested)

    def _confirm_in_place(self, parent: QWidget) -> Optional[sqlite3.Connection]:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_TITLE)
        box.setText(f"Changes will be written directly to:\n{self._path}")
        box.setInformativeText(
            "The original observations are kept, but trajectories you change are "
            "flagged and new records are added, so this file will be modified.\n\n"
            "Choose \"Save to a Copy…\" to leave this file untouched and continue "
            "working in a copy. You won't be asked again for this file during "
            "this session.")
        write_btn = box.addButton("Write to This File", QMessageBox.ButtonRole.AcceptRole)
        copy_btn = box.addButton("Save to a Copy…", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is write_btn:
            self._write_confirmed = True
            logger.info("Confirmed in-place writes to %s", self._path)
            return self._conn
        if clicked is copy_btn:
            dest = self.ask_copy_path(parent)
            return self.copy_and_switch(parent, dest) if dest else None
        return None

    def ask_copy_path(self, parent: QWidget) -> Optional[str]:
        current = Path(self._path)
        suggested = current.with_name(f"{current.stem}_edited{current.suffix or '.sqlite'}")
        path, _ = QFileDialog.getSaveFileName(
            parent, "Save Changes to a Copy", str(suggested), _DB_FILTER,
            options=QFileDialog.Option.DontConfirmOverwrite)   # copy_and_switch confirms
        if not path:
            return None
        remember_dialog_path(path)
        return path

    # ── Copy and switch ───────────────────────────────────────────────

    def copy_and_switch(self, parent: QWidget,
                        dest: str) -> Optional[sqlite3.Connection]:
        """Copy the open database to *dest*, make the copy the session's
        database, and return its connection. None on cancel or failure."""
        dest_path = Path(dest).expanduser()
        if not dest_path.suffix:
            dest_path = dest_path.with_suffix(".sqlite")
        dest_path = dest_path.absolute()

        if same_file(str(dest_path), self._path):
            QMessageBox.warning(parent, _TITLE,
                                "That is the database currently open; choose a different file.")
            return None
        if not dest_path.parent.is_dir():
            QMessageBox.critical(parent, _TITLE, f"Folder does not exist:\n{dest_path.parent}")
            return None
        if dest_path.exists():
            answer = QMessageBox.question(
                parent, _TITLE,
                f"{dest_path}\nalready exists. Replace it with a copy of the current database?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return None

        tmp_path = dest_path.with_name(f".{dest_path.name}.{os.getpid()}.tmp")
        try:
            if self._conn.in_transaction:
                self._conn.commit()      # backup copies committed data only
            self._backup_to(parent, tmp_path)
            os.replace(tmp_path, dest_path)
        except _CopyCancelled:
            self._remove_quietly(tmp_path)
            logger.info("Database copy to %s cancelled", dest_path)
            return None
        except (OSError, sqlite3.Error) as exc:
            self._remove_quietly(tmp_path)
            logger.exception("Database copy to %s failed", dest_path)
            QMessageBox.critical(parent, _TITLE,
                                 f"Could not create the copy:\n{exc}\n\n"
                                 "If the file is open in another program, close it and try again.")
            return None

        new_conn, error = validate_trajectory_db(str(dest_path))
        if new_conn is None:
            QMessageBox.critical(parent, _TITLE, f"The copy could not be opened:\n{error}")
            return None

        old_conn, old_path = self._conn, self._path
        self._conn, self._path, self._write_confirmed = new_conn, str(dest_path), True
        logger.info("Switched database %s -> %s", old_path, self._path)
        self.database_switched.emit(new_conn, old_conn, self._path)
        try:
            old_conn.close()
        except sqlite3.Error:
            logger.exception("Error closing previous database")
        return new_conn

    def _backup_to(self, parent: QWidget, tmp_path: Path) -> None:
        self._remove_quietly(tmp_path)
        progress = QProgressDialog("Copying database…", "Cancel", 0, 100, parent)
        progress.setWindowTitle(_TITLE)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.setValue(0)

        def on_progress(status, remaining, total):
            if total:
                progress.setValue(int(100 * (total - remaining) / total))
            QApplication.processEvents()
            if progress.wasCanceled():
                raise _CopyCancelled()   # propagates out of Connection.backup

        dst = sqlite3.connect(str(tmp_path))
        try:
            self._conn.backup(dst, pages=_BACKUP_PAGES, progress=on_progress)
        finally:
            dst.close()
            progress.close()

    @staticmethod
    def _remove_quietly(path: Path) -> None:
        for p in (path, Path(f"{path}-journal")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary file %s", p)