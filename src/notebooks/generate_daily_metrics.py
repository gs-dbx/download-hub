# Databricks notebook source
# MAGIC %md
# MAGIC # Generate Daily Metrics gold table
# MAGIC
# MAGIC Thin serverless driver. All row math lives in `sample_report.generator` (pure, Spark-free).
# MAGIC This notebook:
# MAGIC 1. reads `catalog` / `schema` from widgets (no hardcoded targets),
# MAGIC 2. creates the schema if needed,
# MAGIC 3. creates the empty typed `download_audit` table (idempotent),
# MAGIC 4. overwrites the `daily_metrics` gold table with 408 deterministic rows.
# MAGIC
# MAGIC Air-gap constraint (LOCKED DECISION L4): built-in Spark + Python stdlib only — no `%pip`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Parameters

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Target catalog")
dbutils.widgets.text("schema", "default", "Target schema")
dbutils.widgets.text("view_group", "download_hub_app_users", "Report view group")
dbutils.widgets.text(
    "download_group", "download_hub_download_users", "Report download group"
)

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
view_group = dbutils.widgets.get("view_group")
download_group = dbutils.widgets.get("download_group")

schema_fqn = f"{catalog}.{schema}"
gold_fqn = f"{schema_fqn}.daily_metrics"
audit_fqn = f"{schema_fqn}.download_audit"

print(f"schema_fqn={schema_fqn}")
print(f"gold_fqn={gold_fqn}")
print(f"audit_fqn={audit_fqn}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Import the pure generator
# MAGIC
# MAGIC The bundle syncs `src/`, so the `sample_report` package sits one directory above this
# MAGIC notebook. Add that directory to `sys.path` and import — no generation logic is
# MAGIC reimplemented here.

# COMMAND ----------

import os
import sys


def _add_src_to_path() -> str | None:
    """Locate the synced `src/` directory (parent of this notebook's folder) and add it to sys.path.

    Returns:
        The directory added to ``sys.path`` (the one containing the ``sample_report``
        package), or ``None`` if it could not be located.
    """
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        nb_path = ctx.notebookPath().get()
    except Exception:
        # Defensive: the dbutils context bridge is opaque; fall back to cwd-based search.
        nb_path = None

    candidates: list[str] = []
    if nb_path:
        ws_nb = nb_path if nb_path.startswith("/Workspace") else f"/Workspace{nb_path}"
        candidates.append(os.path.dirname(os.path.dirname(ws_nb)))
        candidates.append(os.path.dirname(os.path.dirname(nb_path)))
    candidates.append(os.path.join(os.getcwd(), "src"))
    candidates.append(os.path.dirname(os.getcwd()))

    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(candidate, "sample_report")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    return None


_src_dir = _add_src_to_path()
print(f"src dir added to sys.path: {_src_dir}")

from sample_report.generator import build_metric_rows

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Create schema

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Create the audit table (empty, idempotent)
# MAGIC
# MAGIC `CREATE TABLE IF NOT EXISTS ... USING DELTA` so reruns never clobber app-written rows.
# MAGIC No data is inserted here — the app writes rows in a later phase.

# COMMAND ----------

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {audit_fqn} (
      audit_id STRING,
      event_ts TIMESTAMP,
      user_email STRING,
      report_date TIMESTAMP,
      filter_summary STRING,
      search_filter STRING,
      row_count BIGINT,
      export_format STRING,
      justification STRING,
      acknowledged BOOLEAN,
      app_version STRING,
      report_id STRING,
      report_title STRING,
      source_query STRING
    ) USING DELTA
    """
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4b. Create the admin change-audit table (empty, idempotent)
# MAGIC
# MAGIC Tracks all admin mutations (report/view/config upserts) for compliance
# MAGIC and analytics. ``CREATE TABLE IF NOT EXISTS ... USING DELTA`` so reruns
# MAGIC never clobber app-written rows. The app writes rows as the service principal.

# COMMAND ----------

config_audit_fqn = f"{schema_fqn}.config_audit"
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {config_audit_fqn} (
      audit_id STRING,
      event_ts TIMESTAMP,
      actor_email STRING,
      entity_type STRING,
      entity_key STRING,
      action STRING,
      summary STRING,
      payload_json STRING,
      app_version STRING
    ) USING DELTA
    """
)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4a. Migrate pre-existing audit tables (idempotent)
# MAGIC
# MAGIC For installs created before Phase 8 the audit table has only 11 columns.
# MAGIC A Python column-presence guard adds `report_id`/`report_title` only when
# MAGIC missing (existing rows become NULL). Safe on fresh 13-column installs and
# MAGIC on re-runs — this is a no-op once the columns are present.

# COMMAND ----------

existing = {f.name for f in spark.table(audit_fqn).schema.fields}
to_add = [
    (c, "STRING")
    for c in ("report_id", "report_title", "source_query")
    if c not in existing
]
if to_add:
    cols = ", ".join(f"{name} {typ}" for name, typ in to_add)
    spark.sql(f"ALTER TABLE {audit_fqn} ADD COLUMNS ({cols})")
    print(f"added audit columns: {[c for c, _ in to_add]}")
else:
    print("audit columns report_id/report_title already present")

# The generic filter summary column was renamed drain_filter -> filter_summary
# in Milestone 2. Older installs still carry `drain_filter`; the app writes
# `filter_summary`, so add + backfill it (idempotent no-op once present).
if "filter_summary" not in existing:
    spark.sql(f"ALTER TABLE {audit_fqn} ADD COLUMNS (filter_summary STRING)")
    if "drain_filter" in existing:
        spark.sql(
            f"UPDATE {audit_fqn} SET filter_summary = drain_filter "
            "WHERE filter_summary IS NULL"
        )
    print("added audit column: filter_summary (backfilled from drain_filter)")
else:
    print("audit column filter_summary already present")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Build and overwrite the gold table
# MAGIC
# MAGIC Deterministic rows from the pure generator, written with an explicit `StructType`
# MAGIC (LongType => BIGINT, DoubleType nullable for `pct_change`) via Delta `mode("overwrite")`
# MAGIC for idempotency (LOCKED DECISION L5).

# COMMAND ----------

from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

gold_schema = StructType(
    [
        StructField("report_date", TimestampType(), False),
        StructField("channel", StringType(), False),
        StructField("metric_name", StringType(), False),
        StructField("metric_group", StringType(), False),
        StructField("sort_order", IntegerType(), False),
        StructField("value_cy", LongType(), False),
        StructField("value_py", LongType(), False),
        StructField("pct_change", DoubleType(), True),
    ]
)

rows = build_metric_rows()
print(f"generated rows: {len(rows)}")

(
    spark.createDataFrame(rows, gold_schema)
    .write.format("delta")
    .mode("overwrite")
    .saveAsTable(gold_fqn)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Verify

# COMMAND ----------

total = spark.table(gold_fqn).count()
print(f"{gold_fqn} row count: {total}")
spark.table(gold_fqn).groupBy("channel").count().orderBy("channel").show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Create and seed the report registry (`report_config`)
# MAGIC
# MAGIC The Milestone-2 registry that drives the generic per-user report reader (Phase 7).
# MAGIC `CREATE TABLE IF NOT EXISTS` + an idempotent DeltaTable **MERGE on `report_id`** — NEVER
# MAGIC `overwrite`, so any reports added after report #1 survive reruns. The list-valued config
# MAGIC (`columns_json`/`filters_json`) is built with `json.dumps` (compact, valid JSON — no SQL
# MAGIC string-escaping risk) and `updated_at` is re-stamped on every upsert. `source_query` is
# MAGIC derived from the `schema_fqn` widget (no hardcoded catalog/schema). Runtime libs only.

# COMMAND ----------

import json

from delta.tables import DeltaTable
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

config_fqn = f"{schema_fqn}.report_config"

view_fqn = f"{schema_fqn}.report_view"

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {config_fqn} (
      report_id      STRING,
      title          STRING,
      source_query   STRING,
      date_field     STRING,
      columns_json   STRING,
      filters_json   STRING,
      order_by       STRING,
      display_order  INT,
      enabled        BOOLEAN,
      download_group STRING,
      view_key       STRING,
      kind           STRING,
      volume_root    STRING,
      updated_at     TIMESTAMP,
      updated_by     STRING
    ) USING DELTA
    """
)

# The view registry (the switcher). Each view's `view_key` is BOTH the URL key
# and the Databricks group that grants view access; `title` is the switcher
# label. A report's download group derives as `<view_key>` + suffix (default
# `_dl`) unless it sets an explicit `download_group`.
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {view_fqn} (
      view_key      STRING,
      title         STRING,
      display_order INT,
      enabled       BOOLEAN,
      updated_at    TIMESTAMP,
      updated_by    STRING
    ) USING DELTA
    """
)

# System config (key/value). Holds the admin-editable download disclaimer
# (key `download_disclaimer`); the app falls back to env/default when unset.
app_config_fqn = f"{schema_fqn}.app_config"
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {app_config_fqn} (
      config_key   STRING,
      config_value STRING,
      updated_at   TIMESTAMP,
      updated_by   STRING
    ) USING DELTA
    """
)

# Migrate older installs (idempotent). Add any missing report_config columns:
#   * source_query (backfilled from the retired source_fqn as `SELECT * FROM …`)
#   * view_key / updated_by (added by the views + admin feature)
# On fresh installs and reruns these are no-ops.
_config_cols = {f.name for f in spark.table(config_fqn).schema.fields}
if "source_query" not in _config_cols:
    spark.sql(f"ALTER TABLE {config_fqn} ADD COLUMNS (source_query STRING)")
    if "source_fqn" in _config_cols:
        spark.sql(
            f"UPDATE {config_fqn} "
            "SET source_query = concat('SELECT * FROM ', source_fqn) "
            "WHERE source_query IS NULL AND source_fqn IS NOT NULL"
        )
    print("added report_config column: source_query (backfilled from source_fqn)")
for _newcol in ("view_key", "updated_by"):
    if _newcol not in _config_cols:
        spark.sql(f"ALTER TABLE {config_fqn} ADD COLUMNS ({_newcol} STRING)")
        print(f"added report_config column: {_newcol}")
# Volume reports (kind == 'volume', volume_root = pinned /Volumes root). Existing
# rows are query reports, so default kind to 'query' where NULL. source_query is
# already nullable (STRING, no constraint) so volume rows can carry NULL there.
if "kind" not in _config_cols:
    spark.sql(f"ALTER TABLE {config_fqn} ADD COLUMNS (kind STRING)")
    spark.sql(f"UPDATE {config_fqn} SET kind = 'query' WHERE kind IS NULL")
    print("added report_config column: kind (existing rows defaulted to 'query')")
if "volume_root" not in _config_cols:
    spark.sql(f"ALTER TABLE {config_fqn} ADD COLUMNS (volume_root STRING)")
    print("added report_config column: volume_root")

# Seed a default view for report #1. `view_key` and `download_group` come from
# bundle job parameters and must name Databricks groups in the target workspace.
default_view_key = view_group
spark.sql(
    f"""
    MERGE INTO {view_fqn} t
    USING (SELECT '{default_view_key}' AS view_key) s ON t.view_key = s.view_key
    WHEN NOT MATCHED THEN INSERT (view_key, title, display_order, enabled, updated_at, updated_by)
    VALUES ('{default_view_key}', 'Daily Metrics', 1, true, current_timestamp(), 'seed')
    """
)

# Report #1 config. Keep this shape aligned with app.reports.parse_report_config:
# a full SELECT (source_query) + 4 display columns + configured filters. Columns
# could be omitted to show every column the query returns; they are configured
# here to fix labels/formats.
columns_json = json.dumps(
    [
        {"name": "metric_name", "label": "Metric", "format": "text"},
        {"name": "value_cy", "label": "2026", "format": "int"},
        {"name": "value_py", "label": "2025", "format": "int"},
        {"name": "pct_change", "label": "% Change", "format": "pct"},
    ]
)
filters_json = json.dumps(
    [
        {"field": "report_date", "label": "Report date"},
        {"field": "channel", "label": "Channel"},
    ]
)

seed_schema = StructType(
    [
        StructField("report_id", StringType(), False),
        StructField("title", StringType(), False),
        StructField("source_query", StringType(), False),
        StructField("date_field", StringType(), True),
        StructField("columns_json", StringType(), False),
        StructField("filters_json", StringType(), False),
        StructField("order_by", StringType(), True),
        StructField("display_order", IntegerType(), False),
        StructField("enabled", BooleanType(), False),
        StructField("download_group", StringType(), True),
        StructField("view_key", StringType(), True),
        StructField("kind", StringType(), True),
        StructField("volume_root", StringType(), True),
    ]
)

seed_rows = [
    {
        "report_id": "daily_metrics",
        "title": "Daily Metrics Overview",
        "source_query": f"SELECT * FROM {schema_fqn}.daily_metrics",
        "date_field": None,  # retained schema column; dates are ordinary filters
        "columns_json": columns_json,
        "filters_json": filters_json,
        "order_by": "sort_order",
        "display_order": 1,
        "enabled": True,
        "download_group": download_group,
        "view_key": default_view_key,  # the default view's Databricks group
        "kind": "query",  # a query report (volume reports set kind='volume')
        "volume_root": None,
    }
]

# updated_at is stamped at merge time (not in the static row) so every upsert re-stamps it;
# updated_by records the seed source (the admin console stamps the editor's email).
src_df = (
    spark.createDataFrame(seed_rows, seed_schema)
    .withColumn("updated_at", F.current_timestamp())
    .withColumn("updated_by", F.lit("seed"))
)

(
    DeltaTable.forName(spark, config_fqn)
    .alias("t")
    .merge(src_df.alias("s"), "t.report_id = s.report_id")
    .whenMatchedUpdateAll()
    .whenNotMatchedInsertAll()
    .execute()
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7b. Demo VOLUME report (browse + gated download of a UC volume)
# MAGIC
# MAGIC Creates a managed volume `sample_docs`, drops a couple of sample files (incl. a
# MAGIC subfolder to show traversal), seeds a `kind='volume'` report pointing at it, and
# MAGIC grants the app groups READ VOLUME (browsing + download run OBO as the user).
# MAGIC Best-effort: a write/permission hiccup here won't fail the whole seed.

# COMMAND ----------

sample_vol_fqn = f"{schema_fqn}.sample_docs"
sample_vol_path = f"/Volumes/{catalog}/{schema}/sample_docs"
try:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {sample_vol_fqn}")
    os.makedirs(f"{sample_vol_path}/reports", exist_ok=True)
    _demo_files = {
        f"{sample_vol_path}/README.txt": "Sample documents volume for the Download Hub demo.\n",
        f"{sample_vol_path}/overview.csv": "metric,value\nvisits,1200000\norders,72000\n",
        f"{sample_vol_path}/reports/q1_summary.txt": "Q1 summary placeholder.\n",
    }
    for _fp, _content in _demo_files.items():
        with open(_fp, "w") as _fh:
            _fh.write(_content)
    print(f"seeded demo volume {sample_vol_fqn} with {len(_demo_files)} files")

    # READ VOLUME lets the app (as the signed-in user, OBO) list + download files.
    for _grp in (default_view_key, download_group):
        spark.sql(f"GRANT READ VOLUME ON VOLUME {sample_vol_fqn} TO `{_grp}`")
    print(f"granted READ VOLUME on {sample_vol_fqn} to view + download groups")

    # Seed the volume report row (kind='volume'; source_query/date/columns/filters
    # are NULL/empty for a volume report). Explicit-column MERGE since its shape
    # differs from the query-report seed schema above.
    spark.sql(
        f"""
        MERGE INTO {config_fqn} t
        USING (SELECT 'sample_docs' AS report_id) s ON t.report_id = s.report_id
        WHEN NOT MATCHED THEN INSERT
          (report_id, title, source_query, date_field, columns_json, filters_json,
           order_by, display_order, enabled, download_group, view_key, kind,
           volume_root, updated_at, updated_by)
        VALUES
          ('sample_docs', 'Sample Documents', NULL, NULL, NULL, NULL, NULL, 2, true,
           '{download_group}', '{default_view_key}', 'volume', '{sample_vol_path}',
           current_timestamp(), 'seed')
        """
    )
    print(f"seeded volume report 'sample_docs' -> {sample_vol_path}")
except Exception as _exc:  # noqa: BLE001 - demo seed is best-effort
    print(f"demo volume seed skipped ({type(_exc).__name__}: {_exc})")

# Exports spill volume — over-cap query exports are written and read by the app
# service principal. Set APP_EXPORT_VOLUME to this path and grant only the app SP
# USE CATALOG, USE SCHEMA, READ VOLUME, and WRITE VOLUME (see resources/grants.sql).
# Best-effort so a permission hiccup won't fail the seed.
exports_vol_fqn = f"{schema_fqn}.exports"
try:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {exports_vol_fqn}")
    print(
        f"created private exports volume {exports_vol_fqn}; grant the app SP "
        f"READ/WRITE VOLUME, then set "
        f"APP_EXPORT_VOLUME=/Volumes/{catalog}/{schema}/exports to enable over-cap spill"
    )
except Exception as _exc:  # noqa: BLE001 - best-effort
    print(f"exports volume seed skipped ({type(_exc).__name__}: {_exc})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Verify the registry

# COMMAND ----------

config_count = spark.table(config_fqn).count()
print(f"{config_fqn} row count: {config_count}")
spark.table(config_fqn).select("report_id", "title").show(truncate=False)
