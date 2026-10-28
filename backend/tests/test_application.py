import re

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from silencio_set.api.health import dependency_report
from silencio_set.main import create_app


def test_live_is_independent_of_dependencies(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "alive"}
    request_id = response.json()["request_id"]
    assert re.fullmatch(r"[a-f0-9]{32}", request_id)
    assert response.headers["x-request-id"] == request_id
    assert response.headers["cache-control"] == "no-store"


def test_readiness_missing_configuration_is_not_success(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCIES_NOT_READY"
    assert set(response.json()["error"]["details"]["dependencies"].values()) == {"not_configured"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("failed", [None, "database", "redis", "rabbitmq"])
def test_readiness_aggregates_each_dependency(settings, failed):
    app = create_app(settings)
    report = {name: "unavailable" if name == failed else "ok" for name in ("database", "redis", "rabbitmq")}
    app.dependency_overrides[dependency_report] = lambda: report
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == (503 if failed else 200)
    body = response.json()
    actual = body["error"]["details"]["dependencies"] if failed else body["data"]["dependencies"]
    assert actual == report


@pytest.mark.parametrize("path,method,status,code", [
    ("/does-not-exist", "get", 404, "NOT_FOUND"),
    ("/api/v1/health/live", "post", 405, "METHOD_NOT_ALLOWED"),
])
def test_standard_http_errors(settings, path, method, status, code):
    with TestClient(create_app(settings)) as client:
        response = getattr(client, method)(path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_unexpected_errors_do_not_leak_secrets(settings, caplog):
    app = create_app(settings)

    @app.get("/test-failure", include_in_schema=False)
    async def failure():
        raise RuntimeError("password=private-example-secret SQL SELECT hidden")

    with TestClient(app) as client:
        response = client.get("/test-failure")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert "private-example-secret" not in response.text + caplog.text


def test_validation_does_not_echo_submitted_data(settings):
    class Input(BaseModel):
        count: int

    app = create_app(settings)

    @app.post("/test-validation", include_in_schema=False)
    async def validate(body: Input):
        return body

    with TestClient(app) as client:
        response = client.post("/test-validation", json={"count": "private-example-secret"})
    assert response.status_code == 422
    assert "private-example-secret" not in response.text
    assert response.json()["error"]["details"]["issues"][0]["location"] == ["body", "count"]


@pytest.mark.parametrize("supplied,kept", [("a" * 32, True), ("not-a-request-id", False), ("x" * 500, False)])
def test_request_id_accepts_only_bounded_hex(settings, supplied, kept):
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": supplied})
    actual = response.headers["x-request-id"]
    assert (actual == supplied) is kept
    assert re.fullmatch(r"[a-f0-9]{32}", actual)


def test_openapi_contains_health_contracts(settings):
    schema = create_app(settings).openapi()
    assert "/api/v1/health/live" in schema["paths"]
    assert "503" in schema["paths"]["/api/v1/health/ready"]["get"]["responses"]
