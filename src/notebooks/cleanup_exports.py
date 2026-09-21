# Databricks notebook source
# MAGIC %md
# MAGIC # Exports retention cleanup
# MAGIC
# MAGIC Thin serverless driver for the 24h retention policy on generated exports.
# MAGIC All downloads are asynchronous: `POST /download` generates each file to the
# MAGIC exports volume and records an `export_jobs` row. This job:
# MAGIC 1. reads `catalog` / `schema` / `export_volume` / `ttl_hours` from widgets,
# MAGIC 2. deletes files under the exports volume older than `ttl_hours`,
# MAGIC 3. marks the corresponding `export_jobs` rows `expired`.
# MAGIC
# MAGIC The age decision + the expire SQL are the pure helpers in `app.jobs`
# MAGIC (`select_expired_files` / `build_export_jobs_expire_update`), so they are
# MAGIC unit-tested offline. Air-gap constraint: built-in Spark + Python stdlib only.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Parameters

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Target catalog")
dbutils.widgets.text("schema", "default", "Target schema")
dbutils.widgets.text(
    "export_volume", "/Volumes/main/default/download_exports", "Exports volume root"
)
dbutils.widgets.text("ttl_hours", "24", "Retention window (hours)")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
export_volume = dbutils.widgets.get("export_volume").strip()
try:
    ttl_hours = int(dbutils.widgets.get("ttl_hours"))
except (TypeError, ValueError):
    ttl_hours = 24

schema_fqn = f"{catalog}.{schema}"
print(f"schema_fqn={schema_fqn}")
print(f"export_volume={export_volume}")
print(f"ttl_hours={ttl_hours}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Import the pure job helpers
# MAGIC
# MAGIC The bundle syncs `src/`, so the `app` package sits one directory above this
# MAGIC notebook. Add that directory to `sys.path` and import the pure builders —
# MAGIC no retention logic is reimplemented here.

# COMMAND ----------

import os
import sys
import time


def _add_src_to_path() -> str | None:
    """Locate the synced ``src/`` directory and add it to ``sys.path``.

    Returns the directory containing the ``app`` package, or ``None``.
    """
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        nb_path = ctx.notebookPath().get()
    except Exception:
        nb_path = None

    candidates: list[str] = []
    if nb_path:
        ws_nb = nb_path if nb_path.startswith("/Workspace") else f"/Workspace{nb_path}"
        candidates.append(os.path.dirname(os.path.dirname(ws_nb)))
        candidates.append(os.path.dirname(os.path.dirname(nb_path)))
    candidates.append(os.path.join(os.getcwd(), "src"))
    candidates.append(os.path.dirname(os.getcwd()))

    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(candidate, "app")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    return None


_src_dir = _add_src_to_path()
print(f"src dir added to sys.path: {_src_dir}")

from app.jobs import build_export_jobs_expire_update, select_expired_files

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Delete expired files from the exports volume
# MAGIC
# MAGIC Walk the volume recursively, collect files with their modification time,
# MAGIC and delete those older than `ttl_hours` (decision made by the pure
# MAGIC `select_expired_files`). Emptied directories are pruned afterwards.

# COMMAND ----------


def _walk_files(root: str) -> list[dict]:
    """Return ``[{"path", "modified"}]`` for every file under ``root`` (recursive).

    ``modificationTime`` from the Files/DBFS API is epoch milliseconds;
    ``select_expired_files`` handles the ms/seconds heuristic.
    """
    out: list[dict] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = dbutils.fs.ls(current)
        except Exception as exc:  # noqa: BLE001 - a vanished/inaccessible dir is skipped
            print(f"skip {current}: {exc}")
            continue
        for e in entries:
            if e.isDir():
                stack.append(e.path)
            else:
                out.append({"path": e.path, "modified": getattr(e, "modificationTime", None)})
    return out


deleted = 0
if export_volume:
    files = _walk_files(export_volume)
    doomed = select_expired_files(files, time.time(), ttl_hours)
    for path in doomed:
        try:
            dbutils.fs.rm(path)
            deleted += 1
        except Exception as exc:  # noqa: BLE001 - best effort per file
            print(f"could not delete {path}: {exc}")
    print(f"scanned {len(files)} file(s); deleted {deleted} older than {ttl_hours}h")
else:
    print("no export_volume configured; skipping file cleanup")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Mark expired export-job rows
# MAGIC
# MAGIC Rows older than the TTL are flipped to `expired` so the My downloads page
# MAGIC reflects that their file is gone. Idempotent (skips rows already expired).

# COMMAND ----------

expire_sql = build_export_jobs_expire_update(catalog, schema, ttl_hours)
print(expire_sql)
try:
    spark.sql(expire_sql)
    print("export_jobs rows older than the TTL marked expired")
except Exception as exc:  # noqa: BLE001 - table may not exist on a fresh install
    print(f"skipped export_jobs expire ({exc})")
