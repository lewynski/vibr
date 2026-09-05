"""Config parsing, which is one env-var typo away from disabling authentication."""

from __future__ import annotations

import importlib
import os

import pytest

import bot.config


def flag(raw: str | None, default: bool = True) -> bool:
    """Re-read VIBER_VERIFY_SIGNATURE with `raw` in the environment."""
    key = "VIBER_VERIFY_SIGNATURE"
    previous = os.environ.get(key)
    if raw is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = raw
    try:
        return bot.config._flag(key, default)
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " True ", "yes", "y", "on", "enabled"])
def test_truthy_values(raw):
    assert flag(raw, default=False) is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", " no ", "n", "off", "disabled", '"false"'])
def test_falsy_values(raw):
    assert flag(raw, default=True) is False


@pytest.mark.parametrize("raw", [None, "", "   ", "maybe", "sure", "2", "null", "None", "-1"])
def test_unrecognised_values_keep_the_default(raw):
    """Regression: this used to be `value in _TRUTHY`, so anything unexpected
    read as False. On VIBER_VERIFY_SIGNATURE that silently turns off the only
    authentication the webhook has — it must fail closed, not open."""
    assert flag(raw, default=True) is True
    assert flag(raw, default=False) is False


def test_quotes_pasted_around_the_value_are_tolerated():
    # Vercel's env editor makes it easy to include the quotes.
    assert flag('"true"', default=False) is True
    assert flag("'false'", default=True) is False


def test_verify_signature_defaults_to_on_when_unset():
    key = "VIBER_VERIFY_SIGNATURE"
    previous = os.environ.get(key)
    os.environ.pop(key, None)
    try:
        importlib.reload(bot.config)
        assert bot.config.VERIFY_SIGNATURE is True
    finally:
        if previous is not None:
            os.environ[key] = previous
        importlib.reload(bot.config)


def test_missing_reports_only_the_token():
    key = "VIBER_AUTH_TOKEN"
    previous = os.environ.get(key)
    try:
        os.environ[key] = "something"
        importlib.reload(bot.config)
        assert bot.config.missing() == []

        os.environ[key] = "   "
        importlib.reload(bot.config)
        assert bot.config.missing() == ["VIBER_AUTH_TOKEN"]
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
        importlib.reload(bot.config)
