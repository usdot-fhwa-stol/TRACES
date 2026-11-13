"""Database-only catalog and event selection UI."""
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                             QInputDialog, QLabel, QLineEdit, QProgressBar, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from .database_catalog import (CatalogCanceled, DatabaseCatalog, available_tables,
                               open_database)


class _CatalogWorker(QThread):
    log_signal = pyqtSignal(str, bool)
    progress_signal = pyqtSignal(int)

    def __init__(self, catalog, event_id=None):
        super().__init__()
        self.catalog = catalog
        self.interrupt_flag = False
        self.error = None
        self.event_id = event_id
        self.result = None

    def run(self):
        try:
            if self.event_id is None:
                self.catalog.build(self)
            else:
                self.catalog.prepare_events(self)
                self.result = self.catalog.export_event(self.event_id)
        except Exception as exc:
            self.error = exc


class _CatalogProgress(QDialog):
    def __init__(self, worker, parent, title='Loading Database Event Catalog'):
        super().__init__(parent)
        self.worker = worker
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        self.label = QLabel('Reading source data and applying JSON mappings...')
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        progress = QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)
        self.cancel = QPushButton('Cancel')
        self.cancel.clicked.connect(self.request_cancel)
        layout.addWidget(self.cancel)
        worker.log_signal.connect(lambda message, _: self.label.setText(message))
        worker.finished.connect(self.accept)

    def request_cancel(self):
        self.worker.interrupt_flag = True
        self.cancel.setEnabled(False)
        self.label.setText('Canceling after the current database batch...')

    def reject(self):
        if self.worker.isRunning():
            self.request_cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.request_cancel()
            event.ignore()
        else:
            event.accept()


def _choose_event(parent, catalog):
    dialog = QDialog(parent)
    dialog.setWindowTitle('Select Database Event')
    dialog.resize(900, 450)
    layout = QVBoxLayout(dialog)
    label = QLabel(f'Catalog: {catalog.schema}.{catalog.catalog_name}\n'
                   'Select one event to import. Times and duration are in seconds.')
    label.setWordWrap(True)
    layout.addWidget(label)
    columns = ('event_id', 'event_name', 'row_count', 'vehicle_count',
               'start_time', 'end_time', 'duration')
    table = QTableWidget(len(catalog.rows), len(columns))
    table.setHorizontalHeaderLabels(['Event ID', 'Event name', 'Rows', 'Vehicles',
                                    'Start (s)', 'End (s)', 'Duration (s)'])
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    for index, row in enumerate(catalog.rows):
        for col, name in enumerate(columns):
            value = row[name]
            table.setItem(index, col, QTableWidgetItem('' if value is None else str(value)))
    table.resizeColumnsToContents()
    table.selectRow(0)
    layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                               QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    table.doubleClicked.connect(lambda _: dialog.accept())
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted or table.currentRow() < 0:
        return None
    return catalog.rows[table.currentRow()]['event_id']


def process_postgres_csv(parent_widget, file_path, config, config_name, log_fn):
    """Choose a source table, persist a JSON-derived catalog, and stage one event.

    Returns (normalized CSV path, identity config), or None on cancellation.
    The URI and JSON files and the source trajectory table remain unchanged.
    """
    try:
        engine = open_database(file_path, password_prompt=lambda: QInputDialog.getText(
            parent_widget, 'PostgreSQL Password', 'Enter your PostgreSQL password:',
            QLineEdit.EchoMode.Password))
    except CatalogCanceled:
        return None
    catalog = None
    try:
        tables = available_tables(engine)
        if not tables:
            raise ValueError('No source tables are available in this database.')
        labels = [f'{schema}.{name}' for schema, name in tables]
        selected, ok = QInputDialog.getItem(
            parent_widget, 'Select Source Table',
            'Choose a table whose column order matches the JSON source indices:',
            labels, 0, False)
        if not ok or not selected:
            return None
        schema, name = tables[labels.index(selected)]
        catalog = DatabaseCatalog(engine, schema, name, config, config_name)
        worker = _CatalogWorker(catalog)
        worker.log_signal.connect(log_fn)
        progress = _CatalogProgress(worker, parent_widget)
        worker.start()
        progress.exec()
        worker.wait()
        if isinstance(worker.error, CatalogCanceled):
            return None
        if worker.error is not None:
            raise worker.error
        event_id = _choose_event(parent_widget, catalog)
        if event_id is None:
            return None
        export_worker = _CatalogWorker(catalog, event_id=event_id)
        export_worker.log_signal.connect(log_fn)
        export_progress = _CatalogProgress(export_worker, parent_widget,
                                          'Preparing Selected Database Event')
        export_worker.start()
        export_progress.exec()
        export_worker.wait()
        if isinstance(export_worker.error, CatalogCanceled):
            return None
        if export_worker.error is not None:
            raise export_worker.error
        return export_worker.result
    finally:
        if catalog is not None:
            catalog.close()
        engine.dispose()
