"""HTTP-level tests for the Vercel entrypoint.

Only exercises paths that make no outbound calls to Viber: the health page, the
signature gate, the registration probe, and conversation_started (which is
answered in the response body rather than via send_message).
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import json
import os
import sys

import pytest

TOKEN = "unit-test-token"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_app():
    os.environ["VIBER_AUTH_TOKEN"] = TOKEN
    os.environ["VIBER_BOT_NAME"] = "Test Bot"
    os.environ["VIBER_VERIFY_SIGNATURE"] = "true"
    os.environ["COMMAND_PREFIX"] = ""

    # config caches env at import time, so reload before building the app.
    import bot.config

    importlib.reload(bot.config)

    spec = importlib.util.spec_from_file_location("api_index", os.path.join(ROOT, "api", "index.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["api_index"] = module
    spec.loader.exec_module(module)

    module.app.config.update(TESTING=True)
    return module


@pytest.fixture(scope="module")
def client():
    return build_app().app.test_client()


def signed(client, payload: dict, token: str = TOKEN):
    return signed_raw(client, json.dumps(payload).encode(), token=token)


def signed_raw(client, body: bytes, token: str = TOKEN):
    signature = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/",
        data=body,
        headers={"Content-Type": "application/json", "X-Viber-Content-Signature": signature},
    )


def test_health_page(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["missing_config"] == []
    assert "ttt" in body["commands"]
    assert "Tic-Tac-Toe" in body["cogs"]
    assert TOKEN not in response.get_data(as_text=True), "the token must never be exposed"


def test_health_page_works_on_any_path(client):
    # Vercel rewrites every path to this function, so /api/webhook lands here too.
    assert client.get("/api/webhook").status_code == 200


def test_unsigned_post_is_rejected(client):
    response = client.post("/", json={"event": "webhook"})
    assert response.status_code == 403


def test_wrongly_signed_post_is_rejected(client):
    response = signed(client, {"event": "webhook"}, token="not-the-token")
    assert response.status_code == 403


def test_registration_probe_returns_200(client):
    response = signed(client, {"event": "webhook", "timestamp": 1})
    assert response.status_code == 200
    assert response.get_data() == b""


def test_conversation_started_returns_a_message_body(client):
    response = signed(
        client, {"event": "conversation_started", "user": {"id": "abc=", "name": "Lewyn"}}
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["type"] == "text"
    assert "Lewyn" in body["text"]
    assert body["keyboard"]["Buttons"]


def test_malformed_body_still_returns_200(client):
    body = b"not json at all"
    signature = hmac.new(TOKEN.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/",
        data=body,
        headers={"Content-Type": "application/json", "X-Viber-Content-Signature": signature},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [b"5", b"true", b"null", b'"hello"', b"[]", b'[{"event":"message"}]', b""],
)
def test_valid_json_that_is_not_an_object_returns_200(client, body):
    """Regression: get_json(silent=True) only turns *malformed* JSON into None.
    These bodies all parse, and each one used to reach .get() on a non-dict and
    escape as a 500 — which is how Viber decides to unregister a webhook."""
    response = signed_raw(client, body)
    assert response.status_code == 200
    assert response.get_data() == b""


def test_health_page_reports_no_failed_cogs_normally(client):
    assert client.get("/").get_json()["failed_cogs"] == {}


def test_a_failed_cog_shows_up_on_the_health_page_and_clears_ok():
    """A cog that fails to import is skipped rather than fatal, which means the
    only way anyone finds out is this page. If `ok` stayed true the failure
    would be invisible."""
    module = build_app()
    module.bot.failed_cogs["tictactoe"] = "RuntimeError: boom"
    try:
        body = module.app.test_client().get("/").get_json()
        assert body["ok"] is False
        assert body["failed_cogs"] == {"tictactoe": "RuntimeError: boom"}
    finally:
        module.bot.failed_cogs.clear()


def test_signature_gate_runs_before_the_body_is_parsed(client):
    # An unsigned non-object body must still 403, not 200.
    response = client.post(
        "/",
        data=b"5",
        headers={"Content-Type": "application/json", "X-Viber-Content-Signature": "deadbeef"},
    )
    assert response.status_code == 403
