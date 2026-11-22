import unittest
from src.fielddeps import changed_fields, field_consumers, scalar_fields_only


class FieldTests(unittest.TestCase):
    def test_scalar_domain_excludes_container_aliases_and_factories(self):
        source='class A:\n    VALUE = 1\n'
        self.assertTrue(scalar_fields_only(source, source.replace('= 1','= 3')))
        self.assertTrue(scalar_fields_only(source, source.replace('= 1','= None')))
        for old,new in [('{}','{"x": 1}'), ('factory(1)','factory(2)'), ('(1,2)','(1,3)')]:
            a=source.replace('= 1','= '+old)
            self.assertFalse(scalar_fields_only(a,a.replace(old,new)))

    def test_independent_literal_setting_patch(self):
        source = 'class A:\n    class B:\n        VALUE = {"x": 1}\n        OTHER = 1\n'
        self.assertEqual(changed_fields(source, source.replace('"x": 1', '"x": 2')),
                         {('A.B', 'VALUE')})
        self.assertIsNone(changed_fields(source, source.replace('VALUE', 'NEW')))

    def test_copied_metadata_and_unknown_fields_keep_class_guard(self):
        data = {'complete': True, 'fields': {'m:A:X': '1'},
                'initialization': ['m:A:X'], 'class_guards': {'m:A': '2'}}
        self.assertEqual(field_consumers('m', {('A','X')}, data, {'m:A': 14}), 15)
        self.assertEqual(field_consumers('m', {('A','Y')}, data, {'m:A': 14}), 15)

    def test_exact_reads_and_reflection_union(self):
        data = {'complete': True, 'fields': {'m:A:X': '1'},
                'class_guards': {'m:A': '2'}, 'always_include': '4'}
        self.assertEqual(field_consumers('m', {('A','X')}, data, {'m:A': 15}), 7)
        data['complete'] = False
        self.assertIsNone(field_consumers('m', {('A','X')}, data, {'m:A': 15}))


    def test_known_unread_field_and_worker_class_intersection(self):
        data = {'complete': True, 'fields': {'m:A:X': '0'},
                'untrusted': '0', 'opaque': 'a', 'always_include': 'a'}
        self.assertEqual(field_consumers('m', {('A','X')}, data, {'m:A': 4}), 0)
        self.assertEqual(field_consumers('m', {('A','X')}, data, {'m:A': 6}), 2)
        del data['opaque']
        self.assertEqual(field_consumers('m', {('A','X')}, data, {'m:A': 6}), 2)

    def test_observed_reflection_is_not_limited_to_method_consumers(self):
        data = {'complete': True, 'fields': {'m:A:X': '0'},
                'untrusted': '0', 'class_guards': {'m:A': '2'}}
        self.assertEqual(field_consumers('m', {('A','X')}, data, {'m:A': 4}), 2)
        data['class_guards'] = {}
        data['global_guard_classes'] = ['m:A']
        data.pop('_class_observations', None)
        self.assertEqual(field_consumers('m', {('A','X')}, data, {'m:A': 4}), 4)
