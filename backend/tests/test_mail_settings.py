from cryptography.fernet import Fernet
import pytest
from pydantic import ValidationError

from silencio_set.core.config import Settings


def configured_mail(**overrides):
    fields = dict(
        _env_file=None, app_env="test", auth_enabled=True, auth_secret="test-only-not-a-real-auth-secret-" * 2,
        database_url="postgresql+asyncpg://user:example@localhost:5432/test", redis_url="redis://localhost:6379/0",
        celery_broker_url="amqp://user:example@localhost:5672/test", mail_enabled=True,
        mail_payload_key=Fernet.generate_key().decode(),
    )
    fields.update(overrides)
    return Settings(**fields)


def test_mail_requires_all_explicit_security_configuration():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mail_enabled=True)
    with pytest.raises(ValidationError):
        configured_mail(auth_secret=None)
    assert configured_mail().mail_enabled


@pytest.mark.parametrize("field,value", [("mail_payload_key", None), ("mail_payload_key", "private-invalid-key"),
    ("mail_host", "external.example.com"), ("mail_from", "broken\nemail@example.com"), ("mail_max_attempts", 4)])
def test_invalid_or_insecure_mail_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError) as exc:
        configured_mail(**{field: value})
    assert "private-invalid-key" not in str(exc.value)


def test_mail_key_is_independent_and_masked():
    key = Fernet.generate_key().decode()
    with pytest.raises(ValidationError):
        configured_mail(auth_secret=key, mail_payload_key=key)
    settings = configured_mail(mail_payload_key=key)
    assert key not in repr(settings) + settings.model_dump_json()


def test_tls_credentials_must_be_paired_but_secure_external_configuration_is_supported():
    with pytest.raises(ValidationError):
        configured_mail(mail_mode="starttls", mail_host="smtp.example.com", mail_username="private-user")
    assert configured_mail(mail_mode="starttls", mail_host="smtp.example.com", mail_username="private-user", mail_password="private-password")


def test_disabled_mail_accepts_reserved_empty_port_and_secret(tmp_path):
    path = tmp_path / ".env"
    path.write_text("MAIL_ENABLED=false\nMAIL_PORT=\nMAIL_PAYLOAD_KEY=\nMAIL_FROM=\n", encoding="utf-8")
    assert Settings(_env_file=path).mail_port == 1025
