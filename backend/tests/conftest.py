import pytest

from silencio_set.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)


@pytest.fixture
def settings():
    # Never load the developer's actual .env or contact external dependencies.
    return Settings(_env_file=None, app_env="test")
