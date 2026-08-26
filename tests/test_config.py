"""Unit tests for the pure kill-switch parser in ``app.config`` (LOCKED L1).

No fastapi, no databricks.sdk, no network — stdlib-only.
"""

import pytest

from app.config import (
    DEFAULT_BANNER_TEXT,
    DEFAULT_FOOTER_TEXT,
    downloads_enabled,
    resolve_chrome_text,
)


@pytest.mark.parametrize(
    "value",
    [None, "true", "TRUE", "True", "1", "yes", "YES", "on", "enabled", "anything"],
)
def test_downloads_enabled_true(value):
    """Unset and any non-falsey value leave downloads ENABLED (default true)."""
    assert downloads_enabled(value) is True


@pytest.mark.parametrize(
    "value",
    ["false", "FALSE", "False", "0", "no", "NO", "off", "OFF", "", " ", "  false  "],
)
def test_downloads_enabled_false(value):
    """Explicit falsey values (case-insensitive, stripped) DISABLE downloads."""
    assert downloads_enabled(value) is False


def test_resolve_chrome_text_admin_value_wins_verbatim():
    """A set admin value (cfg_value not None) wins verbatim — including blank."""
    # Non-empty admin value wins over env + default.
    assert resolve_chrome_text("Custom banner", "env banner", DEFAULT_BANNER_TEXT) == "Custom banner"
    # Blank admin value stays blank ("if blank, leave them blank") — NOT the default.
    assert resolve_chrome_text("", "env banner", DEFAULT_BANNER_TEXT) == ""
    # Whitespace the admin typed is honored verbatim (not stripped away).
    assert resolve_chrome_text("  ", None, DEFAULT_FOOTER_TEXT) == "  "


def test_resolve_chrome_text_unset_falls_back_to_env_then_default():
    """When unset (cfg_value is None): env if non-empty, else the built-in default."""
    # Unset + env override -> env (stripped).
    assert resolve_chrome_text(None, "  From env  ", DEFAULT_BANNER_TEXT) == "From env"
    # Unset + no/blank env -> default.
    assert resolve_chrome_text(None, None, DEFAULT_BANNER_TEXT) == DEFAULT_BANNER_TEXT
    assert resolve_chrome_text(None, "", DEFAULT_FOOTER_TEXT) == DEFAULT_FOOTER_TEXT
    assert resolve_chrome_text(None, "   ", DEFAULT_FOOTER_TEXT) == DEFAULT_FOOTER_TEXT
