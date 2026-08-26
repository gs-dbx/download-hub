"""Pure runtime configuration helpers for the Data Download Hub app.

Stdlib-only, no SDK import and no network call, so this module is unit-testable
in the pytest-only dev ``.venv``. The download kill switch lives here as a pure
parser so ``main.py`` can gate both the UI panel and the ``POST /download``
route off a single, testable helper; the branding helpers resolve the app name,
logo, and org label from environment values (with generic defaults) so the app
is white-labeled by config alone — no code change.
"""

from __future__ import annotations

# Values that DISABLE downloads (case-insensitive, stripped). Anything else —
# including the unset/None case — leaves downloads ENABLED (default true).
_DISABLED_VALUES: frozenset[str] = frozenset({"false", "0", "no", "off", ""})

# Generic brand defaults. Override each via its env var (see ``app.yaml``):
#   APP_NAME -> app_name, APP_LOGO -> app_logo, APP_ORG_NAME -> app_org_name.
DEFAULT_APP_NAME: str = "Data Download Hub"
DEFAULT_APP_LOGO: str = "/static/img/logo.svg"

# Chrome text defaults — the top government banner and the footer note. Both are
# admin-editable (System Config -> app_config keys ``banner_text`` /
# ``footer_text``) and env-overridable (APP_BANNER_TEXT / APP_FOOTER_TEXT). Once
# an admin SETS one — even to an empty string — that value wins verbatim (a blank
# value stays blank / hides the element); the env value + these defaults apply
# only until a value has ever been set. See :func:`resolve_chrome_text`.
DEFAULT_BANNER_TEXT: str = "An official website of the United States government"
DEFAULT_FOOTER_TEXT: str = "Synthetic data — for demonstration only."


def resolve_chrome_text(
    cfg_value: str | None, env_value: str | None, default: str
) -> str:
    """Return the effective banner/footer text (admin config wins, blank stays blank).

    Unlike :func:`resolve_disclaimer`, an explicitly-set-but-empty admin value is
    honored verbatim ("if blank, leave them blank") rather than falling back to a
    default — so an admin can clear the banner/footer to hide it.

    Args:
        cfg_value: The ``app_config`` value for this key. ``None`` means the key
            has NEVER been set (fall back to env/default); any string — including
            ``""`` — means the admin set it and it wins verbatim.
        env_value: The raw env override (``APP_BANNER_TEXT`` / ``APP_FOOTER_TEXT``),
            or ``None`` if unset. Used only when ``cfg_value`` is ``None``.
        default: The built-in fallback (:data:`DEFAULT_BANNER_TEXT` /
            :data:`DEFAULT_FOOTER_TEXT`).

    Returns:
        ``cfg_value`` verbatim when it is not ``None`` (blank included); otherwise
        ``env_value`` stripped if non-empty, else ``default``.
    """
    if cfg_value is not None:
        return cfg_value
    return (env_value or "").strip() or default


def resolve_disclaimer(value: str | None, default: str) -> str:
    """Return the effective download acknowledgement text.

    Args:
        value: The raw ``DOWNLOAD_DISCLAIMER`` env value (or ``None`` if unset).
        default: The built-in fallback (``exports.DEFAULT_DISCLAIMER``).

    Returns:
        ``value`` stripped if non-empty, else ``default``.
    """
    return (value or "").strip() or default


def app_name(value: str | None) -> str:
    """Return the configured app name, or the generic default.

    Args:
        value: The raw ``APP_NAME`` env value (or ``None`` if unset).

    Returns:
        ``value`` stripped if non-empty, else :data:`DEFAULT_APP_NAME`.
    """
    return (value or "").strip() or DEFAULT_APP_NAME


def app_logo(value: str | None) -> str:
    """Return the configured logo path, or the generic default.

    Args:
        value: The raw ``APP_LOGO`` env value (a ``/static/...`` path), or ``None``.

    Returns:
        ``value`` stripped if non-empty, else :data:`DEFAULT_APP_LOGO`.
    """
    return (value or "").strip() or DEFAULT_APP_LOGO


def app_org_name(value: str | None, name_fallback: str | None = None) -> str:
    """Return the org/label used as the logo ``alt`` text.

    Args:
        value: The raw ``APP_ORG_NAME`` env value, or ``None``.
        name_fallback: The resolved app name to fall back to when unset.

    Returns:
        ``value`` stripped if non-empty, else ``name_fallback`` if given, else
        :data:`DEFAULT_APP_NAME`.
    """
    return (value or "").strip() or (name_fallback or DEFAULT_APP_NAME)


def downloads_enabled(value: str | None) -> bool:
    """Return whether the download feature is globally enabled.

    The kill switch is driven by the ``DOWNLOADS_ENABLED`` environment
    variable. Downloads default to ENABLED when the value
    is unset (``None``) and are DISABLED only for an explicit falsey value.

    Args:
        value: The raw environment-variable value (or ``None`` if unset).

    Returns:
        ``False`` if ``value`` (stripped, lower-cased) is one of
        ``{"false", "0", "no", "off", ""}``; ``True`` otherwise (including when
        ``value`` is ``None``).
    """
    if value is None:
        return True
    return value.strip().lower() not in _DISABLED_VALUES
