from dataclasses import dataclass, field

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128


def validate_new_password(password: str) -> str:
    if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError("新密码须为15至128字符")
    # Preserve spaces and Unicode exactly; never strip, normalize or truncate.
    if any(0xD800 <= ord(char) <= 0xDFFF for char in password):
        raise ValueError("密码包含非法Unicode字符")
    return password


@dataclass(frozen=True)
class PasswordCheck:
    matched: bool
    replacement_hash: str | None = field(default=None, repr=False)


class PasswordService:
    def __init__(self):
        # Local development baseline, not a throughput claim. Bound concurrency
        # in the future HTTP layer; do not execute this CPU work in the event loop.
        self._hasher = PasswordHasher(
            time_cost=3, memory_cost=65_536, parallelism=1,
            hash_len=32, salt_len=16, type=Type.ID,
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(validate_new_password(password))

    def verify(self, stored_hash: str, password: str) -> PasswordCheck:
        # Login accepts historical passwords shorter than the current signup
        # minimum; it must still compare the complete submitted password.
        if not isinstance(password, str) or not 1 <= len(password) <= MAX_PASSWORD_LENGTH:
            return PasswordCheck(False)
        if any(0xD800 <= ord(char) <= 0xDFFF for char in password):
            return PasswordCheck(False)
        if not isinstance(stored_hash, str) or not stored_hash.startswith("$argon2id$"):
            return PasswordCheck(False)
        try:
            self._hasher.verify(stored_hash, password)
            replacement = self._hasher.hash(password) if self._hasher.check_needs_rehash(stored_hash) else None
            return PasswordCheck(True, replacement)
        except (InvalidHashError, VerificationError):
            # A malformed hash is not a successful login. Never echo it.
            return PasswordCheck(False)
