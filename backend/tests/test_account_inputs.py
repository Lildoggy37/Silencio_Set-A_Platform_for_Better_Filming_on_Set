from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from silencio_set.accounts.identity import normalize_display_name, normalize_email
from silencio_set.accounts.models import User
from silencio_set.accounts.schemas import LoginRequest, ProfileUpdate, RegistrationRequest, UserView


def test_email_normalization_keeps_display_dots_and_plus_suffix():
    identity = normalize_email("  Film.Director+Cut@EXAMPLE.com  ")
    assert identity.display == "Film.Director+Cut@EXAMPLE.com"
    assert identity.normalized == "film.director+cut@example.com"
    assert "example.com" not in repr(identity)


def test_international_domain_forms_resolve_to_same_identity():
    assert normalize_email("director@例子.公司").normalized == normalize_email("director@xn--fsqu00a.xn--55qx5d").normalized


@pytest.mark.parametrize("email", ["not-an-email", "name@", "user\n@example.com", 123, None, "x" * 255 + "@example.com"])
def test_email_invalid_inputs_are_rejected(email):
    with pytest.raises(ValueError):
        normalize_email(email)


@pytest.mark.parametrize("name,expected", [("  林同学  ", "林同学"), ("a" * 30, "a" * 30), ("\u3000导演\u3000", "导演"), ("O'Reilly", "O'Reilly"), ("🎬", "🎬")])
def test_nickname_valid_inputs(name, expected):
    assert normalize_display_name(name) == expected


@pytest.mark.parametrize("name", ["", " \t\n\u3000", "a" * 31, "林\n同学", "林\x00同学", "\ud800", 123, None])
def test_nickname_invalid_inputs(name):
    with pytest.raises(ValueError):
        normalize_display_name(name)


@pytest.mark.parametrize("version", [0, -1, True, "1", 1.0, None, 2_147_483_648])
def test_profile_version_is_a_strict_positive_database_integer(version):
    with pytest.raises(ValidationError):
        ProfileUpdate(display_name="导演", expected_lock_version=version)


def test_profile_does_not_accept_client_selected_account_or_completion_state():
    with pytest.raises(ValidationError):
        ProfileUpdate(display_name="导演", expected_lock_version=1, user_id=str(uuid4()), profile_completed=True)


def test_registration_and_login_secrets_are_not_in_repr_or_json():
    password = "correct horse sample battery"
    registration = RegistrationRequest(registration_token="a" * 43, password=password)
    login = LoginRequest(email="director@example.com", password=password)
    assert password not in repr(registration) + registration.model_dump_json() + repr(login) + login.model_dump_json()
    assert "a" * 43 not in repr(registration) + registration.model_dump_json()
    assert "director@example.com" not in repr(login)


def test_invalid_password_validation_string_does_not_show_input():
    with pytest.raises(ValidationError) as exc:
        RegistrationRequest(registration_token="a" * 43, password="short-secret")
    assert "short-secret" not in str(exc.value)


def test_login_preserves_spaces_and_can_check_historical_shorter_password():
    login = LoginRequest(email="Director@EXAMPLE.com", password=" legacy ")
    assert login.password.get_secret_value() == " legacy "


def test_user_view_is_an_allowlist_not_an_orm_dump():
    user = User(id=uuid4(), email="director@example.com", email_normalized="director@example.com",
                password_hash="private-hash-example", display_name=None, profile_completed_at=None, lock_version=1)
    view = UserView.model_validate(user)
    assert not view.profile_completed
    assert view.missing_fields == ["display_name"]
    assert "password_hash" not in view.model_dump()
    assert "private-hash-example" not in view.model_dump_json()
    user.display_name = "导演"
    user.profile_completed_at = datetime.now(UTC)
    assert UserView.model_validate(user).profile_completed


@pytest.mark.parametrize("name,completed_at,completed", [(None, None, False), ("导演", None, False), (None, datetime.now(UTC), False), (" ", datetime.now(UTC), False), ("导演", datetime.now(UTC), True)])
def test_completion_requires_both_valid_nickname_and_time(name, completed_at, completed):
    assert User(display_name=name, profile_completed_at=completed_at).profile_completed is completed
