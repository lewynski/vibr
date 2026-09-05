"""Environment-backed configuration.

Everything is read at import time. On Vercel that means once per cold start,
which is what we want — no config lookups on the hot path.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "t", "yes", "y", "on", "enable", "enabled"}
_FALSY = {"0", "false", "f", "no", "n", "off", "disable", "disabled"}


def _flag(name: str, default: bool = False) -> bool:
    """Parse a boolean env var, falling back to `default` on anything unclear.

    Deliberately does NOT treat "unrecognised" as false. VIBER_VERIFY_SIGNATURE
    defaults to True and is the bot's only authentication, so a typo, a stray
    quote pasted into Vercel's env editor, or a value like "maybe" must leave
    verification ON rather than silently switching it off.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().strip("'\"").lower()
    if value == "":
        return default
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    log.warning("%s=%r is not a boolean, using the default (%s)", name, raw, default)
    return default


AUTH_TOKEN: str = os.environ.get("VIBER_AUTH_TOKEN", "").strip()
BOT_NAME: str = os.environ.get("VIBER_BOT_NAME", "TicTacToe Bot").strip() or "Bot"
BOT_AVATAR: str | None = os.environ.get("VIBER_BOT_AVATAR", "").strip() or None

# Discord-style prefix. Empty string means "no prefix required".
COMMAND_PREFIX: str = os.environ.get("COMMAND_PREFIX", "").strip()

VERIFY_SIGNATURE: bool = _flag("VIBER_VERIFY_SIGNATURE", True)
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").strip().upper()


def missing() -> list[str]:
    """Names of required settings that are not configured."""
    return [] if AUTH_TOKEN else ["VIBER_AUTH_TOKEN"]
