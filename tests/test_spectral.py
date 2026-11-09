import csv
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
from PyQt6.QtWidgets import QApplication, QDialog
from PyQt6.QtCore import Qt
from analysis.spectral.spectral_comp import SpectralComp, write_csv
from analysis.spectral.spectral_dialog import SpectralDialog, SpectralResultsDialog
from ui_kit.plot_export import export_plot_to_svg


class SpectralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE events (event_id, veh_id, veh_time REAL, '
                          'veh_speed REAL, veh_accel REAL)')

    def add(self, values, times=None, event=1, vehicle=1):
        if times is None:
            times = np.arange(len(values)).tolist()
        self.conn.executemany('INSERT INTO events VALUES (?, ?, ?, ?, ?)',
                              [(event, vehicle, t, x, 2*x if x is not None else None)
                               for t, x in zip(times, values)])

    def test_sine_dft_and_frequency(self):
        n = 16
        values = np.sin(2*np.pi*3*np.arange(n)/n)
        self.add(values[::-1], (np.arange(n)*0.1)[::-1].tolist())
        result = SpectralComp().comp_event(self.conn, 1)[0]
        expected = [sum(values[k]*np.exp(-2j*np.pi*f*k/n) for k in range(n))
                    for f in range(n)]
        np.testing.assert_allclose(result.fourier_coefficient, expected, atol=1e-13)
        self.assertAlmostEqual(result.frequency[3], 1.875)
        self.assertAlmostEqual(result.magnitude[3], 8)
        self.assertEqual(len(result.frequency), n)

    def test_grouping_signal_selection_and_no_mutation(self):
        self.add([3, 3, 3, 3])
        self.add([5, 5], vehicle=2)
        self.add([9, 9], event=2)
        before = self.conn.execute('SELECT * FROM events').fetchall()
        results = SpectralComp().comp_series(self.conn, 1, [2], {'selected_signal': 'veh_accel'})
        self.assertEqual(len(results), 1)
        np.testing.assert_allclose(results[0].magnitude, [20, 0])
        self.assertEqual(before, self.conn.execute('SELECT * FROM events').fetchall())
        self.assertEqual(SpectralComp().comp_series(self.conn, 1, []), [])
        with self.assertRaises(ValueError):
            SpectralComp().comp_event(self.conn, 1, {'selected_signal': 'other'})

    def test_one_sided_amplitude_and_power_analytic_signal(self):
        # An odd length also checks that the highest bin is doubled when it
        # has a negative-frequency partner (it is not a Nyquist bin).
        for n in (15, 16):
            with self.subTest(n=n):
                k = n // 2 - (n % 2 == 0)
                values = 20 + 2*np.sin(2*np.pi*k*np.arange(n)/n)
                self.add(values, vehicle=n)
                result = SpectralComp().comp_series(self.conn, 1, [n])[0]
                original = result.magnitude.copy()
                amplitude = result.one_sided_values('amplitude')
                power = result.one_sided_values('power')
                expected_amplitude = np.zeros(n // 2 + 1)
                expected_amplitude[0], expected_amplitude[k] = 20, 2
                expected_power = np.zeros(n // 2 + 1)
                expected_power[0], expected_power[k] = 400, 2
                np.testing.assert_allclose(amplitude, expected_amplitude, atol=1e-12)
                np.testing.assert_allclose(power, expected_power, atol=1e-12)
                self.assertAlmostEqual(power.sum(), np.mean(values**2))
                np.testing.assert_array_equal(result.magnitude, original)

    def test_nyquist_and_total_power(self):
        for n in (2, 3, 15, 16):
            with self.subTest(n=n):
                values = 3 + 4*(-1.)**np.arange(n)
                self.add(values, vehicle=n)
                result = SpectralComp().comp_series(self.conn, 1, [n])[0]
                self.assertAlmostEqual(result.one_sided_values('power').sum(),
                                       np.mean(values**2))
                if n % 2 == 0:
                    self.assertAlmostEqual(result.one_sided_values('amplitude')[-1], 4)
                    self.assertAlmostEqual(result.one_sided_values('power')[-1], 16)

    def test_missing_and_degenerate_samples(self):
        self.add([1, None, 2, float('inf'), 3], [0, 1, 2, 3, 6])
        self.add([None, 2], vehicle=2)
        self.add([1, 2], [0, 0], vehicle=3)
        results = SpectralComp().comp_event(self.conn, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].time_step, 3)
        np.testing.assert_allclose(results[0].fourier_coefficient, np.fft.fft([1, 2, 3]))

    def test_removed_trajectories_and_placeholder_rows_are_excluded(self):
        from utils import IMPUTED_ORIGINAL, IMPUTED_SPLINE, TRAJ_KEPT, TRAJ_REMOVED
        self.add([1, 2, 100])
        self.add([10, 20], vehicle=2)
        self.conn.execute('ALTER TABLE events ADD COLUMN traj_status TEXT')
        self.conn.execute('ALTER TABLE events ADD COLUMN imputed_status TEXT')
        self.conn.execute('UPDATE events SET traj_status = ?, imputed_status = ?',
                          (TRAJ_KEPT, IMPUTED_ORIGINAL))
        self.conn.execute('UPDATE events SET traj_status = ? WHERE veh_id = 2',
                          (TRAJ_REMOVED,))
        self.conn.execute('UPDATE events SET imputed_status = ? '
                          'WHERE veh_id = 1 AND veh_time = 2', (IMPUTED_SPLINE,))
        results = SpectralComp().comp_event(self.conn, 1)
        self.assertEqual([r.veh_id for r in results], [1])
        np.testing.assert_allclose(results[0].fourier_coefficient, np.fft.fft([1, 2]))
        self.assertEqual(SpectralComp().comp_series(self.conn, 1, [2]), [])

    def test_existing_time_diff_and_fallback(self):
        self.add([1, 2, 3], [0, 2, 6])
        self.conn.execute('ALTER TABLE events ADD COLUMN time_diff REAL')
        self.conn.execute('UPDATE events SET time_diff = 0.5 WHERE veh_time > 0')
        self.assertEqual(SpectralComp().comp_event(self.conn, 1)[0].time_step, 0.5)
        self.conn.execute('UPDATE events SET time_diff = NULL WHERE veh_time = 2')
        self.assertEqual(SpectralComp().comp_event(self.conn, 1)[0].time_step, 3)

    def test_plot_navigation_csv_and_svg(self):
        self.add([1, 2, 3, 4])
        self.add([1, 2, 3, 4, 5], vehicle=2)
        results = SpectralComp().comp_event(self.conn, 1)
        dialog = SpectralResultsDialog(None, results)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(len(dialog._plot.listDataItems()[0].xData), 3)
        model = dialog._table_model
        self.assertEqual(model.rowCount(), 4)
        self.assertEqual(model.columnCount(), 6)
        self.assertEqual(model.headerData(4, Qt.Orientation.Horizontal), 'Fourier Coefficient')
        self.assertEqual(complex(model.data(model.index(0, 4))), 10)
        self.assertEqual(float(model.data(model.index(3, 3))), results[0].frequency[3])
        self.assertFalse(dialog._previous.isEnabled())
        dialog._next.click()
        self.assertEqual(dialog._trajectory_combo.currentIndex(), 1)
        self.assertFalse(dialog._next.isEnabled())
        self.assertEqual(model.rowCount(), 5)
        self.assertEqual(model.data(model.index(0, 1)), '2')
        self.assertEqual(complex(model.data(model.index(0, 4))), 15)
        self.assertEqual(len(dialog._plot.listDataItems()[0].xData), 3)
        self.assertIn('<svg', export_plot_to_svg(dialog._plot))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum.csv'
            write_csv(results, path)
            with path.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 9)
            self.assertEqual(complex(rows[0]['fourier_coefficient']), 10)
            self.assertEqual(rows[-1]['veh_id'], '2')
        empty = SpectralResultsDialog(None, [])
        self.assertFalse(empty._save.isEnabled())
        self.assertEqual(empty._table_model.rowCount(), 0)
        empty.close()

    def test_spectrum_selector_plot_navigation_and_export(self):
        for n in (15, 16):
            self.add(20 + 2*np.sin(2*np.pi*3*np.arange(n)/n), vehicle=n)
        results = SpectralComp().comp_event(self.conn, 1)
        dialog = SpectralResultsDialog(None, results)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog._spectrum_combo.currentData(), 'magnitude')
        self.assertAlmostEqual(dialog._plot.listDataItems()[0].yData[3], 15)
        dialog._spectrum_combo.setCurrentIndex(1)
        self.assertAlmostEqual(dialog._plot.listDataItems()[0].yData[3], 2)
        self.assertIn('Amplitude (m/s)', dialog._plot.getAxis('left').labelText)
        self.assertEqual(dialog._tabs.tabText(0), 'Amplitude Spectrum')
        dialog._next.click()
        self.assertAlmostEqual(dialog._plot.listDataItems()[0].yData[3], 2)
        dialog._spectrum_combo.setCurrentIndex(2)
        self.assertAlmostEqual(dialog._plot.listDataItems()[0].yData[3], 2)
        self.assertAlmostEqual(dialog._plot.listDataItems()[0].yData[0], 400)
        self.assertIn('Power ((m/s)²)', dialog._plot.getAxis('left').labelText)
        self.assertIn('Power', export_plot_to_svg(dialog._plot))
        # The table continues to expose raw coefficients and magnitudes.
        self.assertAlmostEqual(float(dialog._table_model.data(
            dialog._table_model.index(3, 5))), 16)
        dialog._spectrum_combo.setCurrentIndex(0)
        self.assertAlmostEqual(dialog._plot.listDataItems()[0].yData[3], 16)
        accel_results = SpectralComp().comp_event(
            self.conn, 1, {'selected_signal': 'veh_accel'})
        accel = SpectralResultsDialog(None, accel_results)
        self.addCleanup(accel.close)
        accel._spectrum_combo.setCurrentIndex(1)
        self.assertAlmostEqual(accel._plot.listDataItems()[0].yData[3], 4)
        self.assertIn('Amplitude (m/s²)', accel._plot.getAxis('left').labelText)
        accel._spectrum_combo.setCurrentIndex(2)
        self.assertIn('Power ((m/s²)²)', accel._plot.getAxis('left').labelText)
        empty = SpectralResultsDialog(None, [])
        self.addCleanup(empty.close)
        for index in (1, 2, 0):
            empty._spectrum_combo.setCurrentIndex(index)
            self.assertFalse(empty._save.isEnabled())
            self.assertEqual(empty._plot.listDataItems(), [])

    def test_dialog_parameters_and_missing_columns(self):
        dialog = SpectralDialog(None, self.conn, event_ids=[1])
        self.assertFalse(dialog.RIGHT_CLICK_ENABLED)
        self.assertEqual(dialog._current_params(), {'selected_signal': 'veh_speed'})
        dialog._signal_combo.setCurrentIndex(1)
        self.assertEqual(dialog._current_params(), {'selected_signal': 'veh_accel'})
        dialog.close()
        self.conn.execute('DROP TABLE events')
        self.conn.execute('CREATE TABLE events (event_id, veh_id, veh_time)')
        dialog = SpectralDialog(None, self.conn, event_ids=[1])
        self.assertFalse(dialog._primary_btn.isEnabled())
        dialog.close()
        with self.assertRaises(ValueError):
            SpectralComp().comp_event(self.conn, 1)

    def test_worker_multi_event_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / 'events.db')
            self.add([1, 2])
            self.add([3, 4], event=2)
            self.conn.commit()
            with sqlite3.connect(db_path) as target:
                self.conn.backup(target)
            dialog = SpectralDialog(None, self.conn, event_ids=[1, 2], export_mode=True)
            dest = str(Path(directory) / 'result.csv')
            worker = dialog._create_worker(db_path, [1, 2], None, dialog._current_params(), dest)
            completed, errors, progress = [], [], []
            worker.finished.connect(completed.append)
            worker.error.connect(errors.append)
            worker.progress.connect(progress.append)
            worker.run()
            self.assertEqual(errors, [])
            self.assertEqual(len(completed[0]), 2)
            self.assertEqual(progress, [1, 2])
            with open(dest) as stream:
                self.assertEqual({r['event_id'] for r in csv.DictReader(stream)}, {'1', '2'})
            dialog.close()

    def test_main_window_handlers(self):
        from gui.main_window import MainWindow
        window = SimpleNamespace(_db_connection=self.conn,
                                 _plot_widget=SimpleNamespace(_current_event_id=7))
        with patch('analysis.spectral.SpectralDialog') as dialog:
            MainWindow._run_spectral_analysis(window)
            dialog.assert_called_once_with(window, self.conn, event_ids=[7])
            with patch('gui.event_selection_dialog.EventSelectionDialog') as selection:
                selection.return_value.exec.return_value = QDialog.DialogCode.Accepted
                selection.return_value.selected_event_ids = [1, 2]
                MainWindow._export_spectral_analysis(window)
                dialog.assert_called_with(window, self.conn, event_ids=[1, 2], export_mode=True)

    def test_merged_noise_and_export_menus_have_working_actions(self):
        from gui.main_window import MainWindow
        with patch.object(MainWindow, '_load_settings'), \
                patch.object(MainWindow, '_setup_home_page'), \
                patch.object(MainWindow, '_load_home_page'):
            window = MainWindow()
        self.addCleanup(window.close)
        noise_menu = window._analysis_menu.actions()[0].menu()
        self.assertEqual(noise_menu.title(), '&Noise')
        actions = {a.text().replace('&', ''): a for a in noise_menu.actions()}
        for label in ('Platoon Consistency', 'Spectral Analysis'):
            self.assertTrue(actions[label].isEnabled())
        exports = next(a.menu() for a in window._export_menu.actions()
                       if a.menu() and 'Analysis' in a.text())
        export_labels = {a.text().replace('&', '') for a in exports.actions()}
        self.assertTrue({'Platoon Consistency', 'Spectral Analysis'} <= export_labels)


if __name__ == '__main__':
    unittest.main()
