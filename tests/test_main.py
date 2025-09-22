"""
Test runner script for all trajectory tools unit tests.
This script can be used to run tests locally or in CI/CD environments.
"""

import unittest
import sys
import os

# Add the test directory to the Python path
test_dir = os.path.dirname(__file__)
sys.path.insert(0, test_dir)

def run_all_tests():
    """Run all unit tests and return the result."""
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Load tests from each test module
    test_modules = [
        'test_top_level',
        'test_data_import_comprehensive', 
        'test_gui_comprehensive'
    ]
    
    for module_name in test_modules:
        try:
            module_suite = loader.loadTestsFromName(module_name)
            suite.addTest(module_suite)
            print(f"✓ Loaded tests from {module_name}")
        except Exception as e:
            print(f"✗ Failed to load tests from {module_name}: {e}")
            return False
    
    # Run the tests
    runner = unittest.TextTestRunner(
        verbosity=2,
        stream=sys.stdout,
        descriptions=True,
        failfast=False
    )
    
    print(f"\n{'='*60}")
    print("RUNNING TRAJECTORY TOOLS UNIT TESTS")
    print(f"{'='*60}\n")
    
    result = runner.run(suite)
    
    # Print summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped) if hasattr(result, 'skipped') else 0}")
    
    if result.failures:
        print(f"\nFAILURES:")
        for test, traceback in result.failures:
            print(f"- {test}")
    
    if result.errors:
        print(f"\nERRORS:")
        for test, traceback in result.errors:
            print(f"- {test}")
    
    success = len(result.failures) == 0 and len(result.errors) == 0
    print(f"\nOverall result: {'PASS' if success else 'FAIL'}")
    print(f"{'='*60}")
    
    return success

def run_specific_test_module(module_name):
    """Run tests from a specific module."""
    loader = unittest.TestLoader()
    runner = unittest.TextTestRunner(verbosity=2)
    
    try:
        suite = loader.loadTestsFromName(module_name)
        result = runner.run(suite)
        return len(result.failures) == 0 and len(result.errors) == 0
    except Exception as e:
        print(f"Error running tests from {module_name}: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) > 1:
        # Run specific test module
        module_name = sys.argv[1]
        success = run_specific_test_module(module_name)
    else:
        # Run all tests
        success = run_all_tests()
    
    sys.exit(0 if success else 1)
