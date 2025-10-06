import pytest
from unittest.mock import Mock, MagicMock, patch, mock_open, call
from pathlib import Path
from queue import Queue
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QDialog
from PyQt6.QtCore import Qt
import sqlite3 as real_sqlite3
import json


@pytest.fixture
def mock_db_connection():
    """Create a mock database connection."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Track queries to determine return values
    queries = []
    
    def execute_side_effect(query, params=()):
        queries.append(query.lower())
    
    def fetchone_side_effect():
        if not queries:
            return (100,)
        
        last_query = queries[-1]
        if 'count' in last_query:
            return (100,)
        elif 'summary_json' in last_query or 'summary' in last_query:
            return (json.dumps({"event_name": "Test Event"}),)
        else:
            return (100,)
    
    def fetchall_side_effect():
        if not queries:
            return []
        
        last_query = queries[-1]
        if 'pragma table_info' in last_query:
            return [
                (0, 'event_id', 'INTEGER'),
                (1, 'veh_id', 'INTEGER'),
                (2, 'veh_time', 'REAL'),
                (3, 'veh_speed', 'REAL')
            ]
        elif 'from ranges' in last_query:
            return [(1,), (2,), (3,), (4,)]
        else:
            return []
    
    mock_cursor.execute.side_effect = execute_side_effect
    mock_cursor.fetchone.side_effect = fetchone_side_effect
    mock_cursor.fetchall.side_effect = fetchall_side_effect
    mock_cursor.fetchmany.return_value = []
    
    # Return the same cursor instance each time
    mock_conn.cursor.return_value = mock_cursor
    
    return mock_conn


@pytest.fixture
def mock_dialogs():
    """Mock file and message dialogs."""
    with patch('gui.export_dialog.QFileDialog') as mock_file, \
         patch('gui.export_dialog.QMessageBox') as mock_msg:
        
        mock_file.getSaveFileName = Mock(return_value=('', ''))
        mock_msg.warning = Mock()
        mock_msg.information = Mock()
        # Use MagicMock for question so return_value can be easily overridden
        mock_msg.question = MagicMock(return_value=QMessageBox.StandardButton.Yes)
        
        yield {
            'file': mock_file,
            'message': mock_msg
        }


class TestExportDialogInitialization:
    """Test ExportDialog initialization."""
    
    def test_init_export_all_mode(self, qtbot, mock_db_connection, mock_dialogs):
        """Test initialization in Export All mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(parent=None, db_connection=mock_db_connection, 
                            selected_columns=None)
        qtbot.addWidget(dialog)
        
        assert dialog.is_export_all_mode is True
        assert dialog.selected_columns == []
        assert dialog.windowTitle() == "Export All"
    
    def test_init_export_selected_mode(self, qtbot, mock_db_connection, mock_dialogs):
        """Test initialization in Export Selected mode."""
        from gui.export_dialog import ExportDialog
        
        selected = ['veh_id', 'veh_time']
        dialog = ExportDialog(parent=None, db_connection=mock_db_connection,
                            selected_columns=selected)
        qtbot.addWidget(dialog)
        
        assert dialog.is_export_all_mode is False
        assert dialog.selected_columns == selected
        assert dialog.windowTitle() == "Export Selected"
    
    def test_init_components_created(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that UI components are created."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        assert dialog._export_button is not None
        assert dialog._interrupt_button is not None
        assert dialog._progress_bar is not None
        assert dialog._interrupt_button.isEnabled() is False
    
    def test_init_with_parent(self, qtbot, mock_db_connection, mock_dialogs):
        """Test initialization with parent window."""
        parent = QMainWindow()
        qtbot.addWidget(parent)
        
        from gui.export_dialog import ExportDialog
        dialog = ExportDialog(parent=parent, db_connection=mock_db_connection,
                            selected_columns=None)
        qtbot.addWidget(dialog)
        
        assert dialog.parent() == parent


class TestUISetup:
    """Test UI setup for different modes."""
    
    def test_export_all_note_label(self, qtbot, mock_db_connection, mock_dialogs):
        """Test note label in Export All mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        # Should have called execute with COUNT query
        calls = [str(call) for call in mock_db_connection.cursor.return_value.execute.call_args_list]
        assert any('count' in str(call).lower() for call in calls)
    
    def test_export_selected_checkboxes_created(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that checkboxes are created in Export Selected mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, 
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        # Should have 4 checkboxes from the mock (event_id, veh_id, veh_time, veh_speed)
        assert len(dialog._column_checkboxes) == 4
        assert 'event_id' in dialog._column_checkboxes
        assert 'veh_id' in dialog._column_checkboxes
        assert 'veh_time' in dialog._column_checkboxes
        
        # veh_id should be checked
        assert dialog._column_checkboxes['veh_id'].isChecked() is True
        assert dialog._column_checkboxes['event_id'].isChecked() is False
    
    def test_export_selected_event_combobox_created(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that event combobox is created in Export Selected mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        assert dialog._event_combobox is not None
        # Should have 4 events from mock ranges query
        assert dialog._event_combobox.count() == 4
    
    def test_export_all_no_combobox(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that no event combobox is created in Export All mode."""
        from gui.export_dialog import ExportDialog
        
        # Override for count query
        mock_cursor = mock_db_connection.cursor.return_value
        mock_cursor.fetchone.return_value = (100,)
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        assert dialog._event_combobox is None


class TestDatabaseQueries:
    """Test database query methods."""
    
    def test_get_record_count(self, qtbot, mock_db_connection, mock_dialogs):
        """Test getting record count."""
        from gui.export_dialog import ExportDialog
        
        mock_cursor = mock_db_connection.cursor.return_value
        mock_cursor.fetchone.return_value = (5432,)
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        count = dialog._get_record_count()
        assert count == 5432
    
    def test_get_record_count_error(self, qtbot, mock_db_connection, mock_dialogs):
        """Test error handling when getting record count."""
        from gui.export_dialog import ExportDialog
        
        mock_cursor = mock_db_connection.cursor.return_value
        mock_cursor.execute.side_effect = real_sqlite3.Error("Database error")
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        count = dialog._get_record_count()
        assert count == 0
    
    def test_get_column_names(self, qtbot, mock_db_connection, mock_dialogs):
        """Test getting column names."""
        from gui.export_dialog import ExportDialog
        
        mock_cursor = mock_db_connection.cursor.return_value
        mock_cursor.fetchall.return_value = [
            (0, 'event_id', 'INTEGER'),
            (1, 'veh_id', 'INTEGER'),
            (2, 'veh_time', 'REAL')
        ]
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        columns = dialog._get_column_names()
        assert columns == ['event_id', 'veh_id', 'veh_time']
    
    def test_get_column_names_error(self, qtbot, mock_db_connection, mock_dialogs):
        """Test error handling when getting column names."""
        from gui.export_dialog import ExportDialog
        
        mock_cursor = mock_db_connection.cursor.return_value
        mock_cursor.execute.side_effect = real_sqlite3.Error("Database error")
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        columns = dialog._get_column_names()
        assert columns == []


class TestColumnSelection:
    """Test column selection methods."""
    
    def test_get_selected_columns_export_all(self, qtbot, mock_db_connection, mock_dialogs):
        """Test getting selected columns in Export All mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        columns = dialog._get_selected_columns()
        assert 'event_id' in columns
        assert 'veh_id' in columns
    
    def test_get_selected_columns_export_selected(self, qtbot, mock_db_connection, mock_dialogs):
        """Test getting selected columns in Export Selected mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        # Check veh_time as well
        dialog._column_checkboxes['veh_time'].setChecked(True)
        
        columns = dialog._get_selected_columns()
        assert 'veh_id' in columns
        assert 'veh_time' in columns
        assert 'event_id' not in columns
    
    def test_get_selected_event_id(self, qtbot, mock_db_connection, mock_dialogs):
        """Test getting selected event ID."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        dialog._event_combobox.setCurrentIndex(1)
        event_id = dialog._get_selected_event_id()
        assert event_id is not None
    
    def test_get_selected_event_id_export_all(self, qtbot, mock_db_connection, mock_dialogs):
        """Test getting event ID in Export All mode returns None."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        event_id = dialog._get_selected_event_id()
        assert event_id is None


class TestEventCombobox:
    """Test event combobox functionality."""
    
    def test_populate_event_combobox(self, qtbot, mock_db_connection, mock_dialogs):
        """Test populating event combobox."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        # Should have 4 events from fixture
        assert dialog._event_combobox.count() == 4
        assert dialog._event_combobox.itemText(0) == "Event 1"
        assert dialog._event_combobox.itemData(0) == 1
    
    def test_on_event_changed(self, qtbot, mock_db_connection, mock_dialogs):
        """Test event selection change."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        # Change event
        dialog._event_combobox.setCurrentIndex(1)
        
        # Should update event data
        assert "Test Event" in dialog._event_name.text()


class TestExportProcess:
    """Test export process functionality."""
    
    def test_on_export_no_columns_selected(self, qtbot, mock_db_connection, mock_dialogs):
        """Test export with no columns selected shows warning."""
        from gui.export_dialog import ExportDialog
        
        mock_cursor = mock_db_connection.cursor.return_value
        mock_cursor.fetchall.return_value = [(0, 'veh_id', 'INTEGER')]
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=[])
        qtbot.addWidget(dialog)
        
        # Uncheck all boxes
        for checkbox in dialog._column_checkboxes.values():
            checkbox.setChecked(False)
        
        dialog._on_export()
        
        mock_dialogs['message'].warning.assert_called()
    
    def test_on_export_user_cancels_file_dialog(self, qtbot, mock_db_connection, mock_dialogs):
        """Test export when user cancels file dialog."""
        from gui.export_dialog import ExportDialog
        
        mock_dialogs['file'].getSaveFileName.return_value = ('', '')
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._on_export()
        
        # Should not proceed with export
        assert dialog._output_path == ""
    
    def test_on_export_file_exists_user_declines(self, qtbot, mock_db_connection, mock_dialogs, tmp_path):
        """Test export when file exists and user declines overwrite."""
        from gui.export_dialog import ExportDialog
        
        # Create existing file
        existing_file = tmp_path / "test.csv"
        existing_file.write_text("existing")
        
        mock_dialogs['file'].getSaveFileName.return_value = (str(existing_file), '')
        mock_dialogs['message'].question.return_value = QMessageBox.StandardButton.No
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._on_export()
        
        # Should ask about overwrite
        mock_dialogs['message'].question.assert_called()
    
    def test_on_export_starts_export(self, qtbot, mock_db_connection, mock_dialogs, tmp_path):
        """Test that export process starts correctly."""
        from gui.export_dialog import ExportDialog
        
        output_file = tmp_path / "test.csv"
        mock_dialogs['file'].getSaveFileName.return_value = (str(output_file), '')
        
        with patch.object(ExportDialog, '_start_export') as mock_start:
            dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
            qtbot.addWidget(dialog)
            
            dialog._on_export()
            
            mock_start.assert_called_once()
    
    def test_init_producer_export_all(self, qtbot, mock_db_connection, mock_dialogs):
        """Test producer initialization in Export All mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._init_producer(['event_id', 'veh_id'], event_id=None)
        
        assert dialog._total_records == 100
        assert dialog._records_processed == 0
        # Should execute query - check that execute was called
        assert mock_db_connection.cursor.return_value.execute.called
    
    def test_init_producer_export_selected(self, qtbot, mock_db_connection, mock_dialogs):
        """Test producer initialization in Export Selected mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        dialog._init_producer(['veh_id'], event_id=5)
        
        assert dialog._total_records == 100
        # Should execute query with WHERE clause - check calls
        calls = [str(call) for call in mock_db_connection.cursor.return_value.execute.call_args_list]
        assert any('where' in str(call).lower() for call in calls)
    
    def test_init_producer_no_records_raises_error(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that producer initialization raises error when no records found."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        # Override fetchone to return 0 count
        mock_db_connection.cursor.return_value.fetchone.side_effect = lambda: (0,)
        
        with pytest.raises(ValueError, match="No records found"):
            dialog._init_producer(['veh_id'], event_id=None)


class TestProducerConsumer:
    """Test producer/consumer pattern."""
    
    def test_produce_data_batch(self, qtbot, mock_db_connection, mock_dialogs):
        """Test producing a data batch."""
        from gui.export_dialog import ExportDialog
        
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.return_value = [(1, 2, 3), (4, 5, 6)]
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._cursor = mock_cursor
        dialog._total_records = 100
        dialog._records_processed = 0
        dialog._data_queue = Queue(maxsize=5)
        dialog._worker_thread = MagicMock()
        dialog._worker_thread.isRunning.return_value = True
        
        dialog._produce_data_batch()
        
        assert not dialog._data_queue.empty()
        data_item = dialog._data_queue.get()
        assert data_item[0] == [(1, 2, 3), (4, 5, 6)]
    
    def test_produce_data_batch_end_marker(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that end marker is sent when no more data."""
        from gui.export_dialog import ExportDialog
        
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.return_value = []  # No more data
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._cursor = mock_cursor
        dialog._data_queue = Queue(maxsize=5)
        dialog._worker_thread = MagicMock()
        dialog._worker_thread.isRunning.return_value = True
        dialog._producer_timer = MagicMock()
        
        dialog._produce_data_batch()
        
        # Should send None as end marker
        assert dialog._data_queue.get() is None
    
    def test_stop_producer(self, qtbot, mock_db_connection, mock_dialogs):
        """Test stopping the producer."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        # Create and set a mock timer
        mock_timer = MagicMock()
        dialog._producer_timer = mock_timer
        
        dialog._stop_producer()
        
        mock_timer.stop.assert_called_once()
        assert dialog._producer_timer is None


class TestInterrupt:
    """Test interrupt functionality."""
    
    def test_interrupt_export(self, qtbot, mock_db_connection, mock_dialogs):
        """Test interrupting export."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        # Set up mock thread and timer
        dialog._worker_thread = MagicMock()
        mock_timer = MagicMock()
        dialog._producer_timer = mock_timer
        
        dialog._interrupt_export()
        
        assert dialog._worker_thread.interrupt_flag is True
        mock_timer.stop.assert_called()
        assert dialog._interrupt_button.isEnabled() is False


class TestExportCompletion:
    """Test export completion handling."""
    
    def test_on_export_finished_success(self, qtbot, mock_db_connection, mock_dialogs):
        """Test successful export completion."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._worker_thread = MagicMock()
        dialog._producer_timer = MagicMock()
        
        dialog._on_export_finished(True, "Export successful")
        
        mock_dialogs['message'].information.assert_called()
        assert dialog._export_button.isEnabled() is True
        assert dialog._interrupt_button.isEnabled() is False
    
    def test_on_export_finished_failure(self, qtbot, mock_db_connection, mock_dialogs):
        """Test failed export completion."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._worker_thread = MagicMock()
        
        dialog._on_export_finished(False, "Export failed")
        
        mock_dialogs['message'].warning.assert_called()
        assert dialog._export_button.isEnabled() is True


class TestCloseEvent:
    """Test dialog close event handling."""
    
    def test_close_event_no_export_running(self, qtbot, mock_db_connection, mock_dialogs):
        """Test closing dialog when no export is running."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        # Mock the export_finished signal
        signal_spy = Mock()
        dialog.export_finished.connect(signal_spy)
        
        dialog.close()
        
        # Should emit selected columns
        signal_spy.assert_called_once()
    
    def test_close_event_export_running_user_cancels(self, qtbot, mock_db_connection, mock_dialogs):
        """Test closing dialog during export when user cancels."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        # Override the question mock for this test
        mock_dialogs['message'].question.return_value = QMessageBox.StandardButton.Cancel
        
        dialog._worker_thread = MagicMock()
        dialog._worker_thread.isRunning.return_value = True
        
        # Create a close event
        from PyQt6.QtGui import QCloseEvent
        event = QCloseEvent()
        
        dialog.closeEvent(event)
        
        # Event should be ignored - check using isAccepted() == False
        assert event.isAccepted() is False
    
    def test_close_event_export_running_user_confirms(self, qtbot, mock_db_connection, mock_dialogs):
        """Test closing dialog during export when user confirms."""
        from gui.export_dialog import ExportDialog
        
        # Use default Yes response from fixture
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        dialog._worker_thread = MagicMock()
        dialog._worker_thread.isRunning.return_value = True
        dialog._worker_thread.wait.return_value = True
        dialog._producer_timer = MagicMock()
        
        # Create a close event
        from PyQt6.QtGui import QCloseEvent
        event = QCloseEvent()
        
        dialog.closeEvent(event)
        
        # Worker thread should be stopped
        assert dialog._worker_thread.interrupt_flag is True
        assert event.isAccepted() is True
    
    def test_close_event_emits_selected_columns(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that close event emits selected columns in Export Selected mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        signal_spy = Mock()
        dialog.export_finished.connect(signal_spy)
        
        dialog._column_checkboxes['veh_time'].setChecked(True)
        
        dialog.close()
        
        # Should emit the selected columns
        args = signal_spy.call_args[0][0]
        assert 'veh_id' in args
        assert 'veh_time' in args
    
    def test_close_event_export_all_emits_empty_list(self, qtbot, mock_db_connection, mock_dialogs):
        """Test that close event emits empty list in Export All mode."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        signal_spy = Mock()
        dialog.export_finished.connect(signal_spy)
        
        dialog.close()
        
        # Should emit empty list
        signal_spy.assert_called_once_with([])


class TestWorkerThread:
    """Test the worker thread functionality."""
    
    def test_worker_thread_init(self):
        """Test worker thread initialization."""
        from gui.export_dialog import _ExportWorkerThread
        
        queue = Queue()
        columns = ['event_id', 'veh_id']
        
        worker = _ExportWorkerThread('/tmp/test.csv', columns, queue)
        
        assert worker.output_path == '/tmp/test.csv'
        assert worker.columns == columns
        assert worker.data_queue == queue
        assert worker.interrupt_flag is False
    
    def test_worker_thread_writes_csv(self, tmp_path):
        """Test that worker thread writes CSV correctly."""
        from gui.export_dialog import _ExportWorkerThread
        
        output_file = tmp_path / "test.csv"
        queue = Queue()
        columns = ['col1', 'col2']
        
        # Put data in queue
        queue.put(([['val1', 'val2'], ['val3', 'val4']], 50))
        queue.put(([['val5', 'val6']], 100))
        queue.put(None)  # End marker
        
        worker = _ExportWorkerThread(str(output_file), columns, queue)
        
        # Connect signals
        progress_values = []
        def on_progress(value):
            progress_values.append(value)
        
        finished_result = []
        def on_finished(success, message):
            finished_result.append((success, message))
        
        worker.progress_signal.connect(on_progress)
        worker.finished_signal.connect(on_finished)
        
        # Run worker
        worker.run()
        
        # Check file was created
        assert output_file.exists()
        
        # Check content
        content = output_file.read_text()
        assert 'col1,col2' in content
        assert 'val1,val2' in content
        assert 'val5,val6' in content
        
        # Check signals
        assert 50 in progress_values
        assert 100 in progress_values
        assert len(finished_result) == 1
        assert finished_result[0][0] is True
    
    def test_worker_thread_interrupt(self, tmp_path):
        """Test that worker thread can be interrupted."""
        from gui.export_dialog import _ExportWorkerThread
        
        output_file = tmp_path / "test.csv"
        queue = Queue()
        columns = ['col1', 'col2']
        
        worker = _ExportWorkerThread(str(output_file), columns, queue)
        worker.interrupt_flag = True
        
        finished_result = []
        def on_finished(success, message):
            finished_result.append((success, message))
        
        worker.finished_signal.connect(on_finished)
        
        worker.run()
        
        assert len(finished_result) == 1
        assert finished_result[0][0] is False
        assert "interrupted" in finished_result[0][1].lower()


class TestIntegration:
    """Integration tests for ExportDialog."""
    
    def test_full_export_all_workflow(self, qtbot, mock_db_connection, mock_dialogs, tmp_path):
        """Test complete Export All workflow."""
        from gui.export_dialog import ExportDialog
        
        output_file = tmp_path / "export_all.csv"
        mock_dialogs['file'].getSaveFileName.return_value = (str(output_file), '')
        
        dialog = ExportDialog(db_connection=mock_db_connection, selected_columns=None)
        qtbot.addWidget(dialog)
        
        assert dialog.is_export_all_mode is True
        assert dialog._export_button.isEnabled() is True
        assert dialog._interrupt_button.isEnabled() is False
    
    def test_full_export_selected_workflow(self, qtbot, mock_db_connection, mock_dialogs):
        """Test complete Export Selected workflow."""
        from gui.export_dialog import ExportDialog
        
        dialog = ExportDialog(db_connection=mock_db_connection,
                            selected_columns=['veh_id'])
        qtbot.addWidget(dialog)
        
        assert dialog.is_export_all_mode is False
        # Should have 4 checkboxes from fixture
        assert len(dialog._column_checkboxes) == 4
        assert dialog._event_combobox is not None
        # Should have 4 events from fixture
        assert dialog._event_combobox.count() == 4
        assert dialog._column_checkboxes['veh_id'].isChecked() is True