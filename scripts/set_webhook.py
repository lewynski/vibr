"""Point Viber at your webhook URL.

    python scripts/set_webhook.py https://your-app.vercel.app/

Viber immediately POSTs a probe event to the URL and only saves it if that
returns HTTP 200, so deploy before running this.
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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    url = argv[1]
    if not url.startswith("https://"):
        print("error: Viber requires an https URL with a valid certificate")
        return 1

    client = ViberClient()
    if not client.token:
        print("error: VIBER_AUTH_TOKEN is not set (check your .env)")
        return 1

    try:
        result = client.set_webhook(url)
    except ViberAPIError as exc:
        print(f"failed: {exc}")
        print("\nCommon causes: the URL is not deployed yet, it returned a non-200,")
        print("or VIBER_AUTH_TOKEN does not match the deployed environment variable.")
        return 1
    except requests.RequestException as exc:
        # DNS failure, TLS error, timeout, proxy in the way — never reached Viber.
        print(f"could not reach the Viber API: {exc}")
        return 1

    print(f"webhook set to {url}")
    print("subscribed events:", ", ".join(result.get("event_types", [])) or "(default)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
