import unittest
from src.classdeps import changed_classes


class ChangeTests(unittest.TestCase):
    def test_scalar_and_nested_class_dictionary(self):
        source = 'class A:\n    class B:\n        VALUES = {"a": 1}\n'
        self.assertEqual(changed_classes(source, source.replace('1','2')), {'A.B'})

    def test_existing_factory_entry_arguments(self):
        source = 'class A:\n    TABLE = {"x": build("x", flag=True), **Base.TABLE}\n'
        self.assertEqual(changed_classes(source,source.replace('flag=True','flag=False')), {'A'})
        self.assertIsNone(changed_classes(source,source.replace('build(','other(')))

    def test_definition_and_binding_changes_expand(self):
        source = 'class A:\n    VALUE = 1\n'
        for updated in (source.replace('A:', 'A(Base):'), source.replace('VALUE','OTHER'),
                        source+'class B:\n    pass\n', source.replace('1','f()')):
            self.assertIsNone(changed_classes(source,updated))

    def test_function_body_is_not_a_class_setting(self):
        source = 'class A:\n    def f(self):\n        return 1\n'
        self.assertIsNone(changed_classes(source,source.replace('1','2')))
