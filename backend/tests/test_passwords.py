import pytest
from argon2 import PasswordHasher, Type

from silencio_set.accounts.passwords import PasswordService, validate_new_password


@pytest.fixture(scope="module")
def passwords():
    return PasswordService()


def test_same_password_gets_random_salt_and_can_be_verified(passwords):
    password = "correct horse sample battery"
    first, second = passwords.hash(password), passwords.hash(password)
    assert first != second
    assert first.startswith("$argon2id$")
    assert "m=65536,t=3,p=1" in first
    assert password not in first
    assert passwords.verify(first, password).matched
    assert passwords.verify(first, password).replacement_hash is None
    assert not passwords.verify(first, "a different sample password").matched


def test_password_spaces_and_unicode_are_not_normalized(passwords):
    password = "  café 🎬 sample password  "
    hashed = passwords.hash(password)
    assert passwords.verify(hashed, password).matched
    assert not passwords.verify(hashed, password.strip()).matched
    assert not passwords.verify(hashed, password.replace("é", "e\u0301")).matched


@pytest.mark.parametrize("password", ["a" * 15, "a" * 128, "电影" * 10, "long sample with spaces"])
def test_password_length_boundaries_and_passphrases(password):
    assert validate_new_password(password) == password


@pytest.mark.parametrize("password", ["", "a" * 14, "a" * 129, 123, None, "a" * 16 + "\ud800"])
def test_password_invalid_inputs_are_rejected(passwords, password):
    with pytest.raises(ValueError):
        passwords.hash(password)


@pytest.mark.parametrize("stored_hash,password", [("not-a-hash", "sample password long"), ("$argon2id$broken", "sample password long"), (None, "sample password long"), ("$argon2id$broken", "a" * 129)])
def test_malformed_hash_and_oversized_input_never_authenticate(passwords, stored_hash, password):
    assert not passwords.verify(stored_hash, password).matched


def test_old_argon2id_parameters_are_upgraded_only_after_success(passwords):
    legacy = PasswordHasher(type=Type.ID, time_cost=2, memory_cost=19_456, parallelism=1)
    password = "legacy short"
    hashed = legacy.hash(password)
    failed = passwords.verify(hashed, "wrong legacy password")
    assert not failed.matched and failed.replacement_hash is None
    result = passwords.verify(hashed, password)
    assert result.matched
    assert result.replacement_hash is not None
    assert passwords.verify(result.replacement_hash, password).matched
    assert result.replacement_hash not in repr(result)
