from datetime import UTC, datetime
import json
from typing import Literal
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, model_validator

from silencio_set.accounts.identity import normalize_email
from silencio_set.mail.payloads import EncryptedRecord, PayloadUnavailable

INVITATION_MAIL_TTL = 900


class InvitationMail(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    purpose: Literal["crew_invitation"] = "crew_invitation"
    payload_id: UUID
    invitation_id: UUID
    crew_id: UUID
    crew_name: str = Field(min_length=1, max_length=50, repr=False)
    email: str = Field(repr=False)
    email_normalized: str = Field(repr=False)
    email_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)
    expires_at: int
    invitation_expires_at: int

    @model_validator(mode="after")
    def valid_binding(self):
        if normalize_email(self.email).normalized != self.email_normalized or self.expires_at > self.invitation_expires_at:
            raise ValueError("Invalid invitation recipient or deadline binding")
        return self


class InvitationPayloads:
    def __init__(self, cache, key: str):
        self.cache = cache
        self.cipher = Fernet(key.encode("ascii"))

    @staticmethod
    def key(payload_id):
        return "crew:invitation_mail:" + str(payload_id)

    async def save(self, payload: InvitationMail):
        now = datetime.now(UTC).timestamp()
        if not now < payload.expires_at <= now + INVITATION_MAIL_TTL:
            raise PayloadUnavailable()
        ciphertext = self.cipher.encrypt(payload.model_dump_json().encode()).decode("ascii")
        stored = await self.cache.call(self.cache.redis.set, self.key(payload.payload_id),
            json.dumps({"key_version": "v1", "ciphertext": ciphertext}), exat=payload.expires_at, nx=True)
        if not stored:
            raise PayloadUnavailable()

    async def read(self, payload_id) -> InvitationMail:
        raw = await self.cache.call(self.cache.redis.get, self.key(payload_id))
        if raw is None or len(raw) > 16_384:
            raise PayloadUnavailable()
        try:
            record = EncryptedRecord.model_validate_json(raw)
            value = self.cipher.decrypt(record.ciphertext.encode("ascii"), ttl=INVITATION_MAIL_TTL)
            payload = InvitationMail.model_validate_json(value)
            if payload.payload_id != payload_id or payload.expires_at <= datetime.now(UTC).timestamp():
                raise PayloadUnavailable()
            return payload
        except (ValueError, TypeError, InvalidToken):
            raise PayloadUnavailable() from None

    async def cleanup(self, payload_id):
        from silencio_set.accounts.errors import AuthError
        try:
            await self.cache.call(self.cache.redis.delete, self.key(payload_id))
        except AuthError:
            pass  # TTL bounds retention if cleanup fails after SMTP acceptance.
