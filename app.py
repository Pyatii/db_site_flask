import os
import re
import sqlite3
from pathlib import Path
import globals
from flask import Flask, abort, current_app, g, render_template, request


READ_QUERY_PATTERN = re.compile(
    r"^\s*(?:(?:--[^\n]*(?:\n|$))|(?:/\*.*?\*/))*\s*(SELECT|WITH)\b",
    re.IGNORECASE | re.DOTALL,
)


def read_only_authorizer(action: int, _arg1: str, _arg2: str, _database: str, _source: str) -> int:
    """Разрешает пользовательскому запросу только операции чтения SQLite."""
    allowed_actions = {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
    return sqlite3.SQLITE_OK if action in allowed_actions else sqlite3.SQLITE_DENY


def get_database_path() -> Path:
    """Возвращает существующий путь к SQLite-базе из конфигурации."""
    database = current_app.config["DATABASE"]
    if not database:
        raise RuntimeError(
            "Не задан путь к базе. Укажите переменную среды SQLITE_DATABASE."
        )

    database_path = Path(database).expanduser()
    if not database_path.is_absolute():
        database_path = Path(current_app.root_path) / database_path

    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite-база не найдена: {database_path}")

    return database_path.resolve()


def get_db() -> sqlite3.Connection:
    """Открывает пользовательскую SQLite-базу только для чтения."""
    if "db" not in g:
        database_uri = f"{get_database_path().as_uri()}?mode=ro"
        g.db = sqlite3.connect(database_uri, uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_table_names() -> list[str]:
    """Возвращает названия пользовательских таблиц, исключая служебные SQLite."""
    rows = get_db().execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name COLLATE NOCASE
        """
    ).fetchall()
    return [row["name"] for row in rows]


def quote_identifier(identifier: str) -> str:
    """Экранирует известное имя таблицы для использования в SQL-выражении."""
    escaped_identifier = identifier.replace('"', '""')
    return f'"{escaped_identifier}"'


def execute_read_query(query: str) -> tuple[list[str], list[tuple], bool]:
    """Выполняет один пользовательский запрос чтения и ограничивает вывод строк."""
    if not query.strip():
        raise ValueError("Введите SQL-запрос.")

    if not READ_QUERY_PATTERN.match(query):
        raise ValueError("Разрешены только запросы, начинающиеся с SELECT или WITH.")

    db = get_db()
    db.set_authorizer(read_only_authorizer)
    try:
        cursor = db.execute(query)
        if cursor.description is None:
            raise ValueError("Запрос не вернул табличный результат.")

        row_limit = current_app.config["ROWS_PER_PAGE"]
        result_rows = cursor.fetchmany(row_limit + 1)
        has_more_rows = len(result_rows) > row_limit
        rows = [tuple(row) for row in result_rows[:row_limit]]
        columns = [column[0] for column in cursor.description]
        return columns, rows, has_more_rows
    except sqlite3.DatabaseError as error:
        raise ValueError(f"Ошибка SQLite: {error}") from error
    finally:
        db.set_authorizer(None)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get("SQLITE_DATABASE", globals.db_path),
        ROWS_PER_PAGE=100,
    )

    if test_config is not None:
        app.config.update(test_config)

    app.teardown_appcontext(close_db)

    @app.get("/")
    def index():
        tables = get_table_names()
        selected_table = request.args.get("table") or (tables[0] if tables else None)

        if selected_table is not None and selected_table not in tables:
            abort(404)

        columns: list[str] = []
        rows: list[tuple] = []
        if selected_table is not None:
            cursor = get_db().execute(
                f"SELECT * FROM {quote_identifier(selected_table)} LIMIT ?",
                (current_app.config["ROWS_PER_PAGE"],),
            )
            columns = [column[0] for column in cursor.description]
            rows = [tuple(row) for row in cursor.fetchall()]

        return render_template(
            "index.html",
            database_name=get_database_path().name,
            tables=tables,
            selected_table=selected_table,
            columns=columns,
            rows=rows,
        )

    @app.route("/query", methods=["GET", "POST"])
    def query():
        sql_query = ""
        columns: list[str] = []
        rows: list[tuple] = []
        has_more_rows = False
        query_error = None
        status_code = 200

        if request.method == "POST":
            sql_query = request.form.get("query", "")
            try:
                columns, rows, has_more_rows = execute_read_query(sql_query)
            except ValueError as error:
                query_error = str(error)
                status_code = 400

        return (
            render_template(
                "query.html",
                database_name=get_database_path().name,
                sql_query=sql_query,
                columns=columns,
                rows=rows,
                has_more_rows=has_more_rows,
                query_error=query_error,
            ),
            status_code,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
