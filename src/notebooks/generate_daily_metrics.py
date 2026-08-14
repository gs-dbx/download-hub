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

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

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
      report_title STRING
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
to_add = [(c, "STRING") for c in ("report_id", "report_title") if c not in existing]
if to_add:
    cols = ", ".join(f"{name} {typ}" for name, typ in to_add)
    spark.sql(f"ALTER TABLE {audit_fqn} ADD COLUMNS ({cols})")
    print(f"added audit columns: {[c for c, _ in to_add]}")
else:
    print("audit columns report_id/report_title already present")

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
# MAGIC string-escaping risk) and `updated_at` is re-stamped on every upsert. `source_fqn` is
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

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {config_fqn} (
      report_id      STRING,
      title          STRING,
      source_fqn     STRING,
      date_field     STRING,
      columns_json   STRING,
      filters_json   STRING,
      order_by       STRING,
      display_order  INT,
      enabled        BOOLEAN,
      download_group STRING,
      updated_at     TIMESTAMP
    ) USING DELTA
    """
)

# Report #1 config. Keep this shape aligned with app.reports.parse_report_config:
# 4 columns (metric_name/value_cy/value_py/pct_change) + 1 channel filter.
columns_json = json.dumps(
    [
        {"name": "metric_name", "label": "Metric", "format": "text"},
        {"name": "value_cy", "label": "2026", "format": "int"},
        {"name": "value_py", "label": "2025", "format": "int"},
        {"name": "pct_change", "label": "% Change", "format": "pct"},
    ]
)
filters_json = json.dumps([{"field": "channel", "label": "CHANNEL"}])

seed_schema = StructType(
    [
        StructField("report_id", StringType(), False),
        StructField("title", StringType(), False),
        StructField("source_fqn", StringType(), False),
        StructField("date_field", StringType(), False),
        StructField("columns_json", StringType(), False),
        StructField("filters_json", StringType(), False),
        StructField("order_by", StringType(), True),
        StructField("display_order", IntegerType(), False),
        StructField("enabled", BooleanType(), False),
        StructField("download_group", StringType(), True),
    ]
)

seed_rows = [
    {
        "report_id": "daily_metrics",
        "title": "Daily Metrics Overview",
        "source_fqn": f"{schema_fqn}.daily_metrics",
        "date_field": "report_date",
        "columns_json": columns_json,
        "filters_json": filters_json,
        "order_by": "sort_order",
        "display_order": 1,
        "enabled": True,
        "download_group": None,  # RESERVED — uses the global download group.
    }
]

# updated_at is stamped at merge time (not in the static row) so every upsert re-stamps it.
src_df = spark.createDataFrame(seed_rows, seed_schema).withColumn(
    "updated_at", F.current_timestamp()
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
# MAGIC ## 8. Verify the registry

# COMMAND ----------

config_count = spark.table(config_fqn).count()
print(f"{config_fqn} row count: {config_count}")
spark.table(config_fqn).select("report_id", "title").show(truncate=False)
