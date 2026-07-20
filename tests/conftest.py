"""Lightweight test stubs when optional runtime packages are unavailable.

Railway installs psycopg2-binary from requirements.txt. The stub only lets pure
unit tests run in restricted build environments without PostgreSQL drivers.
"""
from __future__ import annotations

import sys
import types

try:
    import psycopg2  # noqa: F401
except ModuleNotFoundError:
    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.connect = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("psycopg2 test stub cannot connect")
    )

    extras = types.ModuleType("psycopg2.extras")

    class Json:
        def __init__(self, value):
            self.adapted = value

    class RealDictCursor:
        pass

    extras.Json = Json
    extras.RealDictCursor = RealDictCursor

    sql = types.ModuleType("psycopg2.sql")

    class _SqlToken:
        def __init__(self, value=""):
            self.value = value

        def format(self, *args, **kwargs):
            return self

        def join(self, values):
            return self

    sql.SQL = _SqlToken
    sql.Identifier = _SqlToken
    sql.Placeholder = _SqlToken

    psycopg2.extras = extras
    psycopg2.sql = sql
    sys.modules["psycopg2"] = psycopg2
    sys.modules["psycopg2.extras"] = extras
    sys.modules["psycopg2.sql"] = sql


try:
    import web3  # noqa: F401
except ModuleNotFoundError:
    web3 = types.ModuleType("web3")

    class Web3:
        class HTTPProvider:
            def __init__(self, *args, **kwargs):
                pass

        def __init__(self, *args, **kwargs):
            pass

        @staticmethod
        def to_checksum_address(value):
            return value

    web3.Web3 = Web3
    sys.modules["web3"] = web3
