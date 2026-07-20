from contextlib import contextmanager

from app import db


class FakeCursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()

    def cursor(self, *args, **kwargs):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass


@contextmanager
def fake_conn():
    yield FakeConnection()


def test_init_db_sql_templates_format_without_literal_brace_crash(monkeypatch):
    """Regression test for psycopg2.sql.SQL.format() and JSONB DEFAULT '{}'."""
    monkeypatch.setattr(db, "conn", fake_conn)
    db.init_db()
