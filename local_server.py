"""Run the bot locally for development.

    pip install -r requirements-dev.txt
    cp .env.example .env      # fill in VIBER_AUTH_TOKEN
    python local_server.py

Then expose it and point Viber at the tunnel:

    ngrok http 8000
    python scripts/set_webhook.py https://<subdomain>.ngrok-free.app/

Binds to 127.0.0.1 only. The Viber signature check is the sole authentication
on the webhook, so do not bind this to 0.0.0.0 on a shared network.
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv(os.path.join(ROOT, ".env"))


def load_app():
    """Import api/index.py by path — `api` is not a package on purpose,
    because Vercel treats every module in api/ as its own function."""
    spec = importlib.util.spec_from_file_location("api_index", os.path.join(ROOT, "api", "index.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load api/index.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["api_index"] = module
    spec.loader.exec_module(module)
    return module.app


app = load_app()

if __name__ == "__main__":
    # debug is OFF by default on purpose. The Werkzeug debugger executes
    # arbitrary code from the browser, and the instructions above tell you to
    # put this behind an ngrok tunnel, which is public. Set FLASK_DEBUG=1 only
    # while you are not tunnelling.
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        debug=os.environ.get("FLASK_DEBUG", "").strip() in {"1", "true", "yes", "on"},
    )
