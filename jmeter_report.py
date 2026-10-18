"""Analyze a JMeter HTML dashboard and write test-summary.md + summary.json."""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from tsdownsample import MinMaxLTTBDownsampler

import jmeter_extract as jx

DEFAULT_CONFIG = {
    "budget": 120, "minmax_ratio": 4, "local_window": 1, "local_rel_delta": 0.25,
    "adjacent_rel_delta": 0.5, "adjacent_min_abs": 0.0, "baseline_window": 5,
    "baseline_min_points": 5, "baseline_k": 3.5, "sustained_min_run": 2,
    "sla_ms": None, "sla_by_label": {}, "thread_transition_min_delta": 0.5,
    "phase_boundaries": [], "chart_aliases": {}, "max_correlation_rows": 500,
    "max_compact_events": 1000, "round_decimals": 3,
}
CHART_ORDER = ["active_threads", "response_times", "connect_time",
               "transactions_per_second", "hits_per_second", "total_tps"]
UNITS = {"active_threads": "threads", "response_times": "ms", "connect_time": "ms",
         "transactions_per_second": "transactions/s", "hits_per_second": "hits/s",
         "total_tps": "transactions/s"}
TITLES = {"active_threads": "Active Threads Over Time",
          "response_times": "Response Times Over Time", "connect_time": "Connect Time Over Time",
          "transactions_per_second": "Transactions Per Second",
          "hits_per_second": "Hits Per Second", "total_tps": "Total Transactions Per Second"}
SEVERITY = {"info": 0, "warning": 1, "critical": 2}
TYPE_PRIORITY = ["sla_breach", "sustained_departure", "large_change", "baseline_deviation",
                 "local_peak", "local_trough", "global_max", "global_min",
                 "thread_transition", "phase_boundary"]
STAT_FIELDS = ["is_controller", "samples", "errors", "error_pct", "mean_ms", "median_ms",
               "min_ms", "max_ms", "p90_ms", "p95_ms", "p99_ms", "throughput_tps"]


@dataclass
class Event:
    chart: str
    label: str
    type: str
    x_ms: float
    value: float | None
    severity: str
    rules: list[str]
    reason: str
    related: dict = field(default_factory=dict)


@dataclass
class View:
    chart: str
    series: jx.Series
    events: list[Event]
    keep: list[int]
    protected: list[int]
    downsampled: bool
    overrun: int


def main(argv: list[str] | None = None) -> int:
    args, cfg = parse_args(argv)
    try:
        report = jx.resolve_input(args.input, args.graph_file)
    except jx.ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        verification = run(report, cfg, Path(args.out), args.input, args.json_detail)
    except jx.ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        jx.cleanup(report)
    if args.strict and not verification["all_preserved"]:
        print(f"error: {len(verification['failures'])} event point(s) missing from "
              "the downsampled output", file=sys.stderr)
        return 2
    return 0


def run(report: jx.Report, cfg: dict, out_dir: Path, input_arg: str,
        json_detail: str = "compact") -> dict:
    warnings = list(report.warnings)
    start, end = data_window(report)
    events = collect_events(report, cfg)
    phase_xs = [e.x_ms for e in events if e.type == "phase_boundary"]
    views: list[View] = []
    for key in CHART_ORDER:
        chart = report.charts.get(key)
        if not chart:
            continue
        chart_events = [e for e in events if e.chart == key]
        for series in chart.series:
            own = [e for e in chart_events if e.label == series.label]
            keep, protected, downsampled, overrun, warning = downsample_series(
                series.points, own, phase_xs, cfg)
            if warning:
                warnings.append(warning)
            views.append(View(key, series, own, keep, protected, downsampled, overrun))

    requests = request_statistics(report)
    codes = error_analysis(report, requests)
    correlation = correlate(report, codes, cfg, json_detail)
    verification = verify_preservation(views, events)

    check = codes["cross_check"]
    if check["statistics_samples"] is not None and not check["match"]:
        warnings.append(f"codesPerSecond totals ({check['codes_samples']} samples, "
                        f"{check['codes_errors']} errors) disagree with statistics "
                        f"({check['statistics_samples']} samples, {check['statistics_errors']} errors)")
    by_label = {r["label"]: r for r in requests["items"]}
    for item in (report.dashboard_tables.get("top_errors") or {}).get("items", []):
        data = item.get("data") or []
        expected = by_label.get(data[0], {}).get("samples") if data else None
        if expected is not None and expected != data[1]:
            warnings.append(f"top5 table samples for {data[0]} ({data[1]}) differ from "
                            f"request statistics ({expected}); request statistics are authoritative")

    out_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(report, views, events, requests, codes, correlation,
                            cfg, start, end, input_arg, warnings, json_detail, verification)
    (out_dir / "summary.json").write_text(
        json.dumps(clean(summary, 4), sort_keys=True, separators=(",", ":"),
                   allow_nan=False) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "test-summary.md").write_text(
        render_markdown(report, views, events, requests, codes, correlation,
                        cfg, start, end, warnings, input_arg, verification),
        encoding="utf-8", newline="\n")
    return verification


def parse_args(argv: list[str] | None) -> tuple[argparse.Namespace, dict]:
    p = argparse.ArgumentParser(description="Extract JMeter dashboard charts into an AI-ready summary.")
    p.add_argument("input", help="report directory, graph.js, or .zip")
    p.add_argument("--graph-file", help="explicit graph.js when discovery is ambiguous")
    p.add_argument("--out", default="out", help="output directory (default: out)")
    p.add_argument("--budget", type=int, help="points kept per series (default: 120)")
    p.add_argument("--minmax-ratio", type=int, help="MinMaxLTTB preselection ratio (default: 4)")
    p.add_argument("--sla", type=float, help="response-time SLA in ms (default: disabled)")
    p.add_argument("--json-detail", choices=("minimal", "compact", "full"), default="compact",
                   help="compact omits points/maps; minimal also omits events")
    p.add_argument("--strict", action="store_true",
                   help="exit 2 if any detected event point is missing from the output")
    p.add_argument("--config", help="JSON file of threshold overrides")
    args = p.parse_args(argv)
    cfg = dict(DEFAULT_CONFIG)
    if args.config:
        try:
            cfg.update(json.loads(Path(args.config).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            p.error(f"cannot read --config: {exc}")
    for name, value in (("budget", args.budget), ("minmax_ratio", args.minmax_ratio),
                        ("sla_ms", args.sla)):
        if value is not None:
            cfg[name] = value
    if cfg["budget"] < 3:
        p.error("--budget must be at least 3")
    return args, cfg


# --- anomalies -------------------------------------------------------------

def collect_events(report: jx.Report, cfg: dict) -> list[Event]:
    events = [e for key in CHART_ORDER if key in report.charts
              for series in report.charts[key].series
              for e in detect_events(key, series, cfg)]
    events += [Event("all", "", "phase_boundary", x, None, "info", ["phase_boundary"],
                     f"configured phase boundary at {iso(x)}")
               for x in parse_boundaries(cfg["phase_boundaries"])]
    return dedupe(events)


def detect_events(chart_key: str, series: jx.Series, cfg: dict) -> list[Event]:
    pts, n = series.points, len(series.points)
    if not n:
        return []
    events: list[Event] = []

    def add(i: int, typ: str, severity: str, reason: str, related: dict | None = None):
        events.append(Event(chart_key, series.label, typ, pts[i].x_ms, pts[i].y,
                            severity, [typ], reason, related or {}))

    imax = max(range(n), key=lambda i: pts[i].y)
    imin = min(range(n), key=lambda i: pts[i].y)
    add(imax, "global_max", "info", f"series maximum {pts[imax].y:.3f}")
    if imin != imax:
        add(imin, "global_min", "info", f"series minimum {pts[imin].y:.3f}")

    w, rel = cfg["local_window"], cfg["local_rel_delta"]
    for i in range(w, n - w):
        left = [p.y for p in pts[i - w:i]]
        right = [p.y for p in pts[i + 1:i + 1 + w]]
        if pts[i].y > max(left) * (1 + rel) and pts[i].y > max(right) * (1 + rel):
            add(i, "local_peak", "warning", f"local maximum {pts[i].y:.3f} above both neighbours")
        if pts[i].y < min(left) * (1 - rel) and pts[i].y < min(right) * (1 - rel):
            add(i, "local_trough", "warning", f"local minimum {pts[i].y:.3f} below both neighbours")

    for i in range(1, n):
        prev, delta = pts[i - 1].y, pts[i].y - pts[i - 1].y
        change = abs(delta) / abs(prev) if prev else (math.inf if delta else 0.0)
        if change >= cfg["adjacent_rel_delta"] and abs(delta) >= cfg["adjacent_min_abs"]:
            text = "from zero" if math.isinf(change) else f"{change * 100:.1f}%"
            add(i, "large_change", "warning",
                f"{'jump' if delta > 0 else 'drop'} {delta:+.3f} ({text}) from previous point")

    _baseline_events(pts, cfg, add)
    sla = cfg["sla_by_label"].get(series.label, cfg["sla_ms"])
    if chart_key == "response_times" and sla is not None:
        for a, b in _runs([p.y > sla for p in pts]):
            peak = max(range(a, b + 1), key=lambda i: pts[i].y)
            add(a, "sla_breach", "critical",
                f"{b - a + 1} bucket(s) above SLA {sla:g} ms; peak {pts[peak].y:.3f} ms",
                {"start_ms": pts[a].x_ms, "end_ms": pts[b].x_ms,
                 "count": b - a + 1, "peak_value": pts[peak].y})
    if chart_key == "active_threads":
        for i in range(1, n):
            if abs(pts[i].y - pts[i - 1].y) >= cfg["thread_transition_min_delta"]:
                add(i, "thread_transition", "info",
                    f"thread count {pts[i - 1].y:g} -> {pts[i].y:g}")
    return events


def _baseline_events(pts: list[jx.Point], cfg: dict, add) -> None:
    half = cfg["baseline_window"] // 2
    flags, scores = [], []
    for i in range(len(pts)):
        window = [p.y for p in pts[max(0, i - half):i + half + 1]]
        med = st.median(window) if len(window) >= cfg["baseline_min_points"] else None
        mad = st.median([abs(v - med) for v in window]) * 1.4826 if med is not None else 0.0
        score = abs(pts[i].y - med) / mad if mad else 0.0
        flags.append(bool(mad and score > cfg["baseline_k"]))
        scores.append(score)
    for first, last in _runs(flags):
        if last - first + 1 >= cfg["sustained_min_run"]:
            peak = max(range(first, last + 1), key=lambda i: scores[i])
            add(peak, "sustained_departure", "warning",
                f"{last - first + 1} consecutive points off baseline; peak robust z={scores[peak]:.1f}",
                {"start_ms": pts[first].x_ms, "end_ms": pts[last].x_ms,
                 "count": last - first + 1})
        else:
            add(first, "baseline_deviation", "warning", f"robust z={scores[first]:.1f} above rolling baseline")


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    runs, i = [], 0
    while i < len(flags):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(flags) and flags[j + 1]:
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def dedupe(events: list[Event]) -> list[Event]:
    merged: dict[tuple, Event] = {}
    for event in events:
        key = (event.chart, event.label, round(event.x_ms, 6))
        current = merged.get(key)
        if current is None:
            merged[key] = event
            continue
        current.rules = sorted(set(current.rules) | set(event.rules))
        current.reason = "; ".join(dict.fromkeys([current.reason, event.reason]))
        current.severity = max(current.severity, event.severity, key=SEVERITY.get)
        if TYPE_PRIORITY.index(event.type) < TYPE_PRIORITY.index(current.type):
            current.type = event.type
        current.related.update(event.related)
    order = {name: i for i, name in enumerate(CHART_ORDER + ["all"])}
    return sorted(merged.values(), key=lambda e: (order.get(e.chart, 9), e.label, e.x_ms, e.type))


# --- downsampling ----------------------------------------------------------

def downsample_series(points, events, extra_xs, cfg):
    n = len(points)
    if not n:
        return [], [], False, 0, None
    event_xs = {e.x_ms for e in events} | set(extra_xs)
    protected = sorted({i for i, p in enumerate(points) if p.x_ms in event_xs} | {0, n - 1})
    if n <= cfg["budget"]:
        return list(range(n)), protected, False, 0, None
    if len(protected) >= cfg["budget"]:
        return protected, protected, True, len(protected) - cfg["budget"], None
    try:
        x = np.array([p.x_ms for p in points], dtype=np.float64)
        y = np.array([p.y for p in points], dtype=np.float64)
        idx = MinMaxLTTBDownsampler().downsample(x, y, n_out=cfg["budget"],
                                                 minmax_ratio=cfg["minmax_ratio"])
        keep = sorted(set(int(i) for i in idx) | set(protected))
    except Exception as exc:  # defensive fallback: keep a uniform stride
        step = max(1, n // cfg["budget"])
        keep = sorted(set(range(0, n, step)) | {n - 1} | set(protected))
        return keep, protected, True, max(0, len(keep) - cfg["budget"]), \
            f"downsampler failed ({exc}); used uniform stride"
    return keep, protected, True, max(0, len(keep) - cfg["budget"]), None


# --- statistics and errors -------------------------------------------------

def data_window(report: jx.Report) -> tuple[float, float]:
    xs = [p.x_ms for key in CHART_ORDER if key in report.charts
          for s in report.charts[key].series for p in s.points]
    return (min(xs), max(xs)) if xs else (0.0, 0.0)


def series_stats(points, start_ms: float) -> dict:
    if not points:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None,
                "min_elapsed_s": None, "max_elapsed_s": None}
    ys = [p.y for p in points]
    imin = min(range(len(points)), key=lambda i: points[i].y)
    imax = max(range(len(points)), key=lambda i: points[i].y)
    return {"count": len(points), "mean": st.mean(ys), "median": st.median(ys),
            "min": points[imin].y, "min_elapsed_s": (points[imin].x_ms - start_ms) / 1000,
            "max": points[imax].y, "max_elapsed_s": (points[imax].x_ms - start_ms) / 1000}


def request_statistics(report: jx.Report) -> dict:
    if report.statistics:
        source = "statistics.json"
        rows = [{
            "label": label, "is_controller": _controller_flag(report, label),
            "samples": i.get("sampleCount"), "errors": i.get("errorCount"),
            "error_pct": i.get("errorPct"), "mean_ms": i.get("meanResTime"),
            "median_ms": i.get("medianResTime"), "min_ms": i.get("minResTime"),
            "max_ms": i.get("maxResTime"), "p90_ms": i.get("pct1ResTime"),
            "p95_ms": i.get("pct2ResTime"), "p99_ms": i.get("pct3ResTime"),
            "throughput_tps": i.get("throughput"), "received_kbps": i.get("receivedKBytesPerSec"),
            "sent_kbps": i.get("sentKBytesPerSec"),
        } for label, i in report.statistics.items()]
        overall = next((r for r in rows if r["label"] == "Total"), None)
    else:
        table = report.dashboard_tables.get("statistics") or {}
        source = "dashboard statisticsTable" if table.get("overall", {}).get("data") else None
        overall = _stat_row(table["overall"]["data"], report) if source else None
        rows = [_stat_row(it["data"], report) for it in table.get("items", []) if it.get("data")]
    rows.sort(key=lambda r: (r["label"] == "Total", r["label"]))
    return {"source": source, "overall": overall, "items": rows}


def _stat_row(data: list, report: jx.Report) -> dict:
    return {
        "label": data[0], "is_controller": _controller_flag(report, data[0]),
        "samples": data[1], "errors": data[2], "error_pct": data[3],
        "mean_ms": data[4], "min_ms": data[5], "max_ms": data[6], "median_ms": data[7],
        "p90_ms": data[8], "p95_ms": data[9], "p99_ms": data[10], "throughput_tps": data[11],
        "received_kbps": data[12] if len(data) > 12 else None,
        "sent_kbps": data[13] if len(data) > 13 else None,
    }


def _controller_flag(report: jx.Report, label: str):
    for key in CHART_ORDER:
        for series in (report.charts.get(key).series if key in report.charts else []):
            if series.label == label:
                return series.is_controller
    return None


def classify_code(label: str) -> str:
    code = label.strip()
    return "success" if len(code) == 3 and code.isdigit() and 200 <= int(code) < 400 else "error"


def error_analysis(report: jx.Report, requests: dict) -> dict:
    codes = report.charts.get("error_codes")
    per_bucket = []
    if codes:
        values = {s.label: {p.x_ms: p.y for p in s.points} for s in codes.series}
        for x in sorted({p.x_ms for s in codes.series for p in s.points}):
            total = sum(m.get(x, 0.0) for m in values.values())
            bad = sum(m.get(x, 0.0) for label, m in values.items() if classify_code(label) == "error")
            per_bucket.append({"x_ms": x, "total_rps": total, "error_rps": bad,
                               "error_rate": bad / total if total else None})
    aggregate = [{"type": d[0], "count": d[1], "pct_of_errors": d[2], "pct_of_samples": d[3]}
                 for item in (report.dashboard_tables.get("errors") or {}).get("items", [])
                 if (d := item.get("data") or [])]
    top = report.dashboard_tables.get("top_errors") or {}
    rt_chart = report.charts.get("response_times")
    rt_labels = {s.label for s in rt_chart.series} if rt_chart else set()
    stats_by_label = {r["label"]: r for r in requests["items"]}
    top_by_sampler = {}
    for item in top.get("items", []):
        data = item.get("data") or []
        if data:
            entry = _top_entry(data)
            entry["in_response_time_chart"] = data[0] in rt_labels
            entry["statistics_errors"] = stats_by_label.get(data[0], {}).get("errors")
            top_by_sampler[data[0]] = entry
    top_overall = _top_entry(top["overall"]["data"]) if top.get("overall", {}).get("data") else None

    granularity_s = (codes.granularity_ms if codes else 60000) / 1000
    total_samples = round(sum(r["total_rps"] for r in per_bucket) * granularity_s)
    total_errors = round(sum(r["error_rps"] for r in per_bucket) * granularity_s)
    overall = requests.get("overall") or {}
    return {
        "classification": {"success": "200-399", "error": "4xx/5xx and non-HTTP"},
        "per_bucket": per_bucket, "aggregate": aggregate,
        "top_by_sampler": top_by_sampler, "top_overall": top_overall,
        "cross_check": {"codes_samples": total_samples, "statistics_samples": overall.get("samples"),
                        "codes_errors": total_errors, "statistics_errors": overall.get("errors"),
                        "match": overall.get("samples") == total_samples
                                 and overall.get("errors") == total_errors},
    }


def _top_entry(data: list) -> dict:
    return {"samples": data[1], "errors": data[2],
            "top": [{"type": data[i], "count": data[i + 1]}
                    for i in range(3, len(data) - 1, 2) if data[i]]}


def correlate(report: jx.Report, codes: dict, cfg: dict, detail: str = "compact") -> list[dict]:
    maps, flags = {}, {}
    for key in CHART_ORDER:
        chart = report.charts.get(key)
        if chart:
            maps[key] = {s.label: {p.x_ms: p.y for p in s.points} for s in chart.series}
            flags[key] = {s.label: s.is_controller for s in chart.series}
    errors = {r["x_ms"]: r for r in codes["per_bucket"]}
    xs = set(errors)
    for chart_map in maps.values():
        for series_map in chart_map.values():
            xs.update(series_map)
    rows = []
    for x in sorted(xs):
        rt = {label: m[x] for label, m in maps.get("response_times", {}).items() if x in m}
        ct = {label: m[x] for label, m in maps.get("connect_time", {}).items() if x in m}
        threads = [m[x] for m in maps.get("active_threads", {}).values() if x in m]
        tps = [m[x] for m in maps.get("total_tps", {}).values() if x in m]
        hits = [m[x] for m in maps.get("hits_per_second", {}).values() if x in m]
        error = errors.get(x)
        ctrl = [v for label, v in rt.items() if flags.get("response_times", {}).get(label)]
        rows.append({
            "x_ms": x, "total_rps": error["total_rps"] if error else None,
            "error_rps": error["error_rps"] if error else None,
            "error_rate": error["error_rate"] if error else None,
            "threads_total": sum(threads) if threads else None,
            "tps_total": sum(tps) if tps else None,
            "hits_total": sum(hits) if hits else None,
            "rt_max_ms": max(rt.values()) if rt else None,
            "ct_max_ms": max(ct.values()) if ct else None,
            "rt_controller_mean_ms": st.mean(ctrl) if ctrl else None,
            "rt": rt, "connect": ct})
    limit = max(1, int(cfg["max_correlation_rows"]))
    if detail == "minimal":
        limit = min(limit, 100)
    if len(rows) > limit:
        step = max(1, len(rows) // limit)
        keep = set(range(0, len(rows), step)) | {len(rows) - 1}
        keep |= {i for i, r in enumerate(rows) if r["error_rps"]}
        rows = [rows[i] for i in sorted(keep)]
    return rows


# --- rendering -------------------------------------------------------------

def render_markdown(report, views, events, requests, codes, correlation,
                    cfg, start, end, warnings, input_arg, verification=None) -> str:
    lines = ["# JMeter Dashboard Analysis", "", "## 1. Run and extraction", ""]
    gran = next((report.charts[k].granularity_ms for k in CHART_ORDER if k in report.charts), 60000)
    observed = sorted({p.x_ms for k in CHART_ORDER if k in report.charts
                       for s in report.charts[k].series for p in s.points})
    missing = [x for x in range(int(start), int(end) + 1, gran) if x not in observed]
    lines += [
        f"- Input: `{Path(input_arg).name}`",
        f"- Source files: graph.js `{report.paths.get('graph_js')}`; statistics.json "
        f"`{report.paths.get('statistics_json') or 'not found'}`; dashboard.js "
        f"`{report.paths.get('dashboard_js') or 'not found'}`",
        f"- Data window: {iso(start)} to {iso(end)} ({(end - start) / 1000:g} s)",
        f"- Bucket interval: {gran} ms; observed buckets: {len(observed)}; nominal grid: "
        f"{len(range(int(start), int(end) + 1, gran))}; missing: "
        + (", ".join(iso(x) for x in missing) if missing else "none"),
        "- Charts: " + "; ".join(f"{k} — `{report.charts[k].var_name}` "
                                 f"({len(report.charts[k].series)} series)"
                                 for k in CHART_ORDER if k in report.charts), ""]
    lines += (["Warnings:"] + [f"- {w}" for w in warnings] if warnings else ["Warnings: none"]) + [""]

    lines += ["## 2. Request-level statistics", "",
              f"Source: {requests['source'] or 'not available'}. Request-level values; JMeter "
              "`pct1ResTime`/`pct2ResTime`/`pct3ResTime` are reported as p90/p95/p99.", ""]
    if requests["items"]:
        rows = [[r["label"], _num(r["samples"], 0), _num(r["errors"], 0), _pct(r["error_pct"]),
                 _num(r["mean_ms"]), _num(r["median_ms"]), _num(r["min_ms"]), _num(r["max_ms"]),
                 _num(r["p90_ms"]), _num(r["p95_ms"]), _num(r["p99_ms"]), _num(r["throughput_tps"])]
                for r in requests["items"]]
        lines += [md_table(["Label", "Samples", "Errors", "Error %", "Mean ms", "Median ms",
                            "Min ms", "Max ms", "p90 ms", "p95 ms", "p99 ms", "TPS"], rows), ""]
    else:
        lines += ["No request-level statistics available.", ""]

    lines += ["## 3. Bucket-level series statistics", "",
              "Computed from per-bucket chart values (not request latency percentiles).", ""]
    stat_rows = []
    for view in views:
        s = series_stats(view.series.points, start)
        stat_rows.append([TITLES.get(view.chart, view.chart), view.series.label,
                          "yes" if view.series.is_controller else "no", s["count"],
                          _num(s["mean"]), _num(s["median"]),
                          f"{_num(s['min'])} ({_num(s['min_elapsed_s'])} s)",
                          f"{_num(s['max'])} ({_num(s['max_elapsed_s'])} s)"])
    lines += [md_table(["Chart", "Series", "Controller", "Points", "Mean", "Median",
                        "Min (elapsed)", "Max (elapsed)"], stat_rows), ""]

    lines += ["## 4. Anomalies and phase events", ""]
    if events:
        counts = {}
        for e in events:
            counts[e.severity] = counts.get(e.severity, 0) + 1
        lines += ["Counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), ""]
        lines += [md_table(["Chart", "Series", "Type", "Elapsed s", "Timestamp", "Value",
                            "Severity", "Rules / reason"],
                           [[TITLES.get(e.chart, e.chart) if e.chart != "all" else "all",
                             e.label or "-", e.type, _num((e.x_ms - start) / 1000), iso(e.x_ms),
                             _num(e.value), e.severity,
                             "; ".join(e.rules) + " — " + e.reason] for e in events]), ""]
    else:
        lines += ["No events detected.", ""]

    lines += ["## 5. Downsampled series", "",
              f"MinMaxLTTB budget: {cfg['budget']} points per series (preselection ratio "
              f"{cfg['minmax_ratio']}); first/last and event points are always kept.", ""]
    if verification:
        status = "PASS" if verification["all_preserved"] else "FAIL"
        lines += [f"**Preservation check: {status}** — "
                  f"{verification['events_preserved']}/{verification['events_checked']} "
                  f"event points present with unchanged values across "
                  f"{verification['series_checked']} series.", ""]
        for failure in verification.get("failures", []):
            lines += [f"- FAIL {failure['chart']}/{failure['label']} "
                      f"{failure['type']} at {failure['ts']}: {failure['problem']}"]
        if verification["failures"]:
            lines += [""]
    lines += [md_table(["Chart", "Series", "Original", "Kept", "Downsampled", "Protected", "Overrun"],
                       [[TITLES.get(v.chart, v.chart), v.series.label, len(v.series.points),
                         len(v.keep), "yes" if v.downsampled else "no", len(v.protected), v.overrun]
                        for v in views]), ""]
    for key in CHART_ORDER:
        chart_views = [v for v in views if v.chart == key]
        if not chart_views:
            continue
        lines += [f"### {report.charts[key].title or TITLES[key]} (unit: {UNITS[key]})", ""]
        for view in chart_views:
            lines += [f"#### {view.series.label}", "",
                      md_table(["elapsed_s", "value", "events"],
                               [[_num((view.series.points[i].x_ms - start) / 1000),
                                 _num(view.series.points[i].y),
                                 _event_cell(view, events, view.series.points[i].x_ms)]
                                for i in view.keep]), ""]

    lines += ["## 6. Error analysis", "",
              f"Classification: {codes['classification']['success']} = success; "
              f"{codes['classification']['error']} = error. Absent code series at a bucket count "
              "as zero; absent chart series are missing, not zero.", ""]
    lines += ["### 6.1 Aggregate error types", ""]
    lines += [md_table(["Type", "Count", "% of errors", "% of samples"],
                       [[a["type"], a["count"], _pct(a["pct_of_errors"]), _pct(a["pct_of_samples"])]
                        for a in codes["aggregate"]]) if codes["aggregate"]
              else "No aggregate error table found.", ""]
    lines += ["### 6.2 Top errors by sampler", ""]
    lines += [md_table(["Label", "Samples", "Errors", "In RT chart", "Top errors"],
                       [[label, e["samples"], e["errors"],
                         "yes" if e.get("in_response_time_chart") else "no",
                         "; ".join(f"{t['type']} x{t['count']}" for t in e["top"]) or "-"]
                        for label, e in sorted(codes["top_by_sampler"].items())])
              if codes["top_by_sampler"] else "No per-sampler error table found.", ""]
    lines += ["### 6.3 Per-bucket errors and correlation", ""]
    if correlation:
        lines += [md_table(["elapsed_s", "Timestamp", "Total rps", "Error rps", "Error %",
                            "Threads", "TPS total", "Hits/s", "RT max ms", "CT max ms",
                            "RT controller mean ms"],
                           [[_num((r["x_ms"] - start) / 1000), iso(r["x_ms"]),
                             _num(r["total_rps"]), _num(r["error_rps"]), _pct_rate(r["error_rate"]),
                             _num(r["threads_total"]), _num(r["tps_total"]), _num(r["hits_total"]),
                             _num(r["rt_max_ms"]), _num(r["ct_max_ms"]),
                             _num(r["rt_controller_mean_ms"])] for r in correlation]), "",
                  "`-` means no series data at that bucket.", "", "### 6.4 Factual observations", ""]
        for r in sorted((r for r in correlation if r["error_rate"]),
                        key=lambda r: r["error_rate"], reverse=True)[:3]:
            lines.append(f"- {iso(r['x_ms'])}: error rate {_pct_rate(r['error_rate'])}; "
                         f"threads {_num(r['threads_total'])}; RT max {_num(r['rt_max_ms'])} ms; "
                         f"CT max {_num(r['ct_max_ms'])} ms.")
        for label, key, unit in (("Max response time", "rt_max_ms", "ms"),
                                 ("Max connect time", "ct_max_ms", "ms")):
            row = max((r for r in correlation if r[key] is not None), key=lambda r: r[key], default=None)
            if row:
                lines.append(f"- {label} {_num(row[key])} {unit} at {iso(row['x_ms'])}.")
        lines += ["- These are co-occurrences at the same bucket; they do not establish causality.", ""]
    else:
        lines += ["No per-bucket error data available.", ""]

    lines += ["## 7. Assumptions and limitations", "",
              "- Chart points are sorted by timestamp before use; JMeter serializes them unordered.",
              "- Bucket statistics summarize chart values, not individual requests.",
              "- Request-level percentiles come from statistics.json (or the dashboard table) and are not recomputed.",
              "- Dashboard error totals are test-wide; per-label errors cannot be localized to a bucket.",
              "- Events are statistical/rule-based signals, not confirmed root causes or SLA breaches "
              "unless an SLA rule is configured.",
              "- `-` = no datapoint for that series/bucket; values are never interpolated or zero-filled.",
              ""]
    return "\n".join(lines)


def _event_cell(view: View, events: list[Event], x_ms: float) -> str:
    rules = [r for e in events if e.x_ms == x_ms
             and ((e.chart == view.chart and e.label == view.series.label)
                  or e.type == "phase_boundary") for r in e.rules]
    return ",".join(sorted(set(rules))) if rules else "-"


def verify_preservation(views: list[View], events: list[Event]) -> dict:
    """Every detected event must still exist, unchanged, in the kept points."""
    checked, failures = 0, []
    for view in views:
        by_x = {p.x_ms: p.y for p in view.series.points}
        kept = {view.series.points[i].x_ms for i in view.keep}
        for event in view.events:
            checked += 1
            if event.x_ms not in kept:
                failures.append({"chart": view.chart, "label": event.label,
                                 "type": event.type, "ts": iso(event.x_ms),
                                 "problem": "timestamp dropped"})
            elif by_x.get(event.x_ms) != event.value:
                failures.append({"chart": view.chart, "label": event.label,
                                 "type": event.type, "ts": iso(event.x_ms),
                                 "problem": "value changed"})
    for event in (e for e in events if e.type == "phase_boundary"):
        for view in views:
            if any(p.x_ms == event.x_ms for p in view.series.points):
                checked += 1
                if event.x_ms not in {view.series.points[i].x_ms for i in view.keep}:
                    failures.append({"chart": view.chart, "label": view.series.label,
                                     "type": event.type, "ts": iso(event.x_ms),
                                     "problem": "timestamp dropped"})
    return {"events_checked": checked, "events_preserved": checked - len(failures),
            "all_preserved": not failures, "series_checked": len(views),
            "failures": failures[:50]}


def build_summary(report, views, events, requests, codes, correlation,
                  cfg, start, end, input_arg, warnings, detail: str = "compact",
                  verification: dict | None = None) -> dict:
    observed = sorted({p.x_ms for k in CHART_ORDER if k in report.charts
                       for s in report.charts[k].series for p in s.points})
    gran = next((report.charts[k].granularity_ms for k in CHART_ORDER if k in report.charts), 60000)
    nominal = list(range(int(start), int(end) + 1, gran))
    charts = {}
    for key in CHART_ORDER:
        chart = report.charts.get(key)
        if not chart:
            continue
        series = []
        for v in (v for v in views if v.chart == key):
            entry = {"label": v.series.label, "is_controller": v.series.is_controller,
                     "is_overall": v.series.is_overall,
                     "original_points": len(v.series.points), "kept_points": len(v.keep),
                     "downsampled": v.downsampled, "protected_points": len(v.protected),
                     "overrun": v.overrun, "stats": series_stats(v.series.points, start)}
            if detail == "full":
                entry["points"] = [[(v.series.points[i].x_ms - start) / 1000,
                                    v.series.points[i].y] for i in v.keep]
                entry["events"] = [event_dict(e, start) for e in v.events]
            series.append(entry)
        charts[key] = {"title": chart.title or TITLES[key], "unit": UNITS[key],
                       "granularity_ms": gran, "series": series}

    event_dicts = [event_dict(e, start, compact=(detail != "full")) for e in events]
    total_events = len(event_dicts)
    if detail == "minimal":
        event_dicts = []
    elif detail == "compact":
        cap = max(0, int(cfg["max_compact_events"]))
        if total_events > cap:
            ranked = sorted(event_dicts, key=lambda e: (-SEVERITY[e["severity"]], e["elapsed_s"]))
            event_dicts = sorted(ranked[:cap], key=_event_sort_key)

    errors = {"classification": codes["classification"], "aggregate": codes["aggregate"],
              "top_by_sampler": codes["top_by_sampler"], "top_overall": codes["top_overall"],
              "cross_check": codes["cross_check"]}
    if detail == "full":  # compact mode keeps the same values in `correlation`
        errors["per_bucket"] = [{"elapsed_s": (r["x_ms"] - start) / 1000,
                                 "total_rps": r["total_rps"], "error_rps": r["error_rps"],
                                 "error_rate": r["error_rate"]} for r in codes["per_bucket"]]
    return {
        "schema_version": "1.0",
        "tool": {"name": "jmeter-dashboard-extract", "version": "1.0.0",
                 "json_detail": detail},
        "source": {"input": Path(input_arg).name, "paths": report.paths, "warnings": warnings},
        "run": {"start_ts": iso(start), "end_ts": iso(end), "duration_s": (end - start) / 1000,
                "granularity_ms": gran, "observed_buckets": len(observed),
                "nominal_buckets": len(nominal),
                "missing_bucket_ts": [iso(x) for x in nominal if x not in observed]},
        "settings": {k: cfg[k] for k in sorted(cfg)},
        "statistics": _statistics_block(requests, detail),
        "charts": charts,
        "events": event_dicts,
        "events_meta": {"total": total_events, "included": len(event_dicts)},
        "verification": verification or {},
        "errors": errors,
        "correlation": [corr_dict(r, start, detail) for r in correlation],
    }


def _statistics_block(requests: dict, detail: str) -> dict:
    labels = [r for r in requests["items"] if r["label"] != "Total"]
    block = {"source": requests["source"],
             "percentile_map": {"pct1": "p90", "pct2": "p95", "pct3": "p99"},
             "overall": requests["overall"]}
    if detail == "full":
        block["by_label"] = {r["label"]: r for r in labels}
    else:  # array rows: same data with far less JSON key overhead
        block["by_label_fields"] = STAT_FIELDS
        block["by_label"] = {r["label"]: [r.get(f) for f in STAT_FIELDS] for r in labels}
    return block


def _event_sort_key(event: dict) -> tuple:
    order = {name: i for i, name in enumerate(CHART_ORDER + ["all"])}
    return (order.get(event["chart"], 9), event["label"], event["elapsed_s"], event["type"])


def event_dict(event: Event, start: float, compact: bool = False) -> dict:
    out = {"chart": event.chart, "label": event.label, "type": event.type,
           "elapsed_s": (event.x_ms - start) / 1000, "ts": iso(event.x_ms),
           "value": event.value, "severity": event.severity, "rules": event.rules}
    if not compact:  # reason/related stay in test-summary.md
        out["reason"] = event.reason
        out["related"] = event.related
    return out


def corr_dict(row: dict, start: float, detail: str = "compact") -> dict:
    out = {k: v for k, v in row.items() if k not in ("x_ms", "rt", "connect")}
    out.update(elapsed_s=(row["x_ms"] - start) / 1000, ts=iso(row["x_ms"]))
    if detail == "full":
        out["rt"] = {k: row["rt"][k] for k in sorted(row["rt"])}
        out["connect"] = {k: row["connect"][k] for k in sorted(row["connect"])}
    return out


# --- small helpers ---------------------------------------------------------

def iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_boundaries(values: list) -> list[float]:
    return [float(v) if isinstance(v, (int, float))
            else datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000
            for v in values]


def clean(value, decimals: int = 4):
    if isinstance(value, float):
        return None if not math.isfinite(value) else (0.0 if round(value, decimals) == 0
                                                      else round(value, decimals))
    if isinstance(value, dict):
        return {k: clean(v, decimals) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v, decimals) for v in value]
    return value


def md_table(headers: list, rows: list[list]) -> str:
    lines = ["| " + " | ".join(_escape(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(_escape(str(c)) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _num(value, decimals: int = 3) -> str:
    if value is None:
        return "-"
    value = float(value)
    if not math.isfinite(value):
        return "-"
    value = round(value, decimals)
    value = 0.0 if value == 0 else value
    text = f"{value:.{decimals}f}"
    return (text.rstrip("0").rstrip(".") if decimals else text) or "0"


def _pct(value) -> str:
    return "-" if value is None else _num(value, 2) + "%"


def _pct_rate(value) -> str:
    return "-" if value is None else _num(value * 100, 2) + "%"


if __name__ == "__main__":
    raise SystemExit(main())
