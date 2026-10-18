# CODE MAP — JMeter Dashboard Extractor

Two Python files. `jmeter_extract.py` only discovers and parses; `jmeter_report.py`
does all analysis and rendering. Run from the project root:

```bash
python -I jmeter_report.py 18_10_1 --out out
python -I jmeter_report.py 18_10_1 --out out --json-detail full   # includes raw points
# also accepts: content/js/graph.js, a report directory, or a .zip
```

Outputs: `out/test-summary.md` (single AI-facing report, always keeps every
downsampled point and event) and `out/summary.json` (machine-readable,
deterministic, minified). Dependencies are pinned in `requirements.txt`
(`numpy==1.26.4`, `tsdownsample==0.1.5.1`):

```bash
python -m pip install -r requirements.txt
```

Charts extracted: active threads, response times, connect time, transactions per
second, hits per second, total TPS, and codes per second. The first three are the
core charts; throughput charts are optional. `totalTPSInfos` supplies the
aggregate `tps_total` correlation value (summing per-transaction series would
double-count controllers and samplers).

`summary.json` defaults to `--json-detail compact`: no per-series `points`, no
per-series `events` (top-level `events` is capped by `max_compact_events`), no
per-label `rt`/`connect` maps, no `errors.per_bucket` (those values are in
`correlation`), event `reason`/`related` omitted, and `statistics.by_label`
stored as label → array rows described by `statistics.by_label_fields`. This
keeps a 150-label / 500-bucket report (including TPS/hits) around 480 KB instead
of >17 MB. `--json-detail minimal` also drops the `events` array and caps
`correlation` at 100 rows (~200 KB at that scale). `--json-detail full` writes
the complete payload with object-form statistics.

Compact mode size is bounded by `max_compact_events` and
`max_correlation_rows`; the only label-linear parts are per-series stats and
request statistics.

## Pipeline

```
resolve_input ──► graph.js ──► scan_balanced ──► json.loads ──► Chart/Series
      │              dashboard.js ──► tables: statistics / errors / top5
      │              statistics.json
      ▼
sort + dedupe points ──► detect_events (full resolution) ──► dedupe events
      ▼
MinMaxLTTB per series + force-keep event/endpoint points
      ▼
verify every event still exists with its original value
      ▼
request stats + bucket stats + error rate + correlation
      ▼
test-summary.md + summary.json
```

## `jmeter_extract.py`

| Function | Role |
|---|---|
| `resolve_input(arg, graph_file)` | Accepts dir / `graph.js` / `.zip`; returns `Report`. Caller must `cleanup()`. |
| `_safe_extract_zip` | Rejects traversal, drive letters, symlinks; caps 10k members / 2 GiB. |
| `_discover_graph` | Known paths first, then `rglob`; ambiguity is an error naming `--graph-file`. |
| `_infer_root` | Walks ancestors for `statistics.json`, `index.html`, or `content/`. |
| `scan_balanced(text, start)` | String/escape-aware `{...}` scanner. Never use a non-greedy regex. |
| `extract_chart(text, key, names)` | Finds `var <name> =`, scans from `data:`, `json.loads`, builds `Chart`. |
| `extract_table(text, table_id)` | Parses `createTable($("#id"), {...})` objects. |
| `_normalize_points` | Finite numeric pairs only, **sorted by x**, duplicates dropped (keep first). |

`DEFAULT_CHART_VARS` maps chart keys to JMeter variable names; extend it (or pass
`chart_aliases` in config) if a future JMeter version renames them.

## `jmeter_report.py`

| Area | Functions |
|---|---|
| CLI / orchestration | `main`, `run`, `parse_args`, `data_window` |
| Anomalies | `collect_events`, `detect_events`, `_baseline_events`, `_runs`, `dedupe` |
| Downsampling | `downsample_series`, `verify_preservation` |
| Statistics | `series_stats`, `request_statistics`, `_stat_row`, `_controller_flag` |
| Errors / correlation | `classify_code`, `error_analysis`, `_top_entry`, `correlate` |
| Output | `render_markdown`, `build_summary`, `event_dict`, `corr_dict`, `iso`, `clean` |

`build_summary(..., detail)` and `corr_dict(..., detail)` implement the
`compact`/`full` JSON split. Compact mode drops per-series points/events,
correlation label maps, and event reasons; `events_meta` records truncation.

### Anomaly rules (full resolution, before downsampling)

`global_min/max`, `local_peak/trough`, `large_change`, `baseline_deviation`
(rolling median/MAD), `sustained_departure` (run of deviations), `sla_breach`
(response times only), `thread_transition`, `phase_boundary`. Events are
deduplicated per `(chart, label, timestamp)`: rules are merged, severity takes the
maximum, and one primary `type` is chosen by `TYPE_PRIORITY`.

### Downsampling invariants

- Each series is downsampled independently; no cross-series alignment.
- Points shorter than `budget` are untouched.
- First, last, and every event timestamp are always kept — MinMaxLTTB cannot drop them.
- Original `(timestamp, value)` pairs only; no interpolation or smoothing.
- If protected points exceed the budget, they are kept anyway and `overrun` is recorded.
- `verify_preservation` re-checks every event against the kept points (timestamp
  present and value unchanged) and writes `verification` to the JSON plus a
  **Preservation check** line in the Markdown. `--strict` makes a failure exit 2.

### Errors and correlation

- Codes chart: numeric 200–399 = success; 4xx/5xx and `Non HTTP response code: ...` = error.
- A code series absent at a bucket contributes 0; an absent chart series is `null`, never 0.
- `error_analysis.cross_check` multiplies summed rps by the bucket size and compares to
  request totals (on `18_10_1`: 763 samples / 78 errors, exact match).
- `correlate` joins on exact epoch-ms buckets and adds `threads_total`, `tps_total`
  (from `totalTPSInfos`), `hits_total`, max RT/CT, and controller mean RT. Full mode
  also keeps per-label `rt` / `connect` maps. Co-occurrence is not causality.

## Configuration (`--config file.json`)

Defaults live in `DEFAULT_CONFIG`. CLI `--budget`, `--minmax-ratio`, `--sla` override the file.

| Key | Default | Meaning |
|---|---|---|
| `budget` | 120 | Max points kept per series |
| `minmax_ratio` | 4 | MinMaxLTTB preselection ratio |
| `local_window` | 1 | Neighbours on each side for local extrema |
| `local_rel_delta` | 0.25 | Required relative prominence for a peak/trough |
| `adjacent_rel_delta` | 0.5 | Relative adjacent change that triggers `large_change` |
| `baseline_window` / `baseline_min_points` | 5 / 5 | Rolling MAD window |
| `baseline_k` | 3.5 | Robust z threshold |
| `sustained_min_run` | 2 | Consecutive deviations for `sustained_departure` |
| `sla_ms` / `sla_by_label` | null / {} | Response-time SLA; per-label overrides |
| `thread_transition_min_delta` | 0.5 | Active-thread step size |
| `phase_boundaries` | [] | ISO-8601 or epoch-ms timestamps |
| `max_correlation_rows` | 500 | Cap on correlation/grid rows |
| `max_compact_events` | 1000 | Events kept in compact `summary.json` (most severe first) |
| `chart_aliases` | {} | Alternate graph.js variable names |

## Determinism and limitations

- No generation timestamp, host, or absolute paths in outputs; JSON uses `sort_keys` and
  `allow_nan=False`; numbers are rounded; repeated runs are byte-identical.
- Request-level percentiles come from `statistics.json` / the statistics table and are
  never recomputed from chart bucket means.
- Dashboard errors are test-wide; per-labelled errors cannot be localized to a bucket.
- `top5ErrorsBySamplerTable` controller rows may count fewer samples than request
  statistics (JMeter semantics); the tool warns and trusts request statistics.

## Verification recipe

```bash
python -I jmeter_report.py 18_10_1 --out out_a --strict   # exits 2 if any event was lost
python -I jmeter_report.py 18_10_1 --out out_b --strict
diff -r out_a out_b        # must be empty
```

Confirm preservation in the outputs:

- `summary.json` → `verification.all_preserved` is `true`,
  `events_checked == events_preserved`, and `failures` is empty.
- `test-summary.md` → section 5 starts with
  `**Preservation check: PASS** — N/N event points ...`.
- Section 4 lists every event; the same `elapsed_s` appears in the relevant
  per-series table with the event name(s) in its `events` column.

Expected on `18_10_1`: 60 s granularity, 12 observed / 13 nominal buckets (missing
09:31:00Z), 11 response series, 11 connect series, 1 thread series, 6 code series,
763 samples / 78 errors, and `errors.cross_check.match == true`.
