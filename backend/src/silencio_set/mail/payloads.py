from datetime import UTC, datetime
import json
from typing import Literal
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from silencio_set.accounts.cache import AuthCache
from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.identity import normalize_email


class PayloadUnavailable(Exception):
    pass


class EncryptedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    key_version: Literal["v1"]
    ciphertext: StrictStr = Field(repr=False)


class VerificationMail(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    purpose: Literal["register"] = "register"
    payload_id: UUID
    verification_id: UUID
    email: str = Field(repr=False)
    email_normalized: str = Field(repr=False)
    email_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)
    expires_at: int

    @model_validator(mode="after")
    def valid_binding(self):
        if normalize_email(self.email).normalized != self.email_normalized:
            raise ValueError("Invalid recipient binding")
        return self


class EncryptedPayloads:
    def __init__(self, cache: AuthCache, key: str):
        self.cache = cache
        self._cipher = Fernet(key.encode("ascii"))

    @staticmethod
    def key(payload_id: UUID) -> str:
        return "auth:mail_payload:" + str(payload_id)

    async def save(self, payload: VerificationMail):
        if payload.expires_at <= datetime.now(UTC).timestamp():
            raise PayloadUnavailable()
        # Plaintext serialization is used only as input to authenticated encryption.
        encrypted = self._cipher.encrypt(payload.model_dump_json().encode("utf-8")).decode("ascii")
        accepted = await self.cache.call(self.cache.redis.set, self.key(payload.payload_id),
            json.dumps({"key_version": "v1", "ciphertext": encrypted}), exat=payload.expires_at, nx=True)
        if not accepted:
            raise PayloadUnavailable()

    async def read(self, payload_id: UUID) -> VerificationMail:
        raw = await self.cache.call(self.cache.redis.get, self.key(payload_id))
        if raw is None or len(raw) > 16_384:
            raise PayloadUnavailable()
        try:
            envelope = EncryptedRecord.model_validate_json(raw)
            decrypted = self._cipher.decrypt(envelope.ciphertext.encode("ascii"), ttl=self.cache.settings.auth_verification_ttl)
            payload = VerificationMail.model_validate_json(decrypted)
            if payload.payload_id != payload_id or payload.expires_at <= datetime.now(UTC).timestamp():
                raise PayloadUnavailable()
            return payload
        except (KeyError, TypeError, ValueError, InvalidToken, ValidationError):
            raise PayloadUnavailable() from None

    async def current(self, payload: VerificationMail) -> bool:
        raw = await self.cache.call(self.cache.redis.get, self.cache.challenge_key(payload.email_normalized))
        if raw is None:
            return False
        try:
            current = json.loads(raw)
            expected = self.cache.digest_identity("code", f"{payload.verification_id}|{payload.email_normalized}|{payload.code}")
            return (current["verification_id"] == str(payload.verification_id)
                    and current["code_digest"] == expected
                    and payload.email_digest == self.cache.digest_identity("email", payload.email_normalized)
                    and payload.expires_at > datetime.now(UTC).timestamp())
        except (KeyError, TypeError, ValueError):
            return False

    async def cleanup(self, payload_id: UUID):
        try:
            await self.cache.call(self.cache.redis.delete, self.key(payload_id))
        except AuthError:
            # TTL bounds retention if cleanup fails after an accepted send.
            pass
