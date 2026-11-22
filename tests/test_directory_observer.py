import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from build_directories import Observations


class DirectoryObserverTests(unittest.TestCase):
    def test_stat_failure_flags_and_pathlike_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory);context=SimpleNamespace(active=True,group=1,all=3,opaque=0)
            observer=Observations(source,context);observer.framework_filter=False
            observer.install()
            try:
                path=source/'missing.sql'
                with self.assertRaises(FileNotFoundError) as caught:
                    os.stat(path,follow_symlinks=False)
                self.assertEqual(caught.exception.errno,errno.ENOENT)
                self.assertEqual(observer.probes['missing.sql'],1)
                calls=[]
                class CustomPath:
                    def __fspath__(self):
                        calls.append(1);return str(path)
                with self.assertRaises(FileNotFoundError):
                    os.lstat(CustomPath())
                self.assertEqual(calls,[1])
                self.assertEqual(context.opaque,1)
            finally:
                observer.stop()

    def test_enumeration_and_write_collision_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory);(source/'existing.sql').write_text('select 1')
            context=SimpleNamespace(active=True,group=2,all=3,opaque=0)
            observer=Observations(source,context);observer.framework_filter=False
            observer.install()
            try:
                with os.scandir(source) as entries:
                    self.assertEqual([entry.name for entry in entries],['existing.sql'])
                with self.assertRaises(FileExistsError):
                    with open(source/'existing.sql','x'):
                        pass
                self.assertEqual(observer.directories['.'],2)
                self.assertEqual(observer.opens['existing.sql'],2)
            finally:
                observer.stop()

    @unittest.skipUnless(os.name=='posix','dir_fd behavior uses proc descriptors')
    def test_dir_fd_probe_resolves_exact_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory);fd=os.open(source,os.O_RDONLY)
            context=SimpleNamespace(active=True,group=1,all=1,opaque=0)
            observer=Observations(source,context);observer.framework_filter=False
            observer.install()
            try:
                with self.assertRaises(FileNotFoundError):
                    os.stat('missing.sql',dir_fd=fd)
                self.assertEqual(observer.probes['missing.sql'],1)
            finally:
                observer.stop();os.close(fd)
