"""Unregister the webhook, which takes the bot offline.

    python scripts/remove_webhook.py
"""

from __future__ import annotations

import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv(os.path.join(ROOT, ".env"))

from bot.viber import ViberAPIError, ViberClient  # noqa: E402


def main() -> int:
    client = ViberClient()
    if not client.token:
        print("error: VIBER_AUTH_TOKEN is not set (check your .env)")
        return 1
    try:
        client.remove_webhook()
    except ViberAPIError as exc:
        print(f"failed: {exc}")
        return 1
    except requests.RequestException as exc:
        print(f"could not reach the Viber API: {exc}")
        return 1
    print("webhook removed — the bot will stop receiving events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
