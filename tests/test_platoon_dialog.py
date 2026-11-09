import csv
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication, QDialog

from analysis.platoon.platoon_comp import PlatoonConsistencyComp
from analysis.platoon.platoon_dialog import PlatoonConsistencyDialog, PlatoonResultsDialog, write_csv


class PlatoonDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'sample.db')
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE events (event_id INTEGER, veh_id INTEGER, '
                          'veh_time REAL, lane_id INTEGER, veh_dist_trav REAL, veh_speed REAL)')
        self.conn.executemany('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)',
                             [(1, 1, 0, 1, 0, 10), (1, 2, 0, 1, 4, 10),
                              (1, 1, 1, 1, 10, 10), (1, 2, 1, 1, 14, 10)])
        self.conn.commit()

    def test_parameters_results_and_csv(self):
        dialog = PlatoonConsistencyDialog(None, self.conn, event_ids=[1], veh_ids=[1])
        self.assertEqual(dialog._current_params(), {'unphysical_spacing_threshold': 5,
                                                   'leader_search_distance_threshold': 50})
        results = PlatoonConsistencyComp().comp_series(self.conn, 1, [1])
        table = PlatoonResultsDialog(None, results)
        self.assertEqual(table._table.rowCount(), 1)
        self.assertEqual(table._table.item(0, 7).text(), '4')
        dest = Path(self.temp.name) / 'results.csv'
        write_csv(results, dest)
        with dest.open() as stream:
            rows = list(csv.reader(stream))
        self.assertIn(['number_of_vehicle_pairs_with_unphysical_spacing', '1'], rows)
        self.assertIn(['percentage_of_vehicle_pairs_with_unphysical_spacing', '100'], rows)
        self.assertIn('rmspe_bias', rows[6])
        table.close()
        dialog.close()

    def test_worker_export_preserves_database(self):
        dialog = PlatoonConsistencyDialog(None, self.conn, event_ids=[1], export_mode=True)
        dest = str(Path(self.temp.name) / 'worker.csv')
        worker = dialog._create_worker(self.path, [1], None, dialog._current_params(), dest)
        finished, errors = [], []
        worker.finished.connect(finished.append)
        worker.error.connect(errors.append)
        before = list(self.conn.iterdump())
        worker.run()
        self.assertFalse(errors)
        self.assertEqual(len(finished), 1)
        self.assertTrue(Path(dest).exists())
        self.assertEqual(before, list(self.conn.iterdump()))
        dialog.close()

    def test_top_level_and_export_routes(self):
        from gui.main_window import MainWindow
        parent = SimpleNamespace(_db_connection=self.conn,
                                 _plot_widget=SimpleNamespace(_current_event_id=1))
        with patch('analysis.platoon.PlatoonConsistencyDialog') as dialog:
            MainWindow._run_platoon_consistency(parent)
            self.assertEqual(dialog.call_args.kwargs['event_ids'], [1])
            with patch('gui.event_selection_dialog.EventSelectionDialog') as selection:
                selection.return_value.exec.return_value = QDialog.DialogCode.Accepted
                selection.return_value.selected_event_ids = [1, 2]
                MainWindow._export_platoon_consistency(parent)
                self.assertEqual(dialog.call_args.kwargs['event_ids'], [1, 2])
                self.assertTrue(dialog.call_args.kwargs['export_mode'])

    def test_rightclick_routes_selected_followers(self):
        from plot.custom_view_box import CustomViewBox
        parent = SimpleNamespace(_db_connection=self.conn, _current_event_id=1)
        view = SimpleNamespace(parent_widget=parent, _get_target_vehicle_ids=Mock(return_value=[1]))
        with patch('analysis.platoon.PlatoonConsistencyDialog') as dialog:
            CustomViewBox._run_platoon_rightclick(view)
            self.assertEqual(dialog.call_args.kwargs['veh_ids'], [1])
            view._get_target_vehicle_ids.return_value = []
            dialog.reset_mock()
            CustomViewBox._run_platoon_rightclick(view)
            dialog.assert_not_called()


if __name__ == '__main__':
    unittest.main()
