"""Run with Python 3.13 to check source-read attribution through wrappers."""
import tempfile
import unittest
from pathlib import Path
from build_maps import Collector

class WrappedCollector(Collector):
    def audit(self, event, args):
        super().audit(event, args)

class AuditTests(unittest.TestCase):
    def test_loader_reads_are_excluded_through_a_subclass(self):
        with tempfile.TemporaryDirectory() as directory:
            for tracker_type in (Collector, WrappedCollector):
                tracker = tracker_type(Path(directory), {'test': 0})
                filename = str(Path(directory)/'sample.py')
                loader = compile('tracker.audit("open", (filename, "r", 0))', '<frozen importlib.test>', 'exec')
                exec(loader, {'tracker': tracker, 'filename': filename})
                self.assertEqual(dict(tracker.reads), {})
                tracker.audit('open', (filename, 'r', 0))
                self.assertEqual(tracker.reads['sample.py'], 1)

if __name__ == '__main__':
    unittest.main()
