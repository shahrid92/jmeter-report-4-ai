"""Discover and parse a JMeter 5.x HTML dashboard. No analysis here."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import statistics
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_CHART_VARS = {
    "active_threads": ["activeThreadsOverTimeInfos"],
    "response_times": ["responseTimesOverTimeInfos"],
    "connect_time": ["connectTimeOverTimeInfos"],
    "transactions_per_second": ["transactionsPerSecondInfos"],
    "hits_per_second": ["hitsPerSecondInfos"],
    "total_tps": ["totalTPSInfos"],
    "error_codes": ["codesPerSecondInfos"],
}
TABLE_IDS = {
    "statistics": "statisticsTable",
    "errors": "errorsTable",
    "top_errors": "top5ErrorsBySamplerTable",
}


class ExtractionError(Exception):
    pass


@dataclass
class Point:
    x_ms: float
    y: float


@dataclass
class Series:
    label: str
    is_controller: bool = False
    is_overall: bool = False
    points: list[Point] = field(default_factory=list)
    dropped_nonfinite: int = 0
    dropped_duplicates: int = 0


@dataclass
class Chart:
    key: str
    var_name: str
    title: str | None
    granularity_ms: int
    min_x_ms: float | None
    max_x_ms: float | None
    series: list[Series] = field(default_factory=list)


@dataclass
class Report:
    charts: dict[str, Chart]
    statistics: dict[str, Any] | None
    dashboard_tables: dict[str, Any]
    paths: dict[str, str | None]
    warnings: list[str] = field(default_factory=list)
    temp_dir: Path | None = None


def _inside(path: Path, root: Path) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path.name
    return path.name if rel.startswith("..") else rel.replace(os.sep, "/")


def _safe_extract_zip(path: Path) -> Path:
    """Extract to a fresh temp dir, rejecting traversal and symlinks."""
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > 10_000:
            raise ExtractionError(f"zip has too many members ({len(infos)})")
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in PurePosixPath(name).parts:
                raise ExtractionError(f"unsafe zip member: {info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ExtractionError(f"zip symlink member: {info.filename}")
            total += info.file_size
        if total > 2 * 1024**3:
            raise ExtractionError("zip expands to more than 2 GiB")
        temp = Path(tempfile.mkdtemp(prefix="jmeter-report-"))
        zf.extractall(temp)
    return temp


def _infer_root(graph_file: Path) -> Path:
    for parent in [graph_file.parent, *graph_file.parents]:
        if (parent / "statistics.json").is_file() or (parent / "index.html").is_file():
            return parent
        if (parent / "content").is_dir():
            return parent
    return graph_file.parent


def _discover_graph(root: Path) -> Path:
    known = [root / "content" / "js" / "graph.js", root / "graph.js"]
    found = [p for p in known if p.is_file()]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ExtractionError(
            "multiple graph.js candidates:\n  " + "\n  ".join(map(str, found))
            + "\npass --graph-file to choose one"
        )
    candidates = sorted(root.rglob("graph.js"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ExtractionError(f"no graph.js found under {root}")
    raise ExtractionError(
        "multiple graph.js candidates:\n  " + "\n  ".join(map(str, candidates))
        + "\npass --graph-file to choose one"
    )


def resolve_input(arg: str, graph_file: str | None = None) -> Report:
    """Resolve dir / graph.js / zip and parse everything. Caller cleans temp_dir."""
    src = Path(arg).expanduser()
    if not src.exists():
        raise ExtractionError(f"input does not exist: {src}")
    temp = None
    if src.is_file() and src.suffix.lower() == ".zip":
        temp = _safe_extract_zip(src)
        src = temp
    if graph_file:
        graph = Path(graph_file).expanduser()
        if not graph.is_file():
            raise ExtractionError(f"--graph-file not found: {graph}")
    elif src.is_file():
        graph = src
    else:
        graph = _discover_graph(src)
    root = _infer_root(graph)

    def optional(name: str) -> Path | None:
        for folder in (root, root / "content" / "js", graph.parent):
            candidate = folder / name
            if candidate.is_file():
                return candidate
        hits = sorted(root.rglob(name))
        return hits[0] if hits else None

    dashboard = optional("dashboard.js")
    stats_path = optional("statistics.json")
    warnings: list[str] = []
    tables: dict[str, Any] = {}
    if dashboard:
        text = _read_text(dashboard)
        for key, table_id in TABLE_IDS.items():
            tables[key] = extract_table(text, table_id) or {}
    else:
        warnings.append("dashboard.js not found; error tables unavailable")
    statistics = None
    if stats_path:
        statistics = json.loads(_read_text(stats_path))
    else:
        warnings.append("statistics.json not found; using dashboard table if present")

    chart_text = _read_text(graph)
    charts: dict[str, Chart] = {}
    for key, names in DEFAULT_CHART_VARS.items():
        chart = extract_chart(chart_text, key, names)
        if chart is None:
            warnings.append(f"chart variable missing for {key} ({', '.join(names)})")
        else:
            charts[key] = chart
    if not any(k in charts for k in ("active_threads", "response_times", "connect_time")):
        raise ExtractionError("none of the three core chart variables were found")

    for key in DEFAULT_CHART_VARS:
        chart = charts.get(key)
        if chart:
            for series in chart.series:
                if series.dropped_nonfinite:
                    warnings.append(f"{key}/{series.label}: dropped {series.dropped_nonfinite} non-finite values")
                if series.dropped_duplicates:
                    warnings.append(f"{key}/{series.label}: dropped {series.dropped_duplicates} duplicate timestamps")
        if chart and chart.series:
            xs = [p.x_ms for s in chart.series for p in s.points]
            if xs and chart.min_x_ms is not None and abs(chart.min_x_ms - min(xs)) > chart.granularity_ms:
                warnings.append(f"{key}: minX disagrees with data; trusting data")
            if xs and chart.max_x_ms is not None and abs(chart.max_x_ms - max(xs)) > chart.granularity_ms:
                warnings.append(f"{key}: maxX disagrees with data; trusting data")

    return Report(
        charts=charts,
        statistics=statistics,
        dashboard_tables=tables,
        paths={
            "graph_js": _inside(graph, root),
            "statistics_json": _inside(stats_path, root) if stats_path else None,
            "dashboard_js": _inside(dashboard, root) if dashboard else None,
        },
        warnings=warnings,
        temp_dir=temp,
    )


def cleanup(report: Report) -> None:
    if report.temp_dir and report.temp_dir.is_dir():
        shutil.rmtree(report.temp_dir, ignore_errors=True)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig", errors="replace")


def scan_balanced(text: str, start: int) -> str:
    """Return the {...} object starting at start, aware of JSON strings and escapes."""
    if start >= len(text) or text[start] != "{":
        raise ExtractionError(f"expected '{{' at offset {start}")
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ExtractionError(f"unbalanced object starting at offset {start}")


def _object_after(text: str, search_from: int) -> str:
    brace = text.find("{", search_from)
    if brace < 0:
        raise ExtractionError(f"no object found after offset {search_from}")
    return scan_balanced(text, brace)


def extract_chart(text: str, key: str, var_names: list[str]) -> Chart | None:
    for name in var_names:
        match = re.search(r"\bvar\s+" + re.escape(name) + r"\s*=", text)
        if not match:
            continue
        data_match = re.search(r"\bdata\s*:", text[match.end():])
        if not data_match:
            raise ExtractionError(f"{name}: no data: field")
        payload = json.loads(_object_after(text, match.end() + data_match.end()))
        result = payload.get("result", payload)
        series = []
        for item in result.get("series", []):
            points, nonfinite, duplicates = _normalize_points(item.get("data") or [])
            series.append(Series(
                label=str(item.get("label", "")),
                is_controller=bool(item.get("isController")),
                is_overall=bool(item.get("isOverall")),
                points=points,
                dropped_nonfinite=nonfinite,
                dropped_duplicates=duplicates,
            ))
        granularity = result.get("granularity")
        if not isinstance(granularity, (int, float)) or granularity <= 0:
            granularity = _infer_granularity(series)
        return Chart(
            key=key,
            var_name=name,
            title=result.get("title"),
            granularity_ms=int(granularity),
            min_x_ms=result.get("minX"),
            max_x_ms=result.get("maxX"),
            series=series,
        )
    return None


def extract_table(text: str, table_id: str) -> dict | None:
    match = re.search(
        r"createTable\(\s*\$\(\s*\"#" + re.escape(table_id) + r"\"\s*\)\s*,", text
    )
    if not match:
        return None
    return json.loads(_object_after(text, match.end()))


def _normalize_points(raw: list) -> tuple[list[Point], int, int]:
    parsed: list[tuple[float, float]] = []
    nonfinite = 0
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            nonfinite += 1
            continue
        x, y = pair[0], pair[1]
        if isinstance(x, bool) or isinstance(y, bool):
            nonfinite += 1
            continue
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            nonfinite += 1
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            nonfinite += 1
            continue
        parsed.append((float(x), float(y)))
    parsed.sort(key=lambda pair: pair[0])  # JMeter emits hash-map order
    points: list[Point] = []
    duplicates = 0
    for x, y in parsed:
        if points and x == points[-1].x_ms:
            duplicates += 1
            continue
        points.append(Point(x, y))
    return points, nonfinite, duplicates


def _infer_granularity(series: list[Series]) -> int:
    xs = sorted({p.x_ms for s in series for p in s.points})
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b > a]
    return int(statistics.median(gaps)) if gaps else 60000
