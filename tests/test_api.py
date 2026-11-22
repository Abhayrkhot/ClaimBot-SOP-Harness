from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from claimbot.api.app import create_app
from claimbot.config import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(admin_api_token="secret-token", rate_limit_per_minute=600, log_format="text")
    with TestClient(create_app(settings)) as c:
        yield c


def test_chat_flow_and_audit(client: TestClient) -> None:
    r = client.post("/v1/chat", json={"message": "My name is Margaret Chen"})
    assert r.status_code == 200
    body = r.json()
    sid = body["session_id"]
    assert body["phase"] == "VERIFY_ID" and body["captured_fields"] == ["name"]
    assert body["response_mode"] == "rules"
    r = client.post(
        "/v1/chat", json={"session_id": sid, "message": "DOB is 1985-03-15, SSN last four is 4472"}
    )
    body = r.json()
    assert body["verified"] and body["phase"] == "RESOLVE_INTENT"
    assert body["policy"]["transitions"] == ["VERIFY_ID->RESOLVE_INTENT"]
    audit = client.get(f"/v1/sessions/{sid}/audit", headers={"Authorization": "Bearer secret-token"}).json()
    kinds = [e["kind"] for e in audit["events"]]
    assert "identity_verified" in kinds and "phase_transition" in kinds
    assert "1985" not in str(audit)  # audit events never carry raw identity values


def test_v1_alias_and_end(client: TestClient) -> None:
    r = client.post("/api/chat", json={"text": "hello"})
    sid = r.json()["session_id"]
    assert client.post("/api/end", json={"session_id": sid}).json() == {"ended": True}
    assert client.post(f"/v1/sessions/{sid}/end").status_code == 200
    r = client.post("/v1/chat", json={"session_id": sid, "message": "hi"})
    assert r.json()["phase"] == "CLOSED"


def test_unknown_session_and_validation(client: TestClient) -> None:
    assert client.post("/v1/chat", json={"session_id": "nope", "message": "hi"}).status_code == 404
    assert client.post("/v1/chat", json={"message": "   "}).status_code == 422
    assert client.post("/v1/chat", json={"message": "x" * 4001}).status_code == 422
    assert client.post("/v1/chat", json={"session_id": "bad id!", "message": "hi"}).status_code == 422


def test_admin_endpoints_require_token(client: TestClient) -> None:
    assert client.get("/v1/outbox").status_code == 401
    assert client.get("/v1/outbox", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/outbox", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_admin_endpoints_hidden_without_token() -> None:
    with TestClient(create_app(Settings(log_format="text"))) as c:
        assert c.get("/v1/outbox").status_code == 404


def test_health_ready_metrics_headers(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True}
    ready = client.get("/readyz").json()
    assert ready["ok"] and ready["db"] and ready["mode"] == "rules"
    assert client.get("/api/health").json()["mode"] == "rules"
    client.post("/v1/chat", json={"message": "hello"})
    metrics = client.get("/metrics").text
    assert "claimbot_turns_total" in metrics
    r = client.get("/")
    assert r.status_code == 200 and r.headers["X-Frame-Options"] == "DENY"
    assert "X-Request-ID" in r.headers


def test_rate_limit() -> None:
    with TestClient(create_app(Settings(rate_limit_per_minute=8, log_format="text"))) as c:
        codes = [c.post("/v1/chat", json={"message": "hello"}).status_code for _ in range(5)]
    assert codes[:2] == [200, 200] and 429 in codes
