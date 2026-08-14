"""Repo-hygiene guard tests for Phase 5 branding (LOCKED L2 / L5).

Pure file reads — no app import, no network. Two guards:
  * No case-insensitive "ocfo" anywhere under src/ or resources/ (OCFO scrubbed
    from all shipping code).
  * No external URL in OUR authored front-end files (templates, static/css,
    static/js). The vendored USWDS dist (static/uswds) legitimately contains URLs
    in comments/source maps, so it is explicitly NOT scanned.
"""

import re
from pathlib import Path

# tests/ -> repo root is the parent of this file's parent.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# http(s):// URLs and the common CDN hosts we never want in authored assets.
_URL_RE = re.compile(r"https?://|unpkg|jsdelivr|cdnjs", re.IGNORECASE)


def _iter_files(root: Path):
    """Yield regular files under ``root`` (recursively), skipping __pycache__."""
    for path in root.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def _read_text(path: Path) -> str | None:
    """Read a file as UTF-8 text; return None for binary/undecodable files."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def test_no_ocfo_in_src_or_resources():
    """No case-insensitive 'ocfo' remains under src/ or resources/."""
    offenders = []
    for base in ("src", "resources"):
        root = _REPO_ROOT / base
        if not root.exists():
            continue
        for path in _iter_files(root):
            text = _read_text(path)
            if text is not None and "ocfo" in text.lower():
                offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not offenders, f"OCFO reference(s) found in: {offenders}"


def test_no_external_urls_in_authored_frontend():
    """No external/CDN URL in our authored templates, css, or js (not USWDS)."""
    authored_dirs = [
        _REPO_ROOT / "src" / "app" / "templates",
        _REPO_ROOT / "src" / "app" / "static" / "css",
        _REPO_ROOT / "src" / "app" / "static" / "js",
    ]
    offenders = []
    for root in authored_dirs:
        if not root.exists():
            continue
        for path in _iter_files(root):
            text = _read_text(path)
            if text is not None and _URL_RE.search(text):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not offenders, f"External URL(s) found in authored files: {offenders}"
