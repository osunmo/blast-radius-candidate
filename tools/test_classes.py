"""Regression checks for class-use instrumentation on Python 3.13."""
import tempfile
import unittest
from pathlib import Path
from build_classes import Classes


class ClassTests(unittest.TestCase):
    def test_record_does_not_invoke_a_user_metaclass_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = Classes(Path(directory), {'test':0})
            tracker.group = 1
            class Meta(type):
                def __hash__(cls):
                    raise AssertionError('class identity tracking called custom hash')
            class Example(metaclass=Meta):
                pass
            Example.__module__ = 'sqlglot.synthetic'
            tracker.record(Example)
            tracker.record(Example())
            self.assertEqual(tracker.classes, {'sqlglot.synthetic:ClassTests.test_record_does_not_invoke_a_user_metaclass_hash.<locals>.Example':1})


if __name__ == '__main__':
    unittest.main()
