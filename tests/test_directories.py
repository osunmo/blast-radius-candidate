import unittest
from src.directories import addition_consumers


class DirectoryQueryTests(unittest.TestCase):
    def data(self):
        return {'version':1,'complete':True,'known_directories':['tests','tests/fixtures','tests/fixtures/live'],
                'known_files':['tests/fixtures/existing.sql'], 'directories':{'tests':'80','tests/fixtures':'1','tests/fixtures/live':'2'},
                'probes':{'tests/fixtures/new.sql':'4','tests/fixtures/live':'40','tests/fixtures/missing':'8'},
                'opens':{'tests/fixtures/new.sql':'10'},'always_include':'20'}

    def test_existing_parent_listing_and_missing_file_probes(self):
        self.assertEqual(addition_consumers('tests/fixtures/new.sql',self.data()),0x35)
        # Existing-parent metadata changes, although its existence does not.
        self.assertEqual(addition_consumers('tests/fixtures/live/new.sql',self.data()),0x62)

    def test_existing_parent_open_and_metadata_are_consumers(self):
        data=self.data()
        data['opens']['tests/fixtures/live']='100'
        self.assertEqual(addition_consumers('tests/fixtures/live/new.sql',data),0x162)

    def test_new_ancestor_directory_and_missing_parent_probe(self):
        self.assertEqual(addition_consumers('tests/fixtures/missing/nested/new.sql',self.data()),0x29)

    def test_unproved_file_types_bindings_and_symlinks_fall_back(self):
        for path in ('tests/fixtures/new.py','tests/fixtures/existing.sql','other/new.sql','../tests/fixtures/new.sql'):
            self.assertIsNone(addition_consumers(path,self.data()))
        data=self.data();data['symlinks']=['tests/fixtures/live']
        self.assertIsNone(addition_consumers('tests/fixtures/live/new.sql',data))
        data=self.data();data['complete']=False
        self.assertIsNone(addition_consumers('tests/fixtures/new.sql',data))
