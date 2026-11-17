import pytest
import sqlite3
from abc import ABC
from data_import.base_process import BaseProcess


class ConcreteProcess(BaseProcess):
    """Concrete implementation for testing the abstract base class."""
    
    def process(self, config: dict, input_path: str, db_connection: sqlite3.Connection, worker: object) -> int:
        return 0


class TestBaseProcess:
    """Test suite for BaseProcess abstract base class."""
    
    def test_base_process_is_abstract(self):
        """Test that BaseProcess is an abstract base class."""
        assert issubclass(BaseProcess, ABC)
        
    def test_cannot_instantiate_base_process_directly(self):
        """Test that BaseProcess cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class BaseProcess"):
            BaseProcess()
    
    def test_concrete_implementation_can_be_instantiated(self):
        """Test that a concrete implementation can be instantiated."""
        processor = ConcreteProcess()
        assert isinstance(processor, BaseProcess)
    
    def test_process_method_signature(self):
        """Test that the process method has the correct signature."""
        processor = ConcreteProcess()
        
        # Create a mock database connection
        db_conn = sqlite3.connect(":memory:")
        
        # Test that the method can be called with correct parameters
        result = processor.process({}, "test_path", db_conn, None)
        assert result == 0
        
        db_conn.close()
    
    def test_process_method_is_abstract(self):
        """Test that a class without implementing process cannot be instantiated."""
        
        class IncompleteProcess(BaseProcess):
            pass
        
        with pytest.raises(TypeError, match="Can't instantiate abstract class IncompleteProcess"):
            IncompleteProcess()
    
    def test_process_method_return_type(self):
        """Test that process method returns an integer."""
        processor = ConcreteProcess()
        db_conn = sqlite3.connect(":memory:")
        
        result = processor.process({}, "test_path", db_conn, None)
        assert isinstance(result, int)
        
        db_conn.close()