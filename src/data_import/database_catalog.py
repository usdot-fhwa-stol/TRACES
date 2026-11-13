"""Build a persisted event catalog using an unchanged CSV JSON mapping.

Source columns are read in the database's ordinal order, matching the positional
indices in col_defs. Mapping and unit conversion use CsvProcess itself. Only the
new catalog is written to the source database; source trajectories are read only.
"""
import copy
import csv
import hashlib
import json
import sqlite3
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import (BigInteger, Column, Float, MetaData, String, Table,
                        create_engine, inspect, select)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from .csv_process import CsvProcess


class CatalogCanceled(Exception):
    pass


def open_database(uri_path, password_prompt=None):
    with open(uri_path, newline='', encoding='utf-8-sig') as handle:
        row = next(csv.reader(handle), [])
    if len(row) != 1 or not row[0].strip():
        raise ValueError('URI CSV must contain one connection URI without a header.')
    uri = make_url(row[0].strip())
    placeholders = {'HOST', 'USERNAME', 'PASSWORD', 'DATABASE'}
    if any(value in placeholders for value in
           (uri.host, uri.username, uri.database)):
        raise ValueError('URI CSV still contains placeholder connection details. '
                         'Replace HOST, USERNAME, PASSWORD and DATABASE with the '
                         'PostgreSQL connection values from pgAdmin.')
    if password_prompt is not None and uri.password in (None, '', 'PASSWORD'):
        password, accepted = password_prompt()
        if not accepted:
            raise CatalogCanceled('Database password entry canceled.')
        uri = uri.set(password=password)
    elif uri.password == 'PASSWORD':
        raise ValueError('Replace PASSWORD in the URI CSV with your PostgreSQL password.')
    return create_engine(uri, connect_args={'connect_timeout': 10})


def available_tables(engine):
    inspector = inspect(engine)
    return [(schema, name) for schema in inspector.get_schema_names()
            if schema != 'information_schema' and not schema.startswith('pg_')
            for name in inspector.get_table_names(schema=schema)
            if not name.startswith('traces_catalog_')]


class DatabaseCatalog:
    """Own local staging files until an event has been selected/exported."""

    def __init__(self, engine, schema, table_name, config, config_name):
        self.engine = engine
        self.schema = schema
        self.table_name = table_name
        self.config = copy.deepcopy(config)
        self.config_name = config_name
        self.catalog_name = 'traces_catalog_' + uuid.uuid4().hex[:16]
        self._directory = tempfile.TemporaryDirectory(prefix='traces_db_')
        self.sqlite_path = Path(self._directory.name) / 'mapped.sqlite'
        self.rows = []
        self.columns = {}
        self.group_keys = {}

    def close(self):
        self._directory.cleanup()

    @staticmethod
    def _configuration_key(config):
        """Compare mappings independently of validator metadata and JSON ordering."""
        if not isinstance(config, dict):
            return None
        normalized = copy.deepcopy(config)
        normalized.setdefault('class', 'data_import.csv_process.CsvProcess')
        normalized.setdefault('file_ext', 'csv')
        processor = CsvProcess()
        if processor.check_configuration(normalized, lambda *_: None):
            return None

        def clean(value):
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items() if not key.startswith('_')}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value

        return json.dumps(clean(normalized), sort_keys=True)

    def _load_catalog(self, worker):
        required = {'event_id', 'event_name', 'row_count', 'vehicle_count',
                    'start_time', 'end_time', 'duration', 'source_schema',
                    'source_table', 'configuration_json'}
        key = self._configuration_key(self.config)
        definitions = self.config['col_defs']
        definitions = [definitions] if isinstance(definitions, dict) else definitions
        uses_groups = any(column.get('func') == 'group'
                          for group in definitions for column in group.values())
        names = inspect(self.engine).get_table_names(schema=self.schema)
        for name in sorted(names):
            self._check_canceled(worker)
            if not name.startswith('traces_catalog_'):
                continue
            try:
                with self.engine.connect() as connection:
                    catalog = Table(name, MetaData(), schema=self.schema, autoload_with=connection)
                    if not required.issubset(catalog.c.keys()):
                        continue
                    if uses_groups and 'group_keys_json' not in catalog.c:
                        # Older group catalogs did not persist ID assignments.
                        continue
                    query = select(catalog).where(
                        catalog.c.source_table == self.table_name,
                        catalog.c.source_schema.is_not_distinct_from(self.schema))
                    first = connection.execute(query.limit(1)).mappings().first()
                    if first is None or self._configuration_key(json.loads(first['configuration_json'])) != key:
                        continue
                    rows = connection.execute(query.order_by(catalog.c.event_id)).mappings().all()
                    if not rows:
                        continue
                    group_keys = json.loads(first.get('group_keys_json') or '{}')
                    if not isinstance(group_keys, dict):
                        continue
                    if uses_groups and not group_keys:
                        continue
                    self.rows = [{field: row[field] for field in
                                  ('event_id', 'event_name', 'row_count', 'vehicle_count',
                                   'start_time', 'end_time', 'duration')} for row in rows]
                    self.group_keys = group_keys
                    self.catalog_name = name
                    worker.log_signal.emit(f'Info: Reusing catalog {self.schema}.{name} '
                                           f'with {len(self.rows)} event(s).', True)
                    return True
            except (SQLAlchemyError, ValueError, TypeError):
                # Other/incompatible catalog tables are not reusable for this mapping.
                continue
        return False

    def build(self, worker):
        self._check_canceled(worker)
        # Validate even when a saved catalog exists.
        self._validated_processor(worker)
        if self._load_catalog(worker):
            return
        self.prepare_events(worker)
        with sqlite3.connect(self.sqlite_path) as db:
            name_expr = 'MAX(event_name)' if 'event_name' in self.columns else 'CAST(event_id AS TEXT)'
            cursor = db.execute(f'''SELECT event_id, {name_expr}, COUNT(*), COUNT(DISTINCT veh_id),
                                    MIN(veh_time), MAX(veh_time), MAX(veh_time)-MIN(veh_time)
                                    FROM events GROUP BY event_id ORDER BY event_id''')
            self.rows = [dict(zip(('event_id', 'event_name', 'row_count', 'vehicle_count',
                                   'start_time', 'end_time', 'duration'), row)) for row in cursor]
            if not self.rows or any(row['event_id'] is None or row['duration'] is None for row in self.rows):
                raise ValueError('Mapped data needs valid event IDs and vehicle times.')
        self._check_canceled(worker)
        self._save_catalog()
        worker.log_signal.emit(f'Info: Saved catalog {self.schema}.{self.catalog_name} '
                               f'with {len(self.rows)} event(s).', True)

    def _validated_processor(self, worker):
        processor = CsvProcess()
        if self.config.get('class', 'data_import.csv_process.CsvProcess') != 'data_import.csv_process.CsvProcess':
            raise ValueError('Database catalogs require a CSV column-mapping configuration.')
        if processor.check_configuration(copy.deepcopy(self.config), worker.log_signal.emit):
            raise ValueError('Invalid JSON column mapping.')
        processor._algorithms._group_dict.update(self.group_keys)
        return processor

    def prepare_events(self, worker):
        """Read source trajectories only when needed for a new catalog or export."""
        self._check_canceled(worker)
        if self.sqlite_path.exists():
            return
        processor = self._validated_processor(worker)
        raw_path = Path(self._directory.name) / 'source.csv'
        with self.engine.connect() as connection:
            table = Table(self.table_name, MetaData(), schema=self.schema, autoload_with=connection)
            worker.log_signal.emit('Info: Reading source columns in database order: ' +
                                   ', '.join(table.c.keys()), True)
            with connection.execution_options(stream_results=True).execute(select(table)) as result:
                with raw_path.open('w', newline='', encoding='utf-8') as handle:
                    writer = csv.writer(handle)
                    writer.writerow(['source_column_' + str(i) for i in range(len(table.c))])
                    count = 0
                    for batch in result.partitions(10000):
                        self._check_canceled(worker)
                        writer.writerows(batch)
                        count += len(batch)
                        worker.log_signal.emit(f'Info: Read {count:,} source rows.', True)
                    if not count:
                        raise ValueError('The selected table contains no rows.')
        self._check_canceled(worker)
        if processor.check_file(str(raw_path), worker):
            raise ValueError('Source table columns do not match the selected JSON. See import log.')
        self._check_canceled(worker)
        self.columns = processor.column_defs()
        definitions = ', '.join(
            f'"{key}" {kind}' + (' NOT NULL' if key in ('event_id', 'veh_id', 'veh_time') else '')
            for key, kind in self.columns.items())
        with sqlite3.connect(self.sqlite_path) as db:
            db.execute(f'CREATE TABLE events ({definitions}, PRIMARY KEY (event_id, veh_id, veh_time))')
            if processor.import_data(str(raw_path), db, worker):
                raise ValueError('Could not map source rows using the selected JSON.')
            self._check_canceled(worker)
        self.group_keys = dict(processor._algorithms._group_dict)
        raw_path.unlink()

    @staticmethod
    def _check_canceled(worker):
        if worker.interrupt_flag:
            raise CatalogCanceled('Database catalog creation canceled.')

    def _save_catalog(self):
        catalog = Table(self.catalog_name, MetaData(),
                        Column('event_id', BigInteger, primary_key=True),
                        Column('event_name', String), Column('row_count', BigInteger),
                        Column('vehicle_count', BigInteger), Column('start_time', Float),
                        Column('end_time', Float), Column('duration', Float),
                        Column('source_schema', String), Column('source_table', String),
                        Column('configuration_name', String), Column('configuration_json', String),
                        Column('configuration_sha256', String), Column('group_keys_json', String),
                        schema=self.schema)
        config_json = json.dumps(self.config, sort_keys=True)
        fingerprint = hashlib.sha256(config_json.encode()).hexdigest()
        rows = [dict(row, source_schema=self.schema, source_table=self.table_name,
                     configuration_name=self.config_name, configuration_json=config_json,
                     configuration_sha256=fingerprint, group_keys_json=json.dumps(self.group_keys))
                for row in self.rows]
        with self.engine.begin() as connection:
            # Only create a new table when no compatible catalog was found.
            catalog.create(connection)
            connection.execute(catalog.insert(), rows)

    def export_event(self, event_id):
        """Return an owned temporary CSV and identity mapping of normalized data.

        Applying the original JSON twice would convert units twice and could
        renumber group-derived event IDs. The source mapping was already applied
        when building the catalog; this generated mapping preserves its results.
        """
        handle = tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                             prefix='traces_event_', suffix='.csv', delete=False)
        path = Path(handle.name)
        try:
            with handle, sqlite3.connect(self.sqlite_path) as db:
                writer = csv.writer(handle)
                writer.writerow(self.columns)
                rows = db.execute('SELECT * FROM events WHERE event_id = ? ORDER BY veh_time, veh_id',
                                  (event_id,))
                first = rows.fetchone()
                if first is None:
                    raise ValueError('The selected event is no longer present in the source data. '
                                     'Remove the saved catalog to rebuild it.')
                writer.writerow(first)
                writer.writerows(rows)
            types = {'INTEGER': 'int', 'REAL': 'float', 'TEXT': 'text'}
            mapping = {'file_ext': 'csv', 'class': 'data_import.csv_process.CsvProcess',
                       'col_defs': {name: {'func': 'copy', 'parms': [index], 'type': types[kind]}
                                    for index, (name, kind) in enumerate(self.columns.items())}}
            return str(path), mapping
        except Exception:
            path.unlink(missing_ok=True)
            raise
