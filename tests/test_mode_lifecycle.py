"""Fatal schema rejection must release resources without hiding the error."""
from contextlib import closing
import sqlite3

import pytest

from orchestrator.config import Settings
from orchestrator.db import Database, SCHEMA_VERSION


def test_failed_database_initialization_closes_its_real_connection_pool(tmp_path):
    path = tmp_path / "future.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL)")
        connection.execute("INSERT INTO schema_migrations VALUES(?,0)", (SCHEMA_VERSION + 1,))
    database = Database.__new__(Database)
    try:
        with pytest.raises(ValueError, match="newer"):
            database.__init__(path, settings=Settings(data_dir=tmp_path))
        assert database._closed is True
        assert database._conns_by_tid == {}
    finally:
        database.close()
