# OT Sports Betting System

**Optimal Transport AI for Sports Gambling Edge Detection**

A production-grade system that uses Optimal Transport theory to detect arbitrage opportunities in sports betting markets by measuring the geometric distance between true probability distributions and market-implied distributions.

## 🧠 Theory: Why OT Beats the Market
### The Core Insight
Sportsbooks price expected values accurately, but they do **not** price distribution geometry accurately.

Optimal Transport measures the "cost" to morph one probability distribution into another using the Wasserstein distance:

$$W(P_{true}, P_{market}) = \min_\pi \int\int \|x - y\|^2 \, d\pi(x, y)$$

If \(W > \text{threshold}\) → Edge exists → Bet.

This is the same framework used in:

- Quant finance (volatility arbitrage)
- Machine learning (domain adaptation)
- Economics (income inequality measurement)

### Why Books Don't Use This
- Computational cost: OT is expensive
- Data requirements: Need causal models + simulations
- Liquidity constraints: Books balance action, not optimize distributions
- Public betting bias: Books shade lines toward public sentiment

You have a structural edge.

# 📐 Mathematical Framework

## 1. Wasserstein Distance (W₂)

$$W_2(\mu, \nu) = \inf_{\pi \in \Pi(\mu, \nu)} \int\int |x - y|^2 \, d\pi(x,y)$$

Where:
* $\mu$ = true distribution (from Monte Carlo + causal model)
* $\nu$ = market distribution (from odds)
* $\pi$ = transport plan (how to move probability mass)

**Interpretation:** If $W_2$ is large, the market is geometrically far from truth.

### 2. Causal DAG
Model causal relationships:

- Injuries → Team Strength → Scoring
- Weather → Passing → Total Points
- Matchup → Pace → Distribution Variance

Books model correlations. You model causation.

### 3. Expected Value with OT Correction
\(\text{EV} = P(\text{true}) \times \text{Payout} - (1 - P(\text{true})) \times \text{Stake}\)

Recent update: the EV formula now correctly uses the true win probability against the stake (loss side) rather than market-implied vig, aligning calculations with real betting returns.

If \(W_2(P_{true}, P_{market}) > \delta:\)

\[\text{EV}_{adjusted} = \text{EV} \times (1 + \alpha \times W_2)\]

The OT distance amplifies confidence in EV calculation.

### 4. Kelly Criterion Bet Sizing
\[f^* = \frac{p \times b - q}{b}\]

Where:

- \(p\) = true win probability
- \(b\) = net odds (decimal - 1)
- \(q\) = 1 - p

Use fractional Kelly (0.5×) for risk control.

## 🏗️ System Layout
```
SportsModel/
├── src/
│   ├── agent.py            # CLI entrypoint for demos and sport scans
│   ├── backtest/           # Simple backtest helpers (engine + metrics)
│   ├── data/
│   │   ├── odds_scraper.py # Synthetic odds generator seeded by team lists
│   │   └── team_registry.py# Supported teams per league
│   ├── edge/               # Edge detection, EV math, Kelly sizing
│   ├── models/             # OT distance + distribution helpers
│   ├── sports/             # Lightweight sport-specific analyzers
│   └── discord_bot.py      # Bot wrapper that reuses the analyzer stack
├── tests/                  # Pytest suite (EV math coverage)
├── config.yaml             # Tuning parameters for the modeling stack
├── requirements.txt
└── Readme.md
```

## 🚀 Quick Start
1. **Set up Python environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   The repo ships with stubbed data sources, so no API keys are required to run the demo or tests.

2. **Run demo for a sport**
   ```bash
   python -m src.agent demo --sport basketball_nba --max-games 2
   ```
   The demo uses the synthetic odds generator in `src/data/odds_scraper.py` to create matchups from the team registry and prints any detected edges.

3. **Scan supported sports**
   ```bash
   # Scan every supported code
   python -m src.agent scan --sport all

   # Scan just one league
   python -m src.agent scan --sport football_nfl
   ```
   Scans enumerate teams from `team_registry.py`, generate odds, and report how many matchups were analyzed per league.

4. **Analyze a specific game in code**
   ```python
   from src.sports.nba import NBAAnalyzer

   analyzer = NBAAnalyzer()
   edge = analyzer.analyze_game(
       game_id="nba_001",
       home_team="Golden State Warriors",
       away_team="Memphis Grizzlies",
       total_line=215.5,
       over_odds=-110,
       under_odds=-110,
       injuries=[]
   )

   if edge:
       print(f"Bet {edge.recommendation} {edge.line}")
       print(f"EV: {edge.expected_value:.2%}")
       print(f"Kelly: {edge.kelly_fraction:.2%}")
   ```

## 🔌 Plugging in real data sources
The repo ships with synthetic, no-dependency defaults so it runs anywhere. To connect live feeds, wire them into the existing interfaces instead of touching the modeling stack.

### Odds APIs (any sport)
- Implement your client inside `src/data/odds_scraper.py` by replacing or extending `fetch_odds_api` to call your provider (Odds API, sportsbook feed, etc.).
- Return a list of dicts shaped like the synthetic examples: `{game_id, home_team, away_team, total_line, over_odds, under_odds}`.
- The CLI (`python -m src.agent scan ...`) and examples will automatically use the new feed without changing analyzers.

### NBA injuries via free nba.com endpoints
- Write a lightweight injury fetcher that hits the nba.com/stats injury report endpoint, normalize to `[{"player": str, "status": str, "impact": float}]`.
- Pass the fetcher into your calling flow and supply the injuries list to `NBAAnalyzer.analyze_game(...)` so simulations pick up the adjustments.
- Optional: attach the client to `StatsFetcher(nba_api_client=...)` if you want team metrics (pace/offense/defense) from nba.com as well, keeping the same `fetch_team_metrics` shape used in `StatsFetcher.fetch_team_stats`.

## 🤖 Discord Bot
Bring the scanner directly into your Discord server with slash commands and tiered access:

1. Install dependencies and set environment variables
   ```bash
   pip install -r requirements.txt
   export DISCORD_TOKEN="your_bot_token"
   export DISCORD_GUILD_ID="optional_guild_id_for_faster_sync"  # optional
   export DISCORD_PREMIUM_ROLE="premium"  # role name that unlocks paid commands
   ```

2. Run the bot
   ```bash
   python src/discord_bot.py
   ```

3. Slash commands
   - `/scan` — **Free**: returns the top five edges across all supported sports.
   - `/scan_sport sport:<code>` — **Premium**: returns every edge with EV ≥ 4% for the chosen sport (NBA, NFL, NHL, FBS, CBB, soccer top-5 leagues, Champions League).

Members must have the configured premium role to access sport-specific scans; otherwise they only see the free-tier `/scan` output.

## 📊 Usage Examples
### Example 1: NBA game with a manual injury adjustment
```python
from src.sports.nba import NBAAnalyzer

analyzer = NBAAnalyzer()

edge = analyzer.analyze_game(
    game_id="nba_001",
    home_team="Golden State Warriors",
    away_team="Memphis Grizzlies",
    total_line=215.5,
    over_odds=-110,
    under_odds=-110,
    injuries=[{"player": "Steph Curry", "status": "out", "impact": -5.8}],
)

if edge:
    print(f"Bet {edge.recommendation} {edge.line}")
    print(f"EV: {edge.expected_value:.2%}")
    print(f"Kelly: {edge.kelly_fraction:.2%}")
```

### Example 2: Batch analysis using the synthetic odds generator
```python
from src.data.odds_scraper import OddsScraper
from src.sports.nba import NBAAnalyzer

analyzer = NBAAnalyzer()
scraper = OddsScraper()
odds = scraper.fetch_odds_api(sport="basketball_nba")

edges = []
for game in odds:
    edge = analyzer.analyze_game(**{**game, "injuries": []})
    if edge:
        edges.append(edge)

print(f"Found {len(edges)} edges")
```

### Example 3: Backtesting
```python
from src.backtest.engine import BacktestEngine

engine = BacktestEngine(initial_bankroll=10000)
result = engine.run_backtest(edges, outcomes)

print(f"ROI: {result.roi:.1%}")
print(f"Sharpe: {result.sharpe_ratio:.2f}")
```

## 🎲 Sports Supported
The demo ships with analyzers and team registries for:

| Sport code | Coverage source | Notes |
| --- | --- | --- |
| `basketball_nba` | Full NBA team list | Gaussian scoring model with optional injury adjustments |
| `basketball_cbb_division1` | Division 1 schools | Uses the college basketball analyzer with wider variance |
| `football_nfl` | Full NFL team list | Totals-focused analyzer with pace and variance tweaks |
| `football_cfb_fbs` | FBS programs | Higher-variance distribution for college totals |
| `hockey_nhl` | Full NHL team list | Goal-based distribution tuned for low totals |
| `soccer_*` | EPL, LaLiga, Bundesliga, Serie A, Ligue 1, Champions League | Analyzes totals using league baselines |

## ⚙️ Configuration
Edit `config.yaml`:

```yaml
# Optimal Transport
ot:
  threshold: 0.15        # Minimum W distance for edge
  p_norm: 2              # Wasserstein-2
  reg: 0.01              # Sinkhorn regularization

# Edge Detection
edge:
  min_ev: 0.03           # 3% minimum EV
  min_kelly: 0.01
  max_kelly: 0.05        # Max 5% of bankroll

# Monte Carlo
simulation:
  num_paths: 10000
  confidence_interval: 0.95

# Risk Management
risk:
  max_exposure_per_game: 0.02   # 2% per game
  max_daily_exposure: 0.10      # 10% per day
  stop_loss_daily: -0.05        # Stop at 5% daily loss
```

## 📈 Outputs and metrics
Every detected edge returned by `EdgeDetector` includes:

- Expected Value (EV) computed from true win probability vs. stake
- Kelly fraction (capped) for sizing
- True probability compared to market-implied probability
- Wasserstein distance between the modeled distribution and market line

Example CLI output from the demo:
```
⚡ EDGE DETECTED
{
  "recommendation": "OVER",
  "line": 215.5,
  "expected_value": 0.048,
  "kelly_fraction": 0.02,
  "true_prob": 0.54,
  "market_prob": 0.52,
  "wasserstein_distance": 0.19
}
```

## 🔬 How It Works (Technical)
### Pipeline in this repository
1. **Generate matchups** via `OddsScraper.fetch_odds_api`, which creates synthetic lines from the team registry for the selected sport.
2. **Estimate team baselines** with `StatsFetcher`, which provides lightweight pace/offense/variance priors for each league.
3. **Simulate outcomes** using `MonteCarloSimulator` to build a Gaussian distribution of totals that can accept manual injury impacts.
4. **Measure distance** between the simulated distribution and the market line using `OTEngine.distance_between_distributions`.
5. **Detect edges** with `EdgeDetector`, which applies the EV calculator and Kelly sizing, then filters on OT distance and minimum EV thresholds.

### Extending to real data
- Replace the stubbed odds generator with an API-backed fetcher inside `src/data/odds_scraper.py`.
- Swap `StatsFetcher` internals for live stat feeds or model outputs.
- Tune thresholds in `config.yaml` to match your risk profile once real data is connected.

## 🧪 Testing Individual Components
- Test OT Engine: `python src/models/ot_engine.py`
- Test Causal Graph: `python src/models/causal_graph.py`
- Test Monte Carlo: `python src/models/monte_carlo.py`
- Test Edge Detection: `python src/edge/detector.py`
- Validate EV math and scanners: `pytest tests/test_ev_calculator.py`

Each module has standalone examples and targeted tests for critical calculations.

## 🚨 Disclaimer
This is for educational and research purposes.

Sports betting involves risk
Past performance ≠ future results
Only bet what you can afford to lose
Check local laws and regulations
Use responsible bankroll management
The authors assume no liability for losses.

## 📚 Mathematical References
- Optimal Transport: Villani, "Optimal Transport: Old and New"
- Wasserstein Distance: Cuturi, "Sinkhorn Distances" (NIPS 2013)
- Kelly Criterion: Kelly, "A New Interpretation of Information Rate" (1956)
- Causal Inference: Pearl, "Causality" (2000)
- Sports Analytics: Macdonald, "Adjusted Plus-Minus Models" (2011)

## 🤝 Contributing
Contributions welcome!

Areas for improvement:
- Real-time odds streaming
- Player prop betting
- Live in-game betting
- Machine learning priors
- Multi-book arbitrage
- Automated execution

## 📧 Support
For questions or issues:
- Review the examples in this README
- Open a GitHub issue with reproduction steps

## ⭐ Key Features
- ✅ Optimal Transport distance + EV filtering for edges
- ✅ Kelly Criterion bet sizing with configurable caps
- ✅ Multi-sport analyzers fed by a shared data/OT stack
- ✅ Lightweight backtesting helpers

Good luck, and bet responsibly! 🎯
