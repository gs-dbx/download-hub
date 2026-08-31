"""Governed browsing + download of files under a report's pinned UC Volume root.

A "Volume report" exposes ONE pinned root path under ``/Volumes/...`` (LOCKED
DECISION: single root, traverse-below). Users browse that folder and every
subfolder beneath it, but are JAILED to the root — no path may escape it. This
first cut is metadata + download only (no inline preview rendering).

The security boundary is :func:`resolve_within_root`, a pure, stdlib-only
function: given the report's root and a user-supplied relative ``subpath`` it
returns a clean absolute path guaranteed to be the root or a descendant, or
raises ``ValueError``. Everything the UI links to is a root-relative ``subpath``,
never an absolute path, so a tampered link still has to pass the resolver.

The SDK wrappers (:func:`list_dir`, :func:`download_file`) receive an
already-built, per-user ``WorkspaceClient`` — reads run as the signed-in user via
OBO (built in ``main.py`` with ``auth_type="pat"``), so Unity Catalog
``READ VOLUME`` grants are enforced per-user. This module never builds a client
and never touches network state itself beyond the passed-in ``w.files`` calls, so
all path/logic code is unit-testable in the pytest-only dev ``.venv``.
"""

from __future__ import annotations

import posixpath
from typing import Any

# Every volume path must live under this mount; the resolver refuses anything
# else so a misconfigured root can never point at an arbitrary filesystem path.
_VOLUMES_PREFIX = "/Volumes/"


def _clean_root(root: str) -> str:
    """Validate and normalize a report's pinned volume root.

    Args:
        root: The configured root path (e.g. ``/Volumes/main/default/docs``).

    Returns:
        The normalized root with redundant slashes collapsed and any trailing
        slash removed.

    Raises:
        ValueError: If ``root`` is empty, contains a backslash, or does not sit
            strictly under :data:`_VOLUMES_PREFIX`.
    """
    if not isinstance(root, str) or not root.strip():
        raise ValueError("volume root must be a non-empty path")
    raw = root.strip()
    if "\\" in raw:
        raise ValueError("backslashes are not allowed in a volume path")
    norm = posixpath.normpath(raw)
    if not norm.startswith(_VOLUMES_PREFIX):
        raise ValueError(f"volume root must be under {_VOLUMES_PREFIX}")
    return norm


def resolve_within_root(root: str, subpath: str) -> str:
    """Resolve a user-supplied ``subpath`` against ``root``, jailed to the root.

    This is the security boundary for volume browsing. The joined path is
    normalized (collapsing ``.``/``..``/redundant slashes) and then verified to
    be the root itself or a strict descendant of it. ``""`` and ``"."`` mean the
    root. Absolute subpaths, backslashes, and any ``..`` sequence that would
    climb to or above the root are rejected.

    Args:
        root: The report's pinned volume root (validated + normalized here).
        subpath: A root-RELATIVE path from a UI link or query param.

    Returns:
        A clean absolute ``/Volumes/...`` path equal to the root or beneath it.

    Raises:
        ValueError: If the root is invalid, or the subpath is absolute, uses
            backslashes, or escapes the root.
    """
    root_clean = _clean_root(root)
    sub = (subpath or "").strip()
    if sub in ("", "."):
        return root_clean
    if "\\" in sub:
        raise ValueError("backslashes are not allowed in a subpath")
    if sub.startswith("/"):
        raise ValueError("subpath must be relative to the volume root")
    joined = posixpath.normpath(posixpath.join(root_clean, sub))
    # Jail check: equal to root, or under root + "/" (the trailing slash prevents
    # a sibling-prefix match like ".../docs2" passing as ".../docs").
    if joined != root_clean and not joined.startswith(root_clean + "/"):
        raise ValueError("subpath escapes the volume root")
    return joined


def _subpath_of(root_clean: str, abspath: str) -> str:
    """Return the root-relative subpath of an absolute path (``""`` for root).

    Args:
        root_clean: An already-normalized root (from :func:`_clean_root`).
        abspath: An absolute path expected to be root or a descendant.

    Returns:
        The path relative to the root, or the basename as a defensive fallback
        if ``abspath`` is unexpectedly outside the root.
    """
    ap = posixpath.normpath(abspath)
    if ap == root_clean:
        return ""
    if ap.startswith(root_clean + "/"):
        return ap[len(root_clean) + 1:]
    return posixpath.basename(ap)


def breadcrumbs(root: str, current: str) -> list[dict]:
    """Build breadcrumb links from the root down to the current folder.

    Args:
        root: The report's pinned volume root.
        current: The current folder as a root-relative subpath (``""`` = root).

    Returns:
        An ordered list of ``{"label", "subpath"}`` dicts: the root crumb first
        (``subpath == ""``), then one crumb per path segment. Each ``subpath`` is
        the cumulative root-relative path for that crumb.

    Raises:
        ValueError: If ``current`` fails the :func:`resolve_within_root` jail.
    """
    root_clean = _clean_root(root)
    resolve_within_root(root_clean, current)  # validate: raises if it escapes
    crumbs: list[dict] = [
        {"label": posixpath.basename(root_clean) or root_clean, "subpath": ""}
    ]
    sub = (current or "").strip().strip("/")
    if sub in ("", "."):
        return crumbs
    acc: list[str] = []
    for seg in sub.split("/"):
        if seg in ("", "."):
            continue
        acc.append(seg)
        crumbs.append({"label": seg, "subpath": "/".join(acc)})
    return crumbs


def list_dir(w: Any, root: str, subpath: str) -> dict:
    """List one directory under the root as the signed-in user (OBO).

    Resolves ``subpath`` within the root, then reads the directory via the Files
    API. Entries are split into folders and files and each carries a root-relative
    ``subpath`` so the UI links stay jailed. Both lists are sorted by name
    (case-insensitive); folders are returned separately so the UI can render them
    first.

    Args:
        w: A per-user ``WorkspaceClient`` (OBO). Only ``w.files`` is used.
        root: The report's pinned volume root.
        subpath: The folder to list, root-relative (``""`` = root).

    Returns:
        ``{"folders": [{"name", "subpath"}],
           "files":   [{"name", "subpath", "size_bytes", "modified"}]}``.

    Raises:
        ValueError: If the subpath escapes the root (from the resolver).
    """
    root_clean = _clean_root(root)
    abspath = resolve_within_root(root_clean, subpath)
    folders: list[dict] = []
    files: list[dict] = []
    for entry in w.files.list_directory_contents(directory_path=abspath):
        name = getattr(entry, "name", None) or posixpath.basename(
            getattr(entry, "path", "") or ""
        )
        epath = getattr(entry, "path", None) or posixpath.join(abspath, name)
        esub = _subpath_of(root_clean, epath)
        if getattr(entry, "is_directory", False):
            folders.append({"name": name, "subpath": esub})
        else:
            files.append(
                {
                    "name": name,
                    "subpath": esub,
                    "size_bytes": getattr(entry, "file_size", None),
                    "modified": getattr(entry, "last_modified", None),
                }
            )
    folders.sort(key=lambda d: (d["name"] or "").lower())
    files.sort(key=lambda d: (d["name"] or "").lower())
    return {"folders": folders, "files": files}


def _guard_not_directory(w: Any, abspath: str) -> None:
    """Best-effort refuse: never stream a directory as a file.

    Uses the Files API directory-metadata probe when available; a success means
    the path is a directory (reject), any error means it is not (proceed). No-ops
    if the SDK method is unavailable, so the SDK's own error still surfaces.

    Args:
        w: A per-user ``WorkspaceClient``.
        abspath: The resolved absolute path being downloaded.

    Raises:
        ValueError: If the path is confirmed to be a directory.
    """
    getdir = getattr(getattr(w, "files", None), "get_directory_metadata", None)
    if getdir is None:
        return
    try:
        getdir(directory_path=abspath)
    except Exception:
        return  # not a directory (NotFound etc.) -> safe to download
    raise ValueError("the requested path is a directory, not a file")


def download_file(w: Any, root: str, subpath: str) -> tuple[bytes, str]:
    """Download one file under the root as the signed-in user (OBO).

    Resolves ``subpath`` within the root, refuses the root itself and any
    confirmed directory, then reads the file's bytes fully into memory.

    Args:
        w: A per-user ``WorkspaceClient`` (OBO). Only ``w.files`` is used.
        root: The report's pinned volume root.
        subpath: The file to download, root-relative.

    Returns:
        A tuple ``(data, filename)`` — the full file bytes and its basename (for
        the ``Content-Disposition`` header).

    Raises:
        ValueError: If the subpath escapes the root, targets the root, or is a
            directory.
    """
    root_clean = _clean_root(root)
    abspath = resolve_within_root(root_clean, subpath)
    if abspath == root_clean:
        raise ValueError("refusing to download the volume root as a file")
    _guard_not_directory(w, abspath)
    resp = w.files.download(file_path=abspath)
    contents = getattr(resp, "contents", resp)
    data = contents.read() if hasattr(contents, "read") else bytes(contents)
    return data, posixpath.basename(abspath)


def open_download(w: Any, root: str, subpath: str) -> tuple[Any, str]:
    """Open a volume file as a readable stream without buffering its contents."""
    root_clean = _clean_root(root)
    abspath = resolve_within_root(root_clean, subpath)
    if abspath == root_clean:
        raise ValueError("refusing to download the volume root as a file")
    _guard_not_directory(w, abspath)
    resp = w.files.download(file_path=abspath)
    return getattr(resp, "contents", resp), posixpath.basename(abspath)


def upload_file(w: Any, root: str, subpath: str, data: Any) -> str:
    """Write ``data`` to ``subpath`` under the root as the signed-in user (OBO).

    Used for over-cap exports: instead of returning a huge file in the HTTP
    response, the app spills it to an app-private UC volume. Resolves + jails
    ``subpath`` within the root (rejecting the root itself), then uploads with
    overwrite so a re-run replaces a prior spill of the same name.

    Args:
        w: The app service-principal ``WorkspaceClient``. Only ``w.files`` is used.
        root: The pinned export volume root (validated + normalized here).
        subpath: The destination file, root-relative.
        data: File bytes or a readable binary stream. Streams allow large
            exports to upload without being copied into app memory.

    Returns:
        The resolved absolute ``/Volumes/...`` path the bytes were written to.

    Raises:
        ValueError: If the subpath escapes the root or targets the root itself.
    """
    root_clean = _clean_root(root)
    abspath = resolve_within_root(root_clean, subpath)
    if abspath == root_clean:
        raise ValueError("refusing to write the volume root as a file")
    w.files.upload(file_path=abspath, contents=data, overwrite=True)
    return abspath


def friendly_volume_error(exc: BaseException | str | None) -> str:
    """Map a raw Files-API error to concise, user-facing text.

    File/folder not-found and permission cases get volume-specific wording; any
    other message delegates to :func:`errors.friendly_error` so the two error
    surfaces stay consistent (imported lazily to tolerate both the runtime
    flat-import layout and the ``app.*`` test-import layout).

    Args:
        exc: The raised exception (or its message, or ``None``).

    Returns:
        A short, non-technical explanation. Never a stack trace.
    """
    raw = "" if exc is None else str(exc)
    low = raw.lower()
    if any(s in low for s in ("not found", "does not exist", "no such file",
                              "nosuchkey", "404")):
        return ("That file or folder could not be found. It may have been moved "
                "or removed.")
    if any(s in low for s in ("permission", "denied", "not authorized",
                              "forbidden", "403")):
        return "You do not have permission to access this file or folder."
    try:
        from errors import friendly_error
    except ImportError:  # imported as app.errors under PYTHONPATH=src (tests)
        from app.errors import friendly_error
    return friendly_error(raw)
