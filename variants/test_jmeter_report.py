#!/usr/bin/env python3
"""Unit tests for jmeter_report.py.

Run directly:
    python -m unittest -v test_jmeter_report

Or through the tool's compatibility command:
    python jmeter_report.py selftest
"""

# ===========================================================================
# TABLE OF CONTENTS - test_jmeter_report.py
# ===========================================================================
# Helpers
#   setUp / tearDown ............ temp DuckDB and Parquet paths per test
#   _jtl ........................ write a CSV JTL fixture
#   _ingest ..................... run ingest() on a fixture
#   _markdown ................... run generate_markdown() and read the result
#
# Ingestion and normalisation (22)
#   test_resolve_columns_is_case_insensitive
#   test_minimal_columns_and_success_variants
#   test_reordered_columns_and_derived_success
#   test_iso_timestamp_is_converted
#   test_epoch_seconds_are_scaled
#   test_controller_detection_and_views
#   test_no_controllers_falls_back_to_all_samples
#   test_empty_jtl_does_not_crash
#   test_parquet_round_trip
#   test_statistics_values
#   test_bucket_boundaries_and_chart_output
#   test_controller_fallback_without_message_column
#   test_hierarchy_fallback_when_messages_are_not_transactions
#   test_hyphenated_labels_are_not_controllers
#   test_single_row_percentiles_use_r6
#   test_microsecond_timestamps_are_scaled
#   test_directory_input_unions_by_name
#   test_quoted_commas_are_preserved
#   test_versus_request_groups_by_end_time_count_and_median
#   test_xml_input_is_rejected
#   test_missing_required_column_is_rejected
#   test_missing_input_is_rejected
#
# Markdown report (7)
#   test_report_flags_response_time_spike
#   test_report_flags_error_burst
#   test_report_handles_empty_jtl
#   test_report_is_single_markdown_with_all_sections
#   test_report_ignores_ramp_edge_dips
#   test_report_sampling_is_deterministic
#   test_module_toc_lists_every_top_level_function
#
# Runner
#   run_selftests ............... unittest runner (exit code)
# ===========================================================================

from __future__ import annotations

import csv
import inspect
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

import jmeter_report as jr
from jmeter_report import (
    _qstr,
    _resolve_columns,
    _versus_request_df,
    build_statistics,
    generate_charts,
    generate_markdown,
    ingest,
)


class JmeterReportTests(unittest.TestCase):
    """Unit tests for ingestion normalisation and chart generation."""

    BASE = 1_700_000_000_000  # epoch ms

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "test.duckdb"
        self.parquet = self.dir / "test.parquet"

    def tearDown(self):
        self._tmp.cleanup()

    def _jtl(self, columns, rows, name="test.jtl"):
        path = self.dir / name
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        return path

    def _ingest(self, columns, rows):
        return ingest(self._jtl(columns, rows), self.db, self.parquet)

    def test_resolve_columns_is_case_insensitive(self):
        mapping = _resolve_columns(["TimeStamp", "Elapsed", "Label"])
        self.assertEqual(mapping["timestamp"], "TimeStamp")
        self.assertEqual(mapping["elapsed"], "Elapsed")
        self.assertEqual(mapping["label"], "Label")

    def test_minimal_columns_and_success_variants(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "A", "200", "true"],
            [self.BASE + 1000, 200, "A", "500", "false"],
            [self.BASE + 2000, 300, "B", "200", "1"],
            [self.BASE + 3000, 400, "B", "Non HTTP response code: x", "0"],
        ]
        info = self._ingest(columns, rows)
        self.assertEqual(info["rows"], 4)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            df = con.execute(
                "SELECT label, success, response_code, connect, latency "
                "FROM jmeter_samples ORDER BY timestamp"
            ).df()
        finally:
            con.close()
        self.assertEqual(df["success"].tolist(), [True, False, True, False])
        self.assertEqual(df["response_code"].iloc[3], "Non HTTP response code: x")
        self.assertTrue(df["connect"].isna().all())
        self.assertTrue(df["latency"].isna().all())

    def test_reordered_columns_and_derived_success(self):
        columns = ["label", "unused", "elapsed", "timeStamp", "responseCode"]
        rows = [
            ["A", "x", 10, self.BASE, "200"],
            ["A", "x", 20, self.BASE + 1, "500"],
        ]
        info = self._ingest(columns, rows)
        self.assertEqual(info["rows"], 2)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            df = con.execute(
                "SELECT success FROM jmeter_samples ORDER BY timestamp"
            ).df()
        finally:
            con.close()
        self.assertEqual(df["success"].tolist(), [True, False])

    def test_iso_timestamp_is_converted(self):
        columns = ["timeStamp", "elapsed", "label"]
        rows = [["2023-11-14 22:13:20", 100, "A"]]
        self._ingest(columns, rows)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            value = con.execute("SELECT timestamp FROM jmeter_samples").fetchone()[0]
        finally:
            con.close()
        expected = int(
            datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc).timestamp()
            * 1000
        )
        self.assertEqual(value, expected)

    def test_epoch_seconds_are_scaled(self):
        columns = ["timeStamp", "elapsed", "label"]
        rows = [[self.BASE // 1000, 100, "A"]]
        info = self._ingest(columns, rows)
        self.assertEqual(info["scaled"], "seconds->milliseconds")
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            value = con.execute("SELECT timestamp FROM jmeter_samples").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(value, self.BASE)

    def test_controller_detection_and_views(self):
        columns = [
            "timeStamp", "elapsed", "label", "responseCode", "success",
            "responseMessage",
        ]
        rows = [
            [self.BASE, 100, "T", "200", "true",
             "Number of samples in transaction : 1, number of failing samples : 0"],
            [self.BASE, 100, "T-0", "200", "true", "OK"],
        ]
        info = self._ingest(columns, rows)
        self.assertEqual(info["controllers"], 1)
        self.assertEqual(info["requests"], 1)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            transactions = con.execute(
                "SELECT COUNT(*) FROM jmeter_transactions"
            ).fetchone()[0]
            requests = con.execute(
                "SELECT COUNT(*) FROM jmeter_requests"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(transactions, 1)
        self.assertEqual(requests, 1)

    def test_no_controllers_falls_back_to_all_samples(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "A", "200", "true"],
            [self.BASE, 200, "B", "200", "true"],
        ]
        info = self._ingest(columns, rows)
        self.assertEqual(info["controllers"], 0)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            transactions = con.execute(
                "SELECT COUNT(*) FROM jmeter_transactions"
            ).fetchone()[0]
            requests = con.execute(
                "SELECT COUNT(*) FROM jmeter_requests"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(transactions, 2)
        self.assertEqual(requests, 2)

    def test_empty_jtl_does_not_crash(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        info = self._ingest(columns, [])
        self.assertEqual(info["rows"], 0)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM jmeter_samples").fetchone()[0], 0
            )
            out = self.dir / "charts"
            produced = generate_charts(con, out, 60_000)
            self.assertEqual(produced, [])
        finally:
            con.close()

    def test_parquet_round_trip(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "A", "200", "true"],
            [self.BASE + 1000, 200, "A", "200", "true"],
        ]
        info = self._ingest(columns, rows)
        con = duckdb.connect()
        try:
            count = con.execute(
                f"SELECT COUNT(*) FROM read_parquet({_qstr(self.parquet.as_posix())})"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, info["rows"])

    def test_statistics_values(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "A", "200", "true"],
            [self.BASE + 1000, 200, "A", "200", "true"],
            [self.BASE + 2000, 300, "A", "200", "true"],
            [self.BASE + 3000, 400, "A", "500", "false"],
        ]
        self._ingest(columns, rows)
        con = duckdb.connect(str(self.db))
        try:
            build_statistics(con, self.dir / "stats")
            row = con.execute(
                "SELECT * FROM jmeter_statistics WHERE label = 'A'"
            ).df().iloc[0]
        finally:
            con.close()
        self.assertEqual(int(row["samples"]), 4)
        self.assertEqual(int(row["failures"]), 1)
        self.assertAlmostEqual(float(row["average"]), 250.0)
        self.assertAlmostEqual(float(row["median"]), 250.0)

    def test_bucket_boundaries_and_chart_output(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "A", "200", "true"],
            [self.BASE + 59_999, 200, "A", "200", "true"],
            [self.BASE + 60_000, 300, "A", "200", "true"],
        ]
        self._ingest(columns, rows)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            out = self.dir / "charts"
            produced = generate_charts(con, out, 60_000)
        finally:
            con.close()
        self.assertTrue(produced)
        self.assertTrue((out / "hits_per_second.png").exists())
        self.assertTrue((out / "response_time_over_time.png").exists())
        self.assertGreater((out / "hits_per_second.png").stat().st_size, 0)

    def test_controller_fallback_without_message_column(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "T", "200", "true"],
            [self.BASE, 100, "T-0", "200", "true"],
            [self.BASE, 100, "T-0-0", "200", "true"],
            [self.BASE, 100, "T-0-1", "200", "true"],
        ]
        info = self._ingest(columns, rows)
        self.assertGreaterEqual(info["controllers"], 1)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            controllers = set(
                con.execute(
                    "SELECT DISTINCT label FROM jmeter_samples WHERE is_controller"
                ).df()["label"]
            )
            requests = set(
                con.execute(
                    "SELECT DISTINCT label FROM jmeter_samples WHERE NOT is_controller"
                ).df()["label"]
            )
        finally:
            con.close()
        self.assertIn("T", controllers)
        self.assertIn("T-0-0", requests)
        self.assertIn("T-0-1", requests)

    def test_hierarchy_fallback_when_messages_are_not_transactions(self):
        columns = [
            "timeStamp", "elapsed", "label", "responseCode", "success",
            "responseMessage",
        ]
        rows = [
            [self.BASE, 100, "Home", "200", "OK", "true"],
            [self.BASE, 100, "Home-0", "200", "OK", "true"],
            [self.BASE, 100, "Home-1", "200", "OK", "true"],
            [self.BASE, 100, "Search", "200", "OK", "true"],
        ]
        info = self._ingest(columns, rows)
        self.assertEqual(info["controllers"], 2)  # Home and Search
        self.assertEqual(info["requests"], 2)  # Home-0 and Home-1
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            controllers = set(
                con.execute(
                    "SELECT DISTINCT label FROM jmeter_samples WHERE is_controller"
                ).df()["label"]
            )
        finally:
            con.close()
        self.assertEqual(controllers, {"Home", "Search"})

    def test_hyphenated_labels_are_not_controllers(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "API-v1", "200", "true"],
            [self.BASE, 100, "API-v2", "200", "true"],
        ]
        info = self._ingest(columns, rows)
        self.assertEqual(info["controllers"], 0)

    def test_single_row_percentiles_use_r6(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        self._ingest(columns, [[self.BASE, 123, "A", "200", "true"]])
        con = duckdb.connect(str(self.db))
        try:
            build_statistics(con, self.dir / "stats")
            row = con.execute(
                "SELECT * FROM jmeter_statistics WHERE label = 'A'"
            ).df().iloc[0]
        finally:
            con.close()
        for column in ("median", "p90", "p95", "p99"):
            self.assertEqual(float(row[column]), 123.0)

    def test_microsecond_timestamps_are_scaled(self):
        columns = ["timeStamp", "elapsed", "label"]
        rows = [[self.BASE * 1000, 100, "A"]]
        info = self._ingest(columns, rows)
        self.assertEqual(info["scaled"], "microseconds->milliseconds")
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            value = con.execute("SELECT timestamp FROM jmeter_samples").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(value, self.BASE)

    def test_directory_input_unions_by_name(self):
        batch = self.dir / "batch"
        batch.mkdir()
        file_a = batch / "a.jtl"
        file_b = batch / "b.jtl"
        with open(file_a, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timeStamp", "elapsed", "label", "responseCode", "success"])
            writer.writerow([self.BASE, 100, "A", "200", "true"])
        with open(file_b, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["timeStamp", "elapsed", "label", "responseCode", "success", "connect"]
            )
            writer.writerow([self.BASE, 100, "B", "200", "true", 42])
        info = ingest(batch, self.db, self.parquet)
        self.assertEqual(info["rows"], 2)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            values = con.execute(
                "SELECT label, connect FROM jmeter_samples ORDER BY label"
            ).df()
        finally:
            con.close()
        self.assertEqual(values["label"].tolist(), ["A", "B"])
        self.assertTrue(pd.isna(values["connect"].iloc[0]))
        self.assertEqual(int(values["connect"].iloc[1]), 42)

    def test_quoted_commas_are_preserved(self):
        columns = [
            "timeStamp", "elapsed", "label", "responseCode", "success",
            "responseMessage",
        ]
        self._ingest(columns, [[self.BASE, 100, "A", "200", "true", "hello, world"]])
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            message = con.execute(
                "SELECT response_message FROM jmeter_samples"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(message, "hello, world")

    def test_versus_request_groups_by_end_time_count_and_median(self):
        columns = [
            "timeStamp", "elapsed", "latency", "label", "responseCode", "success",
        ]
        rows = [
            [self.BASE, 100, 90, "A", "200", "true"],
            [self.BASE + 200, 300, 250, "B", "200", "true"],
            [self.BASE + 1000, 500, 400, "C", "200", "true"],
        ]
        self._ingest(columns, rows)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            df = _versus_request_df(con, "elapsed", "median_response_time_ms")
        finally:
            con.close()
        values = dict(
            zip(df["requests_per_second"], df["median_response_time_ms"])
        )
        self.assertEqual(values[1], 500.0)  # one request in the interval
        self.assertEqual(values[2], 200.0)  # two requests -> median of 100/300

    def _markdown(self, sample_rows=0, generated=None):
        con = duckdb.connect(str(self.db))
        try:
            path = self.dir / "report.md"
            generate_markdown(
                con,
                path,
                sample_rows=sample_rows,
                generated=generated
                or datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        finally:
            con.close()
        return path.read_text(encoding="utf-8")

    def test_report_flags_response_time_spike(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = []
        for bucket in range(20):
            elapsed = 3000 if bucket == 10 else 100
            for _ in range(2):
                rows.append([self.BASE + bucket * 60_000, elapsed, "T", "200", "true"])
        self._ingest(columns, rows)
        text = self._markdown()
        self.assertIn("# JMeter Performance Report", text)
        self.assertIn("Response Time Over Time", text)
        self.assertIn("Spike", text)
        self.assertIn("High", text)
        self.assertIn("3,000.00 ms", text)
        for phrase in ("because", "due to", "caused by", "root cause"):
            self.assertNotIn(phrase, text.lower())

    def test_report_flags_error_burst(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = []
        for bucket in range(6):
            for index in range(10):
                failed = bucket == 3
                rows.append(
                    [
                        self.BASE + bucket * 60_000 + index,
                        100,
                        "R",
                        "500" if failed else "200",
                        "false" if failed else "true",
                    ]
                )
        self._ingest(columns, rows)
        text = self._markdown()
        self.assertIn("Failed requests", text)
        self.assertIn("100.00%", text)

    def test_report_handles_empty_jtl(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        self._ingest(columns, [])
        text = self._markdown()
        self.assertIn("# JMeter Performance Report", text)
        self.assertIn("No material anomalies", text)
        self.assertNotIn("{{", text)

    def test_report_is_single_markdown_with_all_sections(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE, 100, "A", "200", "true"],
            [self.BASE + 60_000, 120, "A", "200", "true"],
        ]
        self._ingest(columns, rows)
        text = self._markdown()
        self.assertEqual(list(self.dir.glob("*.md")), [self.dir / "report.md"])
        for heading in (
            "## 1. Executive summary",
            "## 2. Run overview",
            "## 3. Per-transaction statistics",
            "## 4. Anomalies by dashboard chart",
            "## 5. Distribution and load relationships",
            "## 6. Data quality and sampling",
            "## 7. Suggested evidence to collect next",
        ):
            self.assertIn(heading, text)
        self.assertNotIn("{{", text)

    def test_report_ignores_ramp_edge_dips(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = []
        for bucket in range(20):
            elapsed = 200 if bucket in (0, 19) else 1000
            rows.append([self.BASE + bucket * 60_000, elapsed, "T", "200", "true"])
        self._ingest(columns, rows)
        text = self._markdown()
        self.assertNotIn("Dip", text)

    def test_report_sampling_is_deterministic(self):
        columns = ["timeStamp", "elapsed", "label", "responseCode", "success"]
        rows = [
            [self.BASE + index * 1000, 100 + index, "A", "200", "true"]
            for index in range(100)
        ]
        self._ingest(columns, rows)
        first = self._markdown(sample_rows=50)
        second = self._markdown(sample_rows=50)
        self.assertEqual(first, second)

    def test_module_toc_lists_every_top_level_function(self):
        source = Path(jr.__file__).read_text(encoding="utf-8")
        start = source.index("TABLE OF CONTENTS - debugging index")
        end = source.index("from __future__ import annotations", start)
        toc = source[start:end]
        missing = sorted(
            name
            for name, obj in vars(jr).items()
            if inspect.isfunction(obj)
            and obj.__module__ == jr.__name__
            and name not in toc
        )
        self.assertEqual(missing, [])

    def test_xml_input_is_rejected(self):
        path = self.dir / "results.xml.jtl"
        path.write_text(
            '<?xml version="1.0"?><testResults></testResults>', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            ingest(path, self.db, self.parquet)

    def test_missing_required_column_is_rejected(self):
        columns = ["timeStamp", "label"]
        with self.assertRaises(ValueError):
            self._ingest(columns, [[self.BASE, "A"]])

    def test_missing_input_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            ingest(self.dir / "nope.jtl", self.db, self.parquet)


def run_selftests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(JmeterReportTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(run_selftests())
