"""Parameter entry, background computation, results and CSV export."""
import csv
from dataclasses import asdict, fields
import math
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QDoubleSpinBox,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QFileDialog, QMessageBox,
)

from analysis.base_analysis_dialog import BaseAnalysisDialog
from analysis.base_analysis_worker import BaseAnalysisWorker
from utils import last_dialog_dir, remember_dialog_path
from .platoon_comp import PlatoonConsistencyComp, PlatoonPairResult, summarize


COLUMNS = [
    ('Event ID', 'event_id'), ('Follower ID', 'follower_id'),
    ('Leader ID', 'leader_id'), ('Segment ID', 'segment_id'),
    ('Start time (s)', 'start_time'), ('End time (s)', 'end_time'),
    ('Samples', 'sample_count'), ('Min. spacing (m)', 'minimum_spacing'),
    ('Min. bias (m)', 'min_bias'), ('Max. bias (m)', 'max_bias'),
    ('Mean bias (m)', 'mean_bias'), ('RMSE bias (m)', 'rmse_bias'),
    ('RMSPE bias (ratio)', 'rmspe_bias'), ('Valid bias samples', 'valid_bias_count'),
    ('Unphysical spacing', 'unphysical_spacing'),
]


def display(value):
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, float):
        return f'{value:.6g}' if math.isfinite(value) else 'N/A'
    return str(value)


def write_csv(results, dest):
    """Selection summary followed by the report's per-segment table."""
    with open(dest, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['Platoon Consistency'])
        for key, value in summarize(results).items():
            writer.writerow([key, display(value)])
        writer.writerow([])
        names = [f.name for f in fields(PlatoonPairResult)]
        writer.writerow(names)
        for result in results:
            for pair in result.pairs:
                values = asdict(pair)
                writer.writerow(['' if isinstance(values[n], float) and
                                 not math.isfinite(values[n]) else values[n] for n in names])


class PlatoonConsistencyDialog(BaseAnalysisDialog):
    def _csv_export_label(self):
        return 'Platoon Consistency'

    def _build_parameter_form(self):
        form = QFormLayout()
        self._spacing_spin = QDoubleSpinBox()
        self._spacing_spin.setRange(0, 1000000)
        self._spacing_spin.setDecimals(3)
        self._spacing_spin.setValue(5)
        self._spacing_spin.setSuffix(' m')
        form.addRow('Unphysical spacing threshold:', self._spacing_spin)
        self._search_spin = QDoubleSpinBox()
        self._search_spin.setRange(0.001, 1000000)
        self._search_spin.setDecimals(3)
        self._search_spin.setValue(50)
        self._search_spin.setSuffix(' m')
        form.addRow('Leader search distance threshold:', self._search_spin)
        hint = QLabel('Selected trajectories are followers; leaders may be any vehicle in the event.\n'
                      'Missing speeds leave bias metrics unavailable. RMSPE is a ratio.')
        hint.setWordWrap(True)
        form.addRow(hint)
        return form

    def _current_params(self):
        return {'unphysical_spacing_threshold': self._spacing_spin.value(),
                'leader_search_distance_threshold': self._search_spin.value()}

    def _set_controls_enabled(self, enabled):
        super()._set_controls_enabled(enabled)
        self._spacing_spin.setEnabled(enabled)
        self._search_spin.setEnabled(enabled)

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        return BaseAnalysisWorker(db_path, event_ids, veh_ids, params, dest_path,
                                  self._run_for_event, write_csv if dest_path else None)

    @staticmethod
    def _run_for_event(conn, event_id, veh_ids, params, progress_callback=None):
        result = PlatoonConsistencyComp().comp_series(conn, event_id, veh_ids, params)
        if progress_callback:
            progress_callback()
        return result

    def _show_results(self, results):
        dlg = PlatoonResultsDialog(self.parent(), results)
        dlg.exec()


class PlatoonResultsDialog(QDialog):
    def __init__(self, parent, results):
        super().__init__(parent)
        self._results = results
        self.setWindowTitle('Platoon Consistency Results')
        self.resize(1200, 550)
        layout = QVBoxLayout(self)
        summary = summarize(results)
        label = QLabel(
            f"Vehicle pair ID flips: {summary['number_of_vehicle_pair_id_flips']}\n"
            f"Pairs with unphysical spacing: {summary['number_of_vehicle_pairs_with_unphysical_spacing']} "
            f"of {summary['number_of_vehicle_pairs']}\n"
            f"Percentage with unphysical spacing: "
            f"{display(summary['percentage_of_vehicle_pairs_with_unphysical_spacing'])}%")
        layout.addWidget(label)
        pairs = [p for result in results for p in result.pairs]
        if not pairs:
            layout.addWidget(QLabel('No valid leader–follower pairs were found.'))
        layout.addWidget(QLabel('Each row is a continuous pair segment. RMSPE is a ratio (0.1 = 10%).\n'
                                'N/A indicates unavailable data; valid bias samples show metric coverage.'))
        self._table = QTableWidget(len(pairs), len(COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in COLUMNS])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for i, pair in enumerate(pairs):
            for j, (_, attr) in enumerate(COLUMNS):
                self._table.setItem(i, j, QTableWidgetItem(display(getattr(pair, attr))))
        layout.addWidget(self._table)
        buttons = QHBoxLayout()
        buttons.addStretch()
        save = QPushButton('Save CSV…')
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Platoon Consistency',
            str(Path(last_dialog_dir()) / 'platoon_consistency.csv'), 'CSV Files (*.csv)')
        if path:
            try:
                write_csv(self._results, path)
                remember_dialog_path(path)
            except OSError as exc:
                QMessageBox.warning(self, 'Export Error', str(exc))
