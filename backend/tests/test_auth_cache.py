import asyncio
import json
from uuid import uuid4

from fakeredis.aioredis import FakeRedis
import pytest
from redis.exceptions import ConnectionError

from silencio_set.accounts.cache import AuthCache, token_digest
from silencio_set.accounts.errors import AuthError
from silencio_set.core.config import Settings


def auth_settings(**overrides):
    return Settings(_env_file=None, app_env="test", auth_secret="test-only-not-a-real-secret-" * 2, **overrides)


def test_verification_code_is_hmac_protected_and_consumed_once():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            issued = await cache.issue_verification("Director@example.com", "127.0.0.1")
            record = json.loads(await redis.get(cache.challenge_key("director@example.com")))
            assert "code" not in record
            assert len(record["code_digest"]) == 64
            assert issued.code.get_secret_value() not in repr(issued)
            result = await cache.exchange("director@example.com", issued.verification_id, issued.code.get_secret_value(), "127.0.0.1")
            assert result.grant.email_normalized == "director@example.com"
            assert await redis.get(cache.challenge_key("director@example.com")) is None
            assert await cache.grant(result.token.get_secret_value()) == result.grant
            with pytest.raises(AuthError, match="VERIFICATION_INVALID"):
                await cache.exchange("director@example.com", issued.verification_id, issued.code.get_secret_value(), "127.0.0.1")
    asyncio.run(scenario())


def test_resend_invalidates_old_id_and_code_but_keeps_new_challenge_usable():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            first = await cache.issue_verification("director@example.com", "127.0.0.1")
            key = cache.challenge_key("director@example.com")
            with pytest.raises(AuthError) as exc:
                await cache.issue_verification("director@example.com", "127.0.0.1")
            assert exc.value.status == 429
            await redis.delete(key + ":cooldown")  # Advance only the fake resend gate.
            second = await cache.issue_verification("director@example.com", "127.0.0.1")
            with pytest.raises(AuthError):
                await cache.exchange("director@example.com", first.verification_id, first.code.get_secret_value(), "127.0.0.1")
            assert json.loads(await redis.get(key))["failures"] == 0
            assert (await cache.exchange("director@example.com", second.verification_id, second.code.get_secret_value(), "127.0.0.1")).grant
    asyncio.run(scenario())


def test_five_wrong_attempts_revoke_the_challenge_without_extending_its_ttl():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            issued = await cache.issue_verification("director@example.com", "127.0.0.1")
            key = cache.challenge_key("director@example.com")
            wrong = "000000" if issued.code.get_secret_value() != "000000" else "000001"
            ttl = await redis.ttl(key)
            for _ in range(5):
                with pytest.raises(AuthError):
                    await cache.exchange("director@example.com", issued.verification_id, wrong, "127.0.0.1")
            assert await redis.get(key) is None
            assert ttl <= 300
            with pytest.raises(AuthError):
                await cache.exchange("director@example.com", issued.verification_id, issued.code.get_secret_value(), "127.0.0.1")
    asyncio.run(scenario())


def test_expired_verification_cannot_be_exchanged():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            issued = await cache.issue_verification("director@example.com", "127.0.0.1")
            await redis.pexpire(cache.challenge_key("director@example.com"), -1)
            with pytest.raises(AuthError):
                await cache.exchange("director@example.com", issued.verification_id, issued.code.get_secret_value(), "127.0.0.1")
    asyncio.run(scenario())


def test_racing_exchanges_have_only_one_winner_in_the_lua_simulator():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            issued = await cache.issue_verification("director@example.com", "127.0.0.1")
            outcomes = await asyncio.gather(*[
                cache.exchange("director@example.com", issued.verification_id, issued.code.get_secret_value(), "127.0.0.1")
                for _ in range(2)
            ], return_exceptions=True)
            assert sum(isinstance(item, AuthError) for item in outcomes) == 1
            assert len(await redis.keys("auth:registration:*")) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("dimension", ["ip", "identity"])
def test_rate_limiter_bounds_both_dimensions(dimension):
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            for i in range(2):
                await cache.limit("unit", "fixed-ip" if dimension == "ip" else str(i),
                                  "fixed-email" if dimension == "identity" else str(i), ip_limit=2, identity_limit=2)
            with pytest.raises(AuthError) as exc:
                await cache.limit("unit", "fixed-ip" if dimension == "ip" else "new-ip",
                                  "fixed-email" if dimension == "identity" else "new-email", ip_limit=2, identity_limit=2)
            assert exc.value.status == 429 and 1 <= exc.value.retry_after <= 60
    asyncio.run(scenario())


def test_session_rotation_binds_new_csrf_and_revokes_old_cookie():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            old = await cache.prelogin(None)
            raw = old.token.get_secret_value()
            assert raw not in " ".join(k.decode() for k in await redis.keys("*"))
            assert raw not in repr(old)
            await cache.validate_csrf(raw, cache.csrf(raw))
            new = await cache.rotate(old, uuid4())
            assert await cache.read_session(raw) is None
            assert new.token != old.token
            assert cache.csrf(new.token.get_secret_value()) != cache.csrf(raw)
            with pytest.raises(AuthError):
                await cache.validate_csrf(new.token.get_secret_value(), cache.csrf(raw))
            await cache.logout(new)
            assert await cache.read_session(new.token.get_secret_value()) is None
    asyncio.run(scenario())


@pytest.mark.parametrize("csrf", [None, "wrong", "中" * 64, "0" * 64])
def test_invalid_csrf_is_a_controlled_denial(csrf):
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            browser = await cache.prelogin(None)
            with pytest.raises(AuthError) as exc:
                await cache.validate_csrf(browser.token.get_secret_value(), csrf)
            assert exc.value.status == 403
    asyncio.run(scenario())


def test_redis_failures_never_fall_back_to_a_memory_session():
    class BrokenRedis:
        async def get(self, key):
            raise ConnectionError("sensitive redis URL")

    async def scenario():
        cache = AuthCache(BrokenRedis(), auth_settings())
        with pytest.raises(AuthError) as exc:
            await cache.read_session("a" * 43)
        assert exc.value.status == 503
        assert "sensitive" not in str(exc.value)
    asyncio.run(scenario())


def test_receipt_digest_is_not_the_raw_token():
    assert len(token_digest("a" * 43)) == 64
    assert token_digest("a" * 43) != "a" * 43


def test_expired_session_is_not_recovered_from_the_cookie_alone():
    async def scenario():
        async with FakeRedis() as redis:
            cache = AuthCache(redis, auth_settings())
            old = await cache.prelogin(None)
            token = old.token.get_secret_value()
            await redis.pexpire(cache.session_key(token), -1)
            assert await cache.read_session(token) is None
            with pytest.raises(AuthError) as exc:
                await cache.validate_csrf(token, cache.csrf(token))
            assert exc.value.status == 401
    asyncio.run(scenario())


def test_rotating_auth_secret_invalidates_old_sessions():
    async def scenario():
        async with FakeRedis() as redis:
            original = AuthCache(redis, auth_settings())
            browser = await original.prelogin(None)
            changed = Settings(_env_file=None, app_env="test", auth_secret="a-different-test-only-secret-" * 2)
            rotated = AuthCache(redis, changed)
            assert await rotated.read_session(browser.token.get_secret_value()) is None
    asyncio.run(scenario())
