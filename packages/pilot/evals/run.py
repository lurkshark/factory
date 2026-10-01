from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path.cwd()))
suite = unittest.defaultTestLoader.discover('evals', pattern='test_*.py')
class JUnit(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.report = ET.Element('testsuite')
    def startTest(self, test):
        super().startTest(test)
        self.case = ET.SubElement(self.report, 'testcase', name=test._testMethodDoc or test.id())
    def addFailure(self, test, err):
        super().addFailure(test, err)
        ET.SubElement(self.case, 'failure').text = self._exc_info_to_string(err, test)
    def addError(self, test, err):
        super().addError(test, err)
        ET.SubElement(self.case, 'error').text = self._exc_info_to_string(err, test)
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        ET.SubElement(self.case, 'skipped', message=reason)
result = JUnit()
suite.run(result)
path = Path('evals/.results/junit.xml')
path.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(result.report).write(path)
sys.exit(0 if result.wasSuccessful() else 1)
