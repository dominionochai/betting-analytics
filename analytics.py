"""Pure statistical functions for football prediction-board analysis."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Sequence

@dataclass(frozen=True)
class Pick:
    market: str
    result: Literal["W", "L"]
    quoted_odds: float | None

@dataclass(frozen=True)
class MarketMetrics:
    market: str
    settled_count: int
    wins: int
    hit_rate: float | None
    wilson_lower: float | None
    wilson_upper: float | None
    valid_odds_count: int
    mean_quoted_odds: float | None
    breakeven_rate: float | None
    ev_observed: float | None
    ev_wilson_lower: float | None
    status: str | None

@dataclass(frozen=True)
class CalibrationObservation:
    risk_label: str
    result: Literal["W", "L"]

@dataclass(frozen=True)
class CalibrationBin:
    number: int
    labels: tuple[str, ...]
    count: int
    observed_hit_rate: float | None
    plug_in_probability: float | None

@dataclass(frozen=True)
class CalibrationSummary:
    brier_score: float | None
    observation_count: int
    distinct_label_count: int
    bins: tuple[CalibrationBin, ...]

@dataclass(frozen=True)
class CLVSummary:
    observation_count: int
    mean_clv: float | None
    positive_proportion: float | None

def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    """Two-sided Wilson score interval, or None for invalid/empty input."""
    if total <= 0 or wins < 0 or wins > total or z <= 0.0:
        return None
    p = wins / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = (p + z2 / (2.0 * total)) / denominator
    margin = z / denominator * (p * (1.0 - p) / total + z2 / (4.0 * total * total)) ** 0.5
    return max(0.0, centre - margin), min(1.0, centre + margin)

def expected_value(probability: float, decimal_odds: float) -> float | None:
    """Net expected return per unit stake at decimal odds."""
    if not 0.0 <= probability <= 1.0 or decimal_odds <= 1.0:
        return None
    return probability * decimal_odds - 1.0

def quarter_kelly_stake(probability: float, decimal_odds: float, bankroll: float) -> float | None:
    """Nonnegative quarter-Kelly amount using decimal odds and bankroll."""
    if not 0.0 <= probability <= 1.0 or decimal_odds <= 1.0 or bankroll < 0.0:
        return None
    fraction = 0.25 * (probability * decimal_odds - 1.0) / (decimal_odds - 1.0)
    return max(0.0, fraction * bankroll)

def closing_line_value(quoted_odds: float, closing_odds: float) -> float | None:
    """Return (quoted / closing) - 1 for valid decimal odds."""
    if quoted_odds <= 1.0 or closing_odds <= 1.0:
        return None
    return quoted_odds / closing_odds - 1.0

def market_status(lower: float, upper: float, breakeven: float) -> str:
    """Exact interval boundaries are NOT PROVEN."""
    if lower > breakeven:
        return "PROVEN EDGE"
    if upper < breakeven:
        return "NEGATIVE"
    return "NOT PROVEN"

def analyze_market(picks: Sequence[Pick]) -> MarketMetrics:
    """Summarize settled picks; EV uses valid odds and the market's settled hit rate."""
    market = picks[0].market if picks else ""
    count = len(picks)
    wins = sum(1 for pick in picks if pick.result == "W")
    hit_rate = wins / count if count else None
    interval = wilson_interval(wins, count)
    lower = interval[0] if interval is not None else None
    upper = interval[1] if interval is not None else None
    odds = [pick.quoted_odds for pick in picks if pick.quoted_odds is not None]
    odds_count = len(odds)
    mean_odds = sum(odds) / odds_count if odds_count else None
    breakeven = 1.0 / mean_odds if mean_odds is not None else None
    ev_observed = None
    ev_lower = None
    status = None
    if odds_count and hit_rate is not None and lower is not None and upper is not None and breakeven is not None:
        ev_observed = sum(hit_rate * price - 1.0 for price in odds) / odds_count
        ev_lower = sum(lower * price - 1.0 for price in odds) / odds_count
        status = market_status(lower, upper, breakeven)
    return MarketMetrics(market, count, wins, hit_rate, lower, upper, odds_count, mean_odds,
                         breakeven, ev_observed, ev_lower, status)

def summarize_clv(values: Sequence[float]) -> CLVSummary:
    """Summarize already validated closing-line-value observations."""
    count = len(values)
    if not count:
        return CLVSummary(0, None, None)
    return CLVSummary(count, sum(values) / count, sum(1 for value in values if value > 0.0) / count)

def calibration_summary(observations: Sequence[CalibrationObservation]) -> CalibrationSummary:
    """Five categorical bins; empirical bin rate is an in-sample Brier plug-in.

    Labels are not probabilities. Distinct labels sort case-insensitively and are
    distributed across five bins. Populated-bin hit rates are used as plug-in p;
    this is an in-sample diagnostic, not an out-of-sample forecast score.
    """
    if not observations:
        return CalibrationSummary(None, 0, 0, ())
    labels = sorted({item.risk_label for item in observations}, key=lambda value: (value.casefold(), value))
    label_to_bin: dict[str, int] = {}
    if len(labels) == 1:
        label_to_bin[labels[0]] = 2
    elif len(labels) <= 5:
        for index, label in enumerate(labels):
            label_to_bin[label] = round(index * 4 / (len(labels) - 1))
    else:
        for index, label in enumerate(labels):
            label_to_bin[label] = min(4, index * 5 // len(labels))
    members: dict[int, list[CalibrationObservation]] = {index: [] for index in range(5)}
    names: dict[int, list[str]] = {index: [] for index in range(5)}
    for label in labels:
        names[label_to_bin[label]].append(label)
    for item in observations:
        members[label_to_bin[item.risk_label]].append(item)
    bins: list[CalibrationBin] = []
    error_sum = 0.0
    for index in range(5):
        group = members[index]
        if not group:
            bins.append(CalibrationBin(index + 1, tuple(names[index]), 0, None, None))
            continue
        rate = sum(1 for item in group if item.result == "W") / len(group)
        error_sum += sum(((1.0 if item.result == "W" else 0.0) - rate) ** 2 for item in group)
        bins.append(CalibrationBin(index + 1, tuple(names[index]), len(group), rate, rate))
    return CalibrationSummary(error_sum / len(observations), len(observations), len(labels), tuple(bins))
