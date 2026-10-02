# PB — Entry-point auth boundary (src/api/server.py) — FIN-C2-097
#
# PreProcessNode declares required_trust_level = VERIFIED_EXTERNAL, so a
# standalone caller that reaches the graph as ANONYMOUS is denied at the trust
# gate and the agent returns status=error. Nothing in src/api/ sets
# request.state.trust_level (no upstream middleware in the standalone runtime),
# so the server itself is the auth boundary: it must authenticate the caller
# and elevate the trust level.
#
# Contract proven here:
#   1. The module boots with no INVOKE_AUTH_TOKEN configured (no raise).
#   2. Token configured + no / wrong / malformed Bearer  -> 401, generic body.
#   3. Token configured + correct Bearer                 -> not 401 (boundary
#      crossed) and the agent runs at VERIFIED_EXTERNAL (status=success) — the
#      same health assertion a deployment smoke test makes.
#   4. Trust already established upstream is never demoted or re-challenged.
#   5. No token configured -> callers stay ANONYMOUS, never 401 (auth is an
#      opt-in deployment setting).
#
# Non-ASCII Authorization values are covered too: headers decode as latin-1, so
# comparing str with secrets.compare_digest would raise TypeError and surface a
# 500 instead of the generic 401 — the comparison must be on encoded bytes.

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framework.schemas.trust_level import TrustLevel
from src.api.server import app

_TOKEN = "boundary-token-abc123"

# The canonical smoke payload (byte-identical to deploy/invoke_payload.json),
# so a green test here matches the deployment health check on the same input.
_PAYLOAD = json.loads(Path("deploy/invoke_payload.json").read_text())


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _vouched_client(trust: TrustLevel) -> TestClient:
    """A client whose requests arrive with upstream-established trust.

    Starlette exposes ``request.state`` over ``scope["state"]``, so seeding that
    dict is the transport-level equivalent of a platform AuthMiddleware having
    already vouched for the caller.
    """

    async def vouched_app(scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["state"] = {"trust_level": trust, "caller_id": "upstream-middleware"}
        await app(scope, receive, send)

    return TestClient(vouched_app)


class TestServerBoots:
    def test_health_ok_without_token_configured(self, client, monkeypatch):
        """No INVOKE_AUTH_TOKEN configured must never break boot or /health."""
        monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
        resp = client.get("/health")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ok"


class TestInvokeAuthBoundary:
    """INVOKE_AUTH_TOKEN configured — the Bearer boundary is enforced."""

    @pytest.fixture(autouse=True)
    def _token_configured(self, monkeypatch):
        monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)

    def test_missing_bearer_is_401(self, client):
        resp = client.post("/invoke", json=_PAYLOAD)
        assert resp.status_code == 401, (
            f"Unauthenticated ANONYMOUS caller must be rejected at the entry point, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )

    def test_wrong_bearer_is_401(self, client):
        resp = client.post("/invoke", json=_PAYLOAD, headers={"Authorization": "Bearer not-the-token"})
        assert resp.status_code == 401, resp.text[:200]

    def test_malformed_authorization_is_401(self, client):
        for value in (_TOKEN, f"Basic {_TOKEN}", "Bearer", f"bearer {_TOKEN}", ""):
            resp = client.post("/invoke", json=_PAYLOAD, headers={"Authorization": value})
            assert resp.status_code == 401, f"Malformed Authorization {value!r} must 401, got {resp.status_code}"

    def test_non_ascii_bearer_is_401_not_500(self, client):
        """compare_digest must run on encoded bytes — a str compare raises TypeError.

        Headers decode as latin-1, so a non-ASCII credential would 500 the
        endpoint (and leak a traceback) instead of returning the generic 401.
        Sent as raw bytes — an HTTP client cannot ASCII-encode this value, but a
        hostile caller writing the wire format directly can.
        """
        resp = client.post(
            "/invoke",
            json=_PAYLOAD,
            headers={"Authorization": "Bearer töken-é".encode("latin-1")},
        )
        assert resp.status_code == 401, (
            f"Non-ASCII credential must return the generic 401, got {resp.status_code}: " f"{resp.text[:200]}"
        )

    def test_401_body_is_generic_and_leaks_nothing(self, client):
        """The rejection body must not reveal which failure mode occurred."""
        bodies = {
            client.post("/invoke", json=_PAYLOAD).text,
            client.post("/invoke", json=_PAYLOAD, headers={"Authorization": "Bearer wrong"}).text,
            client.post("/invoke", json=_PAYLOAD, headers={"Authorization": "Basic x"}).text,
        }
        assert len(bodies) == 1, f"401 bodies differ per failure mode (leak): {bodies}"
        body = bodies.pop()
        assert _TOKEN not in body, "the configured token must never appear in a response"
        for leak in ("missing", "absent", "malformed", "expected", "header"):
            assert leak not in body.lower(), f"401 body leaks the failure mode: {body}"

    def test_correct_bearer_crosses_the_boundary(self, client):
        """Correct Bearer -> not 401: the auth boundary has been crossed."""
        resp = client.post("/invoke", json=_PAYLOAD, headers={"Authorization": f"Bearer {_TOKEN}"})
        assert (
            resp.status_code != 401
        ), f"A caller presenting the configured token must not be rejected: {resp.text[:200]}"

    def test_correct_bearer_elevates_to_verified_external(self, client):
        """The elevated trust must actually reach the graph.

        PreProcessNode's trust gate (VERIFIED_EXTERNAL) passes only when the
        entry point elevated the caller — the exact regression this boundary
        fixes, and the same assertion a deployment smoke test makes.
        """
        resp = client.post("/invoke", json=_PAYLOAD, headers={"Authorization": f"Bearer {_TOKEN}"})
        assert resp.status_code == 200, resp.text[:300]
        body = resp.json()
        assert body.get("status") == "success", (
            f"Trust gate denied an authenticated caller — trust was not elevated. "
            f"status={body.get('status')} error_log={body.get('error_log')}"
        )

    def test_upstream_trust_is_never_demoted(self):
        """Trust established upstream is honoured — no re-challenge, no demotion."""
        with _vouched_client(TrustLevel.INTERNAL) as vouched:
            resp = vouched.post("/invoke", json=_PAYLOAD)
        assert resp.status_code != 401, (
            "A caller already vouched for by upstream middleware must not be "
            f"re-challenged by the standalone boundary: {resp.text[:200]}"
        )


class TestNoTokenConfigured:
    """No INVOKE_AUTH_TOKEN -> auth is opt-out; callers stay ANONYMOUS (never 401)."""

    def test_invoke_is_not_401_without_token_configured(self, client, monkeypatch):
        monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
        resp = client.post("/invoke", json=_PAYLOAD)
        assert resp.status_code != 401, (
            "With no token configured the server must not reject callers " f"(auth is opt-in): {resp.text[:200]}"
        )

    def test_empty_token_is_treated_as_unconfigured(self, client, monkeypatch):
        monkeypatch.setenv("INVOKE_AUTH_TOKEN", "")
        resp = client.post("/invoke", json=_PAYLOAD)
        assert resp.status_code != 401, (
            "An empty INVOKE_AUTH_TOKEN means 'not configured' — it must never "
            f"become a credential every caller can guess: {resp.text[:200]}"
        )
