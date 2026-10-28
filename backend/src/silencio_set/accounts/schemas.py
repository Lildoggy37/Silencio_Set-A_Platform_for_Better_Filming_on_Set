from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictStr, field_validator

from silencio_set.accounts.identity import normalize_display_name, normalize_email
from silencio_set.accounts.passwords import MAX_PASSWORD_LENGTH, validate_new_password


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class EmailInput(AccountInput):
    email: StrictStr = Field(repr=False)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value).display


class EmailVerificationRequest(EmailInput):
    purpose: Literal["register"] = "register"


class VerificationInput(EmailInput):
    verification_id: UUID
    code: SecretStr = Field(repr=False)

    @field_validator("code", mode="before")
    @classmethod
    def valid_code(cls, value):
        if not isinstance(value, str) or len(value) != 6 or not value.isascii() or not value.isdigit():
            raise ValueError("验证码须为6位数字字符串")
        return value


class RegistrationRequest(AccountInput):
    registration_token: SecretStr = Field(repr=False)
    password: SecretStr = Field(repr=False)

    @field_validator("registration_token", mode="before")
    @classmethod
    def valid_token(cls, value):
        if not isinstance(value, str) or not 32 <= len(value) <= 256:
            raise ValueError("注册凭证格式不正确")
        return value

    @field_validator("password", mode="before")
    @classmethod
    def valid_password(cls, value):
        return validate_new_password(value)


class LoginRequest(EmailInput):
    password: SecretStr = Field(repr=False)

    @field_validator("password", mode="before")
    @classmethod
    def valid_password(cls, value):
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_PASSWORD_LENGTH:
            raise ValueError("登录信息格式不正确")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("登录信息格式不正确")
        return value


class ProfileUpdate(AccountInput):
    display_name: StrictStr
    expected_lock_version: Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value):
        return normalize_display_name(value)


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    profile_completed: bool
    missing_fields: list[str]
    lock_version: int
    # This explicit allowlist intentionally omits password_hash, receipts, etc.


class ProfileView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    display_name: str | None
    profile_completed: bool
    missing_fields: list[str]
    lock_version: int
