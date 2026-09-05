"""Thin wrapper over the Viber REST API.

Deliberately not using the official `viberbot` SDK: it hasn't been updated in
years and its keyboard handling gets in the way. The API surface we need is
three endpoints and one HMAC check, so a small client is easier to reason about.

Docs: https://developers.viber.com/docs/api/rest-bot-api/
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Iterable, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

log = logging.getLogger(__name__)

API_ROOT = "https://chatapi.viber.com/pa"
DEFAULT_TIMEOUT = 8  # seconds; Vercel functions cap out well before this stacks up

# Viber gates newer message features behind API levels and silently ignores any
# parameter above the level you declare. We use Silent (level 6) and
# ActionType "none" (level 3) on buttons, so 6 is the floor. Clients below it
# would post the encoded board into the chat as visible text instead of hiding
# it — which is the whole point of the design. Every actively supported Viber
# client is far past level 6.
MIN_API_VERSION = 6

# Events we ask Viber to deliver. "message" is always delivered and must not be
# listed here. We skip delivered/seen/failed because they are noisy and we do
# not act on them.
DEFAULT_EVENT_TYPES = ("subscribed", "unsubscribed", "conversation_started")


class ViberAPIError(RuntimeError):
    """Viber replied with a non-zero status code in the JSON body."""

    def __init__(self, endpoint: str, payload: dict[str, Any]):
        self.endpoint = endpoint
        self.status = payload.get("status")
        self.status_message = payload.get("status_message", "unknown error")
        super().__init__(f"{endpoint} failed: status={self.status} {self.status_message}")


def verify_signature(raw_body: bytes, signature: str | None, token: str | None = None) -> bool:
    """Check the X-Viber-Content-Signature header.

    Viber signs the raw request body with HMAC-SHA256 keyed by the auth token.
    This is the only authentication on the webhook, so it must not be skipped
    in production — without it anyone who learns the URL can puppet the bot.
    """
    token = config.AUTH_TOKEN if token is None else token
    if not token or not signature:
        return False
    # surrogateescape recovers the original bytes when the token came from an
    # environment variable that is not valid UTF-8. os.environ hands those back
    # as lone surrogates, and a strict .encode() would raise on every request.
    expected = hmac.new(token.encode("utf-8", "surrogateescape"), raw_body, hashlib.sha256).hexdigest()
    # Compare as bytes: WSGI decodes headers as latin-1, and compare_digest
    # raises TypeError on str containing non-ASCII rather than returning False.
    return hmac.compare_digest(
        expected.encode("ascii"), signature.strip().lower().encode("utf-8", "replace")
    )


def build_session() -> requests.Session:
    """A session that retries idempotent failures.

    Serverless containers freeze between invocations, so a pooled keep-alive
    socket is often dead by the next request. requests does not retry by
    default, which turns that into a lost reply.
    """
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.2,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"POST"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session



class ViberClient:
    def __init__(
        self,
        token: str | None = None,
        name: str | None = None,
        avatar: str | None = None,
        session: requests.Session | None = None,
    ):
        self.token = config.AUTH_TOKEN if token is None else token
        self.name = config.BOT_NAME if name is None else name
        self.avatar = config.BOT_AVATAR if avatar is None else avatar
        self.session = session or build_session()

    @property
    def sender(self) -> dict[str, str]:
        sender = {"name": self.name}
        if self.avatar:
            sender["avatar"] = self.avatar
        return sender

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.token:
            raise ViberAPIError(endpoint, {"status": -1, "status_message": "VIBER_AUTH_TOKEN is not set"})
        response = self.session.post(
            f"{API_ROOT}/{endpoint}",
            json=payload,
            headers={"X-Viber-Auth-Token": self.token},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        if body.get("status") != 0:
            raise ViberAPIError(endpoint, body)
        return body

    # --- messaging ----------------------------------------------------------

    def send_text(
        self,
        receiver: str,
        text: str,
        keyboard: dict[str, Any] | None = None,
        tracking_data: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "receiver": receiver,
            "type": "text",
            "sender": self.sender,
            "text": text,
            "min_api_version": MIN_API_VERSION,
        }
        if keyboard:
            payload["keyboard"] = keyboard
        if tracking_data:
            payload["tracking_data"] = tracking_data
        log.debug("send_message -> %s (%d chars, keyboard=%s)", receiver, len(text), bool(keyboard))
        return self._post("send_message", payload)

    def text_payload(
        self, text: str, keyboard: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Message shaped for an inline webhook response.

        `conversation_started` must be answered in the HTTP response body — the
        user hasn't subscribed yet, so send_message would be rejected.
        """
        payload: dict[str, Any] = {
            "type": "text",
            "sender": self.sender,
            "text": text,
            "min_api_version": MIN_API_VERSION,
        }
        if keyboard:
            payload["keyboard"] = keyboard
        return payload

    # --- account / webhook management --------------------------------------

    def set_webhook(self, url: str, event_types: Iterable[str] | None = None) -> dict[str, Any]:
        types = list(DEFAULT_EVENT_TYPES if event_types is None else event_types)
        return self._post("set_webhook", {"url": url, "event_types": types, "send_name": True})

    def remove_webhook(self) -> dict[str, Any]:
        return self._post("set_webhook", {"url": ""})

    def account_info(self) -> dict[str, Any]:
        return self._post("get_account_info", {})


# --- keyboard helpers ------------------------------------------------------
# Viber lays buttons out on a 6-column grid, filling left to right. Three
# buttons of Columns=2 make one full row, which is exactly a tic-tac-toe row.

def keyboard(buttons: Sequence[dict[str, Any]], *, bg_color: str = "#FFFFFF", default_height: bool = False) -> dict[str, Any]:
    return {
        "Type": "keyboard",
        "DefaultHeight": default_height,
        "BgColor": bg_color,
        "min_api_version": MIN_API_VERSION,
        "Buttons": list(buttons),
    }


def button(
    text: str,
    action_body: str = "",
    *,
    columns: int = 6,
    rows: int = 1,
    bg_color: str = "#F4F4F7",
    text_color: str | None = None,
    text_size: str = "regular",
    action_type: str = "reply",
    silent: bool = True,
) -> dict[str, Any]:
    """Build one keyboard button.

    `silent=True` sends the ActionBody back to the bot without printing it in
    the conversation, which is what lets us hide encoded game state in there.
    `action_type="none"` makes a button inert — used for already-filled cells.
    """
    label = text if text_color is None else f'<font color="{text_color}">{text}</font>'
    return {
        "Columns": columns,
        "Rows": rows,
        "ActionType": action_type,
        "ActionBody": action_body or " ",
        "Text": label,
        "TextSize": text_size,
        "TextHAlign": "center",
        "TextVAlign": "middle",
        "BgColor": bg_color,
        "Silent": silent,
    }
