"""Database-only catalog integration tests; run with unittest, no live DB needed."""
import ast
import copy
import csv
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication
from sqlalchemy import create_engine, inspect, text

from data_import.database_catalog import (CatalogCanceled, DatabaseCatalog,
                                          available_tables, open_database)
from data_import.postgres_helper import process_postgres_csv
from gui.import_dialog import ImportDialog, _ImportWorkerThread
from data_import.csv_process import CsvProcess

ROOT = Path(__file__).resolve().parents[2]


class DatabaseCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.source_path = self.base / 'source.sqlite'
        self.engine = create_engine('sqlite:///' + str(self.source_path))
        self.addCleanup(self.engine.dispose)
        self.config = json.loads((ROOT / 'src/config/tgsim-L1.json').read_text())
        self.columns = ['id', 'time', 'xloc_kf', 'yloc_kf', 'lane_kf', 'speed_kf',
                        'acceleration_kf', 'length_smoothed', 'width_smoothed',
                        'type_most_common', 'acc', 'run_index', 'travelled_distance']
        self.source_rows = [
            [42, 5.0, 10.0, 20.0, -3, 15.0, 0.5, 4.0, 2.0, 'car', 'yes', 7, 100.0],
            [42, 8.0, 10.0, 20.0, -3, 15.0, 0.5, 4.0, 2.0, 'car', 'yes', 7, 145.0],
            [55, 100.0, 10.0, 20.0, -4, 15.0, 0.5, 4.0, 2.0, 'car', 'no', 9, 200.0],
        ]
        with sqlite3.connect(self.source_path) as db:
            db.execute('CREATE TABLE "arbitrary source" (' + ','.join('"' + c + '"' for c in self.columns) + ')')
            db.executemany('INSERT INTO "arbitrary source" VALUES (' + ','.join('?' for _ in self.columns) + ')',
                           self.source_rows)
        self.worker = Mock(interrupt_flag=False)

    def build(self, config=None):
        session = DatabaseCatalog(self.engine, None, 'arbitrary source',
                                  self.config if config is None else config, 'selected.json')
        self.addCleanup(session.close)
        session.build(self.worker)
        return session

    def test_catalog_is_grouped_and_persisted_with_provenance(self):
        before = copy.deepcopy(self.config)
        session = self.build()
        self.assertEqual(self.config, before)
        self.assertEqual([r['event_id'] for r in session.rows], [7, 9])
        self.assertEqual(session.rows[0]['row_count'], 2)
        self.assertEqual(session.rows[0]['vehicle_count'], 1)
        self.assertEqual(session.rows[0]['duration'], 3.0)
        with self.engine.connect() as connection:
            rows = connection.execute(text(f'SELECT * FROM {session.catalog_name} ORDER BY event_id')).mappings().all()
            self.assertEqual(rows[0]['event_id'], 7)
            self.assertEqual(rows[0]['source_table'], 'arbitrary source')
            self.assertEqual(json.loads(rows[0]['configuration_json']), before)
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM "arbitrary source"')).scalar(), 3)

    def test_selected_event_reaches_existing_sqlite_importer(self):
        session = self.build()
        path, mapping = session.export_event(9)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        processor = CsvProcess()
        self.assertEqual(processor.check_configuration(mapping, Mock()), 0)
        output = self.base / 'selected.sqlite'
        worker = _ImportWorkerThread(processor, path, str(output))
        worker.run()
        self.assertTrue(worker.success)
        with sqlite3.connect(output) as db:
            self.assertEqual(db.execute('SELECT event_id, veh_id, lane_id, orig_lane_id FROM events').fetchall(),
                             [(9, 55, 4, -4)])
            self.assertEqual(db.execute('SELECT event_id FROM ranges').fetchall(), [(9,)])

    def test_group_mapping_ids_preserved_after_event_selection(self):
        config = copy.deepcopy(self.config)
        config['col_defs']['event_id'] = {'func': 'group', 'parms': [11], 'type': 'int'}
        session = self.build(config)
        self.assertEqual([r['event_id'] for r in session.rows], [1, 2])
        path, mapping = session.export_event(2)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        with open(path, newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['event_id'], '2')

    def test_const_event_mapping_yields_one_catalog_entry(self):
        config = copy.deepcopy(self.config)
        config['col_defs']['event_id'] = {'func': 'const', 'parms': [1], 'type': 'int'}
        session = self.build(config)
        self.assertEqual(len(session.rows), 1)
        self.assertEqual(session.rows[0]['event_id'], 1)
        self.assertEqual(session.rows[0]['row_count'], 3)
        self.assertEqual(session.rows[0]['vehicle_count'], 2)

    def test_units_applied_once(self):
        config = copy.deepcopy(self.config)
        config['col_defs']['veh_speed']['units'] = 'kph'
        session = self.build(config)
        path, mapping = session.export_event(7)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        self.assertNotIn('units', mapping['col_defs']['veh_speed'])
        with open(path, newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertAlmostEqual(float(row['veh_speed']), 15 / 3.6)

    def test_repeated_builds_reuse_catalog_without_scanning_source(self):
        first = self.build()
        with patch.object(DatabaseCatalog, 'prepare_events', side_effect=AssertionError('source scan')), \
             patch.object(DatabaseCatalog, '_save_catalog', side_effect=AssertionError('catalog write')):
            second = self.build()
        self.assertEqual(first.catalog_name, second.catalog_name)
        self.assertEqual(first.rows, second.rows)
        self.assertFalse(second.sqlite_path.exists())
        self.assertEqual(len(inspect(self.engine).get_table_names()), 2)
        self.assertEqual(available_tables(self.engine), [('main', 'arbitrary source')])

    def test_reused_catalog_can_export_selected_event(self):
        first = self.build()
        second = self.build()
        second.prepare_events(self.worker)
        path, _ = second.export_event(9)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        with open(path, newline='') as handle:
            self.assertEqual([row['event_id'] for row in csv.DictReader(handle)], ['9'])
        self.assertEqual(first.catalog_name, second.catalog_name)

    def test_changed_json_creates_a_separate_catalog(self):
        first = self.build()
        config = copy.deepcopy(self.config)
        config['col_defs']['veh_speed']['units'] = 'kph'
        second = self.build(config)
        self.assertNotEqual(first.catalog_name, second.catalog_name)
        self.assertIn(first.catalog_name, inspect(self.engine).get_table_names())

    def test_different_source_table_does_not_reuse_catalog(self):
        first = self.build()
        with self.engine.begin() as connection:
            connection.execute(text('CREATE TABLE other_source AS SELECT * FROM "arbitrary source"'))
        second = DatabaseCatalog(self.engine, None, 'other_source', self.config, 'selected.json')
        self.addCleanup(second.close)
        second.build(self.worker)
        self.assertNotEqual(first.catalog_name, second.catalog_name)

    def test_validator_metadata_does_not_prevent_reuse(self):
        first = self.build()
        config = copy.deepcopy(self.config)
        config.setdefault('file_ext', 'csv')
        CsvProcess().check_configuration(config, Mock())
        second = self.build(config)
        self.assertEqual(first.catalog_name, second.catalog_name)

    def test_catalog_from_previous_code_reused_for_copy_event_mapping(self):
        first = self.build()
        with self.engine.begin() as connection:
            connection.execute(text(f'ALTER TABLE {first.catalog_name} DROP COLUMN group_keys_json'))
        second = self.build()
        self.assertEqual(first.catalog_name, second.catalog_name)

    def test_group_ids_survive_source_row_reordering_when_catalog_reused(self):
        config = copy.deepcopy(self.config)
        config['col_defs']['event_id'] = {'func': 'group', 'parms': [11], 'type': 'int'}
        first = self.build(config)
        with sqlite3.connect(self.source_path) as db:
            db.execute('DELETE FROM "arbitrary source"')
            db.executemany('INSERT INTO "arbitrary source" VALUES (' + ','.join('?' for _ in self.columns) + ')',
                           list(reversed(self.source_rows)))
        second = self.build(config)
        self.assertEqual(first.catalog_name, second.catalog_name)
        second.prepare_events(self.worker)
        path, _ = second.export_event(2)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        with open(path, newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([(row['event_id'], row['veh_id']) for row in rows], [('2', '55')])

    def test_interruption_does_not_write_catalog(self):
        self.worker.interrupt_flag = True
        with self.assertRaises(CatalogCanceled):
            self.build()
        self.assertEqual(inspect(self.engine).get_table_names(), ['arbitrary source'])

    def test_empty_table_does_not_write_catalog(self):
        with self.engine.begin() as connection:
            connection.execute(text('DELETE FROM "arbitrary source"'))
        with self.assertRaisesRegex(ValueError, 'no rows'):
            self.build()
        self.assertEqual(inspect(self.engine).get_table_names(), ['arbitrary source'])

    def test_catalog_write_failure_keeps_source_unchanged(self):
        with patch.object(DatabaseCatalog, '_save_catalog', side_effect=PermissionError('write denied')):
            with self.assertRaises(PermissionError):
                self.build()
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM "arbitrary source"')).scalar(), 3)

    def test_db_ui_runs_source_and_event_selection(self):
        with patch('data_import.postgres_helper.open_database', return_value=self.engine), \
             patch('data_import.postgres_helper.QInputDialog.getItem', return_value=('main.arbitrary source', True)), \
             patch('data_import.postgres_helper._choose_event', return_value=9):
            result = process_postgres_csv(None, 'uri.csv', self.config, 'selected.json', Mock())
        self.assertIsNotNone(result)
        path, mapping = result
        self.addCleanup(Path(path).unlink, missing_ok=True)
        with open(path, newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r['event_id'] for r in rows], ['9'])

    def make_dialog(self):
        with patch.object(ImportDialog, '_initialize'), \
             patch('gui.import_dialog.get_user_data_path', side_effect=lambda name: self.base / name):
            dialog = ImportDialog()
        self.addCleanup(dialog.deleteLater)
        dialog._configs = {'tgsim-L1': {'config': copy.deepcopy(self.config), 'processor_class': CsvProcess}}
        return dialog

    def test_db_button_chooses_uri_json_output_without_changing_csv_choices(self):
        session = self.build()
        path, mapping = session.export_event(9)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        dialog = self.make_dialog()
        before = copy.deepcopy(dialog._configs)
        output = str(self.base / 'chosen.sqlite')
        with patch('gui.import_dialog.QFileDialog.getOpenFileName', return_value=
                   (str(self.base / 'uri.csv'), 'tgsim-L1 (*.csv)')) as select_uri, patch('gui.import_dialog.QFileDialog.getSaveFileName', return_value=(output, '')), \
             patch('gui.import_dialog.process_postgres_csv', return_value=(path, mapping)) as prepare, \
             patch('gui.import_dialog._ImportWorkerThread') as thread:
            dialog._select_db_input()
        self.assertEqual(dialog._configs, before)
        select_uri.assert_called_once()
        self.assertIn('tgsim-L1 (*.csv)', select_uri.call_args.args[3])
        self.assertEqual(prepare.call_args.args[1], str(self.base / 'uri.csv'))
        self.assertEqual(prepare.call_args.args[2], self.config)
        self.assertEqual(prepare.call_args.args[3], 'tgsim-L1.json')
        self.assertEqual(thread.call_args.args[1:], (path, output))
        thread.return_value.start.assert_called_once()

    def test_cancel_output_removes_only_generated_event_csv(self):
        session = self.build()
        path, mapping = session.export_event(9)
        uri = self.base / 'uri.csv'
        uri.write_text('connection URI')
        dialog = self.make_dialog()
        with patch('gui.import_dialog.QFileDialog.getOpenFileName', return_value=
                   (str(uri), 'tgsim-L1 (*.csv)')), patch('gui.import_dialog.QFileDialog.getSaveFileName', return_value=('', '')), \
             patch('gui.import_dialog.process_postgres_csv', return_value=(path, mapping)), \
             patch('gui.import_dialog._ImportWorkerThread') as thread:
            dialog._select_db_input()
        thread.assert_not_called()
        self.assertFalse(Path(path).exists())
        self.assertTrue(uri.exists())

    def test_cancel_source_selection_leaves_database_unchanged(self):
        with patch('data_import.postgres_helper.open_database', return_value=self.engine), \
             patch('data_import.postgres_helper.QInputDialog.getItem', return_value=('', False)):
            self.assertIsNone(process_postgres_csv(None, 'uri.csv', self.config, 'selected.json', Mock()))
        self.assertEqual(inspect(self.engine).get_table_names(), ['arbitrary source'])

    def test_placeholder_hostname_rejected_before_connecting(self):
        uri = self.base / 'uri.csv'
        uri.write_text('postgresql+psycopg2://USERNAME:PASSWORD@HOST:5432/DATABASE\n')
        with patch('data_import.database_catalog.create_engine') as create:
            with self.assertRaisesRegex(ValueError, 'placeholder connection details'):
                open_database(uri)
        create.assert_not_called()

    def test_password_prompt_supplies_password_without_changing_uri_file(self):
        uri = self.base / 'uri.csv'
        original = 'postgresql+psycopg2://postgres@127.0.0.1:5432/tgsim\n'
        uri.write_text(original)
        prompt = Mock(return_value=('test@password', True))
        with patch('data_import.database_catalog.create_engine') as create:
            open_database(uri, password_prompt=prompt)
        prompt.assert_called_once()
        self.assertEqual(create.call_args.args[0].password, 'test@password')
        self.assertEqual(uri.read_text(), original)

    def test_cancel_password_prompt_does_not_connect(self):
        uri = self.base / 'uri.csv'
        uri.write_text('postgresql+psycopg2://postgres@127.0.0.1:5432/tgsim\n')
        with patch('data_import.database_catalog.create_engine') as create:
            with self.assertRaises(CatalogCanceled):
                open_database(uri, password_prompt=lambda: ('', False))
        create.assert_not_called()

    def test_csv_procedure_and_json_files_unchanged(self):
        old = subprocess.check_output(['git', 'show', 'HEAD:src/gui/import_dialog.py'], cwd=ROOT, text=True)
        new = (ROOT / 'src/gui/import_dialog.py').read_text()
        old_cls = next(n for n in ast.parse(old).body if isinstance(n, ast.ClassDef) and n.name == 'ImportDialog')
        new_cls = next(n for n in ast.parse(new).body if isinstance(n, ast.ClassDef) and n.name == 'ImportDialog')
        old_methods = {n.name: ast.dump(n) for n in old_cls.body if isinstance(n, ast.FunctionDef)}
        new_methods = {n.name: ast.dump(n) for n in new_cls.body if isinstance(n, ast.FunctionDef)}
        for name, method in old_methods.items():
            if name != '_select_db_input':
                self.assertEqual(method, new_methods[name], name)
        changed = subprocess.check_output(['git', 'diff', '--name-only', '--', 'src/config'], cwd=ROOT, text=True)
        self.assertEqual(changed, '')


if __name__ == '__main__':
    unittest.main()
