import pytest
from pydantic import SecretStr, ValidationError

from silencio_set.core.config import PROJECT_ROOT, Settings, find_repository_root, load_settings
from silencio_set.worker import create_worker


def test_dotenv_resolves_to_repository():
    assert PROJECT_ROOT is not None
    assert (PROJECT_ROOT / "backend" / "pyproject.toml").is_file()


def test_installed_package_outside_checkout_uses_environment_only(tmp_path):
    assert find_repository_root(tmp_path / "site-packages" / "silencio_set" / "config.py") is None


def test_optional_services_and_unknown_future_settings(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATABASE_URL=\nQWEN_API_KEY=example-not-real\nMAIL_PORT=\n", encoding="utf-8")
    settings = Settings(_env_file=dotenv)
    assert settings.database_url is None
    assert not settings.diane_enabled
    assert "example-not-real" not in repr(settings)


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("APP_ENV=development\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "test")
    assert Settings(_env_file=dotenv).app_env == "test"


def test_connection_credentials_are_masked():
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:secret-example@localhost:5432/db")
    assert isinstance(settings.database_url, SecretStr)
    assert "secret-example" not in repr(settings)
    assert "secret-example" not in settings.model_dump_json()


@pytest.mark.parametrize("field,value", [
    ("database_url", "sqlite:///secret-example"),
    ("redis_url", "https://user:secret-example@localhost:6379"),
    ("celery_broker_url", "amqp://user:secret-example@localhost:invalid"),
])
def test_invalid_urls_do_not_leak_values(field, value):
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **{field: value})
    assert "secret-example" not in str(exc.value)


def test_startup_config_errors_are_sanitized(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///secret-example")
    with pytest.raises(RuntimeError) as exc:
        load_settings(env_file=None)
    assert "database_url" in str(exc.value)
    assert "secret-example" not in str(exc.value)


def test_cannot_enable_unimplemented_diane():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, diane_enabled=True)


def test_worker_requires_explicit_broker(settings):
    with pytest.raises(RuntimeError, match="CELERY_BROKER_URL"):
        create_worker(settings)


def test_worker_has_three_declared_json_only_queues():
    settings = Settings(_env_file=None, celery_broker_url="amqp://user:example@localhost:5672/silencio")
    worker = create_worker(settings)
    assert {q.name for q in worker.conf.task_queues} == {"notifications", "documents", "agent"}
    assert worker.conf.accept_content == ["json"]
    assert not worker.conf.task_create_missing_queues
    assert worker.conf.result_backend is None
    assert not worker.conf.task_acks_late
    worker.close()


def test_auth_cannot_be_enabled_without_required_configuration():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_enabled=True)


@pytest.mark.parametrize("origin", ["*", "null", "http://remote.example", "http://localhost:99999", "http://localhost/path", "https://user:password@example.com"])
def test_auth_origin_configuration_rejects_unsafe_or_malformed_values(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_allowed_origins=(origin,))


def test_auth_secret_is_not_shown_in_validation_errors():
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, auth_secret="private-short-key")
    assert "private-short-key" not in str(exc.value)
