from io import StringIO

from alembic import command
from alembic.config import Config

from silencio_set.core import config as config_module
from silencio_set.core.config import Settings


def test_baseline_migration_generates_offline_sql_without_a_database(monkeypatch):
    # Supply a synthetic URL; offline generation never opens a connection.
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "0001_foundation", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE alembic_version" in sql
    assert "0001_foundation" in sql
    assert "CREATE TABLE users" not in sql


def test_account_migration_offline_sql_has_keys_constraints_and_no_seed_users(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE registration_receipts" in sql
    assert "uq_users_email_normalized UNIQUE" in sql
    assert "uq_registration_receipts_token_digest UNIQUE" in sql
    assert "ck_users_profile_completion_pair CHECK" in sql
    assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT" in sql
    assert "0002_accounts" in sql
    assert "INSERT INTO users" not in sql


def test_mail_migration_has_durable_keys_and_no_plaintext_payload_fields(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "0003_mail_jobs" in sql
    assert "CREATE TABLE background_jobs" in sql and "CREATE TABLE outbox_events" in sql
    assert "uq_background_jobs_type_key UNIQUE (job_type, idempotency_key)" in sql
    assert "uq_outbox_events_event_key UNIQUE" in sql
    assert "ck_outbox_events_lease_pair CHECK" in sql
    assert "plaintext_code" not in sql and "encryption_key" not in sql


def test_crew_migration_has_same_crew_keys_catalog_and_separate_audit(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "0004_crews" in sql
    assert "UNIQUE (crew_id, user_id)" in sql and "UNIQUE (crew_id, id)" in sql
    assert "FOREIGN KEY(crew_id, member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT" in sql
    assert "CREATE TABLE audit_logs" in sql
    assert "organization_override_forbidden" in sql and "INSERT INTO roles" in sql
    assert "[[:cntrl:]]" in sql and "[[:space:]]" in sql
    assert "INSERT INTO crews" not in sql and "INSERT INTO crew_members" not in sql
