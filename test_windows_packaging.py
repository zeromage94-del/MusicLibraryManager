import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class PackagingTests(unittest.TestCase):
    def test_scripts_exist(self):
        for rel in ('.github/workflows/build-windows.yml',
                    'scripts/build_windows.ps1',
                    'packaging/MusicLibraryManager.iss',
                    'tools/tiddl_entry.py','tools/spotdl_entry.py'):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_cli_entrypoints_parse(self):
        for rel in ('tools/tiddl_entry.py','tools/spotdl_entry.py','app.py'):
            ast.parse((ROOT/rel).read_text(encoding='utf-8'))

    def test_no_data_deleted_on_uninstall(self):
        setup = (ROOT/'packaging/MusicLibraryManager.iss').read_text(encoding='utf-8')
        self.assertNotIn('[UninstallDelete]', setup)
        self.assertIn('PrivilegesRequired=lowest', setup)

    def test_build_creates_cli_binaries(self):
        build=(ROOT/'scripts/build_windows.ps1').read_text(encoding='utf-8')
        self.assertIn('--name spotdl', build)
        self.assertIn('--name tiddl', build)
        self.assertIn('--build-check', build)

if __name__=='__main__':
    unittest.main()
