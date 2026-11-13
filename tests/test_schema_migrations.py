import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from storage.migrations import MIGRATIONS, SchemaTooNewError, latest_version, run_migrations, schema_version
from storage.sqlite_backend import SQLiteRepository


BASE = latest_version()


def make_repo(tmp):
    repo = SQLiteRepository(Path(tmp) / "t.db")
    repo.init_storage()
    return repo


class SchemaMigrationsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_a_new_database_is_stamped_with_the_latest_version(self):
        repo = make_repo(self._tmp.name)
        conn = repo.open_read()
        try:
            self.assertEqual(schema_version(conn), latest_version())
        finally:
            conn.close()

    def test_init_storage_twice_is_harmless(self):
        repo = make_repo(self._tmp.name)
        repo.init_storage()
        conn = repo.open_read()
        try:
            self.assertEqual(schema_version(conn), latest_version())
        finally:
            conn.close()

    def test_an_unversioned_legacy_database_is_adopted(self):
        repo = make_repo(self._tmp.name)
        conn = repo.open_write()
        try:
            conn.execute("PRAGMA user_version = 0;")
        finally:
            conn.close()
        repo.init_storage()
        conn = repo.open_read()
        try:
            self.assertEqual(schema_version(conn), latest_version())
        finally:
            conn.close()

    def test_steps_run_once_in_order_and_a_failed_step_rolls_back(self):
        repo = make_repo(self._tmp.name)
        seen = []

        def two(conn):
            seen.append(2)
            conn.execute("CREATE TABLE t2 (x INTEGER);")

        def three(conn):
            seen.append(3)
            conn.execute("CREATE TABLE t3 (x INTEGER);")
            raise RuntimeError("boom")

        steps = list(MIGRATIONS) + [(BASE + 1, "two", two), (BASE + 2, "three", three)]
        conn = repo.open_write()
        try:
            with self.assertRaises(RuntimeError):
                run_migrations(conn, steps)
            self.assertEqual(seen, [2, 3])
            self.assertEqual(schema_version(conn), BASE + 1)  # step 2 stayed, step 3 rolled back with its table
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")}
            self.assertIn("t2", tables)
            self.assertNotIn("t3", tables)
            steps[-1] = (BASE + 2, "three", lambda c: seen.append("3b"))
            self.assertEqual(run_migrations(conn, steps), BASE + 2)
            self.assertEqual(run_migrations(conn, steps), BASE + 2)
            self.assertEqual(seen, [2, 3, "3b"])
        finally:
            conn.close()

    def test_a_database_from_a_newer_release_is_refused(self):
        repo = make_repo(self._tmp.name)
        conn = repo.open_write()
        try:
            conn.execute("PRAGMA user_version = %d;" % (latest_version() + 1))
        finally:
            conn.close()
        with self.assertRaises(SchemaTooNewError):
            repo.init_storage()

    def test_numbers_must_not_have_gaps(self):
        repo = make_repo(self._tmp.name)
        conn = repo.open_write()
        try:
            with self.assertRaises(ValueError):
                run_migrations(conn, [(1, "a", lambda c: None), (3, "c", lambda c: None)])
        finally:
            conn.close()

    def test_services_starting_together_apply_a_step_once(self):
        repo = make_repo(self._tmp.name)
        calls = []

        def two(conn):
            calls.append(1)
            conn.execute("CREATE TABLE t2 (x INTEGER);")

        steps = list(MIGRATIONS) + [(BASE + 1, "two", two)]
        errors = []

        def worker():
            conn = repo.open_write()
            try:
                run_migrations(conn, steps)
            except sqlite3.Error as exc:  # pragma: no cover - would be a real failure
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
