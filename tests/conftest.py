import os
import tempfile

import pytest


@pytest.fixture()
def env(monkeypatch):
    home = tempfile.mkdtemp()
    monkeypatch.setenv("SURPLUS_HOME", home)
    from surplus.config import load_settings
    from surplus.store import Store

    settings = load_settings()
    store = Store(settings.db_path)
    yield settings, store
    store.close()
