"""Pure, in-memory per-user snapshot cache + row operations (LOCKED DECISION L2).

Stdlib-only (``collections.OrderedDict`` / ``time`` / ``dataclasses`` / ``typing``)
— NO fastapi/databricks/pyspark import — so it is importable and unit-testable in
the pytest-only dev ``.venv``. The OBO/SP reads stay in ``main.py`` (the sole I/O
boundary); this module holds only the data structure and the pure row operations
(filter/search/paginate/distinct) that run server-side over a cached snapshot, so
no re-query to the warehouse is needed for filtering, searching, or paging.

The cache is keyed by ``(user_email, report_id, selected_date)`` so it only ever
holds a user's own OBO-authorized rows — there is no cross-user leak.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

Key = tuple[str, str, str]  # (user_email, report_id, selected_date)


@dataclass
class Snapshot:
    """A cached, date-scoped read of a report's source table.

    Attributes:
        columns: All SELECTed column names (display columns ∪ filter fields).
        rows: Row dicts keyed by column name; values are the raw scalars the
            Statement Execution API returns (``str`` or ``None``).
        fetched_at: ``time.time()`` at fetch (feeds the "Last updated" label).
    """

    columns: list[str]
    rows: list[dict]
    fetched_at: float


def make_key(user_email: str, report_id: str, selected_date: str) -> Key:
    """Build the cache key for a (user, report, date) snapshot.

    Args:
        user_email: The signed-in user's email (``X-Forwarded-User``).
        report_id: The report registry key.
        selected_date: The formatted report_date the snapshot is scoped to.

    Returns:
        The ``(user_email, report_id, selected_date)`` tuple key.
    """
    return (user_email, report_id, selected_date)


class SnapshotCache:
    """In-process, per-user LRU snapshot cache; bounded by ``max_size``.

    Optional TTL expiry (``ttl_seconds``). ``get`` marks the entry most-recently
    used; ``put`` evicts the least-recently-used entry once the store exceeds
    ``max_size``; ``evict`` removes a key (used by refresh).
    """

    def __init__(self, max_size: int = 128, ttl_seconds: float | None = None) -> None:
        """Initialize the cache.

        Args:
            max_size: Maximum number of snapshots retained (LRU beyond this).
            ttl_seconds: Optional time-to-live; a snapshot older than this is
                treated as a miss and dropped on ``get``. ``None`` disables TTL.
        """
        self._store: "OrderedDict[Key, Snapshot]" = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def get(self, key: Key) -> Snapshot | None:
        """Return the cached snapshot for ``key`` (and mark it MRU), or ``None``.

        Args:
            key: The ``(user_email, report_id, selected_date)`` key.

        Returns:
            The cached :class:`Snapshot`, or ``None`` on a miss or TTL expiry.
        """
        snap = self._store.get(key)
        if snap is None:
            return None
        if self._ttl is not None and (time.time() - snap.fetched_at) > self._ttl:
            del self._store[key]  # expired -> treat as a miss
            return None
        self._store.move_to_end(key)  # mark most-recently-used
        return snap

    def put(self, key: Key, snap: Snapshot) -> None:
        """Store ``snap`` under ``key`` (MRU), evicting the LRU beyond ``max_size``.

        Args:
            key: The ``(user_email, report_id, selected_date)`` key.
            snap: The :class:`Snapshot` to cache.
        """
        self._store[key] = snap
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)  # evict least-recently-used

    def evict(self, key: Key) -> None:
        """Remove ``key`` from the cache if present (used by refresh/logout).

        Args:
            key: The key to drop; a no-op if absent.
        """
        self._store.pop(key, None)

    def __len__(self) -> int:
        """Return the number of cached snapshots."""
        return len(self._store)


def apply_filters(rows: list[dict], filters: dict[str, str]) -> list[dict]:
    """Filter rows by equality across every selected field (AND).

    Each ``field -> value`` narrows the set by ``str(row.get(field, "")) == value``.
    An empty-string value is the sentinel for "no constraint on this field".

    Args:
        rows: The snapshot rows (dicts keyed by column name).
        filters: A ``field -> selected value`` map; ``""`` means no constraint.

    Returns:
        The rows matching every non-empty filter (equality AND).
    """
    out = rows
    for field, value in filters.items():
        if value == "":  # sentinel: no constraint on this field
            continue
        out = [r for r in out if str(r.get(field, "")) == value]
    return out


def apply_search(
    rows: list[dict], query: str, haystack: Callable[[dict], str]
) -> list[dict]:
    """Filter rows by a case-insensitive substring over their display text.

    Args:
        rows: The rows to search.
        query: The search text; an empty/whitespace query returns all rows.
        haystack: A callable mapping a row to its searchable display text.

    Returns:
        The rows whose ``haystack`` text contains ``query`` (case-insensitive).
    """
    q = query.strip().lower()
    if not q:
        return rows
    return [r for r in rows if q in haystack(r).lower()]


def filters_summary(selected: dict[str, str]) -> str:
    """Summarize the applied filters as a stable ``"field=value; ..."`` string.

    Sorted by field for determinism; empty-value selections (the "no
    constraint" sentinel) are dropped. Used as the audit ``drain_filter`` value
    (its column keeps the legacy name).

    Args:
        selected: The ``field -> selected value`` map.

    Returns:
        ``"field=value; ..."`` (sorted, non-empty only), or ``""`` when no
        filters are active.
    """
    return "; ".join(f"{k}={v}" for k, v in sorted(selected.items()) if v)


def distinct_values(rows: list[dict], field: str) -> list[str]:
    """Return the sorted, distinct, non-null string values of ``field``.

    Feeds a filter dropdown directly from the cached snapshot (no extra query).

    Args:
        rows: The snapshot rows.
        field: The column name to collect distinct values of.

    Returns:
        A sorted list of distinct ``str`` values, skipping ``None``/absent.
    """
    seen = {str(r[field]) for r in rows if r.get(field) is not None}
    return sorted(seen)


def sort_rows(
    rows: list[dict], key: str, direction: str = "asc", numeric: bool = False
) -> list[dict]:
    """Return ``rows`` sorted by ``key`` (stable); missing values always last.

    Rows whose ``key`` is ``None`` or a blank string are treated as "missing" and
    are appended after the sorted present-value rows regardless of ``direction``,
    so an ascending or descending sort never floats blanks to the top. Present
    rows sort numerically (parsing the raw scalar as ``float``) when ``numeric``
    is set, else case-insensitively as text. Python's sort is stable, so rows
    that compare equal keep their prior (server) order.

    Args:
        rows: The rows to sort.
        key: The column name to sort by; an empty/falsey key returns ``rows``
            unchanged (no sort).
        direction: ``"desc"`` for descending; anything else is ascending.
        numeric: When ``True``, compare parsed ``float`` values (non-numeric
            values sort as ``0.0``); when ``False``, compare lowercased text.

    Returns:
        A new list: present-value rows in sort order, then missing-value rows.
    """
    if not key:
        return rows
    present: list[dict] = []
    missing: list[dict] = []
    for r in rows:
        v = r.get(key)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            missing.append(r)
        else:
            present.append(r)

    def _key(r: dict):
        v = r.get(key)
        if numeric:
            try:
                return float(str(v).replace(",", "").replace("%", "").strip())
            except (TypeError, ValueError):
                return 0.0
        return str(v).lower()

    present.sort(key=_key, reverse=(direction == "desc"))
    return present + missing


def paginate(
    rows: list[dict], page: int, size: int | None
) -> tuple[list[dict], int, int]:
    """Slice ``rows`` into a page and report totals.

    Args:
        rows: The rows to paginate.
        page: The 1-based page number (clamped into range).
        size: The page size; ``None`` or ``<= 0`` means "All" (one page).

    Returns:
        A tuple ``(page_rows, total_rows, total_pages)``.
    """
    total = len(rows)
    if size is None or size <= 0:
        return rows, total, 1
    total_pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * size
    return rows[start : start + size], total, total_pages
