from __future__ import annotations

import os
import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USING_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

try:
    import psycopg
except ImportError:  # Local SQLite development does not require psycopg.
    psycopg = None


class PostgresRow:
    def __init__(self, columns, values):
        self._data = dict(zip(columns, values))
        self._columns = columns

    def __getitem__(self, key):
        return self._data[key] if isinstance(key, str) else tuple(self._data.values())[key]

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data)

    def items(self):
        return self._data.items()


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.description = cursor.description

    def _row(self, values):
        if values is None:
            return None
        columns = [column.name for column in self.cursor.description]
        return PostgresRow(columns, values)

    def fetchone(self):
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self.cursor.fetchall()]


class PostgresConnection(AbstractContextManager):
    def __init__(self, url):
        if psycopg is None:
            raise RuntimeError("Install psycopg[binary] to use DATABASE_URL.")
        self.connection = psycopg.connect(url)

    def execute(self, sql, params=()):
        cursor = self.connection.cursor()
        cursor.execute(sql.replace("?", "%s"), params)
        return PostgresCursor(cursor)

    def executemany(self, sql, rows):
        cursor = self.connection.cursor()
        cursor.executemany(sql.replace("?", "%s"), rows)
        return PostgresCursor(cursor)

    def executescript(self, script):
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        if exception_type:
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()
        return False


def open_database():
    if USING_POSTGRES:
        return PostgresConnection(DATABASE_URL)
    connection = sqlite3.connect(Path(__file__).parent / "ag_consulting.sqlite3")
    connection.row_factory = sqlite3.Row
    return connection
