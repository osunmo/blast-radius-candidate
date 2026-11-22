import unittest
from src.catalog import CatalogRemapper


class CatalogTest(unittest.TestCase):
    def test_permutation_subset_and_unknown(self):
        mapper = CatalogRemapper(['a','b','c'],[{'id':'c','number':0},{'id':'new','number':1},{'id':'a','number':2}])
        self.assertEqual(mapper.bits('7'),5)
        self.assertEqual(mapper.bits('2'),0)
        self.assertEqual(mapper.bits('1'),4)
        self.assertEqual(mapper.unknown,2)

    def test_identity_and_zero(self):
        mapper = CatalogRemapper(['a'],[{'id':'a','number':0}])
        self.assertTrue(mapper.identity)
        self.assertEqual(mapper.bits('0'),0)
        self.assertEqual(mapper.bits('1'),1)
        self.assertEqual(CatalogRemapper(['a'],[]).bits('1'),0)

    def test_reject_duplicate_or_invalid_catalog(self):
        for baseline, tests in [(['a','a'],[]), (['a'],[{'id':'a','number':0},{'id':'a','number':1}]), (['a'],[{'id':'a','number':1}])]:
            with self.assertRaises(ValueError):
                CatalogRemapper(baseline,tests)

    def test_validate_before_remapping(self):
        mapper = CatalogRemapper(['a'],[{'id':'a','number':0}])
        for value in ['2','-1','garbage']:
            with self.assertRaises(ValueError):
                mapper.bits(value)
        mapper.validate_catalog(['a'])
        with self.assertRaises(ValueError):
            mapper.validate_catalog(['b'])
