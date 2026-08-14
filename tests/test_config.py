"""Unit tests for the pure kill-switch parser in ``app.config`` (LOCKED L1).

No fastapi, no databricks.sdk, no network — stdlib-only.
"""

import pytest

from app.config import downloads_enabled


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
