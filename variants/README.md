# JMeter DuckDB Report

JMeter's HTML dashboard is a good visual reference, but it is not something you can hand to a chat assistant. This tool takes a CSV JTL, loads it into DuckDB, writes a Parquet copy, renders the dashboard charts as PNGs, and produces a single Markdown report with the performance signals that stand out.

The Markdown report is the part intended for upload to M365 Copilot Chat or a similar text-only assistant. It contains tables and evidence windows, not root-cause guesses.

The tool is one Python file (`jmeter_report.py`). The tests live in `test_jmeter_report.py`.

## What it does

- Ingests any JMeter CSV JTL. Column order and casing do not matter.
- Normalizes timestamps, success flags, controller rows, and optional columns.
- Writes `jmeter_samples.parquet` and loads `jmeter.duckdb`.
- Builds JMeter-compatible statistics, including R-6 percentiles.
- Renders the 16 dashboard charts as PNGs.
- Scans each chart metric for spikes, dips, sustained trends, error bursts, label outliers, load relationships, and distribution shifts.
- Writes one `jmeter_report.md` with the findings.

## Requirements

- Python 3.11 or newer. Tested on 3.13.
- `duckdb`, `matplotlib`, `pandas`, `numpy`.
- A CSV JTL. XML JTLs are rejected with a clear error.
- About 500 MB of free disk space for a 3M-row run.

Tested with duckdb 2.0.0, matplotlib 3.11.2, pandas 3.1.1, and numpy 2.5.4.

Copy the project folder or clone the repo, then create the virtual environment on each machine. A `myenv/` copied from another laptop usually will not run.

## Install

Windows PowerShell:

```powershell
cd "C:\path\to\jmeter-duckdb"
py -3.13 -m venv myenv
.\myenv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install duckdb matplotlib pandas numpy
```

Windows Command Prompt: use `myenv\Scripts\activate.bat` instead of the `Activate.ps1` line. If `py -3.13` is not available, use `py -3` or `python`.

macOS / Linux:

```bash
cd /path/to/jmeter-duckdb
python3 -m venv myenv
source myenv/bin/activate
python -m pip install --upgrade pip
python -m pip install duckdb matplotlib pandas numpy
```

If you need the exact versions used during development:

```bash
python -m pip install "duckdb==2.0.0" "matplotlib==3.11.2" "pandas==3.1.1" "numpy==2.5.4"
```

You can also skip activation and call the interpreter directly: `myenv\Scripts\python.exe` on Windows, `myenv/bin/python` on macOS/Linux.

## Quick start

Run this from the project root:

```bash
python jmeter_report.py all --jtl orangehrm-test.jtl --db jmeter.duckdb --parquet jmeter_samples.parquet --out-dir charts --md jmeter_report.md
```

`all` creates the database and Parquet files, so you do not need to prepare them. The run starts with:

```
Ingested 1404 rows (385 controllers, 1019 requests) from orangehrm-test.jtl
```

and ends with:

```
===== MARKDOWN REPORT =====
  findings: 4
  wrote jmeter_report.md
```

Output:

```
jmeter.duckdb
jmeter_samples.parquet
charts/
  16 PNGs, statistics.csv, statistics.parquet
jmeter_report.md
```

## Input format

- CSV only. In JMeter, set `jmeter.save.saveservice.output_format=csv`.
- Required columns: `timeStamp`, `elapsed`, `label`.
- Optional columns: response code and message, thread name, success, bytes, sent bytes, latency, connect, active threads, group threads, failure message, data type, URL, idle time.
- Missing optional columns become `NULL`, and charts that depend on them are limited.
- Names are matched case- and punctuation-insensitively.
- Timestamps can be epoch milliseconds, seconds, microseconds, nanoseconds, or ISO.
- `success` accepts `true/false/1/0/yes/no`. If it is absent, a 2xx or 3xx response code counts as success.
- Controllers are detected from `Number of samples in transaction ...`. If that message is missing, labels without a hyphen act as controllers when hyphenated child labels exist. If there are no hyphenated labels at all, every sample is treated as a request.
- `--jtl` can point to a directory. All `*.jtl` and `*.csv` files are unioned by column name.

## Commands

| Command | What it does |
|---|---|
| `all` | Ingest, statistics, charts, and the Markdown report |
| `ingest` | JTL to DuckDB and Parquet |
| `stats` | Build `jmeter_statistics` and export CSV/Parquet |
| `charts` | Render the 16 PNGs |
| `report` | Write the Markdown report |
| `selftest` | Run the test suite |

`stats`, `charts`, and `report` need a database from `ingest` or `all`. `charts` opens it read-only; `stats` and `report` open it read-write.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--jtl` | `orangehrm-test.jtl` | JTL file or directory |
| `--db` | `jmeter.duckdb` | DuckDB database path |
| `--parquet` | `jmeter_samples.parquet` | Parquet output path |
| `--out-dir` | `charts` | PNG and statistics output directory |
| `--md` | `jmeter_report.md` | Markdown report path |
| `--granularity` | `60000` | Chart bucket size in milliseconds |
| `--include-sub-samples` | off | Include every label, not only controllers |
| `--sample-rows` | `200000` | Reservoir sample cap; `0` disables sampling |
| `--top-n` | `25` | Maximum anomaly windows shown per chart |

`--table` is available for advanced ingestion, but keep the default `jmeter_samples` for the statistics and report pipeline. Run `python jmeter_report.py --help` for the full list.

## The Markdown report

`jmeter_report.md` has seven sections:

1. Executive summary with severity counts and the top signals.
2. Run overview.
3. Per-transaction statistics.
4. Anomalies by dashboard chart, with a coverage table and evidence windows.
5. Distribution and load relationships.
6. Data quality and sampling notes.
7. Suggested evidence to collect next.

Each metric is bucketed and compared against a median/MAD baseline. Consecutive flagged buckets become one window. The scan also checks error bursts, label p95 outliers, response time vs request rate, response time vs threads, and first-half/second-half distribution changes.

The report is descriptive. It gives values, baselines, windows, and log sources to check. It does not claim what caused a change.

Upload the file as-is to M365 Copilot Chat or any other Markdown-capable assistant. The report contains no images.

## Performance

Measured on a laptop-class machine with a synthetic 3M-row JTL:

| Step | Time |
|---|---:|
| Ingest, 3,000,000 rows | ~12 s |
| Report with default `--sample-rows 200000` | ~9 s |
| Report with `--sample-rows 0` | ~5 s |

By default the tool samples up to 200,000 rows for the distribution and first/second-half queries; bucket aggregates always scan every row. At around 3M rows, full scans are faster, so `--sample-rows 0` is the speed option.

## Testing on a clean machine

After copying the project and creating a virtual environment:

```bash
python -m unittest -v test_jmeter_report
```

Or use the tool's shortcut:

```bash
python jmeter_report.py selftest
```

Both should finish with:

```
Ran 29 tests in ...s
OK
```

Run the sample pipeline:

```bash
python jmeter_report.py all --jtl orangehrm-test.jtl --db jmeter.duckdb --parquet jmeter_samples.parquet --out-dir charts --md jmeter_report.md
```

Check the ingestion count: `1404 rows (385 controllers, 1019 requests)`.

The per-label statistics should match:

| Label | Samples | Average | Median | P90 | P95 | P99 |
|---|---:|---:|---:|---:|---:|---:|
| `01_01_Launch` | 130 | 1,782.63 | 1,545.00 | 2,763.80 | 3,189.60 | 5,036.64 |
| `01_02_Login` | 129 | 1,763.92 | 1,507.00 | 2,717.00 | 3,618.00 | 5,281.50 |
| `01_03_HomePage` | 126 | 1,413.89 | 1,191.50 | 2,324.70 | 2,983.40 | 4,730.66 |
| `TOTAL` | 385 | 1,655.68 | 1,426.00 | 2,601.00 | 3,308.90 | 4,858.16 |

Check that `charts/` contains 16 PNGs:

```bash
python -c "from pathlib import Path; print(len(list(Path('charts').glob('*.png'))))"
```

Check that the report was filled in:

```bash
python -c "from pathlib import Path; t=Path('jmeter_report.md').read_text(encoding='utf-8'); print('{{' in t)"
```

Expected output is `16` and `False`.

## Notes and limitations

- `TOTAL` in the statistics table covers transaction controllers. JMeter's `statistics.json` total includes child samples; use `--include-sub-samples` to compare.
- Time buckets use sample start time, matching the PNG charts. JMeter's own over-time charts use completion time, so a few boundary samples can differ.
- `report` recreates `jmeter_statistics` inside the database. Close other DuckDB processes before running it; DuckDB allows one writer.
- At 3M rows, `--sample-rows 0` is faster and exact for the distribution tables.
- `selftest` needs `test_jmeter_report.py` beside the tool. The other commands work with `jmeter_report.py` alone.
- `--table` is an advanced option. The statistics and report queries expect the default `jmeter_samples`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No module named 'duckdb'` | Activate the venv, or call `myenv\Scripts\python.exe` / `myenv/bin/python` directly. |
| `database not found` | Run `all` or `ingest` before `stats`, `charts`, or `report`. |
| `Could not set lock on file` | Another Python or DuckDB process is using the database. Close it and retry. |
| XML JTL rejected | Save the results as CSV (`jmeter.save.saveservice.output_format=csv`). |
| Missing required column | The JTL needs timestamp, elapsed, and label columns. |
| Charts skipped or empty | Optional columns such as latency, connect, or allThreads are missing or empty. |
| `No module named 'pytz'` | Only happens when querying timezone-aware timestamps. Install it with `python -m pip install pytz`; normal runs do not need it. |
| PowerShell blocks `Activate.ps1` | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, use `activate.bat`, or call the venv Python directly. |
| `python3 -m venv` fails with `ensurepip is not available` | Install your distribution's venv package. Debian/Ubuntu: `sudo apt install python3-venv`. |

## Project layout

```
jmeter_report.py          tool
test_jmeter_report.py     29 tests
orangehrm-test.jtl        sample JTL
orangehrm-test.jmx        sample JMeter plan
user.properties           JMeter properties for the sample run
0611-2/                   exported JMeter HTML dashboard used as a reference
README.md
```

Generated files:

```
jmeter.duckdb
jmeter_samples.parquet
charts/
jmeter_report.md
```
