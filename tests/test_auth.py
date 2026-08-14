"""Unit tests for the pure OBO token extraction in ``app.auth``.

No fastapi, no databricks.sdk, no network (LOCKED L3).
"""

from types import SimpleNamespace

import pytest

from app.auth import (
    DOWNLOAD_GROUP,
    USER_TOKEN_HEADER,
    effective_download_group,
    extract_user_email,
    extract_user_token,
    group_display_names,
    is_member,
)


def test_extract_user_token_present_lowercase():
    """Lowercase header (as Starlette normalizes) yields the token."""
    headers = {USER_TOKEN_HEADER: "tok-abc123"}
    assert extract_user_token(headers) == "tok-abc123"


def test_extract_user_token_present_any_case():
    """A differently-cased header key is matched case-insensitively."""
    headers = {"X-Forwarded-Access-Token": "tok-XYZ"}
    assert extract_user_token(headers) == "tok-XYZ"


def test_extract_user_token_absent_raises():
    """Absent header raises PermissionError (no fallback, LOCKED L3)."""
    with pytest.raises(PermissionError):
        extract_user_token({})


def test_extract_user_token_empty_raises():
    """Empty header value is treated as absent and raises PermissionError."""
    with pytest.raises(PermissionError):
        extract_user_token({USER_TOKEN_HEADER: ""})


def test_download_group_constant():
    """DOWNLOAD_GROUP is the generic default group display name."""
    assert DOWNLOAD_GROUP == "download_hub_download_users"


def test_extract_user_email_present():
    """x-forwarded-user is read case-insensitively."""
    assert extract_user_email({"X-Forwarded-User": "a@b.c"}) == "a@b.c"


def test_extract_user_email_absent_returns_empty():
    """Absent email header yields '' (best-effort, not an auth gate)."""
    assert extract_user_email({}) == ""


def test_is_member_true():
    """is_member is True when a group's .display matches."""
    user = SimpleNamespace(
        groups=[
            SimpleNamespace(display="users"),
            SimpleNamespace(display=DOWNLOAD_GROUP),
        ]
    )
    assert is_member(user, DOWNLOAD_GROUP) is True


def test_is_member_false_when_not_in_groups():
    """is_member is False when the group is not among the user's groups."""
    user = SimpleNamespace(groups=[SimpleNamespace(display="users")])
    assert is_member(user, DOWNLOAD_GROUP) is False


def test_is_member_false_empty_groups():
    """is_member is False for a user with no groups."""
    assert is_member(SimpleNamespace(groups=[]), DOWNLOAD_GROUP) is False


def test_effective_download_group_uses_report_group_when_set():
    """A report with a download_group returns it (stripped)."""
    assert effective_download_group(SimpleNamespace(download_group="grp_x")) == "grp_x"
    assert effective_download_group(SimpleNamespace(download_group="  grp_y  ")) == "grp_y"


def test_effective_download_group_falls_back_to_code_default():
    """None / '' / whitespace-only download_group falls back to DOWNLOAD_GROUP."""
    assert effective_download_group(SimpleNamespace(download_group=None)) == DOWNLOAD_GROUP
    assert effective_download_group(SimpleNamespace(download_group="")) == DOWNLOAD_GROUP
    assert effective_download_group(SimpleNamespace(download_group="   ")) == DOWNLOAD_GROUP


def test_group_display_names_tolerates_missing_attrs():
    """group_display_names tolerates a missing .groups and missing .display."""
    # No .groups attribute at all.
    assert group_display_names(SimpleNamespace()) == []
    # A group entry with no .display is skipped.
    user = SimpleNamespace(
        groups=[SimpleNamespace(), SimpleNamespace(display="ok")]
    )
    assert group_display_names(user) == ["ok"]
