import enum
from types import ModuleType
import unittest
from src.importcheck import scalar_namespace


class NamespaceTests(unittest.TestCase):
    def test_enum_and_descriptor_rewrites_are_not_scalar_fields(self):
        module=ModuleType('demo')
        class Config:
            VALUE=1
        class Choice(enum.IntEnum):
            VALUE=1
        class Descriptor:
            VALUE=property(lambda self:1)
        module.Config, module.Choice, module.Descriptor=Config, Choice, Descriptor
        self.assertTrue(scalar_namespace(module,[('Config','VALUE')]))
        self.assertFalse(scalar_namespace(module,[('Choice','VALUE')]))
        self.assertFalse(scalar_namespace(module,[('Descriptor','VALUE')]))

    def test_missing_or_transformed_binding_is_conservative(self):
        module=ModuleType('demo')
        module.Config={}
        self.assertFalse(scalar_namespace(module,[('Config','VALUE')]))
