import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app


class IndexPageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "test.db"
        db = sqlite3.connect(database_path)
        try:
            db.execute(
                """
                CREATE TABLE employees (
                    employee_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    department TEXT,
                    salary INTEGER
                )
                """
            )
            db.execute(
                """
                INSERT INTO employees (full_name, department, salary)
                VALUES ('Anna Petrova', 'Development', 125000)
                """
            )
            db.execute(
                """
                CREATE TABLE departments (
                    code TEXT PRIMARY KEY,
                    title TEXT NOT NULL
                )
                """
            )
            db.execute("INSERT INTO departments VALUES ('DEV', 'Development')")
            db.commit()
        finally:
            db.close()

        self.database_path = database_path
        self.app = create_app({"TESTING": True, "DATABASE": database_path})
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_index_displays_selected_table_from_custom_database(self) -> None:
        response = self.client.get("/?table=employees")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"employees", response.data)
        self.assertIn(b"full_name", response.data)
        self.assertIn(b"Anna Petrova", response.data)
        self.assertIn(b"departments", response.data)

    def test_index_does_not_change_custom_database(self) -> None:
        response = self.client.get("/?table=departments")

        self.assertEqual(response.status_code, 200)
        db = sqlite3.connect(self.database_path)
        try:
            table_names = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            employee_count = db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        finally:
            db.close()

        self.assertEqual(table_names, {"departments", "employees"})
        self.assertEqual(employee_count, 1)

    def test_custom_query_displays_results(self) -> None:
        response = self.client.post(
            "/query",
            data={"query": "SELECT full_name, salary FROM employees"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"full_name", response.data)
        self.assertIn(b"Anna Petrova", response.data)
        self.assertIn(b"125000", response.data)

    def test_custom_query_rejects_data_changes(self) -> None:
        response = self.client.post(
            "/query",
            data={
                "query": (
                    "WITH selected AS (SELECT employee_id FROM employees) "
                    "DELETE FROM employees "
                    "WHERE employee_id IN (SELECT employee_id FROM selected)"
                )
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Ошибка SQLite".encode(), response.data)

        db = sqlite3.connect(self.database_path)
        try:
            employee_count = db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        finally:
            db.close()

        self.assertEqual(employee_count, 1)


if __name__ == "__main__":
    unittest.main()
