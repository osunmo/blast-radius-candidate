import unittest
from src.equivalence import computationally_equal, local_bodies_only


class EquivalenceTests(unittest.TestCase):
    def test_metadata_refinement_does_not_cross_bindings_or_decorators(self):
        self.assertTrue(local_bodies_only('def f():\n    return 1\n','def f():\n    return 2\n'))
        self.assertFalse(local_bodies_only('VALUE = 1\n','VALUE = 2\n'))
        self.assertFalse(local_bodies_only('def f():\n    return 1\n','def f(x=1):\n    return 1\n'))
        self.assertFalse(local_bodies_only('@wrap\ndef f():\n    return 1\n',
                                           '@wrap\ndef f():\n    return 2\n'))
        self.assertFalse(local_bodies_only('def f():\n    "old"\n','def f():\n    "new"\n'))
    def test_constant_folding_retains_computation(self):
        self.assertTrue(computationally_equal('LIMIT = 1 + 2\n', 'LIMIT = 3\n'))
        self.assertTrue(computationally_equal('def f():\n    return 2 * 3\n',
                                             '\ndef f():\n    return 6\n'))

    def test_runtime_types_docs_and_exception_regions_are_observable(self):
        for before, after in [('x = 1\n','x = True\n'),
                              ('def f():\n    "old"\n','def f():\n    "new"\n'),
                              ('def f():\n    return 1\n','def f():\n    return 2\n'),
                              ('def f(x):\n    return x\n','def f(x):\n    yield x\n')]:
            self.assertFalse(computationally_equal(before,after))

    def test_nested_codes_and_scope_names_are_compared(self):
        self.assertFalse(computationally_equal('def f():\n    return lambda: 1\n',
                                              'def f():\n    return lambda: 2\n'))
        self.assertFalse(computationally_equal('def f(a):\n    return a\n',
                                              'def f(b):\n    return b\n'))
