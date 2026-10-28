from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet


def find_repository_root(source: Path) -> Path | None:
    for parent in source.resolve().parents:
        if (parent / "backend" / "pyproject.toml").is_file():
            return parent
    return None


# Source checkout: resolve .env without depending on the shell's directory.
# Installed wheel / Docker: environment only, never search site-packages for .env.
PROJECT_ROOT = find_repository_root(Path(__file__))
ENV_FILE = PROJECT_ROOT / ".env" if PROJECT_ROOT is not None else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test"] = "development"
    app_timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    celery_broker_url: SecretStr | None = None
    diane_enabled: bool = False
    auth_enabled: bool = False
    auth_secret: SecretStr | None = None
    auth_allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:8000", "http://localhost:8000",
    )
    auth_cookie_secure: bool = False
    auth_session_ttl: int = Field(default=28_800, ge=60, le=86_400)
    auth_prelogin_ttl: int = Field(default=900, ge=60, le=3600)
    auth_verification_ttl: int = Field(default=300, ge=60, le=900)
    auth_registration_ttl: int = Field(default=300, ge=60, le=900)
    auth_resend_interval: int = Field(default=60, ge=1, le=300)
    auth_verification_attempts: int = Field(default=5, ge=1, le=10)
    mail_enabled: bool = False
    mail_payload_key: SecretStr | None = None
    mail_host: str = "127.0.0.1"
    mail_port: int = Field(default=1025, ge=1, le=65535)
    mail_mode: Literal["local", "starttls", "ssl"] = "local"
    mail_username: SecretStr | None = None
    mail_password: SecretStr | None = None
    mail_from: str = "silencio@example.com"
    mail_timeout: int = Field(default=5, ge=1, le=5)
    mail_max_attempts: int = Field(default=3, ge=1, le=3)
    invitation_public_origin: str = "http://127.0.0.1:5173"

    @field_validator("invitation_public_origin")
    @classmethod
    def valid_invitation_origin(cls, value):
        parsed = urlsplit(value)
        _ = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or parsed.hostname == "*"
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("Invitation URL requires an explicit frontend origin")
        return value

    @field_validator("mail_port", mode="before")
    @classmethod
    def empty_mail_port(cls, value):
        return 1025 if value == "" else value

    @field_validator("mail_payload_key", "mail_username", "mail_password", mode="before")
    @classmethod
    def empty_mail_secret(cls, value):
        return None if value == "" else value

    @field_validator("mail_from", mode="before")
    @classmethod
    def valid_mail_sender(cls, value):
        from silencio_set.accounts.identity import normalize_email
        return normalize_email(value or "silencio@example.com").display

    @field_validator("mail_payload_key")
    @classmethod
    def valid_mail_key(cls, value):
        if value is not None:
            try:
                Fernet(value.get_secret_value().encode("ascii"))
            except (ValueError, UnicodeError):
                raise ValueError("MAIL_PAYLOAD_KEY must be an independent Fernet key") from None
        return value

    @model_validator(mode="after")
    def check_mail_configuration(self):
        if self.mail_enabled and self.invitation_public_origin not in self.auth_allowed_origins:
            raise ValueError("Invitation frontend origin must be an allowed authentication origin")
        if self.mail_enabled and (not self.auth_enabled or self.auth_secret is None or self.database_url is None
                                 or self.redis_url is None or self.mail_payload_key is None or self.celery_broker_url is None):
            raise ValueError("Mail requires authentication, MAIL_PAYLOAD_KEY and CELERY_BROKER_URL")
        if self.mail_enabled and self.mail_payload_key.get_secret_value() == self.auth_secret.get_secret_value():
            raise ValueError("Mail encryption and authentication must use independent keys")
        if self.mail_enabled and self.mail_mode == "local":
            if self.mail_host not in {"127.0.0.1", "localhost", "::1", "mailpit"} or self.mail_username or self.mail_password:
                raise ValueError("Plain SMTP is only allowed for the local test inbox without credentials")
        if self.mail_enabled and bool(self.mail_username) != bool(self.mail_password):
            raise ValueError("SMTP username and password must be configured together")
        return self

    @field_validator("auth_secret", mode="before")
    @classmethod
    def empty_secret_is_missing(cls, value):
        return None if value == "" else value

    @field_validator("auth_secret")
    @classmethod
    def valid_auth_secret(cls, value):
        if value is not None and not 32 <= len(value.get_secret_value().encode("utf-8")) <= 256:
            raise ValueError("AUTH_SECRET must contain 32 to 256 bytes")
        return value

    @field_validator("auth_allowed_origins")
    @classmethod
    def valid_origins(cls, origins):
        if not origins:
            raise ValueError("At least one explicit origin is required")
        for origin in origins:
            parsed = urlsplit(origin)
            _ = parsed.port  # Reject malformed/out-of-range port numbers.
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path or parsed.query or parsed.fragment):
                raise ValueError("Use explicit HTTP origins without paths or credentials")
            # A non-Secure cookie is allowed only for loopback development.
            if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("HTTP authentication origins must be loopback addresses")
        return origins

    @model_validator(mode="after")
    def check_auth_configuration(self):
        if self.auth_enabled and (self.auth_secret is None or self.database_url is None or self.redis_url is None):
            raise ValueError("Authentication requires AUTH_SECRET, DATABASE_URL and REDIS_URL")
        if self.auth_enabled and not self.auth_cookie_secure and any(
            urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "::1"}
            for origin in self.auth_allowed_origins
        ):
            raise ValueError("Non-Secure session cookies are only allowed for loopback development")
        return self

    @field_validator("database_url", "redis_url", "celery_broker_url", mode="before")
    @classmethod
    def empty_url_is_missing(cls, value):
        return None if value == "" else value

    @field_validator("database_url", "redis_url", "celery_broker_url")
    @classmethod
    def check_url(cls, value, info):
        if value is None:
            return None
        schemes = {
            "database_url": {"postgresql+asyncpg"},
            "redis_url": {"redis", "rediss"},
            "celery_broker_url": {"amqp", "amqps"},
        }
        try:
            url = urlsplit(value.get_secret_value())
            valid = url.scheme in schemes[info.field_name] and url.hostname and url.port
        except ValueError:
            valid = False
        if not valid:
            # Never interpolate a connection URL into an exception.
            raise ValueError("Use the supported network URL with an explicit port")
        return value

    @model_validator(mode="after")
    def reject_unimplemented_agent(self):
        if self.diane_enabled:
            raise ValueError("Diane is not implemented in this development milestone")
        return self


def load_settings(*, env_file: Path | None = ENV_FILE) -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        # Startup exceptions must not include raw dotenv values or exception contexts.
        fields = sorted({".".join(map(str, e["loc"])) or "application" for e in exc.errors()})
        raise RuntimeError("Invalid configuration fields: " + ", ".join(fields)) from None
