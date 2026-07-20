from __future__ import annotations

from contextlib import contextmanager

from app import db


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.query = None
        self.params = None

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, row):
        self.row = row
        self.cursor_instance = FakeCursor(row)

    def cursor(self, cursor_factory=None):
        return self.cursor_instance

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_get_decision_returns_row_and_uses_epoch(monkeypatch):
    connection = FakeConnection({"betting_epoch": 123, "signal": "UP"})

    @contextmanager
    def fake_conn():
        yield connection

    monkeypatch.setattr(db, "conn", fake_conn)
    result = db.get_decision(123)

    assert result == {"betting_epoch": 123, "signal": "UP"}
    assert connection.cursor_instance.params == (123,)


def test_get_decision_returns_none_when_missing(monkeypatch):
    connection = FakeConnection(None)

    @contextmanager
    def fake_conn():
        yield connection

    monkeypatch.setattr(db, "conn", fake_conn)
    assert db.get_decision(999) is None
