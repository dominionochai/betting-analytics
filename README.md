# Football Betting Analytics

A standard-library command-line tool for auditing historical football betting picks, market performance, calibration, and staking illustrations.

## Getting started

```sh
python -m pip install -r requirements.txt
python main.py sample_board.csv --bankroll 100000
python -m unittest discover
```

The report is written next to the input CSV as an HTML file. The project uses only the Python standard library; `requirements.txt` is included so the install command is reproducible.

## Input data

The CSV requires `date`, `market`, `selection`, and `result` columns. `result` should be `W`, `L`, or `V` (void); optional metric columns are `quoted_odds`, `closing_odds`, and `risk_label`. Missing optional columns generate warnings and make dependent metrics unavailable rather than being silently inferred. Odds are decimal odds greater than 1.

## Metrics and interpretation

- **Wilson interval:** a two-sided 95% score interval for a market's observed win rate. The lower bound is a conservative estimate used by the staking illustration; small samples have wide uncertainty.
- **Breakeven rate:** `1 / mean quoted decimal odds`, the win rate needed to break even before other costs.
- **Expected value (EV):** net return per unit stake, `p × decimal_odds − 1`. The report shows observed EV from the settled hit rate and a conservative EV using the Wilson lower bound; these are historical diagnostics, not a forecast.
- **Brier score and calibration:** the Brier score is a mean squared error diagnostic for binary outcomes against the in-sample plug-in rates. Categorical risk labels are grouped into up to five ordered bins; labels are not probabilities. A constant risk label has zero information for distinguishing picks, and groups with fewer than 30 settled picks are inconclusive.
- **Closing-line value (CLV):** `(quoted odds / closing odds) − 1`; positive CLV means the quoted price was better than the closing price. It is evidence about price quality, not proof of an edge or a guarantee.
- **Quarter-Kelly:** an illustrative one-quarter Kelly stake based on a market's Wilson lower-bound probability and valid decimal odds. It floors negative stakes at zero. The calculation is single-leg arithmetic and does not account for correlation, limits, or other portfolio constraints; it is not a staking recommendation.

Treat all results as descriptive, in-sample analysis. Historical performance and CLV do not guarantee future outcomes.
