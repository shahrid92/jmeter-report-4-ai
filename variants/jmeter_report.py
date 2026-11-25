#!/usr/bin/env python3
"""
jmeter_report.py
================
One-file JMeter results pipeline: JTL -> Parquet -> DuckDB -> PNG charts.

It accepts any JMeter CSV JTL (column order/casing does not matter), keeps the
raw values as text while loading so unusual response codes never break the
import, normalises the columns, writes a Parquet copy, loads a DuckDB table,
and renders the charts that the JMeter HTML dashboard produces.

Usage
-----
    python jmeter_report.py all --jtl results.jtl --db jmeter.duckdb \
        --parquet jmeter_samples.parquet --out-dir charts
    python jmeter_report.py ingest --jtl results.jtl
    python jmeter_report.py stats
    python jmeter_report.py charts
    python jmeter_report.py report --md jmeter_report.md
    python jmeter_report.py selftest

Notes
-----
* Only CSV JTLs are supported. Configure JMeter with
  ``jmeter.save.saveservice.output_format=csv``.
* Transaction controllers are detected from the "Number of samples in
  transaction ..." response message. If that is unavailable, labels without a
  hyphen are treated as transactions.
* Chart buckets default to 60 s, matching JMeter's report generator
  (``granularity = 60000`` in ``content/js/graph.js``).
* Unit tests live in ``test_jmeter_report.py``; the ``selftest`` command
  delegates to that module.
"""

# ===========================================================================
# TABLE OF CONTENTS - debugging index
# ===========================================================================
# Keep this list in sync when functions are added or renamed. Line numbers are
# intentionally omitted (they go stale immediately); for current line numbers:
#     grep -nE "^class |^def |^    def " jmeter_report.py
#
# CONFIGURATION (top of file)
#   COLUMN_ALIASES ................ canonical column -> accepted JTL aliases
#   REQUIRED ...................... timestamp, label, elapsed
#   REPORT_TEMPLATE ............... Markdown skeleton filled by the renderer
#   CHART_ORDER ................... JMeter dashboard chart order
#   DEFAULT_* / ANOMALY_* ......... report defaults and detection thresholds
#
# 1. SQL HELPERS
#   _qident ....................... quote a SQL identifier
#   _qstr ......................... quote a SQL string literal
#   _norm ......................... lower-case/depunctuate a column name
#   _bucket_expr .................. FLOOR(timestamp/g)*g bucket SQL
#   _r6_case_sql .................. R-6 percentile CASE expression
#   _r6_curve ..................... vectorised R-6 curve (percentiles 0..100)
#   _resolve_columns .............. map canonical names to actual JTL columns
#
# 2. INGESTION - JTL -> normalised rows -> Parquet + DuckDB
#   _list_jtl_files ............... file or directory -> JTL file list
#   _looks_like_xml ............... reject XML JTLs early
#   _source_sql ................... read_csv_auto(...) source expression
#   _describe_columns ............. read the JTL column list
#   _derived_success .............. derive success from response code
#   _build_normalised_sql ......... canonical 18-column SELECT
#     ref / optional_varchar / optional_bigint   (nested cast helpers)
#   _normalise_timestamp_units .... seconds/microseconds/nanoseconds -> ms
#   _mark_top_level_controllers ... fallback controller detection by label
#   _create_views ................. jmeter_transactions/requests/successful
#   ingest ........................ main ingestion entry point
#
# 3. STATISTICS
#   build_statistics .............. R-6 stats table + optional CSV/Parquet export
#
# 4. PLOT HELPERS
#   _fetch ........................ SQL -> pandas DataFrame
#   _localise ..................... UTC epoch -> local display time
#   _finish ....................... titles, grid, legend, save/close
#   _line_chart ................... line/point chart
#   _scatter_chart ................ scatter chart
#   _bar_chart .................... simple bar chart
#   _grouped_bar_chart ............ grouped bars (response distribution)
#   _versus_request_df ............ JMeter Vs-Request 1 s end-time aggregation
#
# 5. CHART BUILDERS - one per dashboard chart, plus the driver
#   chart_response_time_over_time
#   chart_response_time_percentiles_over_time
#   chart_active_threads_over_time
#   chart_bytes_throughput_over_time
#   chart_latencies_over_time
#   chart_connect_time_over_time
#   chart_hits_per_second
#   chart_codes_per_second
#   chart_transactions_per_second
#   chart_total_tps
#   chart_response_time_percentiles
#   chart_response_time_distribution
#   chart_time_vs_threads
#   chart_response_time_vs_request
#   chart_latencies_vs_request
#   chart_synthetic_distribution
#   generate_charts ............... run all 16 builders, isolate failures
#
# 6. MARKDOWN ANOMALY REPORT
#   6a. TYPES AND FORMATTING
#       Finding ................... one evidence row (no causal claims)
#       ReportData ................ everything collected from DuckDB
#       _num / _signed_pct ........ number and percentage formatting
#       _fmt_time ................. local HH:MM:SS or date+time
#       _md_cell / _md_table ...... Markdown escaping and table builder
#   6b. DATA COLLECTION
#       _empty_series ............. empty series frame
#       _series_frame ............. bucket/scope/value/n frame
#       _sum_series ............... sum a column per bucket
#       _weighted_series .......... n-weighted average per bucket
#       _label_series ............. per-label series
#       _sampled_source ........... DuckDB reservoir sampling subquery
#       _collect_report_data ...... run all report queries -> ReportData
#   6c. ANOMALY DETECTION
#       _robust_stats ............. median + MAD/IQR/std scale
#       _windows .................. merge consecutive flagged buckets
#       _change_severity .......... High/Medium/Low from change and z
#       _detect_series ............ spike/dip detection per series
#       _detect_trends ............ first-third vs last-third shift
#       _detect_errors ............ failed-request bursts
#       _detect_label_outliers .... label p95/error rate vs TOTAL
#       _load_relation_rows ....... low/high request-rate medians
#       _detect_load_relation ..... response/latency vs request rate
#       _thread_relation .......... low/high thread-level averages
#       _detect_thread_relation ... response time vs threads
#       _detect_distribution_shift  first half vs second half
#       _detect_synthetic ......... share above 1500 ms
#       _detect_findings .......... run every detector -> findings
#       _finding_sort_key ......... severity then magnitude ordering
#   6d. RENDERING
#       _render_summary ........... executive summary + top signals
#       _render_overview .......... run overview table
#       _render_stats ............. per-transaction statistics table
#       _render_anomalies ......... coverage table + windows by chart
#       _render_distribution ...... load/distribution summary tables
#       _render_data_quality ...... missing columns, sampling, zero series
#       _render_next_evidence ..... evidence-collection windows
#       _render_report ............ fill REPORT_TEMPLATE
#       generate_markdown ......... public entry point -> one .md file
#
# 7. SELF-TESTS (separate file; see test_jmeter_report.py)
#   run_selftests ................. delegates to test_jmeter_report.py
#   test_jmeter_report.py ......... 29 unittest cases + TOC coverage guard
#
# 8. CLI
#   build_parser .................. argparse definition
#   main .......................... dispatch all/ingest/stats/charts/report
# ===========================================================================

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TABLE = "jmeter_samples"
DEFAULT_GRANULARITY_MS = 60_000
VERSUS_REQUEST_GRANULARITY_MS = 1_000
LOCAL_TZ = datetime.now().astimezone().tzinfo

# --- Markdown anomaly report ------------------------------------------------
DEFAULT_SAMPLE_ROWS = 200_000
DEFAULT_TOP_N = 25
ANOMALY_Z = 3.5             # robust z-score threshold (median/MAD)
ANOMALY_MIN_REL = 0.20      # ignore changes smaller than 20% of baseline
RAMP_GUARD_FRACTION = 0.10  # ignore dips in the first/last 10% of buckets
REPORT_SEED = 42

OPTIONAL_COLUMNS = (
    "response_code", "response_message", "thread_name", "failure_message",
    "bytes", "sent_bytes", "all_threads", "latency", "connect",
)

REPORT_TEMPLATE = """# JMeter Performance Report

- **Generated:** {{GENERATED}}
- **Source:** {{SOURCE}}
- **Run window:** {{WINDOW}}
- **Rows:** {{ROWS}} ({{CONTROLLERS}} transaction samples, {{REQUESTS}} request samples)
- **Bucket size:** {{GRANULARITY}}
- **Downsampling:** {{SAMPLING}}

> **Scope:** This report lists observations, statistical thresholds, and evidence windows only. It does not draw causal conclusions. Pair the windows with server, database, network, and JMeter logs.

## 1. Executive summary

{{SUMMARY}}

## 2. Run overview

{{OVERVIEW}}

## 3. Per-transaction statistics

{{STATS}}

## 4. Anomalies by dashboard chart

{{ANOMALIES}}

## 5. Distribution and load relationships

{{DISTRIBUTION}}

## 6. Data quality and sampling

{{DATA_QUALITY}}

## 7. Suggested evidence to collect next

{{NEXT_EVIDENCE}}

---
*Generated by `jmeter_report.py`. Section 3 percentiles use R-6 (JMeter-compatible); per-bucket percentiles in section 4 use DuckDB `quantile_cont` for scan speed. Values are rounded for readability.*
"""

CHART_ORDER = [
    "Response Time Over Time",
    "Response Time Percentiles Over Time",
    "Active Threads",
    "Bytes Throughput",
    "Latencies",
    "Connect Time",
    "Hits Per Second",
    "Codes Per Second",
    "Transactions Per Second",
    "Total Transactions Per Second",
    "Response Time Percentiles",
    "Response Time Distribution",
    "Time VS Threads",
    "Response Time Vs Request",
    "Latencies Vs Request",
    "Synthetic Response Times Distribution",
]

SEVERITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


# canonical name -> accepted normalised column names
COLUMN_ALIASES = {
    "timestamp": ["timestamp", "timestampms", "time", "starttime"],
    "elapsed": ["elapsed", "elapsedtime", "responsetime", "duration"],
    "label": ["label", "samplename", "name", "transaction"],
    "response_code": ["responsecode", "responsecode", "code", "httpcode", "status"],
    "response_message": ["responsemessage", "message", "statusmessage"],
    "thread_name": ["threadname", "thread", "threadgroup"],
    "data_type": ["datatype"],
    "success": ["success", "successful", "ok"],
    "failure_message": ["failuremessage", "errormessage", "error"],
    "bytes": ["bytes", "bytestransferred", "receivedbytes", "size"],
    "sent_bytes": ["sentbytes", "sentbytestransferred"],
    "grp_threads": ["grpthreads", "groupthreads"],
    "all_threads": ["allthreads", "activethreads", "threads"],
    "url": ["url", "requesturl", "sampleurl"],
    "latency": ["latency", "latencytime"],
    "idle_time": ["idletime", "idle"],
    "connect": ["connect", "connecttime"],
}

REQUIRED = ("timestamp", "label", "elapsed")


# ---------------------------------------------------------------------------
# Small SQL helpers
# ---------------------------------------------------------------------------


def _qident(name: str) -> str:
    """Quote a SQL identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def _qstr(value) -> str:
    """Quote a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _norm(name: str) -> str:
    """Normalise a column name for alias matching."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _bucket_expr(granularity_ms: int) -> str:
    """Epoch-millisecond bucket expression, aligned like JMeter's report."""
    return (
        f"CAST(FLOOR(timestamp / {granularity_ms}.0) * {granularity_ms} AS BIGINT)"
    )


def _r6_case_sql(alias: str, h_col: str) -> str:
    """SQL for the R-6 (Weibull) percentile, matching Commons-Math/JMeter.

    Requires ``r`` (window-ranked rows with ``rn``/``elapsed``) and ``c``
    (per-group ``n`` and ``h_*`` columns) in the surrounding query.
    """
    return (
        "CASE\n"
        f"            WHEN {h_col} <= 1 THEN MIN(CASE WHEN r.rn = 1 THEN r.elapsed END)\n"
        f"            WHEN {h_col} >= c.n THEN MIN(CASE WHEN r.rn = c.n THEN r.elapsed END)\n"
        "            ELSE\n"
        f"                MIN(CASE WHEN r.rn = FLOOR({h_col}) THEN r.elapsed END)\n"
        f"                + ({h_col} - FLOOR({h_col})) *\n"
        f"                  (MIN(CASE WHEN r.rn = FLOOR({h_col}) + 1 THEN r.elapsed END)\n"
        f"                   - MIN(CASE WHEN r.rn = FLOOR({h_col}) THEN r.elapsed END))\n"
        "        END"
        f" AS {alias}"
    )


def _r6_curve(values, percentiles) -> np.ndarray:
    """Vectorised R-6 percentile curve for percentiles 0..100."""
    ordered = np.sort(np.asarray(values, dtype=float))
    n = len(ordered)
    if n == 0:
        return np.array([], dtype=float)
    h = np.clip((n + 1) * np.asarray(percentiles, dtype=float) / 100.0, 1, n)
    lower = np.floor(h).astype(int)
    fraction = h - lower
    lo_idx = np.clip(lower - 1, 0, n - 1)
    hi_idx = np.clip(lower, 0, n - 1)
    return ordered[lo_idx] + fraction * (ordered[hi_idx] - ordered[lo_idx])


def _resolve_columns(actual_columns):
    """Map canonical column names to the actual columns in a JTL."""
    by_norm = {_norm(c): c for c in actual_columns}
    mapping = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in by_norm:
                mapping[canonical] = by_norm[alias]
                break
    return mapping


# ---------------------------------------------------------------------------
# Ingestion: JTL -> normalised table -> Parquet -> DuckDB
# ---------------------------------------------------------------------------


def _list_jtl_files(path) -> list[str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JTL input not found: {p}")
    if p.is_dir():
        files = sorted(list(p.glob("*.jtl")) + list(p.glob("*.csv")))
        if not files:
            raise FileNotFoundError(f"No .jtl or .csv files found in directory: {p}")
        return [str(f) for f in files]
    return [str(p)]


def _looks_like_xml(path) -> bool:
    with open(path, "rb") as handle:
        head = handle.read(512).lstrip().lower()
    return head.startswith(b"<?xml") or head.startswith(b"<testresults")


def _source_sql(files) -> str:
    paths = ", ".join(_qstr(Path(f).resolve().as_posix()) for f in files)
    # With all_varchar there is no type inference; the header defines the
    # columns. For a single large JTL, sample_size=-1 forces a full-file schema
    # pass twice (DESCRIBE + CREATE) and roughly triples ingest time. Keep -1
    # only for multi-file directory unions where schemas may differ.
    sample_size = -1 if len(files) > 1 else 20_480
    return (
        f"read_csv_auto([{paths}], header = true, all_varchar = true, "
        f"sample_size = {sample_size}, union_by_name = true)"
    )


def _describe_columns(con, source_sql: str) -> list[str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM {source_sql}").fetchall()
    return [row[0] for row in rows]


def _derived_success(code_ref: str | None) -> str:
    """Infer success from the HTTP status when the success column is absent."""
    if not code_ref:
        return "TRUE"
    return (
        "CASE\n"
        f"            WHEN {code_ref} IS NULL THEN TRUE\n"
        f"            WHEN regexp_matches(CAST({code_ref} AS VARCHAR), '^[23][0-9][0-9]$') THEN TRUE\n"
        "            ELSE FALSE\n"
        "        END"
    )


def _build_normalised_sql(source_sql: str, actual_columns) -> str:
    mapping = _resolve_columns(actual_columns)
    missing = [name for name in REQUIRED if name not in mapping]
    if missing:
        raise ValueError(
            "JTL is missing required column(s): "
            + ", ".join(missing)
            + f". Found columns: {actual_columns}"
        )

    def ref(canonical):
        column = mapping.get(canonical)
        return _qident(column) if column else None

    label_ref = ref("label")
    code_ref = ref("response_code")
    message_ref = ref("response_message")
    success_ref = ref("success")

    def optional_varchar(canonical):
        column = ref(canonical)
        return f"CAST({column} AS VARCHAR)" if column else "CAST(NULL AS VARCHAR)"

    def optional_bigint(canonical):
        column = ref(canonical)
        return f"TRY_CAST({column} AS BIGINT)" if column else "CAST(NULL AS BIGINT)"

    ts_ref = ref("timestamp")
    timestamp_expr = (
        "COALESCE("
        f"TRY_CAST(TRY_CAST({ts_ref} AS DOUBLE) AS BIGINT), "
        f"EPOCH_MS(TRY_CAST({ts_ref} AS TIMESTAMP))"
        ")"
    )

    elapsed_expr = f"TRY_CAST({ref('elapsed')} AS BIGINT)"

    if success_ref:
        success_expr = (
            "CASE\n"
            f"            WHEN lower(trim({success_ref})) IN ('true','1','yes','y','t') THEN TRUE\n"
            f"            WHEN lower(trim({success_ref})) IN ('false','0','no','n','f') THEN FALSE\n"
            f"            WHEN trim({success_ref}) = '' THEN {_derived_success(code_ref)}\n"
            "            ELSE NULL\n"
            "        END"
        )
    else:
        success_expr = _derived_success(code_ref)

    message_expr = message_ref or "CAST(NULL AS VARCHAR)"
    controller_expr = (
        "CASE\n"
        f"            WHEN {message_expr} LIKE 'Number of samples in transaction%' THEN TRUE\n"
        "            ELSE FALSE\n"
        "        END"
    )

    columns = [
        ("timestamp", timestamp_expr),
        ("elapsed", elapsed_expr),
        ("label", f"CAST({label_ref} AS VARCHAR)"),
        ("response_code", optional_varchar("response_code")),
        ("response_message", optional_varchar("response_message")),
        ("thread_name", optional_varchar("thread_name")),
        ("data_type", optional_varchar("data_type")),
        ("success", success_expr),
        ("failure_message", optional_varchar("failure_message")),
        ("bytes", optional_bigint("bytes")),
        ("sent_bytes", optional_bigint("sent_bytes")),
        ("grp_threads", optional_bigint("grp_threads")),
        ("all_threads", optional_bigint("all_threads")),
        ("url", optional_varchar("url")),
        ("latency", optional_bigint("latency")),
        ("idle_time", optional_bigint("idle_time")),
        ("connect", optional_bigint("connect")),
        ("is_controller", controller_expr),
    ]
    select_list = ",\n        ".join(f"{expr} AS {name}" for name, expr in columns)
    return (
        "SELECT * FROM (\n"
        "    SELECT\n"
        f"        {select_list}\n"
        f"    FROM {source_sql}\n"
        ") WHERE timestamp IS NOT NULL"
    )


def _normalise_timestamp_units(con, table: str) -> str | None:
    """Scale timestamps to milliseconds when the JTL used s/us/ns."""
    max_ts = con.execute(f"SELECT MAX(timestamp) FROM {table}").fetchone()[0]
    if max_ts is None:
        return None
    if 0 < max_ts < 100_000_000_000:  # seconds
        con.execute(f"UPDATE {table} SET timestamp = timestamp * 1000")
        return "seconds->milliseconds"
    if max_ts >= 100_000_000_000_000_000:  # nanoseconds
        con.execute(f"UPDATE {table} SET timestamp = timestamp // 1000000")
        return "nanoseconds->milliseconds"
    if max_ts >= 100_000_000_000_000:  # microseconds
        con.execute(f"UPDATE {table} SET timestamp = timestamp // 1000")
        return "microseconds->milliseconds"
    return None


def _mark_top_level_controllers(con, table: str) -> None:
    """Fallback controller detection when no transaction messages exist.

    JMeter appends ``-<n>`` to nested sample labels, so a label without a
    hyphen is treated as a top-level transaction (including single-sampler
    transactions such as ``Feed`` and ``search``). If the JTL contains no
    hyphenated labels at all, there is no hierarchy to exploit: leave
    everything as a request so hits/statistics still work.
    """
    child_rows = con.execute(
        f"SELECT COUNT(*) FROM {table} WHERE label LIKE '%-%'"
    ).fetchone()[0]
    if child_rows == 0:
        return
    con.execute(
        f"UPDATE {table} SET is_controller = TRUE WHERE label NOT LIKE '%-%'"
    )


def _create_views(con, table: str) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE VIEW jmeter_transactions AS
        SELECT * FROM {table}
        WHERE is_controller
           OR NOT EXISTS (SELECT 1 FROM {table} WHERE is_controller)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW jmeter_requests AS
        SELECT * FROM {table}
        WHERE NOT is_controller
           OR NOT EXISTS (SELECT 1 FROM {table} WHERE is_controller)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW jmeter_successful_requests AS
        SELECT * FROM jmeter_requests WHERE success IS TRUE
        """
    )


def ingest(jtl_path, db_path, parquet_path, table: str = DEFAULT_TABLE) -> dict:
    """Load one or more JTL files into DuckDB and write a Parquet copy."""
    files = _list_jtl_files(jtl_path)
    for file in files:
        if _looks_like_xml(file):
            raise ValueError(
                f"XML JTL is not supported: {file}. "
                "Save results as CSV (jmeter.save.saveservice.output_format=csv)."
            )

    db_path = Path(db_path)
    parquet_path = Path(parquet_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    source_sql = _source_sql(files)
    con = duckdb.connect(str(db_path))
    try:
        actual_columns = _describe_columns(con, source_sql)
        normalised_sql = _build_normalised_sql(source_sql, actual_columns)
        con.execute(f"CREATE OR REPLACE TABLE {table} AS {normalised_sql}")

        controller_rows = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE is_controller"
        ).fetchone()[0]
        if controller_rows == 0:
            _mark_top_level_controllers(con, table)

        scaled = _normalise_timestamp_units(con, table)

        rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        controllers = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE is_controller"
        ).fetchone()[0]
        requests = rows - controllers

        _create_views(con, table)

        con.execute(
            f"COPY {table} TO {_qstr(parquet_path.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE jmeter_metadata AS
            SELECT
                {_qstr(', '.join(Path(f).name for f in files))} AS source,
                {rows} AS rows,
                {controllers} AS controller_rows,
                {requests} AS request_rows,
                {_qstr(scaled or "already milliseconds")} AS timestamp_scaling,
                current_timestamp AS ingested_at
            """
        )
        return {
            "rows": rows,
            "controllers": controllers,
            "requests": requests,
            "parquet": str(parquet_path),
            "source": ", ".join(files),
            "scaled": scaled,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def build_statistics(con, out_dir=None, include_sub_samples: bool = False,
                     write_files: bool = True, verbose: bool = True):
    """Build the JMeter statistics table and (optionally) export CSV + Parquet."""
    relation = "jmeter_samples" if include_sub_samples else "jmeter_transactions"
    percentile_cases = ",\n            ".join(
        [
            _r6_case_sql("median", "c.h_median"),
            _r6_case_sql("p90", "c.h_p90"),
            _r6_case_sql("p95", "c.h_p95"),
            _r6_case_sql("p99", "c.h_p99"),
        ]
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE jmeter_statistics AS
        WITH ranked AS (
            SELECT
                group_label,
                elapsed,
                ROW_NUMBER() OVER (PARTITION BY group_label ORDER BY elapsed) AS rn,
                COUNT(*) OVER (PARTITION BY group_label) AS n
            FROM (
                SELECT label AS group_label, elapsed
                FROM {relation}
                WHERE elapsed IS NOT NULL
                UNION ALL
                SELECT 'TOTAL', elapsed
                FROM {relation}
                WHERE elapsed IS NOT NULL
            )
        ),
        base AS (
            SELECT
                CASE WHEN GROUPING(label) = 1 THEN 'TOTAL' ELSE label END AS group_label,
                COUNT(*) AS samples,
                COUNT(*) FILTER (WHERE success IS FALSE) AS failures,
                AVG(elapsed) AS average,
                MIN(elapsed) AS min_elapsed,
                MAX(elapsed) AS max_elapsed,
                COALESCE(SUM(bytes), 0) AS total_bytes,
                COALESCE(SUM(sent_bytes), 0) AS total_sent_bytes,
                MIN(timestamp) AS first_timestamp,
                MAX(timestamp + elapsed) AS last_timestamp
            FROM {relation}
            GROUP BY GROUPING SETS ((label), ())
        ),
        calc AS (
            SELECT
                group_label,
                n,
                (n + 1) * 0.50 AS h_median,
                (n + 1) * 0.90 AS h_p90,
                (n + 1) * 0.95 AS h_p95,
                (n + 1) * 0.99 AS h_p99
            FROM ranked
            GROUP BY group_label, n
        ),
        pct AS (
            SELECT
                c.group_label,
                {percentile_cases}
            FROM calc c
            JOIN ranked r ON r.group_label = c.group_label
            GROUP BY c.group_label, c.n, c.h_median, c.h_p90, c.h_p95, c.h_p99
        ),
        durations AS (
            SELECT
                b.*,
                p.median,
                p.p90,
                p.p95,
                p.p99,
                NULLIF(b.last_timestamp - b.first_timestamp, 0) / 1000.0
                    AS duration_seconds
            FROM base b
            LEFT JOIN pct p ON p.group_label = b.group_label
        )
        SELECT
            group_label AS label,
            samples,
            failures,
            ROUND(failures * 100.0 / NULLIF(samples, 0), 2) AS error_pct,
            ROUND(average, 2) AS average,
            ROUND(median, 2) AS median,
            ROUND(p90, 2) AS p90,
            ROUND(p95, 2) AS p95,
            ROUND(p99, 2) AS p99,
            min_elapsed,
            max_elapsed,
            ROUND(samples / duration_seconds, 2) AS throughput,
            ROUND(total_bytes / 1024.0 / duration_seconds, 2) AS received_kb_per_sec,
            ROUND(total_sent_bytes / 1024.0 / duration_seconds, 2) AS sent_kb_per_sec
        FROM durations
        """
    )

    order = "ORDER BY CASE WHEN label = 'TOTAL' THEN 1 ELSE 0 END, label"
    df = con.execute(f"SELECT * FROM jmeter_statistics {order}").df()

    if write_files:
        if out_dir is None:
            raise ValueError("out_dir is required when write_files=True")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = out_dir / "statistics.parquet"
        csv_path = out_dir / "statistics.csv"
        con.execute(
            f"COPY (SELECT * FROM jmeter_statistics {order}) "
            f"TO {_qstr(parquet_path.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.execute(
            f"COPY (SELECT * FROM jmeter_statistics {order}) "
            f"TO {_qstr(csv_path.resolve().as_posix())} "
            "(FORMAT CSV, HEADER, DELIMITER ',')"
        )
        if verbose:
            print("\n===== JMETER STATISTICS =====")
            print(df.to_string(index=False))
            print(f"\nWrote {csv_path} and {parquet_path}")
    elif verbose:
        print("\n===== JMETER STATISTICS =====")
        print(df.to_string(index=False))

    return df


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------


def _fetch(con, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def _localise(df: pd.DataFrame, column: str = "time") -> pd.DataFrame:
    df = df.copy()
    df[column] = (
        pd.to_datetime(df[column], utc=True)
        .dt.tz_convert(LOCAL_TZ)
        .dt.tz_localize(None)
    )
    return df


def _finish(fig, ax, title, xlabel, ylabel, out_path, legend_title=None):
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(
            title=legend_title,
            loc="upper left",
            bbox_to_anchor=(1.01, 1),
            frameon=True,
        )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _line_chart(
    df,
    x,
    y,
    series,
    title,
    xlabel,
    ylabel,
    out_path,
    legend_title=None,
    date_axis=True,
    marker=False,
):
    if df.empty:
        print(f"  skip {Path(out_path).name}: no data")
        return None
    if date_axis:
        df = _localise(df, x)
    fig, ax = plt.subplots(figsize=(16, 7))
    plot_kwargs = {"linewidth": 1.6}
    if marker:
        plot_kwargs.update(marker="o", markersize=4)
    if series:
        for name, group in df.groupby(series, sort=True):
            ax.plot(group[x], group[y], label=str(name), **plot_kwargs)
    else:
        ax.plot(df[x], df[y], label=ylabel, **plot_kwargs)
    if date_axis:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.tick_params(axis="x", rotation=45)
    return _finish(
        fig, ax, title, xlabel, ylabel, out_path, legend_title=legend_title
    )


def _scatter_chart(df, x, y, series, title, xlabel, ylabel, out_path, legend_title=None):
    if df.empty:
        print(f"  skip {Path(out_path).name}: no data")
        return None
    fig, ax = plt.subplots(figsize=(16, 7))
    for name, group in df.groupby(series, sort=True):
        ax.scatter(group[x], group[y], label=str(name), s=18, alpha=0.7)
    return _finish(
        fig, ax, title, xlabel, ylabel, out_path, legend_title=legend_title
    )


def _bar_chart(categories, values, title, xlabel, ylabel, out_path):
    fig, ax = plt.subplots(figsize=(16, 7))
    ax.bar([str(c) for c in categories], values, color="#1f77b4")
    ax.tick_params(axis="x", rotation=20)
    return _finish(fig, ax, title, xlabel, ylabel, out_path)


def _grouped_bar_chart(
    df, x, y, series, title, xlabel, ylabel, out_path,
    legend_title=None, bin_width=None,
):
    if df.empty:
        print(f"  skip {Path(out_path).name}: no data")
        return None
    pivot = df.pivot_table(
        index=x, columns=series, values=y, aggfunc="sum", fill_value=0
    )
    x_values = pivot.index.to_numpy()
    columns = list(pivot.columns)
    count = len(columns)
    if bin_width is None:
        unique = np.sort(np.unique(x_values))
        bin_width = float(np.min(np.diff(unique))) if len(unique) > 1 else 1.0
    bar_width = bin_width / max(count, 1)
    offsets = (np.arange(count) - (count - 1) / 2.0) * bar_width

    fig, ax = plt.subplots(figsize=(16, 7))
    for index, column in enumerate(columns):
        ax.bar(
            x_values + offsets[index],
            pivot[column].to_numpy(),
            width=bar_width,
            label=str(column),
        )
    ax.tick_params(axis="x", rotation=45)
    return _finish(
        fig, ax, title, xlabel, ylabel, out_path, legend_title=legend_title
    )


def _versus_request_df(con, value_column: str, value_alias: str) -> pd.DataFrame:
    """JMeter's Response Time/Latency Vs Request aggregation.

    Samples are bucketed by *end* time into 1-second intervals. Every request
    in an interval is counted, then samples are grouped by that count and the
    median of the requested value is taken per success/failure series.
    """
    return _fetch(
        con,
        f"""
        WITH b AS (
            SELECT
                success,
                {value_column} AS value,
                (timestamp + elapsed)
                    - ((timestamp + elapsed) % {VERSUS_REQUEST_GRANULARITY_MS})
                    AS interval
            FROM jmeter_requests
        ),
        counts AS (
            SELECT interval, COUNT(*) AS requests_per_second
            FROM b
            GROUP BY interval
        ),
        joined AS (
            SELECT b.success, b.value, c.requests_per_second
            FROM b
            JOIN counts c USING (interval)
        )
        SELECT
            requests_per_second,
            CASE
                WHEN success IS TRUE THEN 'Successes'
                ELSE 'Failures'
            END AS result,
            MEDIAN(value) AS {value_alias}
        FROM joined
        GROUP BY requests_per_second, result
        ORDER BY requests_per_second, result
        """,
    )


# ---------------------------------------------------------------------------
# Charts (mirrors JMeter's content/js/graph.js titles)
# ---------------------------------------------------------------------------


def chart_response_time_over_time(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    df = _fetch(
        con,
        f"""
        SELECT EPOCH_MS(bucket_ms) AS time, label, AVG(elapsed) AS response_time_ms
        FROM (
            SELECT label, {bucket} AS bucket_ms, elapsed
            FROM {relation}
            WHERE elapsed IS NOT NULL
        )
        GROUP BY bucket_ms, label
        ORDER BY bucket_ms, label
        """,
    )
    return _line_chart(
        df, "time", "response_time_ms", "label",
        "Response Time Over Time", "Time", "Response time (ms)",
        Path(out_dir) / "response_time_over_time.png", "Transaction",
        date_axis=True, marker=True,
    )


def chart_response_time_percentiles_over_time(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    percentile_cases = ",\n                ".join(
        [
            _r6_case_sql("median", "c.h_median"),
            _r6_case_sql("p90", "c.h_p90"),
            _r6_case_sql("p95", "c.h_p95"),
            _r6_case_sql("p99", "c.h_p99"),
        ]
    )
    df = _fetch(
        con,
        f"""
        WITH ranked AS (
            SELECT
                bucket_ms,
                elapsed,
                ROW_NUMBER() OVER (PARTITION BY bucket_ms ORDER BY elapsed) AS rn,
                COUNT(*) OVER (PARTITION BY bucket_ms) AS n
            FROM (
                SELECT {bucket} AS bucket_ms, elapsed
                FROM jmeter_successful_requests
                WHERE elapsed IS NOT NULL
            )
        ),
        calc AS (
            SELECT
                bucket_ms,
                n,
                (n + 1) * 0.50 AS h_median,
                (n + 1) * 0.90 AS h_p90,
                (n + 1) * 0.95 AS h_p95,
                (n + 1) * 0.99 AS h_p99
            FROM ranked
            GROUP BY bucket_ms, n
        ),
        pct AS (
            SELECT
                c.bucket_ms,
                MIN(r.elapsed) AS min_ms,
                {percentile_cases},
                MAX(r.elapsed) AS max_ms
            FROM calc c
            JOIN ranked r ON r.bucket_ms = c.bucket_ms
            GROUP BY c.bucket_ms, c.n, c.h_median, c.h_p90, c.h_p95, c.h_p99
        )
        SELECT
            EPOCH_MS(bucket_ms) AS time,
            min_ms, median, p90, p95, p99, max_ms
        FROM pct
        ORDER BY bucket_ms
        """,
    )
    if df.empty:
        print("  skip response_time_percentiles_over_time.png: no data")
        return None
    long = df.melt(id_vars="time", var_name="percentile", value_name="response_time_ms")
    order = ["min_ms", "median", "p90", "p95", "p99", "max_ms"]
    long["percentile"] = pd.Categorical(long["percentile"], order, ordered=True)
    long = long.sort_values(["percentile", "time"])
    return _line_chart(
        long, "time", "response_time_ms", "percentile",
        "Response Time Percentiles Over Time (successful requests only)",
        "Time", "Response time (ms)",
        Path(out_dir) / "response_time_percentiles_over_time.png", "Percentile",
    )


def chart_active_threads_over_time(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    df = _fetch(
        con,
        f"""
        SELECT EPOCH_MS(bucket_ms) AS time, thread_group, AVG(all_threads) AS threads
        FROM (
            SELECT
                {bucket} AS bucket_ms,
                COALESCE(regexp_replace(thread_name, '-[0-9]+$', ''), 'all') AS thread_group,
                all_threads
            FROM jmeter_samples
            WHERE all_threads IS NOT NULL
        )
        GROUP BY bucket_ms, thread_group
        ORDER BY bucket_ms, thread_group
        """,
    )
    return _line_chart(
        df, "time", "threads", "thread_group",
        "Active Threads Over Time", "Time", "Active threads",
        Path(out_dir) / "active_threads_over_time.png", "Thread group",
    )


def chart_bytes_throughput_over_time(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    seconds = granularity / 1000.0
    df = _fetch(
        con,
        f"""
        SELECT
            EPOCH_MS(bucket_ms) AS time,
            SUM(COALESCE(bytes, 0)) / {seconds} AS "Bytes received/s",
            SUM(COALESCE(sent_bytes, 0)) / {seconds} AS "Bytes sent/s"
        FROM (
            SELECT {bucket} AS bucket_ms, bytes, sent_bytes
            FROM jmeter_requests
        )
        GROUP BY bucket_ms
        ORDER BY bucket_ms
        """,
    )
    if df.empty:
        print("  skip bytes_throughput_over_time.png: no data")
        return None
    long = df.melt(id_vars="time", var_name="series", value_name="bytes_per_sec")
    return _line_chart(
        long, "time", "bytes_per_sec", "series",
        "Bytes Throughput Over Time", "Time", "Bytes per second",
        Path(out_dir) / "bytes_throughput_over_time.png", "Series",
    )


def chart_latencies_over_time(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    df = _fetch(
        con,
        f"""
        SELECT EPOCH_MS(bucket_ms) AS time, label, AVG(latency) AS latency_ms
        FROM (
            SELECT label, {bucket} AS bucket_ms, latency
            FROM {relation}
            WHERE latency IS NOT NULL
        )
        GROUP BY bucket_ms, label
        ORDER BY bucket_ms, label
        """,
    )
    return _line_chart(
        df, "time", "latency_ms", "label",
        "Latencies Over Time", "Time", "Latency (ms)",
        Path(out_dir) / "latencies_over_time.png", "Transaction", marker=True,
    )


def chart_connect_time_over_time(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    df = _fetch(
        con,
        f"""
        SELECT EPOCH_MS(bucket_ms) AS time, label, AVG(connect) AS connect_ms
        FROM (
            SELECT label, {bucket} AS bucket_ms, connect
            FROM {relation}
            WHERE connect IS NOT NULL
        )
        GROUP BY bucket_ms, label
        ORDER BY bucket_ms, label
        """,
    )
    return _line_chart(
        df, "time", "connect_ms", "label",
        "Connect Time Over Time", "Time", "Connect time (ms)",
        Path(out_dir) / "connect_time_over_time.png", "Transaction", marker=True,
    )


def chart_hits_per_second(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    seconds = granularity / 1000.0
    df = _fetch(
        con,
        f"""
        SELECT EPOCH_MS(bucket_ms) AS time, COUNT(*) / {seconds} AS hits_per_second
        FROM (SELECT {bucket} AS bucket_ms FROM jmeter_requests)
        GROUP BY bucket_ms
        ORDER BY bucket_ms
        """,
    )
    return _line_chart(
        df, "time", "hits_per_second", None,
        "Hits Per Second", "Time", "Hits per second",
        Path(out_dir) / "hits_per_second.png", marker=True,
    )


def chart_codes_per_second(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    seconds = granularity / 1000.0
    df = _fetch(
        con,
        f"""
        SELECT
            EPOCH_MS(bucket_ms) AS time,
            COALESCE(response_code, 'unknown') AS response_code,
            COUNT(*) / {seconds} AS per_second
        FROM (
            SELECT {bucket} AS bucket_ms, response_code
            FROM jmeter_requests
        )
        GROUP BY bucket_ms, response_code
        ORDER BY bucket_ms, response_code
        """,
    )
    return _line_chart(
        df, "time", "per_second", "response_code",
        "Codes Per Second", "Time", "Responses per second",
        Path(out_dir) / "codes_per_second.png", "Response code",
    )


def chart_transactions_per_second(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    seconds = granularity / 1000.0
    df = _fetch(
        con,
        f"""
        SELECT EPOCH_MS(bucket_ms) AS time, label, COUNT(*) / {seconds} AS tps
        FROM (
            SELECT label, {bucket} AS bucket_ms
            FROM {relation}
        )
        GROUP BY bucket_ms, label
        ORDER BY bucket_ms, label
        """,
    )
    return _line_chart(
        df, "time", "tps", "label",
        "Transactions Per Second", "Time", "Transactions per second",
        Path(out_dir) / "transactions_per_second.png", "Transaction",
    )


def chart_total_tps(con, out_dir, granularity, relation):
    bucket = _bucket_expr(granularity)
    seconds = granularity / 1000.0
    df = _fetch(
        con,
        f"""
        SELECT
            EPOCH_MS(bucket_ms) AS time,
            SUM(CASE WHEN success IS TRUE THEN 1 ELSE 0 END) / {seconds} AS "Success",
            SUM(CASE WHEN success IS FALSE THEN 1 ELSE 0 END) / {seconds} AS "Failure"
        FROM (
            SELECT {bucket} AS bucket_ms, success
            FROM jmeter_transactions
        )
        GROUP BY bucket_ms
        ORDER BY bucket_ms
        """,
    )
    if df.empty:
        print("  skip total_transactions_per_second.png: no data")
        return None
    long = df.melt(id_vars="time", var_name="series", value_name="tps")
    return _line_chart(
        long, "time", "tps", "series",
        "Total Transactions Per Second", "Time", "Transactions per second",
        Path(out_dir) / "total_transactions_per_second.png", "Result",
    )


def chart_response_time_percentiles(con, out_dir, granularity, relation):
    raw = con.execute(
        f"SELECT label, elapsed FROM {relation} WHERE elapsed IS NOT NULL"
    ).df()
    if raw.empty:
        print("  skip response_time_percentiles.png: no data")
        return None
    percentiles = list(range(101))
    frames = []
    for label, group in raw.groupby("label", sort=True):
        frames.append(
            pd.DataFrame(
                {
                    "label": label,
                    "percentile": percentiles,
                    "response_time_ms": _r6_curve(
                        group["elapsed"].to_numpy(), percentiles
                    ),
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    return _line_chart(
        df, "percentile", "response_time_ms", "label",
        "Response Time Percentiles", "Percentile", "Response time (ms)",
        Path(out_dir) / "response_time_percentiles.png", "Transaction",
        date_axis=False,
    )


def chart_response_time_distribution(con, out_dir, granularity, relation):
    df = _fetch(
        con,
        f"""
        SELECT label, FLOOR(elapsed / 100.0) * 100 AS bin_ms, COUNT(*) AS count
        FROM {relation}
        WHERE elapsed IS NOT NULL
        GROUP BY label, bin_ms
        ORDER BY label, bin_ms
        """,
    )
    return _grouped_bar_chart(
        df, "bin_ms", "count", "label",
        "Response Time Distribution", "Response times in ms",
        "Number of responses",
        Path(out_dir) / "response_time_distribution.png", "Transaction",
        bin_width=100,
    )


def chart_time_vs_threads(con, out_dir, granularity, relation):
    df = _fetch(
        con,
        f"""
        SELECT label, all_threads, AVG(elapsed) AS average_response_time_ms
        FROM {relation}
        WHERE all_threads IS NOT NULL AND elapsed IS NOT NULL
        GROUP BY label, all_threads
        ORDER BY label, all_threads
        """,
    )
    return _line_chart(
        df, "all_threads", "average_response_time_ms", "label",
        "Time VS Threads", "Number of active threads",
        "Average response times in ms",
        Path(out_dir) / "time_vs_threads.png", "Transaction",
        date_axis=False, marker=True,
    )


def chart_response_time_vs_request(con, out_dir, granularity, relation):
    df = _versus_request_df(con, "elapsed", "median_response_time_ms")
    return _scatter_chart(
        df, "requests_per_second", "median_response_time_ms", "result",
        "Response Time Vs Request", "Global number of requests per second",
        "Median Response Time in ms",
        Path(out_dir) / "response_time_vs_request.png", "Result",
    )


def chart_latencies_vs_request(con, out_dir, granularity, relation):
    df = _versus_request_df(con, "latency", "median_latency_ms")
    return _scatter_chart(
        df, "requests_per_second", "median_latency_ms", "result",
        "Latencies Vs Request", "Global number of requests per second",
        "Median Latency in ms",
        Path(out_dir) / "latencies_vs_request.png", "Result",
    )


def chart_synthetic_distribution(con, out_dir, granularity, relation):
    if con.execute("SELECT COUNT(*) FROM jmeter_requests").fetchone()[0] == 0:
        print("  skip synthetic_response_times_distribution.png: no data")
        return None
    df = _fetch(
        con,
        """
        SELECT
            COALESCE(SUM(CASE WHEN elapsed <= 500 THEN 1 ELSE 0 END), 0) AS "<= 500 ms",
            COALESCE(SUM(CASE WHEN elapsed > 500 AND elapsed <= 1500 THEN 1 ELSE 0 END), 0) AS "500-1500 ms",
            COALESCE(SUM(CASE WHEN elapsed > 1500 THEN 1 ELSE 0 END), 0) AS "> 1500 ms",
            COALESCE(SUM(CASE WHEN success IS FALSE THEN 1 ELSE 0 END), 0) AS "Errors"
        FROM jmeter_requests
        """,
    )
    if df.empty:
        print("  skip synthetic_response_times_distribution.png: no data")
        return None
    row = df.iloc[0]
    return _bar_chart(
        row.index.tolist(), row.tolist(),
        "Synthetic Response Times Distribution", "Response time range", "Requests",
        Path(out_dir) / "synthetic_response_times_distribution.png",
    )


CHART_BUILDERS = [
    ("response_time_over_time", chart_response_time_over_time),
    ("response_time_percentiles_over_time", chart_response_time_percentiles_over_time),
    ("active_threads_over_time", chart_active_threads_over_time),
    ("bytes_throughput_over_time", chart_bytes_throughput_over_time),
    ("latencies_over_time", chart_latencies_over_time),
    ("connect_time_over_time", chart_connect_time_over_time),
    ("hits_per_second", chart_hits_per_second),
    ("codes_per_second", chart_codes_per_second),
    ("transactions_per_second", chart_transactions_per_second),
    ("total_transactions_per_second", chart_total_tps),
    ("response_time_percentiles", chart_response_time_percentiles),
    ("response_time_distribution", chart_response_time_distribution),
    ("time_vs_threads", chart_time_vs_threads),
    ("response_time_vs_request", chart_response_time_vs_request),
    ("latencies_vs_request", chart_latencies_vs_request),
    ("synthetic_response_times_distribution", chart_synthetic_distribution),
]


def generate_charts(con, out_dir, granularity=DEFAULT_GRANULARITY_MS,
                    include_sub_samples: bool = False):
    """Render every supported chart. Returns the list of produced PNG paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    relation = "jmeter_samples" if include_sub_samples else "jmeter_transactions"
    produced = []
    for name, builder in CHART_BUILDERS:
        try:
            path = builder(con, out_dir, granularity, relation)
            if path:
                produced.append(path)
                print(f"  wrote {path}")
        except Exception as exc:  # keep going; one bad chart must not abort all
            print(f"  FAILED {name}: {exc}", file=sys.stderr)
    return produced


# ---------------------------------------------------------------------------
# Markdown anomaly report
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """One degradation signal. Facts only: no causal interpretation."""

    severity: str
    chart: str
    metric: str
    scope: str
    start_ms: float
    end_ms: float
    observed: float
    baseline: float
    change_pct: float | None
    samples: int
    z: float
    direction: str
    detail: str
    unit: str = ""


@dataclass
class ReportData:
    generated: datetime
    source: str
    timestamp_scaling: str
    start_ms: float | None
    end_ms: float | None
    rows: int
    controllers: int
    requests: int
    failures: int
    duration_s: float
    granularity_ms: int
    sample_rows: int
    bucket_count: int
    label_count: int
    optional_missing: list[str]
    null_elapsed: int
    error_codes: list[tuple[str, int]]
    stats: pd.DataFrame
    series: dict[str, pd.DataFrame]
    request_buckets: pd.DataFrame
    versus: pd.DataFrame
    time_vs_threads: pd.DataFrame
    distribution: pd.DataFrame
    shift: pd.DataFrame


def _num(value, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(number):
        return "n/a"
    return f"{number:,.{digits}f}"


def _signed_pct(value) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(number):
        return "n/a"
    return f"{number:+.1f}%"


def _fmt_time(ms, with_date: bool = False) -> str:
    if ms is None:
        return "n/a"
    try:
        value = float(ms)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(value):
        return "n/a"
    moment = datetime.fromtimestamp(value / 1000.0, LOCAL_TZ)
    return moment.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


def _md_cell(value) -> str:
    text = "n/a" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _md_table(headers, rows) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(_md_cell(h) for h in headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
    return "\n".join(lines)


def _empty_series() -> pd.DataFrame:
    return pd.DataFrame(columns=["bucket_ms", "scope", "value", "n"])


def _series_frame(bucket_ms, values, scope, n_values=None) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "bucket_ms": list(bucket_ms),
            "scope": scope,
            "value": list(values),
            "n": list(n_values) if n_values is not None else 1,
        }
    )
    return frame.dropna(subset=["value"]).reset_index(drop=True)


def _sum_series(frame, value_col, scale=1.0, scope="All") -> pd.DataFrame:
    if frame is None or frame.empty:
        return _empty_series()
    grouped = frame.groupby("bucket_ms")
    values = grouped[value_col].sum() * scale
    counts = grouped["n"].sum()
    return _series_frame(values.index, values.to_numpy(), scope, counts.to_numpy())


def _weighted_series(frame, value_col, scale=1.0, scope="All") -> pd.DataFrame:
    if frame is None or frame.empty:
        return _empty_series()
    frame = frame.dropna(subset=[value_col])
    if frame.empty:
        return _empty_series()
    numerator = (frame[value_col] * frame["n"]).groupby(frame["bucket_ms"]).sum()
    denominator = frame.groupby("bucket_ms")["n"].sum()
    values = (numerator / denominator) * scale
    return _series_frame(values.index, values.to_numpy(), scope, denominator.to_numpy())


def _label_series(frame, value_col, scale=1.0) -> pd.DataFrame:
    if frame is None or frame.empty:
        return _empty_series()
    out = pd.DataFrame(
        {
            "bucket_ms": frame["bucket_ms"].to_numpy(),
            "scope": frame["label"].to_numpy(),
            "value": pd.to_numeric(frame[value_col], errors="coerce").to_numpy()
            * scale,
            "n": pd.to_numeric(frame["n"], errors="coerce").to_numpy(),
        }
    )
    return out.dropna(subset=["value"]).reset_index(drop=True)


def _sampled_source(relation: str, sample_rows: int) -> str:
    """DuckDB reservoir downsampling. seed keeps reports reproducible."""
    if sample_rows and sample_rows > 0:
        return (
            f"(SELECT * FROM {relation} USING SAMPLE "
            f"reservoir({int(sample_rows)} ROWS) REPEATABLE ({REPORT_SEED}))"
        )
    return relation


def _collect_report_data(con, granularity: int, include_sub_samples: bool,
                         sample_rows: int) -> ReportData:
    bucket_expr = _bucket_expr(granularity)
    seconds = granularity / 1000.0

    rows = con.execute("SELECT COUNT(*) FROM jmeter_samples").fetchone()[0]
    effective_sample_rows = (
        0 if (sample_rows and rows and sample_rows >= rows) else sample_rows
    )
    controller_rows = con.execute(
        "SELECT COUNT(*) FROM jmeter_samples WHERE is_controller"
    ).fetchone()[0]
    has_controllers = controller_rows > 0
    null_elapsed = con.execute(
        "SELECT COUNT(*) FROM jmeter_samples WHERE elapsed IS NULL"
    ).fetchone()[0]

    bounds = con.execute(
        "SELECT MIN(timestamp), MAX(timestamp + elapsed) FROM jmeter_samples"
    ).fetchone()
    start_ms, end_ms = bounds
    duration_s = (
        (float(end_ms) - float(start_ms)) / 1000.0
        if start_ms is not None and end_ms is not None
        else 0.0
    )

    source, scaling = "n/a", "n/a"
    try:
        meta = con.execute(
            "SELECT source, timestamp_scaling FROM jmeter_metadata LIMIT 1"
        ).fetchone()
        if meta:
            source, scaling = meta
    except Exception:
        pass

    bucket_df = con.execute(
        f"""
        SELECT {bucket_expr} AS bucket_ms,
               label,
               is_controller,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE success IS FALSE) AS errors,
               AVG(elapsed) AS avg_elapsed,
               quantile_cont(elapsed, 0.95) AS p95_elapsed,
               AVG(latency) AS avg_latency,
               AVG(connect) AS avg_connect,
               SUM(COALESCE(bytes, 0)) AS rx_bytes,
               SUM(COALESCE(sent_bytes, 0)) AS tx_bytes,
               AVG(all_threads) AS avg_threads
        FROM jmeter_samples
        GROUP BY bucket_ms, label, is_controller
        ORDER BY bucket_ms, label
        """
    ).df()

    if has_controllers and not include_sub_samples:
        request_filter = "AND NOT is_controller"
        label_df = bucket_df[bucket_df["is_controller"] == True]  # noqa: E712
        request_df = bucket_df[bucket_df["is_controller"] == False]  # noqa: E712
    else:
        request_filter = ""
        label_df = bucket_df
        request_df = bucket_df

    percentile_df = con.execute(
        f"""
        SELECT {bucket_expr} AS bucket_ms,
               quantile_cont(elapsed, 0.50) AS p50,
               quantile_cont(elapsed, 0.95) AS p95,
               quantile_cont(elapsed, 0.99) AS p99,
               COUNT(*) AS n
        FROM jmeter_samples
        WHERE elapsed IS NOT NULL AND success IS TRUE {request_filter}
        GROUP BY bucket_ms
        ORDER BY bucket_ms
        """
    ).df()

    codes_df = con.execute(
        f"""
        SELECT {bucket_expr} AS bucket_ms,
               COALESCE(response_code, 'unknown') AS response_code,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE success IS FALSE) AS errors
        FROM jmeter_samples
        WHERE 1 = 1 {request_filter}
        GROUP BY bucket_ms, response_code
        ORDER BY bucket_ms
        """
    ).df()

    versus_df = con.execute(
        f"""
        WITH b AS (
            SELECT (timestamp + elapsed)
                       - ((timestamp + elapsed) % {VERSUS_REQUEST_GRANULARITY_MS})
                       AS second_ms,
                   elapsed,
                   latency
            FROM jmeter_samples
            WHERE elapsed IS NOT NULL {request_filter}
        )
        SELECT second_ms,
               COUNT(*) AS requests,
               MEDIAN(elapsed) AS median_elapsed,
               MEDIAN(latency) AS median_latency
        FROM b
        GROUP BY second_ms
        ORDER BY second_ms
        """
    ).df()

    metric_filter = "AND is_controller" if has_controllers and not include_sub_samples else ""
    time_vs_threads_df = con.execute(
        f"""
        SELECT label,
               all_threads,
               AVG(elapsed) AS avg_elapsed,
               COUNT(*) AS n
        FROM jmeter_samples
        WHERE all_threads IS NOT NULL AND elapsed IS NOT NULL {metric_filter}
        GROUP BY label, all_threads
        ORDER BY label, all_threads
        """
    ).df()

    sampled = _sampled_source("jmeter_samples", effective_sample_rows)
    distribution_df = con.execute(
        f"""
        SELECT FLOOR(elapsed / 100.0) * 100 AS bin_ms, COUNT(*) AS n
        FROM {sampled} sampled
        WHERE elapsed IS NOT NULL {request_filter}
        GROUP BY bin_ms
        ORDER BY bin_ms
        """
    ).df()

    shift_df = con.execute(
        f"""
        WITH selected AS (
            SELECT timestamp, elapsed
            FROM {sampled} sampled
            WHERE elapsed IS NOT NULL {request_filter}
        ),
        bounds AS (
            SELECT MIN(timestamp) AS lo, MAX(timestamp) AS hi
            FROM jmeter_samples
        )
        SELECT CASE
                   WHEN selected.timestamp < (bounds.lo + bounds.hi) / 2.0
                   THEN 'first half'
                   ELSE 'second half'
               END AS half,
               COUNT(*) AS n,
               quantile_cont(elapsed, 0.50) AS median_ms,
               COUNT(*) FILTER (WHERE elapsed > 1500) AS slow
        FROM selected, bounds
        GROUP BY half
        ORDER BY half
        """
    ).df()

    error_codes = con.execute(
        """
        SELECT COALESCE(response_code, 'unknown') AS code, COUNT(*) AS n
        FROM jmeter_samples
        WHERE success IS FALSE
        GROUP BY code
        ORDER BY n DESC
        LIMIT 5
        """
    ).fetchall()

    optional_missing = []
    for column in OPTIONAL_COLUMNS:
        present = con.execute(
            f"SELECT COUNT({column}) FROM jmeter_samples"
        ).fetchone()[0]
        if present == 0:
            optional_missing.append(column)

    stats_df = build_statistics(
        con, None, include_sub_samples=include_sub_samples,
        write_files=False, verbose=False,
    )

    series = {
        "Response time (avg)": _label_series(label_df, "avg_elapsed"),
        "Response time (p95)": _series_frame(
            percentile_df["bucket_ms"], percentile_df["p95"], "All requests",
            percentile_df["n"],
        ),
        "Response time (p99)": _series_frame(
            percentile_df["bucket_ms"], percentile_df["p99"], "All requests",
            percentile_df["n"],
        ),
        "Latency (avg)": _label_series(label_df, "avg_latency"),
        "Connect time (avg)": _label_series(label_df, "avg_connect"),
        "Hits/s": _sum_series(request_df, "n", scale=1.0 / seconds),
        "Bytes received/s": _sum_series(
            request_df, "rx_bytes", scale=1.0 / seconds
        ),
        "Bytes sent/s": _sum_series(
            request_df, "tx_bytes", scale=1.0 / seconds
        ),
        "Active threads": _weighted_series(bucket_df, "avg_threads"),
        "Transactions/s": _label_series(label_df, "n", scale=1.0 / seconds),
        # JMeter's Total TPS chart counts every sample row, not only controllers.
        "Total TPS": _sum_series(bucket_df, "n", scale=1.0 / seconds),
        "Failures/s": _sum_series(
            bucket_df, "errors", scale=1.0 / seconds
        ),
        "Errors/s": _sum_series(
            request_df, "errors", scale=1.0 / seconds
        ),
    }

    request_buckets = (
        request_df.groupby("bucket_ms", as_index=False)
        .agg(n=("n", "sum"), errors=("errors", "sum"))
        if not request_df.empty
        else pd.DataFrame(columns=["bucket_ms", "n", "errors"])
    )

    controllers = int(bucket_df.loc[bucket_df["is_controller"], "n"].sum())
    failures = int(bucket_df["errors"].sum())
    labels = label_df["label"].nunique() if not label_df.empty else 0

    return ReportData(
        generated=datetime.now(LOCAL_TZ),
        source=source,
        timestamp_scaling=scaling,
        start_ms=float(start_ms) if start_ms is not None else None,
        end_ms=float(end_ms) if end_ms is not None else None,
        rows=int(rows),
        controllers=controllers,
        requests=int(rows) - controllers,
        failures=failures,
        duration_s=duration_s,
        granularity_ms=granularity,
        sample_rows=sample_rows,
        bucket_count=int(bucket_df["bucket_ms"].nunique()) if not bucket_df.empty else 0,
        label_count=int(labels),
        optional_missing=optional_missing,
        null_elapsed=int(null_elapsed),
        error_codes=[(str(code), int(count)) for code, count in error_codes],
        stats=stats_df,
        series=series,
        request_buckets=request_buckets,
        versus=versus_df,
        time_vs_threads=time_vs_threads_df,
        distribution=distribution_df,
        shift=shift_df,
    )


def _robust_stats(values):
    """Median and a MAD-based scale, with IQR/std fallbacks."""
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return 0.0, 0.0
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    scale = 1.4826 * mad
    if scale <= 1e-12:
        q75, q25 = np.percentile(array, [75, 25])
        scale = float(q75 - q25) / 1.349
    if scale <= 1e-12:
        scale = float(np.std(array))
    return median, max(scale, 0.0)


def _windows(indices, gap: int = 1):
    """Merge consecutive bucket indices into windows."""
    groups = []
    for index in indices:
        if groups and index - groups[-1][-1] <= gap:
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def _change_severity(change_pct, z) -> str:
    magnitude = abs(change_pct) / 100.0 if change_pct is not None else 0.0
    if magnitude >= 0.75 or z >= 10:
        return "High"
    if magnitude >= 0.40 or z >= 6:
        return "Medium"
    return "Low"


def _detect_series(series, chart, metric, unit="", min_abs=0.0,
                   min_rel=ANOMALY_MIN_REL, spikes=True, dips=True,
                   ramp_guard=True):
    findings = []
    if series is None or series.empty:
        return findings
    for scope, group in series.groupby("scope", sort=False):
        group = group.sort_values("bucket_ms").reset_index(drop=True)
        if len(group) < 5 or float(group["n"].sum()) < 10:
            continue
        values = group["value"].to_numpy(dtype=float)
        first_bucket = float(group["bucket_ms"].iloc[0])
        last_bucket = float(group["bucket_ms"].iloc[-1])
        span = max(last_bucket - first_bucket, 1.0)
        guard_low = first_bucket + span * RAMP_GUARD_FRACTION
        guard_high = last_bucket - span * RAMP_GUARD_FRACTION
        middle = (group["bucket_ms"] >= guard_low) & (
            group["bucket_ms"] <= guard_high
        )
        baseline_values = values[middle.to_numpy()] if middle.sum() >= 3 else values
        baseline, scale = _robust_stats(baseline_values)
        if not np.isfinite(baseline):
            continue

        flagged = []
        for index, value in enumerate(values):
            change = value - baseline
            relative = (
                abs(change) / abs(baseline)
                if abs(baseline) > 1e-12
                else (float("inf") if abs(change) > 1e-12 else 0.0)
            )
            if abs(change) < min_abs or relative < min_rel:
                continue
            z = abs(change) / scale if scale > 1e-12 else float("inf")
            if z < ANOMALY_Z:
                continue
            bucket = float(group["bucket_ms"].iloc[index])
            # The first/last bucket is usually partial coverage; skip it.
            if len(group) > 4 and bucket in (first_bucket, last_bucket):
                continue
            if change < 0:
                if not dips:
                    continue
                if ramp_guard and not (guard_low <= bucket <= guard_high):
                    continue
            elif not spikes:
                continue
            flagged.append(index)

        for window in _windows(flagged):
            peak = max(window, key=lambda i: abs(values[i] - baseline))
            observed = float(values[peak])
            change = observed - baseline
            change_pct = (
                (change / baseline) * 100.0 if abs(baseline) > 1e-12 else None
            )
            z = abs(change) / scale if scale > 1e-12 else float("inf")
            direction = "spike" if change >= 0 else "dip"
            severity = _change_severity(change_pct, z)
            # A one-bucket spike is noisy evidence; keep High for very large moves.
            if (
                len(window) == 1
                and severity == "High"
                and abs(change_pct or 0.0) < 200.0
            ):
                severity = "Medium"
            findings.append(
                Finding(
                    severity=severity,
                    chart=chart,
                    metric=metric,
                    scope=str(scope),
                    start_ms=float(group["bucket_ms"].iloc[window[0]]),
                    end_ms=float(group["bucket_ms"].iloc[window[-1]]),
                    observed=observed,
                    baseline=baseline,
                    change_pct=change_pct,
                    samples=int(group["n"].iloc[window].sum()),
                    z=z,
                    direction=direction,
                    detail=(
                        f"{direction.capitalize()} vs robust baseline across "
                        f"{len(window)} bucket(s); robust z={z:.1f}."
                    ),
                    unit=unit,
                )
            )
    return findings


def _detect_trends(series, chart, metric, unit="", trend="both"):
    findings = []
    if series is None or series.empty:
        return findings
    for scope, group in series.groupby("scope", sort=False):
        group = group.sort_values("bucket_ms").reset_index(drop=True)
        if len(group) < 6:
            continue
        first_bucket = float(group["bucket_ms"].iloc[0])
        last_bucket = float(group["bucket_ms"].iloc[-1])
        span = max(last_bucket - first_bucket, 1.0)
        middle = group[
            (group["bucket_ms"] >= first_bucket + span * RAMP_GUARD_FRACTION)
            & (group["bucket_ms"] <= last_bucket - span * RAMP_GUARD_FRACTION)
        ]
        if len(middle) < 6:
            middle = group
        chunk = max(2, len(middle) // 3)
        early = float(np.median(middle["value"].iloc[:chunk]))
        late = float(np.median(middle["value"].iloc[-chunk:]))
        if abs(early) <= 1e-12:
            continue
        ratio = late / early
        if not (ratio >= 1.5 or ratio <= 0.667):
            continue
        if trend == "up" and ratio < 1.0:
            continue
        if trend == "down" and ratio > 1.0:
            continue
        direction = "increase" if ratio >= 1.0 else "decrease"
        severity = "High" if (ratio >= 2.0 or ratio <= 0.5) else "Medium"
        findings.append(
            Finding(
                severity=severity,
                chart=chart,
                metric=metric,
                scope=str(scope),
                start_ms=float(middle["bucket_ms"].iloc[0]),
                end_ms=float(middle["bucket_ms"].iloc[-1]),
                observed=late,
                baseline=early,
                change_pct=(ratio - 1.0) * 100.0,
                samples=int(middle["n"].sum()),
                z=0.0,
                direction=direction,
                detail=(
                    f"Sustained {direction}: first-third median {early:.2f} -> "
                    f"last-third median {late:.2f} ({ratio:.2f}x)."
                ),
                unit=unit,
            )
        )
    return findings


def _detect_errors(request_buckets, chart="Codes Per Second"):
    findings = []
    if request_buckets is None or request_buckets.empty:
        return findings
    frame = request_buckets.sort_values("bucket_ms").reset_index(drop=True)
    flagged = [
        index
        for index in range(len(frame))
        if frame["errors"].iloc[index] > 0 and frame["n"].iloc[index] >= 10
    ]
    for window in _windows(flagged):
        errors = int(frame["errors"].iloc[window].sum())
        samples = int(frame["n"].iloc[window].sum())
        rate = errors / samples if samples else 0.0
        severity = "High" if rate >= 0.02 else "Medium" if rate >= 0.005 else "Low"
        findings.append(
            Finding(
                severity=severity,
                chart=chart,
                metric="Failed requests",
                scope="All requests",
                start_ms=float(frame["bucket_ms"].iloc[window[0]]),
                end_ms=float(frame["bucket_ms"].iloc[window[-1]]),
                observed=errors,
                baseline=0.0,
                change_pct=None,
                samples=samples,
                z=0.0,
                direction="errors",
                detail=(
                    f"{errors} failed request(s), {rate:.2%} of {samples} "
                    "in window."
                ),
            )
        )
    return findings


def _detect_label_outliers(stats, start_ms, end_ms):
    findings = []
    if stats is None or stats.empty or "label" not in stats:
        return findings
    total = stats[stats["label"] == "TOTAL"]
    if total.empty:
        return findings
    try:
        total_p95 = float(total.iloc[0]["p95"])
    except (TypeError, ValueError):
        total_p95 = None
    for _, row in stats[stats["label"] != "TOTAL"].iterrows():
        try:
            samples = int(row["samples"])
            p95 = float(row["p95"])
            failures = int(row["failures"])
            error_pct = float(row["error_pct"])
        except (TypeError, ValueError):
            continue
        if samples < 30:
            continue
        if total_p95 and total_p95 > 0 and p95 / total_p95 >= 1.5:
            ratio = p95 / total_p95
            if ratio >= 2.0 and samples >= 100:
                severity = "High"
            elif ratio >= 2.0:
                severity = "Medium"
            else:
                severity = "Low"
            findings.append(
                Finding(
                    severity=severity,
                    chart="Response Time Percentiles",
                    metric="p95 vs TOTAL",
                    scope=str(row["label"]),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    observed=p95,
                    baseline=total_p95,
                    change_pct=(ratio - 1.0) * 100.0,
                    samples=samples,
                    z=0.0,
                    direction="above",
                    detail=(
                        f"p95 is {ratio:.2f}x the TOTAL p95 "
                        f"({samples:,} samples)."
                    ),
                    unit=" ms",
                )
            )
        if failures >= 5 and error_pct >= 1.0:
            findings.append(
                Finding(
                    severity="High" if error_pct >= 5.0 else "Medium",
                    chart="Codes Per Second",
                    metric="Label error rate",
                    scope=str(row["label"]),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    observed=error_pct,
                    baseline=0.0,
                    change_pct=None,
                    samples=samples,
                    z=0.0,
                    direction="errors",
                    detail=(
                        f"{failures} failure(s) in {samples:,} samples "
                        f"({error_pct:.2f}%)."
                    ),
                    unit="%",
                )
            )
    return findings


def _load_relation_rows(versus):
    """Low/high request-rate medians for the two versus-request charts."""
    if versus is None or versus.empty or len(versus) < 8:
        return []
    frame = versus[versus["requests"] > 0].dropna(subset=["requests"])
    if len(frame) < 8:
        return []
    low_cut = float(frame["requests"].quantile(0.25))
    high_cut = float(frame["requests"].quantile(0.75))
    low = frame[frame["requests"] <= low_cut]
    high = frame[frame["requests"] >= high_cut]
    if len(low) < 3 or len(high) < 3:
        return []
    rows = []
    for column, metric in (
        ("median_elapsed", "Median response time"),
        ("median_latency", "Median latency"),
    ):
        if column not in frame or frame[column].notna().sum() == 0:
            continue
        low_value = float(low[column].median())
        high_value = float(high[column].median())
        if low_value <= 0 or not np.isfinite(high_value):
            continue
        rows.append(
            {
                "metric": metric,
                "low": low_value,
                "high": high_value,
                "ratio": high_value / low_value,
                "low_cut": low_cut,
                "high_cut": high_cut,
                "samples": int(low["requests"].sum() + high["requests"].sum()),
                "start_ms": float(frame["second_ms"].min()),
                "end_ms": float(frame["second_ms"].max()),
            }
        )
    return rows


def _detect_load_relation(versus):
    findings = []
    for row in _load_relation_rows(versus):
        ratio = row["ratio"]
        if ratio < 1.5:
            continue
        chart = (
            "Latencies Vs Request"
            if "Latency" in row["metric"]
            else "Response Time Vs Request"
        )
        findings.append(
            Finding(
                severity=(
                    "High" if ratio >= 2.5 else "Medium" if ratio >= 1.75 else "Low"
                ),
                chart=chart,
                metric=row["metric"] + " vs request rate",
                scope="All requests",
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                observed=row["high"],
                baseline=row["low"],
                change_pct=(ratio - 1.0) * 100.0,
                samples=row["samples"],
                z=0.0,
                direction="increase",
                detail=(
                    f"Median at high request rate is {ratio:.2f}x the "
                    f"low-rate median (high-rate cutoff {row['high_cut']:.0f} req/s)."
                ),
                unit=" ms",
            )
        )
    return findings


def _thread_relation(tv):
    if tv is None or tv.empty:
        return None
    frame = tv.dropna(subset=["all_threads", "avg_elapsed"]).copy()
    frame["all_threads"] = pd.to_numeric(frame["all_threads"], errors="coerce")
    frame = frame.dropna(subset=["all_threads"])
    if frame.empty:
        return None
    numerator = (
        frame["avg_elapsed"] * frame["n"]
    ).groupby(frame["all_threads"]).sum()
    denominator = frame.groupby("all_threads")["n"].sum()
    averages = (numerator / denominator).dropna()
    if len(averages) < 8:
        return None
    low_cut = float(averages.index.to_series().quantile(0.25))
    high_cut = float(averages.index.to_series().quantile(0.75))
    low = averages[averages.index <= low_cut]
    high = averages[averages.index >= high_cut]
    if low.empty or high.empty:
        return None
    low_value = float(low.median())
    high_value = float(high.median())
    if low_value <= 0:
        return None
    return {
        "low": low_value,
        "high": high_value,
        "ratio": high_value / low_value,
        "samples": int(denominator.sum()),
    }


def _detect_thread_relation(tv, start_ms, end_ms):
    relation = _thread_relation(tv)
    if relation is None or relation["ratio"] < 1.5:
        return []
    ratio = relation["ratio"]
    return [
        Finding(
            severity="High" if ratio >= 2.5 else "Medium" if ratio >= 1.75 else "Low",
            chart="Time VS Threads",
            metric="Response time vs threads",
            scope="All transactions",
            start_ms=start_ms,
            end_ms=end_ms,
            observed=relation["high"],
            baseline=relation["low"],
            change_pct=(ratio - 1.0) * 100.0,
            samples=relation["samples"],
            z=0.0,
            direction="increase",
            detail=(
                f"Average response time at high thread counts is {ratio:.2f}x "
                "the low-thread average."
            ),
            unit=" ms",
        )
    ]


def _detect_distribution_shift(shift, start_ms, end_ms):
    if shift is None or len(shift) < 2:
        return []
    lookup = {str(row["half"]): row for _, row in shift.iterrows()}
    first = lookup.get("first half")
    second = lookup.get("second half")
    if first is None or second is None:
        return []
    n1, n2 = int(first["n"]), int(second["n"])
    if n1 < 30 or n2 < 30:
        return []
    share1 = float(first["slow"]) / n1
    share2 = float(second["slow"]) / n2
    median1 = float(first["median_ms"])
    median2 = float(second["median_ms"])
    ratio = median2 / median1 if median1 > 0 else None
    delta = (share2 - share1) * 100.0
    if not (delta >= 10.0 or (ratio is not None and ratio >= 1.5)):
        return []
    severity = (
        "High"
        if delta >= 25.0 or (ratio is not None and ratio >= 2.5)
        else "Medium"
    )
    return [
        Finding(
            severity=severity,
            chart="Response Time Distribution",
            metric="Slow-request share (>1500 ms)",
            scope="All requests",
            start_ms=start_ms,
            end_ms=end_ms,
            observed=share2 * 100.0,
            baseline=share1 * 100.0,
            change_pct=delta,
            samples=n1 + n2,
            z=0.0,
            direction="increase",
            detail=(
                f"Share above 1500 ms moved from {share1:.1%} to {share2:.1%}; "
                f"median moved from {median1:.0f} to {median2:.0f} ms."
            ),
            unit="%",
        )
    ]


def _detect_synthetic(distribution, start_ms, end_ms):
    if distribution is None or distribution.empty:
        return []
    total = int(distribution["n"].sum())
    if total <= 0:
        return []
    slow = int(distribution.loc[distribution["bin_ms"] >= 1500, "n"].sum())
    share = slow / total
    if share < 0.10:
        return []
    return [
        Finding(
            severity="Medium" if share >= 0.25 else "Low",
            chart="Synthetic Response Times Distribution",
            metric="Share > 1500 ms",
            scope="All requests",
            start_ms=start_ms,
            end_ms=end_ms,
            observed=share * 100.0,
            baseline=0.0,
            change_pct=None,
            samples=total,
            z=0.0,
            direction="high",
            detail=(
                f"{slow:,} of {total:,} analysed requests ({share:.1%}) "
                "exceeded 1500 ms."
            ),
            unit="%",
        )
    ]


def _detect_findings(data: ReportData):
    findings = []
    configs = [
        ("Response Time Over Time", "Response time (avg)", " ms", 100.0, True, True, "up"),
        ("Response Time Percentiles Over Time", "Response time (p95)", " ms", 100.0, True, True, "up"),
        ("Response Time Percentiles Over Time", "Response time (p99)", " ms", 100.0, True, True, "up"),
        ("Latencies", "Latency (avg)", " ms", 50.0, True, True, "up"),
        ("Connect Time", "Connect time (avg)", " ms", 20.0, True, True, "up"),
        ("Hits Per Second", "Hits/s", "", 1.0, True, True, "down"),
        ("Bytes Throughput", "Bytes received/s", " B/s", 1024.0, False, True, "down"),
        ("Bytes Throughput", "Bytes sent/s", " B/s", 256.0, False, True, "down"),
        ("Active Threads", "Active threads", "", 1.0, True, True, "down"),
        ("Transactions Per Second", "Transactions/s", "", 0.1, True, True, "down"),
        ("Total Transactions Per Second", "Total TPS", "", 0.1, True, True, "down"),
        ("Total Transactions Per Second", "Failures/s", "", 0.0, False, True, "up"),
        ("Codes Per Second", "Errors/s", "", 0.0, False, True, "up"),
    ]
    for chart, metric, unit, min_abs, spikes, dips, trend in configs:
        series = data.series.get(metric)
        if series is None:
            continue
        findings.extend(
            _detect_series(
                series, chart, metric, unit=unit, min_abs=min_abs,
                spikes=spikes, dips=dips,
            )
        )
        findings.extend(
            _detect_trends(series, chart, metric, unit=unit, trend=trend)
        )

    findings.extend(_detect_errors(data.request_buckets))
    findings.extend(
        _detect_label_outliers(
            data.stats, data.start_ms or 0, data.end_ms or 0
        )
    )
    findings.extend(_detect_load_relation(data.versus))
    findings.extend(
        _detect_thread_relation(
            data.time_vs_threads, data.start_ms or 0, data.end_ms or 0
        )
    )
    findings.extend(
        _detect_distribution_shift(
            data.shift, data.start_ms or 0, data.end_ms or 0
        )
    )
    findings.extend(
        _detect_synthetic(
            data.distribution, data.start_ms or 0, data.end_ms or 0
        )
    )
    return findings


def _finding_sort_key(finding: Finding):
    magnitude = (
        abs(finding.change_pct)
        if finding.change_pct is not None
        else abs(finding.observed)
    )
    return (SEVERITY_RANK.get(finding.severity, 9), -magnitude)


def _render_summary(data: ReportData, findings) -> str:
    counts = {"High": 0, "Medium": 0, "Low": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    failure_pct = (data.failures / data.rows * 100.0) if data.rows else 0.0
    lines = [
        f"- **Samples:** {data.rows:,} total; {data.controllers:,} transaction, "
        f"{data.requests:,} request.",
        f"- **Failures:** {data.failures:,} ({failure_pct:.2f}% of rows).",
        f"- **Signals:** {counts['High']} high, {counts['Medium']} medium, "
        f"{counts['Low']} low.",
    ]
    if data.stats is not None and not data.stats.empty:
        labels = data.stats[data.stats["label"] != "TOTAL"]
        if not labels.empty:
            slowest = labels.loc[labels["p95"].astype(float).idxmax()]
            lines.append(
                f"- **Slowest label by p95:** `{slowest['label']}` at "
                f"{_num(slowest['p95'])} ms ({int(slowest['samples']):,} samples)."
            )
    top = sorted(findings, key=_finding_sort_key)[:5]
    if top:
        lines.append("\n**Top signals**\n")
        lines.append(
            _md_table(
                ["Severity", "Chart", "Metric", "Scope", "Window", "Change"],
                [
                    [
                        finding.severity,
                        finding.chart,
                        finding.metric,
                        finding.scope,
                        f"{_fmt_time(finding.start_ms)}-{_fmt_time(finding.end_ms)}",
                        (
                            _signed_pct(finding.change_pct)
                            if finding.change_pct is not None
                            else f"observed {_num(finding.observed)}{finding.unit}"
                        ),
                    ]
                    for finding in top
                ],
            )
        )
    else:
        lines.append(
            "\nNo material anomalies were detected in the available series."
        )
    return "\n".join(lines)


def _render_overview(data: ReportData) -> str:
    rows = [
        ["Rows", f"{data.rows:,}"],
        ["Transaction samples", f"{data.controllers:,}"],
        ["Request samples", f"{data.requests:,}"],
        ["Failures", f"{data.failures:,}"],
        ["Run duration", f"{data.duration_s:,.1f} s"],
        ["Buckets", f"{data.bucket_count:,}"],
        ["Labels", f"{data.label_count:,}"],
        ["Bucket size", f"{data.granularity_ms / 1000.0:g} s"],
        [
            "Downsampling",
            (
                f"reservoir({data.sample_rows:,} rows), seed {REPORT_SEED}"
                if data.sample_rows
                else "disabled"
            ),
        ],
        ["Timestamp scaling", data.timestamp_scaling],
    ]
    return _md_table(["Metric", "Value"], rows)


def _render_stats(data: ReportData, findings=None) -> str:
    if data.stats is None or data.stats.empty:
        return "_No completed samples._"
    frame = data.stats.copy()
    total = frame[frame["label"] == "TOTAL"]
    labels = frame[frame["label"] != "TOTAL"]
    flagged = {finding.scope for finding in (findings or [])}
    top = labels.sort_values("samples", ascending=False).head(20)
    extra = labels[
        labels["label"].isin(flagged) & ~labels["label"].isin(top["label"])
    ]
    keep = (
        pd.concat([top, extra])
        .drop_duplicates("label")
        .sort_values("samples", ascending=False)
    )
    omitted = len(labels) - len(keep)
    shown = pd.concat([keep, total]) if not total.empty else keep
    rows = []
    for _, row in shown.iterrows():
        rows.append(
            [
                row["label"],
                f"{int(row['samples']):,}",
                f"{int(row['failures']):,}",
                f"{float(row['error_pct']):.2f}",
                _num(row["average"]),
                _num(row["median"]),
                _num(row["p90"]),
                _num(row["p95"]),
                _num(row["p99"]),
                _num(row["throughput"]),
            ]
        )
    text = _md_table(
        [
            "Label", "Samples", "Failures", "Error %", "Average",
            "Median", "P90", "P95", "P99", "Throughput/s",
        ],
        rows,
    )
    if omitted:
        text += (
            f"\n\n_{omitted:,} label(s) omitted; the 20 largest plus every "
            "flagged label are shown._"
        )
    return text


def _render_anomalies(findings, top_n: int) -> str:
    by_chart = {chart: [] for chart in CHART_ORDER}
    for finding in findings:
        by_chart.setdefault(finding.chart, []).append(finding)

    coverage = []
    details = []
    for chart in CHART_ORDER:
        items = sorted(by_chart.get(chart, []), key=_finding_sort_key)
        if items:
            coverage.append([chart, len(items), items[0].severity])
            shown = items[:top_n]
            details.append(
                f"### {chart}\n\n"
                + _md_table(
                    [
                        "Severity", "Metric", "Scope", "Window", "Observed",
                        "Baseline", "Change", "Samples", "Evidence",
                    ],
                    [
                        [
                            finding.severity,
                            finding.metric,
                            finding.scope,
                            f"{_fmt_time(finding.start_ms)}-"
                            f"{_fmt_time(finding.end_ms)}",
                            _num(finding.observed) + finding.unit,
                            _num(finding.baseline) + finding.unit,
                            _signed_pct(finding.change_pct),
                            f"{finding.samples:,}",
                            finding.detail,
                        ]
                        for finding in shown
                    ],
                )
                + (
                    f"\n\n_{len(items) - len(shown)} additional window(s) omitted._"
                    if len(items) > len(shown)
                    else ""
                )
            )
        else:
            coverage.append([chart, 0, "No signal"])

    text = "**Chart coverage**\n\n" + _md_table(
        ["Chart", "Findings", "Highest severity"], coverage
    )
    if details:
        text += "\n\n**Detailed windows**\n\n" + "\n\n".join(details)
    else:
        text += "\n\n_No material anomalies were flagged in any chart._"
    return text


def _render_distribution(data: ReportData) -> str:
    parts = []

    load_rows = _load_relation_rows(data.versus)
    if load_rows:
        parts.append(
            "**Response and latency vs request rate**\n\n"
            + _md_table(
                ["Metric", "Low-rate median", "High-rate median", "Ratio"],
                [
                    [
                        row["metric"],
                        _num(row["low"]) + " ms",
                        _num(row["high"]) + " ms",
                        f"{row['ratio']:.2f}x",
                    ]
                    for row in load_rows
                ],
            )
        )

    thread = _thread_relation(data.time_vs_threads)
    if thread is not None:
        parts.append(
            "**Response time vs active threads**\n\n"
            + _md_table(
                ["Metric", "Low-thread average", "High-thread average", "Ratio"],
                [
                    [
                        "Average response time",
                        _num(thread["low"]) + " ms",
                        _num(thread["high"]) + " ms",
                        f"{thread['ratio']:.2f}x",
                    ]
                ],
            )
        )

    if data.shift is not None and len(data.shift) >= 2:
        lookup = {str(row["half"]): row for _, row in data.shift.iterrows()}
        first = lookup.get("first half")
        second = lookup.get("second half")
        if first is not None and second is not None:
            n1, n2 = int(first["n"]), int(second["n"])
            parts.append(
                "**First half vs second half**\n\n"
                + _md_table(
                    ["Half", "Samples", "Median", "Share > 1500 ms"],
                    [
                        [
                            "First half",
                            f"{n1:,}",
                            _num(first["median_ms"]) + " ms",
                            f"{float(first['slow']) / n1:.1%}" if n1 else "n/a",
                        ],
                        [
                            "Second half",
                            f"{n2:,}",
                            _num(second["median_ms"]) + " ms",
                            f"{float(second['slow']) / n2:.1%}" if n2 else "n/a",
                        ],
                    ],
                )
            )

    if data.distribution is not None and not data.distribution.empty:
        total = int(data.distribution["n"].sum())
        if total:
            fast = int(data.distribution.loc[data.distribution["bin_ms"] < 500, "n"].sum())
            middle = int(
                data.distribution.loc[
                    (data.distribution["bin_ms"] >= 500)
                    & (data.distribution["bin_ms"] < 1500),
                    "n",
                ].sum()
            )
            slow = int(data.distribution.loc[data.distribution["bin_ms"] >= 1500, "n"].sum())
            parts.append(
                "**Response-time ranges in the analysis set**\n\n"
                + _md_table(
                    ["Range", "Requests", "Share"],
                    [
                        ["< 500 ms", f"{fast:,}", f"{fast / total:.1%}"],
                        ["500-1500 ms", f"{middle:,}", f"{middle / total:.1%}"],
                        [">= 1500 ms", f"{slow:,}", f"{slow / total:.1%}"],
                    ],
                )
            )

    return "\n\n".join(parts) if parts else "_No distribution data._"


def _render_data_quality(data: ReportData) -> str:
    lines = []
    if data.optional_missing:
        lines.append(
            "- Columns absent or empty (related charts are limited): "
            + ", ".join(f"`{name}`" for name in data.optional_missing)
            + "."
        )
    else:
        lines.append("- All optional JTL columns were present.")
    if data.null_elapsed:
        lines.append(
            f"- {data.null_elapsed:,} row(s) had no elapsed time and are "
            "excluded from time-series metrics."
        )
    lines.append(f"- Timestamp handling: {data.timestamp_scaling}.")
    lines.append(
        "- Time buckets use sample start time (matching the PNG charts); "
        "JMeter's over-time charts use completion time, so boundary samples "
        "can differ."
    )
    lines.append(
        "- Percentile-over-time series use successful requests only, matching "
        "the dashboard."
    )
    lines.append(
        "- `Transactions/s` uses transaction controllers; `Total TPS` and "
        "`Failures/s` use every sample row, matching the dashboard's Total TPS."
    )
    zero_series = []
    for name, frame in data.series.items():
        if frame is None or frame.empty:
            continue
        values = pd.to_numeric(frame["value"], errors="coerce").dropna()
        if not values.empty and float(values.abs().max()) <= 1e-12:
            zero_series.append(name)
    if zero_series:
        lines.append(
            "- Zero in every bucket (no signal in this run): "
            + ", ".join(f"`{name}`" for name in zero_series)
            + "."
        )
    lines.append(
        "- Active Threads findings describe the load profile and are context, "
        "not a degradation verdict."
    )
    if data.sample_rows and data.rows and data.sample_rows >= data.rows:
        lines.append(
            f"- The downsampling request (`reservoir({data.sample_rows:,} ROWS)`, "
            f"seed {REPORT_SEED}) exceeded the table size ({data.rows:,} rows), "
            "so every row was used."
        )
    elif data.sample_rows:
        lines.append(
            f"- Distribution and first/second-half queries used a deterministic "
            f"`reservoir({data.sample_rows:,} ROWS)` sample (seed {REPORT_SEED}); "
            "their shares are estimates, while bucket aggregates used all rows."
        )
    else:
        lines.append("- Downsampling disabled; per-row queries used the full table.")
    if data.error_codes:
        lines.append(
            "\n**Top failure response codes**\n\n"
            + _md_table(
                ["Response code", "Count"],
                [[code, f"{count:,}"] for code, count in data.error_codes],
            )
        )
    lines.append(
        "- Section 3 `TOTAL` covers transaction samples; use "
        "`--include-sub-samples` to include every label."
    )
    return "\n".join(lines)


def _render_next_evidence(findings, window_limit: int = 5) -> str:
    lines = []
    items = sorted(findings, key=_finding_sort_key)[:window_limit]
    if items:
        lines.append(
            "For each window below, collect the corresponding server, database, "
            "network, and JMeter evidence:"
        )
        for finding in items:
            lines.append(
                f"- **{finding.metric} / {finding.scope}** at "
                f"{_fmt_time(finding.start_ms, with_date=True)} to "
                f"{_fmt_time(finding.end_ms, with_date=True)} (local time): "
                "server CPU/memory/disk, application logs, database slow-query "
                "logs, network latency/errors, load-balancer logs, and JMeter "
                "thread dumps."
            )
        lines.append("")
    lines.extend(
        [
            "- Compare the same windows with JMeter's own HTML dashboard and "
            "`statistics.json`.",
            "- Check whether the load generator or target host was saturated in "
            "those windows.",
            "- Record any deployment, configuration change, or scheduled job "
            "active in those windows.",
        ]
    )
    return "\n".join(lines)


def _render_report(data: ReportData, findings, top_n: int) -> str:
    values = {
        "GENERATED": data.generated.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "SOURCE": data.source,
        "WINDOW": (
            f"{_fmt_time(data.start_ms, True)} to {_fmt_time(data.end_ms, True)}"
            if data.start_ms is not None
            else "n/a"
        ),
        "ROWS": f"{data.rows:,}",
        "CONTROLLERS": f"{data.controllers:,}",
        "REQUESTS": f"{data.requests:,}",
        "GRANULARITY": f"{data.granularity_ms / 1000.0:g} s",
        "SAMPLING": (
            f"deterministic `reservoir({data.sample_rows:,} ROWS)`, seed {REPORT_SEED}"
            if data.sample_rows
            else "disabled (full rows)"
        ),
        "SUMMARY": _render_summary(data, findings),
        "OVERVIEW": _render_overview(data),
        "STATS": _render_stats(data, findings),
        "ANOMALIES": _render_anomalies(findings, top_n),
        "DISTRIBUTION": _render_distribution(data),
        "DATA_QUALITY": _render_data_quality(data),
        "NEXT_EVIDENCE": _render_next_evidence(findings),
    }
    text = REPORT_TEMPLATE
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def generate_markdown(con, out_path, granularity: int = DEFAULT_GRANULARITY_MS,
                      include_sub_samples: bool = False,
                      sample_rows: int = DEFAULT_SAMPLE_ROWS,
                      top_n: int = DEFAULT_TOP_N,
                      generated: datetime | None = None) -> Path:
    """Analyse DuckDB and write exactly one Markdown report."""
    if granularity <= 0:
        raise ValueError("granularity must be a positive number of milliseconds")
    if sample_rows < 0:
        raise ValueError("sample_rows must be >= 0")
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    data = _collect_report_data(
        con, granularity, include_sub_samples, sample_rows
    )
    if generated is not None:
        data.generated = generated
    findings = _detect_findings(data)
    text = _render_report(data, findings, top_n)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print("\n===== MARKDOWN REPORT =====")
    print(f"  findings: {len(findings)}")
    print(f"  wrote {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Self-test runner (tests live in test_jmeter_report.py)
# ---------------------------------------------------------------------------


def run_selftests() -> int:
    """Run the unit tests from test_jmeter_report.py."""
    if __name__ == "__main__":
        # When executed as a script, __main__ is this module; make the test
        # module import it under its real name instead of loading a copy.
        sys.modules.setdefault("jmeter_report", sys.modules[__name__])
    try:
        from test_jmeter_report import run_selftests as _run_selftests
    except ImportError as exc:
        print(
            f"ERROR: cannot import test_jmeter_report: {exc}",
            file=sys.stderr,
        )
        return 1
    return _run_selftests()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert JMeter JTL results to Parquet/DuckDB and render PNG charts."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=["all", "ingest", "stats", "charts", "report", "selftest"],
        help="Action to perform (default: all).",
    )
    parser.add_argument("--jtl", default="orangehrm-test.jtl",
                        help="JTL file, or a directory containing .jtl files.")
    parser.add_argument("--db", default="jmeter.duckdb", help="DuckDB database file.")
    parser.add_argument("--parquet", default="jmeter_samples.parquet",
                        help="Parquet output path.")
    parser.add_argument("--out-dir", default="charts", help="Chart output directory.")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="DuckDB table name.")
    parser.add_argument(
        "--granularity",
        type=int,
        default=DEFAULT_GRANULARITY_MS,
        help="Chart bucket size in milliseconds (default: 60000).",
    )
    parser.add_argument(
        "--include-sub-samples",
        action="store_true",
        help="Plot every label instead of only transaction controllers.",
    )
    parser.add_argument(
        "--md",
        default="jmeter_report.md",
        help="Markdown report output path (default: jmeter_report.md).",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help=(
            "Rows kept by DuckDB reservoir sampling for per-row report queries "
            f"(default: {DEFAULT_SAMPLE_ROWS}; 0 disables sampling). At ~3M "
            "rows full scans are faster; use 0 for maximum speed."
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Maximum anomaly windows shown per chart (default: {DEFAULT_TOP_N}).",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "selftest":
        return run_selftests()
    if args.granularity <= 0:
        print("ERROR: --granularity must be a positive number of milliseconds.", file=sys.stderr)
        return 1
    if args.sample_rows < 0:
        print("ERROR: --sample-rows must be >= 0.", file=sys.stderr)
        return 1
    if args.top_n <= 0:
        print("ERROR: --top-n must be positive.", file=sys.stderr)
        return 1
    if args.command in ("stats", "charts", "report") and not Path(args.db).exists():
        print(
            f"ERROR: database not found: {args.db}. Run `ingest` first.",
            file=sys.stderr,
        )
        return 1

    try:
        if args.command in ("all", "ingest"):
            info = ingest(args.jtl, args.db, args.parquet, args.table)
            print(
                f"Ingested {info['rows']} rows "
                f"({info['controllers']} controllers, {info['requests']} requests) "
                f"from {info['source']}"
            )
            if info["scaled"]:
                print(f"  timestamp scaling: {info['scaled']}")
            print(f"  db: {args.db}\n  parquet: {info['parquet']}")

        if args.command in ("all", "stats", "charts", "report"):
            read_only = args.command == "charts"
            con = duckdb.connect(args.db, read_only=read_only)
            try:
                if args.command in ("all", "stats"):
                    build_statistics(con, args.out_dir, args.include_sub_samples)
                if args.command in ("all", "charts"):
                    print("\n===== CHARTS =====")
                    generate_charts(
                        con, args.out_dir, args.granularity,
                        args.include_sub_samples,
                    )
                if args.command in ("all", "report"):
                    print("\n===== ANOMALY REPORT =====")
                    generate_markdown(
                        con, args.md, args.granularity,
                        args.include_sub_samples, args.sample_rows, args.top_n,
                    )
            finally:
                con.close()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
