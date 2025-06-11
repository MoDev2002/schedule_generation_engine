# test_runner.py


import logging
import sys
import traceback
import unittest
from datetime import datetime
from typing import Dict

from scheduler_test import (
    TestConstraintManager,
    TestResourceManager,
    TestScheduleValidator,
    TestSchedulingEngine,
)


class DetailedTestResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.successes = []
        self.start_times = {}
        self.execution_times = {}

    def startTest(self, test):
        self.start_times[test] = datetime.now()
        super().startTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append(test)
        end_time = datetime.now()
        self.execution_times[test] = (end_time - self.start_times[test]).total_seconds()

    def addError(self, test, err):
        super().addError(test, err)
        end_time = datetime.now()
        self.execution_times[test] = (end_time - self.start_times[test]).total_seconds()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        end_time = datetime.now()
        self.execution_times[test] = (end_time - self.start_times[test]).total_seconds()


class TestRunner:
    def __init__(self, output_dir: str = "test_results"):
        self.output_dir = output_dir
        self.setup_logging()

    def setup_logging(self):
        """Configure logging for test execution"""
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("test_execution.log"),
                logging.StreamHandler(sys.stdout),
            ],
        )

    def run_component_tests(self):
        """Run tests for each component separately"""
        components = [
            (TestConstraintManager, "Constraint Manager"),
            (TestResourceManager, "Resource Manager"),
            (TestSchedulingEngine, "Scheduling Engine"),
            (TestScheduleValidator, "Schedule Validator"),
        ]

        results = {}
        for test_class, component_name in components:
            logging.info(f"\nTesting component: {component_name}")
            result = self.run_test_suite(test_class, component_name)
            results[component_name] = result

        return results

    def run_test_suite(self, test_class, component_name: str) -> Dict:
        """Run a specific test suite and return results"""
        suite = unittest.TestLoader().loadTestsFromTestCase(test_class)
        result = DetailedTestResult()
        suite.run(result)

        # Compile results
        test_results = {
            "component": component_name,
            "total_tests": result.testsRun,
            "passed": len(result.successes),
            "failed": len(result.failures),
            "errors": len(result.errors),
            "execution_time": sum(result.execution_times.values()),
            "details": {
                "failures": [
                    {
                        "test": str(test),
                        "message": err,
                        "execution_time": result.execution_times[test],
                    }
                    for test, err in result.failures
                ],
                "errors": [
                    {
                        "test": str(test),
                        "message": err,
                        "execution_time": result.execution_times[test],
                    }
                    for test, err in result.errors
                ],
            },
        }

        # Log results
        self.log_results(test_results)

        return test_results

    def log_results(self, results: Dict):
        """Log test results in a readable format"""
        logging.info(f"\nResults for {results['component']}:")
        logging.info("-" * 50)
        logging.info(f"Total Tests: {results['total_tests']}")
        logging.info(f"Passed: {results['passed']}")
        logging.info(f"Failed: {results['failed']}")
        logging.info(f"Errors: {results['errors']}")
        logging.info(f"Total Execution Time: {results['execution_time']:.2f} seconds")

        if results["details"]["failures"]:
            logging.info("\nFailures:")
            for failure in results["details"]["failures"]:
                logging.error(f"\nTest: {failure['test']}")
                logging.error(f"Error: {failure['message']}")
                logging.error(
                    f"Execution Time: {failure['execution_time']:.2f} seconds"
                )

        if results["details"]["errors"]:
            logging.info("\nErrors:")
            for error in results["details"]["errors"]:
                logging.error(f"\nTest: {error['test']}")
                logging.error(f"Error: {error['message']}")
                logging.error(f"Execution Time: {error['execution_time']:.2f} seconds")


def run_tests():
    """Main function to run all tests"""
    runner = TestRunner()

    try:
        logging.info("Starting test execution...")
        start_time = datetime.now()

        # Run component tests
        results = runner.run_component_tests()

        # Calculate overall statistics
        total_tests = sum(r["total_tests"] for r in results.values())
        total_passed = sum(r["passed"] for r in results.values())
        total_failed = sum(r["failed"] for r in results.values())
        total_errors = sum(r["errors"] for r in results.values())
        total_time = sum(r["execution_time"] for r in results.values())

        # Log overall results
        logging.info("\nOverall Test Results:")
        logging.info("=" * 50)
        logging.info(f"Total Tests Run: {total_tests}")
        logging.info(f"Total Passed: {total_passed}")
        logging.info(f"Total Failed: {total_failed}")
        logging.info(f"Total Errors: {total_errors}")
        logging.info(f"Total Execution Time: {total_time:.2f} seconds")

        # Calculate success rate
        success_rate = (total_passed / total_tests) * 100 if total_tests > 0 else 0
        logging.info(f"Success Rate: {success_rate:.2f}%")

    except Exception as e:
        logging.error(f"Test execution failed: {str(e)}")
        logging.error(traceback.format_exc())

    finally:
        end_time = datetime.now()
        total_duration = (end_time - start_time).total_seconds()
        logging.info(f"\nTotal Test Suite Duration: {total_duration:.2f} seconds")


if __name__ == "__main__":
    run_tests()
