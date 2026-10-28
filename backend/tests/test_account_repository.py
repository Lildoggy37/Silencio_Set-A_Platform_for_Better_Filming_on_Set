import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from silencio_set.accounts.cache import Grant
from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import RegistrationReceipt, User
from silencio_set.accounts.repository import AccountRepository, receipt_outcome


def fake_session_factory():
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.scalar.return_value = None
    transaction = session.begin.return_value
    transaction.__aexit__.return_value = False
    return Mock(return_value=session), session, transaction


def grant():
    now = int(datetime.now(UTC).timestamp())
    return Grant(email="Director@example.com", email_normalized="director@example.com", verified_at=now, expires_at=now + 300)


def test_registration_writes_user_then_receipt_in_one_committed_context():
    factory, session, transaction = fake_session_factory()
    outcome = asyncio.run(AccountRepository(factory).create_registration("example-digest", grant(), "example-hash"))
    assert outcome.created
    transaction.__aexit__.assert_awaited_once()
    user, receipt = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(user, User) and isinstance(receipt, RegistrationReceipt)
    assert user.id == receipt.user_id == outcome.user_id
    assert user.email_normalized == receipt.email_normalized
    assert receipt.token_digest == "example-digest"
    assert session.flush.await_count == 2


def test_commit_failure_cannot_return_a_success_outcome():
    factory, session, transaction = fake_session_factory()
    transaction.__aexit__.side_effect = OperationalError("test SQL", {}, Exception("lost commit response"))
    with pytest.raises(OperationalError):
        asyncio.run(AccountRepository(factory).create_registration("example-digest", grant(), "example-hash"))


def test_same_valid_receipt_returns_without_inserting_or_changing_password():
    factory, session, transaction = fake_session_factory()
    receipt = RegistrationReceipt(user_id=uuid4(), token_expires_at=datetime.now(UTC) + timedelta(minutes=5))
    session.scalar.return_value = receipt
    outcome = asyncio.run(AccountRepository(factory).create_registration("example-digest", grant(), "new-hash-must-not-be-used"))
    assert not outcome.created and outcome.user_id == receipt.user_id
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


def test_integrity_race_recovers_the_committed_receipt():
    factory, session, transaction = fake_session_factory()
    session.flush.side_effect = IntegrityError("example SQL", {}, Exception("race"))
    receipt = RegistrationReceipt(user_id=uuid4(), token_expires_at=datetime.now(UTC) + timedelta(minutes=5))
    session.scalar.side_effect = [None, receipt]
    outcome = asyncio.run(AccountRepository(factory).create_registration("example-digest", grant(), "example-hash"))
    assert not outcome.created and outcome.user_id == receipt.user_id


def test_integrity_error_for_another_token_is_not_misreported_as_success():
    factory, session, transaction = fake_session_factory()
    session.flush.side_effect = IntegrityError("example SQL", {}, Exception("private details"))
    with pytest.raises(AuthError) as exc:
        asyncio.run(AccountRepository(factory).create_registration("example-digest", grant(), "example-hash"))
    assert exc.value.status == 409
    assert "private details" not in str(exc.value)


def test_expired_receipt_cannot_be_reused():
    with pytest.raises(AuthError) as exc:
        receipt_outcome(RegistrationReceipt(user_id=uuid4(), token_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert exc.value.code == "REGISTRATION_TOKEN_INVALID"
