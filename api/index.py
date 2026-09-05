"""Vercel entrypoint. Everything routes here via the rewrite in vercel.json.

Vercel's Python runtime looks for a module-level WSGI app named `app`.
Cogs load once per cold start, not once per request.
"""

from __future__ import annotations

import json
import logging
import os
import sys

# Vercel copies the repo into the function, but the root is not reliably on
# sys.path. Two lines here beats debugging import errors in a deploy log.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flask import Flask, jsonify, request  # noqa: E402

from bot import config  # noqa: E402
from bot.core import Bot  # noqa: E402
from bot.viber import ViberClient, verify_signature  # noqa: E402

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(levelname)s %(name)s: %(message)s",
    # Vercel's runtime installs a root handler before importing this module,
    # which makes basicConfig a silent no-op. force=True replaces it so the
    # level we actually configured is the one that applies.
    force=True,
)
log = logging.getLogger("viber-bot")

if config.missing():
    log.warning("missing configuration: %s", ", ".join(config.missing()))

app = Flask(__name__)
client = ViberClient()
bot = Bot(client, prefix=config.COMMAND_PREFIX)
bot.load_cogs()


@app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@app.route("/<path:path>", methods=["GET", "POST"])
def entry(path: str):
    if request.method == "GET":
        return health()
    return webhook()


def health():
    """Browser-visitable status page. Exposes no secrets."""
    return jsonify(
        {
            "ok": not config.missing() and not bot.failed_cogs,
            "bot": config.BOT_NAME,
            "prefix": config.COMMAND_PREFIX or None,
            "signature_verification": config.VERIFY_SIGNATURE,
            "cogs": sorted(bot.cogs),
            "commands": sorted(bot.commands),
            "failed_cogs": bot.failed_cogs,
            "missing_config": config.missing(),
        }
    )


def webhook():
    # The whole body of this view is inside one try. Viber unregisters a webhook
    # that keeps failing, so no exception may reach the WSGI layer and become a
    # 500 — not even from reading the request or checking the signature.
    try:
        raw_body = request.get_data()
        signature = request.headers.get("X-Viber-Content-Signature")

        # The signature is the ONLY thing authenticating this endpoint. Viber
        # requires the URL to be public, so without this check anyone who learns
        # the URL could forge events. Never disable it in production.
        if config.VERIFY_SIGNATURE and not verify_signature(raw_body, signature):
            log.warning("rejected request with a bad or missing signature")
            return jsonify({"error": "invalid signature"}), 403

        # Parsed from the raw bytes rather than request.get_json(), which
        # returns None unless the Content-Type is application/json. Viber does
        # send that header, but the body is already in hand for the HMAC, and a
        # bot that goes silently deaf over a header is a bad failure mode.
        event = json.loads(raw_body)
        # A body of `5`, `true`, `"hello"` or `[...]` is valid JSON and would
        # blow up on event.get(). Signed-body fuzzing found this.
        if not isinstance(event, dict):
            log.warning("ignoring payload that is not a JSON object")
            return "", 200

        log.info("event=%s", event.get("event"))
        response = bot.handle(event)
        return (jsonify(response), 200) if response else ("", 200)
    except Exception:
        log.exception("unhandled error while processing event")
        return "", 200
