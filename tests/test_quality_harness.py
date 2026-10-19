import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.verify_browser import verify


def test_http_quality_harness_rejects_existing_database_before_opening_the_application(tmp_path):
    database = tmp_path / 'existing.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE sentinel(value TEXT)')
        connection.execute("INSERT INTO sentinel VALUES('preserved')")
    with pytest.raises(ValueError, match='new independent database'):
        asyncio.run(verify(SimpleNamespace(database=database, scripts=['quality_evaluation.js'])))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('sentinel',)]
        assert connection.execute('SELECT value FROM sentinel').fetchall() == [('preserved',)]
