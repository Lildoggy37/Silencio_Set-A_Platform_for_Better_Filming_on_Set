import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from silencio_set.accounts.models import User
from silencio_set.accounts.profile import (
    AccountUnavailable, ProfileIncomplete, ProfileVersionConflict,
    profile_update_statement, require_completed_profile, save_profile,
)
from silencio_set.accounts.schemas import ProfileUpdate


def test_profile_update_uses_one_conditional_statement_and_bound_parameters():
    user_id = uuid4()
    changes = ProfileUpdate(display_name="O'Reilly", expected_lock_version=3)
    statement = profile_update_statement(user_id, changes, datetime.now(UTC))
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    where = sql.partition(" WHERE ")[2].partition(" RETURNING ")[0]
    assert "users.id =" in where
    assert "users.status =" in where
    assert "users.lock_version =" in where
    assert "coalesce(users.profile_completed_at" in sql
    assert "lock_version=(users.lock_version +" in sql
    assert "O'Reilly" not in sql
    assert "O'Reilly" in compiled.params.values()
    assert user_id in compiled.params.values()


def test_profile_time_must_be_timezone_aware():
    with pytest.raises(ValueError):
        profile_update_statement(uuid4(), ProfileUpdate(display_name="导演", expected_lock_version=1), datetime(2026, 1, 1))


@pytest.mark.parametrize("status,name,completed_at,error", [
    ("active", None, None, ProfileIncomplete),
    ("disabled", "导演", datetime.now(UTC), AccountUnavailable),
    ("disabled", None, None, AccountUnavailable),
])
def test_business_gate_rejects_incomplete_or_disabled_account(status, name, completed_at, error):
    user = User(status=status, display_name=name, profile_completed_at=completed_at)
    with pytest.raises(error):
        require_completed_profile(user)


def test_business_gate_accepts_active_completed_profile():
    require_completed_profile(User(status="active", display_name="导演", profile_completed_at=datetime.now(UTC)))


def test_profile_service_returns_allowlisted_data_and_leaves_commit_to_caller():
    session = AsyncMock(spec=AsyncSession)
    user = User(display_name="导演", profile_completed_at=datetime.now(UTC), lock_version=2)
    result = Mock()
    result.scalar_one_or_none.return_value = user
    session.execute.return_value = result
    view = asyncio.run(save_profile(session, uuid4(), ProfileUpdate(display_name="导演", expected_lock_version=1)))
    assert view.profile_completed and view.lock_version == 2
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("active_version,error", [(None, AccountUnavailable), (2, ProfileVersionConflict), (2_147_483_647, ProfileVersionConflict)])
def test_zero_updated_rows_cannot_be_reported_as_saved(active_version, error):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    session.scalar.return_value = active_version
    with pytest.raises(error):
        asyncio.run(save_profile(session, uuid4(), ProfileUpdate(display_name="导演", expected_lock_version=1)))
    session.commit.assert_not_awaited()
