"""每个测试使用独立 SQLite，避免状态泄漏到本地业务库。"""

import pytest

from app import db


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
