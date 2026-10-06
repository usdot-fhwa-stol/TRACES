"""Spectral parameter entry, background analysis and magnitude plots."""
from pathlib import Path

import pyqtgraph as pg
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QFileDialog, QMessageBox, QTabWidget, QTableView,
)

from comp.base_analysis_dialog import BaseAnalysisDialog
from comp.base_comp import BaseAnalysisWorker
from comp.plot_export import export_plot_to_svg
from utils import last_dialog_dir, remember_dialog_path
from .spectral_comp import SpectralComp, write_csv


class SpectralDialog(BaseAnalysisDialog):
    RIGHT_CLICK_ENABLED = False

    def _csv_export_label(self):
        return 'Spectral Analysis'

    def _build_parameter_form(self):
        form = QFormLayout()
        self._signal_combo = QComboBox()
        columns = SpectralComp._events_columns(self._db_connection)
        for signal in ('veh_speed', 'veh_accel'):
            if signal in columns:
                self._signal_combo.addItem(signal, signal)
        form.addRow('Selected signal:', self._signal_combo)
        hint = QLabel('Raw DFT magnitude using the mean positive sampling interval.\n'
                      'Missing signal values are omitted; no resampling is applied.')
        hint.setWordWrap(True)
        form.addRow(hint)
        return form

    def _setup_ui(self):
        super()._setup_ui()
        self._set_controls_enabled(True)
        if not self._signal_combo.count():
            self.layout().addWidget(QLabel('No speed or acceleration column is available.'))

    def _current_params(self):
        return {'selected_signal': self._signal_combo.currentData()}

    def _set_controls_enabled(self, enabled):
        super()._set_controls_enabled(enabled)
        self._signal_combo.setEnabled(enabled)
        self._primary_btn.setEnabled(enabled and self._signal_combo.count() > 0)

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        return BaseAnalysisWorker(db_path, event_ids, veh_ids, params, dest_path,
                                  self._run_for_event, write_csv if dest_path else None)

    @staticmethod
    def _run_for_event(conn, event_id, veh_ids, params, progress_callback=None):
        results = SpectralComp().comp_series(conn, event_id, veh_ids, params)
        if progress_callback:
            progress_callback()
        return results

    def _show_results(self, results):
        self._results_dialog = SpectralResultsDialog(self, results)
        self._results_dialog.exec()


class SpectralTableModel(QAbstractTableModel):
    """Expose all bins of the current trajectory without copying FFT arrays."""
    HEADERS = ('Event ID', 'Vehicle ID', 'Frequency Index', 'Frequency (Hz)',
               'Fourier Coefficient', 'Magnitude')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None

    def set_result(self, result):
        self.beginResetModel()
        self.result = result
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.result is None else len(self.result.frequency_index)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return self.HEADERS[section]
            return str(section + 1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.result is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            r, i = self.result, index.row()
            values = (r.event_id, r.veh_id, r.frequency_index[i], r.frequency[i],
                      r.fourier_coefficient[i], r.magnitude[i])
            return str(values[index.column()])


class SpectralResultsDialog(QDialog):
    def __init__(self, parent, results):
        super().__init__(parent)
        # Retain the complete spectrum table in memory, independently of the DB.
        self.frequency_spectrum = results
        self.setWindowTitle('Spectral Analysis — Spectrum and Results Table')
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        navigation = QHBoxLayout()
        self._previous = QPushButton('Previous')
        self._next = QPushButton('Next')
        self._trajectory_combo = QComboBox()
        for result in results:
            self._trajectory_combo.addItem(f'Event {result.event_id} — Vehicle {result.veh_id}')
        navigation.addWidget(self._previous)
        navigation.addWidget(self._trajectory_combo, 1)
        navigation.addWidget(self._next)
        layout.addLayout(navigation)
        self._plot = pg.PlotWidget(background='w')
        self._plot.setLabel('bottom', 'Frequency', units='Hz')
        self._plot.setLabel('left', 'Magnitude (raw DFT)')
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._plot, 'Magnitude Spectrum')
        self._table = QTableView()
        self._table_model = SpectralTableModel(self._table)
        self._table.setModel(self._table_model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        for column, width in enumerate((90, 100, 120, 170, 290, 170)):
            self._table.setColumnWidth(column, width)
        self._tabs.addTab(self._table, 'Spectrum Table (all bins)')
        layout.addWidget(self._tabs)
        self._info = QLabel()
        self._info.setWordWrap(True)
        layout.addWidget(self._info)
        buttons = QHBoxLayout()
        self._save = QPushButton('Save Plot…')
        self._save.clicked.connect(self._save_plot)
        buttons.addWidget(self._save)
        self._save_table = QPushButton('Save Table…')
        self._save_table.setToolTip('Save all frequency bins for the selected vehicle to CSV')
        self._save_table.clicked.connect(self._save_table_csv)
        buttons.addWidget(self._save_table)
        buttons.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._previous.clicked.connect(lambda: self._trajectory_combo.setCurrentIndex(
            self._trajectory_combo.currentIndex() - 1))
        self._next.clicked.connect(lambda: self._trajectory_combo.setCurrentIndex(
            self._trajectory_combo.currentIndex() + 1))
        self._trajectory_combo.currentIndexChanged.connect(self._update_plot)
        self._update_plot(self._trajectory_combo.currentIndex())

    def _update_plot(self, index):
        self._plot.clear()
        valid = 0 <= index < len(self.frequency_spectrum)
        self._table_model.set_result(self.frequency_spectrum[index] if valid else None)
        self._save.setEnabled(valid)
        self._save_table.setEnabled(valid)
        self._previous.setEnabled(valid and index > 0)
        self._next.setEnabled(valid and index < len(self.frequency_spectrum) - 1)
        if not valid:
            self._info.setText('No analyzable trajectories: at least two finite samples '
                               'and a positive time interval are required.')
            return
        result = self.frequency_spectrum[index]
        n = len(result.frequency_index)
        stop = n // 2 + 1
        self._plot.plot(result.frequency[:stop], result.magnitude[:stop],
                        pen=pg.mkPen('#0000ff', width=1), fillLevel=0,
                        brush=pg.mkBrush(0, 0, 255, 100))
        self._plot.setTitle(f'{result.selected_signal} — Event {result.event_id}, '
                            f'Vehicle {result.veh_id}')
        self._plot.enableAutoRange()
        self._plot.setXRange(0, float(result.frequency[stop - 1]), padding=0)
        self._plot.setYRange(0, max(float(result.magnitude[:stop].max()), 1e-12), padding=0.03)
        self._info.setText(f'{n} samples; mean time increment {result.time_step:.6g} s. '
                           'Magnitude is unnormalized. Irregular sampling and gaps are '
                           'represented by the mean increment, without interpolation. '
                           'Spectrum Table shows all frequency bins for this trajectory; '
                           'Save Table exports the selected vehicle to CSV. '
                           'File → Export → Export Analysis → Spectral Analysis '
                           'saves the table for selected events.')

    def _save_table_csv(self):
        result = self._table_model.result
        if result is None:
            return
        filename = (f'spectral_analysis_e{result.event_id}_v{result.veh_id}_'
                    f'{result.selected_signal}.csv')
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Spectrum Table',
            str(Path(last_dialog_dir()) / filename), 'CSV Files (*.csv)')
        if path:
            try:
                dest = Path(path)
                if dest.suffix.lower() != '.csv':
                    dest = Path(str(dest) + '.csv')
                write_csv([result], dest)
                remember_dialog_path(str(dest))
            except Exception as exc:
                QMessageBox.warning(self, 'Export Error', str(exc))

    def _save_plot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Spectrum Plot',
            str(Path(last_dialog_dir()) / 'spectral_analysis.svg'), 'SVG Files (*.svg)')
        if path:
            try:
                Path(path).write_text(export_plot_to_svg(self._plot), encoding='utf-8')
                remember_dialog_path(path)
            except Exception as exc:
                QMessageBox.warning(self, 'Export Error', str(exc))
