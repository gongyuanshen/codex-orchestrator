"""Route A migration must preserve old runs without inventing acceptance."""
from contextlib import closing
import sqlite3

import pytest

from orchestrator.config import Settings
from orchestrator.db import Database, SCHEMA_VERSION


def test_future_schema_is_rejected_before_ddl(tmp_path):
    path = tmp_path / 'future.db'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL)')
        conn.execute('INSERT INTO schema_migrations VALUES(999,0)')
    with pytest.raises(ValueError, match='newer'):
        Database(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("select name from sqlite_master where type='table' order by name").fetchall() == [('schema_migrations',)]


def test_migration_is_idempotent_and_existing_runs_remain_legacy(tmp_path):
    path = tmp_path / 'legacy.db'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL)')
        conn.execute('INSERT INTO schema_migrations VALUES(3,0)')
        conn.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,root_path TEXT UNIQUE,name TEXT,is_git INTEGER,created_at REAL)')
        conn.execute('INSERT INTO projects VALUES(?,?,?,?,?)', ('proj', str(tmp_path), 'legacy', 0, 0))
        conn.execute("CREATE TABLE runs(id TEXT PRIMARY KEY,project_id TEXT,mode TEXT,title TEXT,status TEXT,metadata_json TEXT,created_at REAL,updated_at REAL)")
        conn.execute("INSERT INTO runs VALUES('old','proj','swarm','Existing','active','{}',1,2)")
    db = Database(path, settings=Settings(data_dir=tmp_path))
    assert db.get_run('old')['workflow_version'] == 0
    new = db.create_run(mode='boost', title='New', project_id='proj')
    assert new['workflow_version'] == 5
    assert db.migrate() == SCHEMA_VERSION == 9
    assert db.get_run('old')['created_at'] == 1
    with db.read_connection() as conn:
        assert conn.execute('select count(*) from verification_records').fetchone()[0] == 0
        assert conn.execute('pragma foreign_key_check').fetchall() == []
    db.close()
