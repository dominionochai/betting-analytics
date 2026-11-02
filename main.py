"""CSV command-line interface and self-contained HTML report generator."""
from __future__ import annotations
import argparse
import csv
import html
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from analytics import (CLVSummary, CalibrationObservation, CalibrationSummary,
                       MarketMetrics, Pick, analyze_market, calibration_summary,
                       closing_line_value, quarter_kelly_stake, summarize_clv)

REQUIRED_COLUMNS: tuple[str, ...] = ("date", "market", "selection", "result")
OPTIONAL_METRIC_COLUMNS: tuple[str, ...] = ("quoted_odds", "closing_odds", "risk_label")

@dataclass(frozen=True)
class ParsedRow:
    row_number: int
    date: str | None
    market: str | None
    selection: str | None
    result: str | None
    quoted_odds: float | None
    closing_odds: float | None
    risk_label: str | None

@dataclass(frozen=True)
class ParsedBoard:
    headers: tuple[str, ...]
    rows: tuple[ParsedRow, ...]
    warnings: tuple[str, ...]

@dataclass(frozen=True)
class Analysis:
    source_rows: int
    settled_count: int
    void_count: int
    unresolved_count: int
    markets: tuple[MarketMetrics, ...]
    calibration: CalibrationSummary
    clv: CLVSummary
    clv_columns_available: bool
    risk_label_constant: str | None
    flagged_legs: int
    total_flagged_stake: float
    warnings: tuple[str, ...]

@dataclass(frozen=True)
class Arguments:
    csv_path: Path
    bankroll: float
    output_path: Path

def _read_odds(raw: str | None, column: str, row_number: int,
               column_exists: bool, warnings: list[str]) -> float | None:
    if not column_exists:
        return None
    if raw is None or not raw.strip():
        warnings.append(f"Row {row_number}: '{column}' is blank; metrics requiring it exclude this row.")
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        warnings.append(f"Row {row_number}: '{column}' is not a valid decimal number; metrics requiring it exclude this row.")
        return None
    if not math.isfinite(value):
        warnings.append(f"Row {row_number}: '{column}' is not finite; metrics requiring it exclude this row.")
        return None
    if value <= 1.0:
        warnings.append(f"Row {row_number}: '{column}' must be greater than 1.0; value rejected for odds-based metrics.")
        return None
    return value

def load_board(path: Path) -> ParsedBoard:
    """Read CSV and record malformed or unavailable fields instead of silently skipping them."""
    warnings: list[str] = []
    parsed_rows: list[ParsedRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        try:
            raw_headers = next(reader)
        except StopIteration:
            raw_headers = []
            warnings.append("CSV has no header row.")
        headers = [item.strip() for item in raw_headers]
        if len(set(headers)) != len(headers):
            warnings.append("CSV has duplicate header names; the first occurrence is used.")
        header_index: dict[str, int] = {}
        for index, name in enumerate(headers):
            if name and name not in header_index:
                header_index[name] = index
        for column in REQUIRED_COLUMNS:
            if column not in header_index:
                warnings.append(f"Missing required column '{column}'; affected analyses may be unavailable.")
        for column in OPTIONAL_METRIC_COLUMNS:
            if column not in header_index:
                warnings.append(f"Missing column '{column}'; metrics requiring this column are unavailable.")
        if not headers:
            warnings.append("CSV contains no named columns.")
        for row_number, values in enumerate(reader, start=2):
            if len(values) < len(headers):
                warnings.append(f"Row {row_number}: {len(headers) - len(values)} field(s) missing at end of row; affected cells treated as blank.")
                values.extend([""] * (len(headers) - len(values)))
            elif len(values) > len(headers):
                warnings.append(f"Row {row_number}: {len(values) - len(headers)} surplus field(s) have no named column and are not analyzed.")
            def cell(column: str) -> str | None:
                index = header_index.get(column)
                if index is None or index >= len(values):
                    return None
                value = values[index].strip()
                return value if value else None
            date_value = cell("date")
            market_value = cell("market")
            selection_value = cell("selection")
            result_value = cell("result")
            for name, value in (("date", date_value), ("market", market_value), ("selection", selection_value)):
                if name in header_index and value is None:
                    warnings.append(f"Row {row_number}: required field '{name}' is blank.")
            if "result" in header_index:
                if result_value is None:
                    warnings.append(f"Row {row_number}: required field 'result' is blank; outcome metrics exclude this row.")
                else:
                    result_value = result_value.upper()
                    if result_value not in ("W", "L", "V"):
                        warnings.append(f"Row {row_number}: result '{result_value}' is invalid; expected W, L, or V, so outcome metrics exclude this row.")
                        result_value = None
            quoted = _read_odds(cell("quoted_odds"), "quoted_odds", row_number, "quoted_odds" in header_index, warnings)
            closing = _read_odds(cell("closing_odds"), "closing_odds", row_number, "closing_odds" in header_index, warnings)
            risk_label = cell("risk_label")
            if "risk_label" in header_index and risk_label is None:
                warnings.append(f"Row {row_number}: 'risk_label' is blank; calibration metrics exclude this row.")
            parsed_rows.append(ParsedRow(row_number, date_value, market_value, selection_value,
                                         result_value, quoted, closing, risk_label))
    if not parsed_rows:
        warnings.append("CSV contains no data rows.")
    return ParsedBoard(tuple(headers), tuple(parsed_rows), tuple(warnings))

def build_analysis(board: ParsedBoard, bankroll: float) -> Analysis:
    """Calculate metrics and attach explicit warnings for all unavailable observations."""
    warnings = list(board.warnings)
    settled_rows = [row for row in board.rows if row.result in ("W", "L")]
    void_count = sum(1 for row in board.rows if row.result == "V")
    unresolved_count = sum(1 for row in board.rows if row.result is None)
    market_names = sorted({row.market for row in board.rows if row.market is not None},
                          key=lambda value: (value.casefold(), value))
    picks_by_market: dict[str, list[Pick]] = {market: [] for market in market_names}
    for row in settled_rows:
        if row.market is None:
            warnings.append(f"Row {row.row_number}: settled result has no market; counted overall but not assigned to a market.")
        elif row.result == "W":
            picks_by_market[row.market].append(Pick(row.market, "W", row.quoted_odds))
        else:
            picks_by_market[row.market].append(Pick(row.market, "L", row.quoted_odds))
    market_results: list[MarketMetrics] = []
    for market in market_names:
        metrics = analyze_market(picks_by_market[market])
        market_results.append(metrics)
        if metrics.settled_count < 30:
            warnings.append(f"Market '{market}' has {metrics.settled_count} settled picks (<30); any apparent edge is inconclusive at this sample size.")

    observations: list[CalibrationObservation] = []
    for row in settled_rows:
        if row.risk_label is not None:
            outcome = "W" if row.result == "W" else "L"
            observations.append(CalibrationObservation(row.risk_label, outcome))
    calibration = calibration_summary(observations)
    if not observations:
        warnings.append("No settled W/L rows have a usable risk_label; Brier score and calibration table are unavailable.")
    distinct_labels = {item.risk_label for item in observations}
    constant_label = next(iter(distinct_labels)) if len(distinct_labels) == 1 else None
    if constant_label is not None:
        warnings.append(f"All usable settled picks share risk_label '{constant_label}'; this constant label carries zero information for distinguishing picks.")

    clv_values: list[float] = []
    for row in board.rows:
        if row.quoted_odds is not None and row.closing_odds is not None:
            value = closing_line_value(row.quoted_odds, row.closing_odds)
            if value is not None:
                clv_values.append(value)
    clv_columns_available = "quoted_odds" in board.headers and "closing_odds" in board.headers
    clv_summary = summarize_clv(clv_values)
    if clv_columns_available and not clv_values:
        warnings.append("No rows have both valid quoted_odds and closing_odds; CLV metrics are unavailable.")

    flagged_legs = 0
    total_stake = 0.0
    for metrics in market_results:
        if metrics.status != "PROVEN EDGE" or metrics.wilson_lower is None:
            continue
        for pick in picks_by_market[metrics.market]:
            if pick.quoted_odds is not None:
                stake = quarter_kelly_stake(metrics.wilson_lower, pick.quoted_odds, bankroll)
                if stake is not None:
                    flagged_legs += 1
                    total_stake += stake
    return Analysis(len(board.rows), len(settled_rows), void_count, unresolved_count,
                    tuple(market_results), calibration, clv_summary, clv_columns_available,
                    constant_label, flagged_legs, total_stake, tuple(warnings))

def _number(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value:.4f}"

def _percent(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value * 100.0:.2f}%"

def render_html(report: Analysis, source_name: str, bankroll: float) -> str:
    """Render an HTML report with inline CSS and no external resources."""
    esc = lambda value: html.escape(value, quote=True)
    market_rows: list[str] = []
    for item in report.markets:
        interval = f"{_percent(item.wilson_lower)} – {_percent(item.wilson_upper)}"
        market_rows.append("<tr>" + "".join((
            f"<td>{esc(item.market)}</td><td>{item.settled_count}</td><td>{_percent(item.hit_rate)}</td>",
            f"<td>{interval}</td><td>{_number(item.mean_quoted_odds)}</td><td>{_percent(item.breakeven_rate)}</td>",
            f"<td>{_percent(item.ev_observed)}</td><td>{_percent(item.ev_wilson_lower)}</td>",
            f"<td>{esc(item.status or 'Unavailable')}</td>")) + "</tr>")
    if not market_rows:
        market_rows.append('<tr><td colspan="9">No market names available from the CSV.</td></tr>')
    ranked = sorted((item for item in report.markets if item.ev_wilson_lower is not None),
                    key=lambda item: (-item.ev_wilson_lower, item.market.casefold(), item.market))[:3]
    top_rows = "".join(f"<tr><td>{esc(item.market)}</td><td>{_percent(item.ev_wilson_lower)}</td>"
                       f"<td>{item.settled_count}</td><td>{item.valid_odds_count}</td></tr>" for item in ranked)
    if not top_rows:
        top_rows = '<tr><td colspan="4">Unavailable: no market has settled outcomes and valid quoted odds.</td></tr>'
    bin_rows = "".join(
        f"<tr><td>{item.number}</td><td>{esc(', '.join(item.labels) if item.labels else '—')}</td>"
        f"<td>{item.count}</td><td>{_percent(item.observed_hit_rate)}</td><td>{_percent(item.plug_in_probability)}</td></tr>"
        for item in report.calibration.bins)
    if not bin_rows:
        bin_rows = '<tr><td colspan="5">Unavailable: no settled picks with a risk_label.</td></tr>'
    warning_html = "".join(f"<li>{esc(item)}</li>" for item in report.warnings) or "<li>None.</li>"
    clv_available = report.clv_columns_available and report.clv.mean_clv is not None
    clv_text = (f"n={report.clv.observation_count}; mean CLV {_percent(report.clv.mean_clv)}; "
                f"positive { _percent(report.clv.positive_proportion) }" if clv_available else "Unavailable: valid quoted_odds and closing_odds pairs are required.")
    constant_note = (f"Constant risk_label '{esc(report.risk_label_constant)}' carries zero information for distinguishing picks."
                     if report.risk_label_constant is not None else "Risk labels are categorical; no probability is inferred from label names.")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Football Betting Analytics Report</title><style>
:root{{color-scheme:dark;--bg:#171b22;--panel:#202631;--line:#394352;--text:#e8edf3;--muted:#aeb8c5;--accent:#86a7c8;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:32px 20px}}h1,h2{{line-height:1.2}}h1{{font-size:2rem;margin:0 0 8px}}h2{{font-size:1.25rem;margin:28px 0 12px}}p,.muted{{color:var(--muted)}}.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}.stat{{padding:12px;background:#1a2029;border:1px solid var(--line);border-radius:8px}}.stat strong{{display:block;font-size:1.15rem}}
table{{width:100%;border-collapse:collapse;display:block;overflow-x:auto}}th,td{{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--accent);font-weight:650}}code{{color:#d9e3ef}}ul{{padding-left:22px}}li{{margin:4px 0}}.small{{font-size:.92rem}}footer{{margin:28px 0;color:var(--muted)}}
</style></head><body><main>
<h1>Football prediction-board analytics</h1><p>Source: <code>{esc(source_name)}</code> · bankroll: {_number(bankroll)}</p>
<div class="panel grid"><div class="stat"><span class="muted">CSV rows</span><strong>{report.source_rows}</strong></div>
<div class="stat"><span class="muted">Settled W/L</span><strong>{report.settled_count}</strong></div>
<div class="stat"><span class="muted">Voids (V)</span><strong>{report.void_count}</strong></div>
<div class="stat"><span class="muted">Unresolved / invalid</span><strong>{report.unresolved_count}</strong></div></div>
<h2>Per-market results</h2><div class="panel"><table><thead><tr><th>Market</th><th>Settled n</th><th>Hit rate</th><th>Wilson 95% CI</th><th>Mean quoted odds</th><th>Breakeven</th><th>EV observed</th><th>EV at CI lower</th><th>Status</th></tr></thead><tbody>{''.join(market_rows)}</tbody></table>
<p class="small">EV per leg is p × decimal quoted odds − 1, averaged over valid quoted odds. “Observed” uses the market hit rate; “CI lower” uses the Wilson lower bound. Breakeven is 1 / mean quoted odds. Status requires strict interval separation; equality is NOT PROVEN.</p></div>
<h2>Top markets by conservative EV</h2><div class="panel"><table><thead><tr><th>Market</th><th>Mean EV using CI lower</th><th>Settled n</th><th>Valid quoted-odds n</th></tr></thead><tbody>{top_rows}</tbody></table><p class="small">Every group with fewer than 30 settled picks is inconclusive, regardless of its displayed status.</p></div>
<h2>Closing-line value (CLV)</h2><div class="panel"><p>{clv_text}</p><p class="small">CLV = (quoted odds / closing odds) − 1. Positive CLV means the quoted price exceeded the closing price; among available diagnostics, CLV is the strongest available evidence of edge, not proof or a guarantee.</p></div>
<h2>Risk-label Brier and five-bin calibration</h2><div class="panel"><p>Brier score: {_number(report.calibration.brier_score)} · usable settled observations: {report.calibration.observation_count} · distinct labels: {report.calibration.distinct_label_count}. {constant_note}</p>
<table><thead><tr><th>Bin</th><th>risk_label value(s)</th><th>n</th><th>Observed hit rate</th><th>Plug-in probability</th></tr></thead><tbody>{bin_rows}</tbody></table>
<p class="small">Labels are categorical, sorted case-insensitively and assigned to at most five bins. Each populated bin's own observed hit rate is the plug-in probability used for this in-sample Brier diagnostic. It is not an out-of-sample forecast score.</p></div>
<h2>Quarter-Kelly staking illustration</h2><div class="panel"><p>PROVEN EDGE market legs with valid odds: {report.flagged_legs}. Sum of single-leg quarter-Kelly stakes using each market's Wilson lower hit-rate bound and bankroll {_number(bankroll)}: {_number(report.total_flagged_stake)}.</p><p class="small">This arithmetic ignores correlation, limits and other portfolio constraints; it is not a staking recommendation.</p></div>
<h2>Warnings and data quality</h2><div class="panel"><p>{len(report.warnings)} warning(s). Missing inputs and rejected rows are reported rather than silently skipped.</p><ul>{warning_html}</ul></div>
<footer>Deterministic report; no external assets or third-party packages.</footer></main></body></html>'''

def _arguments(argv: Sequence[str] | None) -> Arguments:
    parser = argparse.ArgumentParser(description="Analyze football tipster CSV outcomes and decimal odds.")
    parser.add_argument("csv", type=Path, help="Path to prediction-board CSV")
    parser.add_argument("--bankroll", type=float, default=100000.0, help="Bankroll for quarter-Kelly illustration (default: 100000)")
    parser.add_argument("--output", type=Path, help="HTML report path (default: <input>.report.html)")
    namespace = parser.parse_args(argv)
    if not math.isfinite(namespace.bankroll) or namespace.bankroll <= 0.0:
        parser.error("--bankroll must be a finite positive number")
    output_path = namespace.output or namespace.csv.with_name(f"{namespace.csv.stem}.report.html")
    return Arguments(namespace.csv, namespace.bankroll, output_path)

def _console_summary(report: Analysis, source: Path, output: Path, bankroll: float) -> str:
    lines = [f"Football prediction-board analytics: {source}",
             f"Rows: {report.source_rows} | settled W/L: {report.settled_count} | voids: {report.void_count} | unresolved/invalid outcomes: {report.unresolved_count}",
             "", "Market results (95% Wilson intervals):"]
    if report.markets:
        for item in report.markets:
            interval = f"{_percent(item.wilson_lower)}–{_percent(item.wilson_upper)}"
            lines.append(f"  {item.market}: n={item.settled_count}, hit={_percent(item.hit_rate)}, CI={interval}, breakeven={_percent(item.breakeven_rate)}, EV lower={_percent(item.ev_wilson_lower)}, status={item.status or 'Unavailable'}")
    else:
        lines.append("  Unavailable: no market names found.")
    lines.extend(("", f"CLV: n={report.clv.observation_count}, mean={_percent(report.clv.mean_clv)}, positive={_percent(report.clv.positive_proportion)}",
                  f"Brier: {_number(report.calibration.brier_score)} (n={report.calibration.observation_count})",
                  f"Flagged proven-edge legs: {report.flagged_legs}; total single-leg quarter-Kelly stake: {_number(report.total_flagged_stake)} of bankroll {_number(bankroll)}",
                  f"HTML report written: {output}", f"Warnings ({len(report.warnings)}):"))
    lines.extend(f"  - {warning}" for warning in report.warnings)
    if not report.warnings:
        lines.append("  None.")
    return "\n".join(lines)

def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        board = load_board(args.csv_path)
        report = build_analysis(board, args.bankroll)
        args.output_path.write_text(render_html(report, args.csv_path.name, args.bankroll), encoding="utf-8")
    except (OSError, csv.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(_console_summary(report, args.csv_path, args.output_path, args.bankroll))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
