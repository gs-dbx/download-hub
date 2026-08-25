"""Unit tests for the pure volume path-jail + listing/download logic in
``app.volumes``.

No fastapi, no databricks.sdk, no network — the SDK boundary is exercised with a
fake ``w`` built from ``SimpleNamespace``.
"""

import os
import sys
from types import SimpleNamespace

import pytest

# Make the src package importable without relying on PYTHONPATH.
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from app.volumes import (  # noqa: E402
    breadcrumbs,
    download_file,
    friendly_volume_error,
    list_dir,
    resolve_within_root,
    upload_file,
)

ROOT = "/Volumes/main/default/docs"


class _UploadRecorder:
    """Fake ``w.files`` that records the last upload call."""

    def __init__(self):
        self.files = self
        self.last = None

    def upload(self, file_path, contents, overwrite=False):
        self.last = {"file_path": file_path, "contents": contents, "overwrite": overwrite}


def test_upload_file_writes_to_resolved_path():
    w = _UploadRecorder()
    out = upload_file(w, ROOT, "alice_at_x.com/report_2026.csv", b"a,b\n1,2\n")
    assert out == ROOT + "/alice_at_x.com/report_2026.csv"
    assert w.last["file_path"] == out
    assert w.last["contents"] == b"a,b\n1,2\n"
    assert w.last["overwrite"] is True


def test_upload_file_rejects_escape():
    w = _UploadRecorder()
    with pytest.raises(ValueError):
        upload_file(w, ROOT, "../evil.csv", b"x")
    assert w.last is None  # never uploaded


def test_upload_file_refuses_root():
    w = _UploadRecorder()
    with pytest.raises(ValueError):
        upload_file(w, ROOT, "", b"x")
    assert w.last is None


# --- resolve_within_root: the security boundary --------------------------


def test_resolve_empty_and_dot_are_root():
    """'' and '.' resolve to the root itself."""
    assert resolve_within_root(ROOT, "") == ROOT
    assert resolve_within_root(ROOT, ".") == ROOT
    assert resolve_within_root(ROOT, None) == ROOT  # type: ignore[arg-type]


def test_resolve_normalizes_root_trailing_slash():
    """A trailing slash / redundant slashes on the root are normalized away."""
    assert resolve_within_root(ROOT + "/", "") == ROOT
    assert resolve_within_root("/Volumes/main//default/docs", "a") == ROOT + "/a"


def test_resolve_child_and_nested_child():
    """A plain child and a nested child resolve under the root."""
    assert resolve_within_root(ROOT, "a") == ROOT + "/a"
    assert resolve_within_root(ROOT, "a/b/c") == ROOT + "/a/b/c"


def test_resolve_collapses_single_dot():
    """A '.' segment collapses but stays within the root."""
    assert resolve_within_root(ROOT, "a/./b") == ROOT + "/a/b"


@pytest.mark.parametrize(
    "evil",
    [
        "..",
        "../..",
        "../sibling",
        "a/../../b",
        "a/../..",
        "/etc/passwd",
        "/Volumes/main/default/other",
        "a/../../../../../../etc/passwd",
    ],
)
def test_resolve_rejects_escapes(evil):
    """Any subpath that climbs to/above the root or is absolute is rejected."""
    with pytest.raises(ValueError):
        resolve_within_root(ROOT, evil)


def test_resolve_rejects_backslashes():
    """Backslash tricks are rejected outright."""
    with pytest.raises(ValueError):
        resolve_within_root(ROOT, "a\\..\\..\\b")


def test_resolve_rejects_sibling_prefix():
    """A sibling that merely shares the root's name prefix is not 'within'."""
    # ".../docs" root must not accept a path resolving to ".../docs2".
    with pytest.raises(ValueError):
        resolve_within_root(ROOT, "../docs2")


def test_resolve_rejects_bad_root():
    """A root outside /Volumes/ or empty is rejected."""
    with pytest.raises(ValueError):
        resolve_within_root("/etc", "a")
    with pytest.raises(ValueError):
        resolve_within_root("", "a")
    with pytest.raises(ValueError):
        resolve_within_root("/Volumes", "a")  # not strictly under /Volumes/


# --- breadcrumbs ----------------------------------------------------------


def test_breadcrumbs_root_only():
    """At the root, only the root crumb is returned (subpath '')."""
    crumbs = breadcrumbs(ROOT, "")
    assert crumbs == [{"label": "docs", "subpath": ""}]


def test_breadcrumbs_nested_are_cumulative():
    """Each crumb's subpath is the cumulative root-relative path."""
    crumbs = breadcrumbs(ROOT, "a/b/c")
    assert crumbs == [
        {"label": "docs", "subpath": ""},
        {"label": "a", "subpath": "a"},
        {"label": "b", "subpath": "a/b"},
        {"label": "c", "subpath": "a/b/c"},
    ]


def test_breadcrumbs_ignores_blank_segments():
    """Redundant slashes produce no empty crumbs."""
    crumbs = breadcrumbs(ROOT, "a//b/")
    assert [c["subpath"] for c in crumbs] == ["", "a", "a/b"]


def test_breadcrumbs_rejects_escape():
    """A current path that escapes the root raises."""
    with pytest.raises(ValueError):
        breadcrumbs(ROOT, "../..")


# --- list_dir (fake SDK) --------------------------------------------------


def _entry(path, is_dir=False, size=None, modified=None):
    """Build a fake DirectoryEntry (SDK-shaped) via SimpleNamespace."""
    return SimpleNamespace(
        path=path,
        name=os.path.basename(path),
        is_directory=is_dir,
        file_size=size,
        last_modified=modified,
    )


def _fake_w(entries):
    """A fake WorkspaceClient whose files.list_directory_contents yields entries."""
    return SimpleNamespace(
        files=SimpleNamespace(
            list_directory_contents=lambda directory_path: iter(entries)
        )
    )


def test_list_dir_splits_and_sorts_folders_first():
    """Folders and files are separated and each sorted case-insensitively."""
    entries = [
        _entry(ROOT + "/Zeta.csv", size=10, modified="t1"),
        _entry(ROOT + "/beta", is_dir=True),
        _entry(ROOT + "/alpha", is_dir=True),
        _entry(ROOT + "/apple.txt", size=5, modified="t2"),
    ]
    out = list_dir(_fake_w(entries), ROOT, "")
    assert [f["name"] for f in out["folders"]] == ["alpha", "beta"]
    # Case-insensitive: 'apple' sorts before 'Zeta'.
    assert [f["name"] for f in out["files"]] == ["apple.txt", "Zeta.csv"]


def test_list_dir_files_carry_metadata_and_relative_subpath():
    """File entries carry size/modified and a ROOT-relative subpath."""
    entries = [_entry(ROOT + "/sub/report.csv", size=42, modified="2026-01-01")]
    out = list_dir(_fake_w(entries), ROOT, "sub")
    assert out["folders"] == []
    (f,) = out["files"]
    assert f == {
        "name": "report.csv",
        "subpath": "sub/report.csv",
        "size_bytes": 42,
        "modified": "2026-01-01",
    }


def test_list_dir_passes_resolved_abspath_to_sdk():
    """list_dir asks the SDK for the resolved absolute directory path."""
    seen = {}

    def _capture(directory_path):
        seen["dir"] = directory_path
        return iter([])

    w = SimpleNamespace(files=SimpleNamespace(list_directory_contents=_capture))
    list_dir(w, ROOT, "a/b")
    assert seen["dir"] == ROOT + "/a/b"


def test_list_dir_rejects_escape_before_sdk_call():
    """An escaping subpath raises before any SDK call."""
    called = {"n": 0}

    def _boom(directory_path):
        called["n"] += 1
        return iter([])

    w = SimpleNamespace(files=SimpleNamespace(list_directory_contents=_boom))
    with pytest.raises(ValueError):
        list_dir(w, ROOT, "../..")
    assert called["n"] == 0


# --- download_file (fake SDK) ---------------------------------------------


def _fake_download_w(data, get_dir=None):
    """A fake client whose files.download returns an object with .contents.read()."""
    files = SimpleNamespace(
        download=lambda file_path: SimpleNamespace(
            contents=SimpleNamespace(read=lambda: data)
        )
    )
    if get_dir is not None:
        files.get_directory_metadata = get_dir
    return SimpleNamespace(files=files)


def test_download_returns_bytes_and_filename():
    """download_file streams .contents.read() and returns (bytes, basename)."""
    w = _fake_download_w(b"hello,world\n")
    data, filename = download_file(w, ROOT, "sub/report.csv")
    assert data == b"hello,world\n"
    assert filename == "report.csv"


def test_download_passes_resolved_abspath():
    """download_file asks the SDK for the resolved absolute file path."""
    seen = {}

    def _dl(file_path):
        seen["p"] = file_path
        return SimpleNamespace(contents=SimpleNamespace(read=lambda: b"x"))

    w = SimpleNamespace(files=SimpleNamespace(download=_dl))
    download_file(w, ROOT, "a/b.txt")
    assert seen["p"] == ROOT + "/a/b.txt"


def test_download_refuses_root():
    """Refusing to treat the root path itself as a downloadable file."""
    w = _fake_download_w(b"x")
    with pytest.raises(ValueError):
        download_file(w, ROOT, "")


def test_download_refuses_escape():
    """An escaping subpath is rejected before download."""
    w = _fake_download_w(b"x")
    with pytest.raises(ValueError):
        download_file(w, ROOT, "../secret")


def test_download_refuses_confirmed_directory():
    """When the dir-metadata probe succeeds, the path is a directory -> refuse."""
    w = _fake_download_w(b"x", get_dir=lambda directory_path: SimpleNamespace())
    with pytest.raises(ValueError):
        download_file(w, ROOT, "a")


def test_download_proceeds_when_dir_probe_raises():
    """A dir-metadata probe that raises means 'not a directory' -> proceed."""
    def _raise(directory_path):
        raise RuntimeError("NOT_FOUND")

    w = _fake_download_w(b"file-bytes", get_dir=_raise)
    data, filename = download_file(w, ROOT, "a.txt")
    assert data == b"file-bytes"
    assert filename == "a.txt"


# --- friendly_volume_error ------------------------------------------------


def test_friendly_not_found():
    """Not-found style messages get file/folder-specific wording."""
    msg = friendly_volume_error(Exception("RESOURCE_DOES_NOT_EXIST: file not found"))
    assert "could not be found" in msg.lower()


def test_friendly_permission():
    """Permission style messages get access wording."""
    msg = friendly_volume_error("PERMISSION_DENIED: not authorized")
    assert "permission" in msg.lower()


def test_friendly_delegates_to_errors_module():
    """An unrecognized message delegates to errors.friendly_error (non-empty)."""
    msg = friendly_volume_error("some unclassified failure")
    assert isinstance(msg, str) and msg
