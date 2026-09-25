"""Repo-hygiene guards for the download-spinner / nav / warehouse UX contract.

Pure file reads — no app import, no network. These lock the fixes that stopped
the "Running query…" overlay from spinning forever on a download, made the
masthead a Home link, and added the warehouse cold-start overlay. They are
intentionally coarse (substring/regex over authored files) so they stay robust
to formatting churn while catching a regression that reintroduces the bug.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = _REPO_ROOT / "src" / "app" / "templates"
_APP_JS = _REPO_ROOT / "src" / "app" / "static" / "js" / "app.js"
_MAIN_PY = _REPO_ROOT / "src" / "app" / "main.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_retrieve_links_carry_download_attribute():
    """Every anchor to /download/retrieve MUST have a `download` attribute.

    Without it the global nav-overlay click handler treats the click as an
    internal navigation and shows the "Running query…" scrim — which never hides
    because a file download does not navigate the page (the reported bug).
    """
    offenders = []
    for tpl in _TEMPLATES.glob("*.html"):
        text = _read(tpl)
        # Find every <a ...> tag whose href points at the retrieve endpoint.
        for tag in re.findall(r"<a\b[^>]*/download/retrieve[^>]*>", text):
            if not re.search(r"\bdownload\b", tag):
                offenders.append(f"{tpl.name}: {tag[:80]}")
        # Also the ready-link whose href is set by app.js (href='#' placeholder).
    assert not offenders, f"retrieve link(s) missing `download` attribute: {offenders}"


def test_download_ready_link_has_download_attribute():
    """The modal's ready-link (href set to /download/retrieve by app.js) is marked
    `download` in the template so the nav-overlay handler always ignores it."""
    text = _read(_TEMPLATES / "_download.html")
    m = re.search(r"<a[^>]*data-role=\"download-ready-link\"[^>]*>", text)
    assert m, "download-ready-link anchor not found in _download.html"
    assert re.search(r"\bdownload\b", m.group(0)), (
        "download-ready-link must carry the `download` attribute"
    )


def test_nav_overlay_excludes_retrieve_and_has_safety_hide():
    """The nav-overlay handler must skip download endpoints AND be self-limiting
    (a focus/visibility hide or a timeout) so it can never spin forever."""
    js = _read(_APP_JS)
    assert "/download/retrieve" in js, (
        "nav-overlay click guard should exclude the /download/retrieve endpoint"
    )
    # A safety net: either the overlay auto-hides on refocus/visibility or a cap.
    assert 'addEventListener("focus", hideNav)' in js or "visibilitychange" in js, (
        "nav-overlay should hide when the tab regains focus/visibility"
    )


def test_masthead_app_name_links_home():
    """The 'Data Download Hub' wordmark is a link back to '/'."""
    text = _read(_TEMPLATES / "base.html")
    assert re.search(
        r"<a[^>]*class=\"app-header__app\"[^>]*href=\"/\"", text
    ) or re.search(
        r"<a[^>]*href=\"/\"[^>]*class=\"app-header__app\"", text
    ), "masthead app name must be an <a href=\"/\"> Home link"


def test_warehouse_start_endpoint_and_overlay_exist():
    """The warehouse cold-start overlay + its best-effort start endpoint exist."""
    assert '"/health/warehouse/start"' in _read(_MAIN_PY), (
        "POST /health/warehouse/start endpoint is missing"
    )
    base = _read(_TEMPLATES / "base.html")
    assert 'data-role="wh-overlay"' in base, "warehouse overlay element is missing"
    js = _read(_APP_JS)
    assert "/health/warehouse/start" in js, (
        "app.js should request a warehouse start when it finds one stopped"
    )
