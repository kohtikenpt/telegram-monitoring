import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import core


class DatabaseConnectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='monitor-db-test-')
        self.db_patch = patch.object(core, 'DB', Path(self.folder.name) / 'monitor.db')
        self.db_patch.start()
        core.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.folder.cleanup()

    def test_successful_transaction_commits_and_closes(self):
        with core.conn() as connection:
            connection.execute("INSERT INTO settings VALUES('test', 'saved')")
        self.assertEqual(core.setting('test'), 'saved')
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_failed_transaction_rolls_back_and_closes(self):
        with self.assertRaisesRegex(RuntimeError, 'test failure'):
            with core.conn() as connection:
                connection.execute("INSERT INTO settings VALUES('test', 'discard')")
                raise RuntimeError('test failure')
        self.assertEqual(core.setting('test'), '')
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')


if __name__ == '__main__':
    unittest.main()
