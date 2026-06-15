# FIFA World Cup 2026 — Prediction & Simulation

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Python framework for predicting **FIFA World Cup 2026** match outcomes, simulating the full 48-team tournament, comparing model probabilities to prediction-market prices, and producing an actionable bet slip. Everything runs from a single script: `wc2026_simulation.py`.

> **Disclaimer:** This project is for research and entertainment. It is **not financial advice**. Prediction markets involve risk of loss. Past backtest performance does not guarantee future results.

---

## Table of contents

1. [What this project does](#what-this-project-does)
2. [Installation & usage guide](#installation--usage-guide)
3. [What's in Git vs created locally](#whats-in-git-vs-created-locally)
4. [Project folder layout](#project-folder-layout)
5. [Pipeline (8 steps)](#pipeline-8-steps)
6. [Model methodology](#model-methodology)
7. [Data sources](#data-sources)
8. [Configuration](#configuration)
9. [Output files](#output-files)
10. [Prediction markets & betting workflow](#prediction-markets--betting-workflow)
11. [Backtest (2018 & 2022)](#backtest-2018--2022)
12. [HTML report](#html-report)
13. [Maintenance & refreshing data](#maintenance--refreshing-data)
14. [Troubleshooting](#troubleshooting)
15. [Known limitations](#known-limitations)
16. [Architecture overview](#architecture-overview)
17. [License & attribution](#license--attribution)

---

## What this project does

| Capability | Description |
|------------|-------------|
| **Team strength** | Blends historical Elo, 12-month form Elo, full-squad market value, and expected starting-XI value |
| **Match predictions** | Win / draw / loss probabilities and fair decimal odds for every upcoming WC 2026 fixture |
| **Value bets** | Compares model vs `market_odds.csv` (Polymarket or bookmaker prices) |
| **Bet slip** | Filters to BET / LEAN tiers with Kelly-based stake suggestions |
| **Tournament simulation** | 10,000 Monte Carlo runs of the full 48-team bracket (groups → R32 → final) |
| **Backtest** | Point-in-time validation on 2018 & 2022 World Cup group stages |
| **HTML dashboard** | Self-contained `wc2026_report.html` with all outputs |
| **Live mode** | Fast refresh: Elo, Polymarket odds, FotMob lineups, bet slip (~30–60s) |
| **Pre-kickoff scheduler** | Auto-run live pipeline 60 min before each match (Windows Task Scheduler or daemon) |

The tournament format modelled: **12 groups of 4**, top 2 advance plus **8 best third-place** teams → 32-team knockout bracket.

**Host nations** (USA, Mexico, Canada) receive a home-advantage boost at non-neutral venues.

---

## Installation & usage guide

Complete steps for anyone cloning the repo or downloading the project.

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.10+** | Check with `python --version` or `python3 --version` |
| **pip** | Usually bundled with Python |
| **Git** | Only needed to clone the repo |
| **Internet** | Required on first run (downloads match data, FIFA squads, Transfermarkt values) |

Optional but recommended: a virtual environment to keep dependencies isolated.

---

### Step 1 — Get the project

**Option A — Clone from GitHub (recommended)**

```bash
git clone https://github.com/AliCodeMxzzi/fifa-2026-world-cup-prediction.git
cd fifa-2026-world-cup-prediction
```

**Option B — Download ZIP**

1. Open [github.com/AliCodeMxzzi/fifa-2026-world-cup-prediction](https://github.com/AliCodeMxzzi/fifa-2026-world-cup-prediction)
2. Click **Code → Download ZIP**
3. Extract the folder and open a terminal inside it

---

### Step 2 — Create a virtual environment (optional)

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

### Step 3 — Install dependencies

From the project folder:

```bash
pip install -r requirements.txt
```

This installs: `numpy`, `pandas`, `scipy`, `requests`, `tqdm`, `tabulate`, `beautifulsoup4`, `pdfplumber`.

---

### Step 4 — Run the simulator

**Windows (PowerShell)** — set UTF-8 to avoid console errors with team names:

```powershell
$env:PYTHONIOENCODING='utf-8'
python wc2026_simulation.py
```

**macOS / Linux**

```bash
PYTHONIOENCODING=utf-8 python3 wc2026_simulation.py
```

**Expected runtime:** ~2–5 minutes on first run (downloads + scraping). Later runs are faster (~1–3 min) because data is cached locally.

**What you will see:** 8 progress steps in the terminal:

```
[1/8] Loading international match data …
[2/8] Backtesting model on 2018 & 2022 group stages …
[3/8] Computing Elo ratings …
[4/8] Building squad strength …
[5/8] Initialising Poisson match engine …
[6/8] Predicting upcoming World Cup matches …
[7/8] Running 10,000 Monte Carlo simulations …
[8/8] Building HTML report …
```

---

### Step 5 — View the results

After a successful run, open these files in the project folder:

| File | What it is |
|------|------------|
| **`wc2026_report.html`** | Main dashboard — double-click to open in Chrome, Firefox, Edge, Safari |
| **`wc2026_bet_slip.csv`** | Actionable BET / LEAN picks for the next 3 days |
| **`wc2026_match_predictions.csv`** | Win/draw/loss probabilities for every upcoming fixture |
| **`wc2026_value_bets.csv`** | Fair odds vs market for all three outcomes per match |
| **`wc2026_backtest.csv`** | 2018 & 2022 validation results |

> Generated files are listed in `.gitignore` and are **not** in the GitHub repo — you create them locally by running the script.

---

### Step 6 — Add your market prices (for value bets & bet slip)

The repo includes a sample `market_odds.csv`. To compare against **your** prediction-market prices:

1. Open `market_odds.csv` in Excel, Google Sheets, or any text editor
2. For each match, fill in three prices:

| Column | Example (Polymarket) | Example (decimal odds) |
|--------|----------------------|-------------------------|
| `market_home_decimal` | `0.69` (69¢) | `1.45` |
| `market_draw_decimal` | `0.21` | `4.50` |
| `market_away_decimal` | `0.11` | `9.00` |

**Price format rules:**

- Values **between 0 and 1** → treated as Polymarket contract prices (e.g. `0.69` = 69%)
- Values **≥ 1** → treated as decimal odds (e.g. `1.85`)

3. Save the file
4. Re-run `python wc2026_simulation.py`

Without market prices, you still get match predictions and tournament simulations — but no value bets or bet slip.

---

### Step 7 — Match-day workflow (before placing bets)

**Recommended (automated):** install the pre-kickoff scheduler once — it runs `--live` 60 minutes before each match (lineups + odds + predictions). See [Pre-kickoff scheduler](#pre-kickoff-scheduler-auto-t-60-min).

**Manual alternative:**

```
1. Run: python wc2026_simulation.py --live     ← refreshes odds, lineups, Elo
2. Open wc2026_bet_slip.csv or wc2026_live_report.html
3. Check team news / confirmed lineups
4. Place only BET-tier picks at full stake; LEAN at half or skip
5. Log each wager in paper_trade_log.csv
```

`--live` auto-fetches Polymarket prices (next 7 days) and FotMob confirmed XIs (within ~3 h of kickoff). You can still edit `market_odds.csv` or `expected_lineups.json` by hand if needed.

**Bet tiers (printed in terminal and bet slip):**

| Tier | Criteria | Suggested stake (100-unit bankroll) |
|------|----------|-------------------------------------|
| **BET** | Model ≥ 58% **and** edge ≥ 8% | 2 units (2%) |
| **LEAN** | Model ≥ 55% **and** edge ≥ 6% | 1 unit (1%) |
| **SKIP** | Below thresholds | Do not bet |

Max **8 units (8%)** total exposure per calendar day.

---

### Step 8 — Log your bets (track performance)

`paper_trade_log.csv` is created automatically. After each real wager, add a row:

```csv
date,match,pick,tier,market_price,stake_units,model_prob,edge,result,pnl_units,notes
2026-06-11,Mexico vs South Africa,Mexico,BET,0.69,2.0,0.7634,0.0802,W,1.45,Mexico won 2-0
```

- **`result`:** `W` (win), `L` (loss), or `P` (push/refund)
- **`pnl_units`:** profit/loss in bankroll units (win on Polymarket ≈ `stake × (1/market_price − 1)`)

This file stays on your machine only (not pushed to GitHub).

---

### Re-running & updating data

| When | What to do |
|------|------------|
| **Before each matchday (fast)** | `python wc2026_simulation.py --live` (~30–60s) |
| **Auto lineups + schedule** | `python wc2026_scheduler.py install` (see below) |
| **Before each matchday** | Update `expected_lineups.json` if lineups changed |
| **Full champion / group odds** | `python wc2026_simulation.py` (no flags, ~3 min) |
| **Polymarket only** | `python wc2026_simulation.py --fetch-odds` |
| **Force fresh match results** | `python wc2026_simulation.py --refresh-data` |
| **After WC matches finish** | `--live` or `--refresh-data` re-downloads match CSV |
| **New Transfermarkt values** | Delete `squad_market_values.json` and `squad_xi_values.json`, re-run |
| **Lineup changes** | Edit `expected_lineups.json`, re-run |
| **Pull repo updates** | `git pull` then re-run |

### Live mode (`--live`)

```powershell
$env:PYTHONIOENCODING='utf-8'
python wc2026_simulation.py --live
```

- Re-downloads match results (updates Elo when games finish)
- Auto-fetches Polymarket odds for next 7 days
- Auto-fetches confirmed lineups from FotMob (within ~3 h of kickoff)
- Skips backtest + Monte Carlo → outputs `wc2026_live_report.html` + bet slip

Use `--fetch-lineups` alone to refresh `expected_lineups.json` without running the full live pipeline.

### Pre-kickoff scheduler (auto T-60 min)

`wc2026_scheduler.py` runs the live pipeline **60 minutes before each match**:
FotMob confirmed lineups → updated predictions → bet slip.

**One-time setup (Windows)**

```powershell
$env:PYTHONIOENCODING='utf-8'
.\install_scheduler.ps1
```

Or manually:

```powershell
python wc2026_scheduler.py refresh    # cache FotMob kickoff times + match IDs
python wc2026_scheduler.py install    # create one Task Scheduler job per match
```

**Commands**

| Command | Purpose |
|---------|---------|
| `refresh` | Download WC fixture schedule → `wc2026_fixture_schedule.json` |
| `install` | Windows Task Scheduler: run at kickoff − 60 min (local time) |
| `uninstall` | Remove all `WC2026_PreKickoff_*` tasks |
| `prekickoff --match-id ID` | Fetch lineups + run `--live` for one match |
| `daemon` | Poll every 5 min and trigger T-60 runs (macOS/Linux or no admin) |

**Notes**

- FotMob usually publishes confirmed XIs ~75 minutes before kickoff; the scheduler targets T-60.
- Re-run `install` after the schedule changes (e.g. knockout draw) to add new tasks.
- On macOS/Linux, use `python wc2026_scheduler.py daemon` and keep the terminal open.

---

### One-command cheat sheet

**Windows — full run from scratch**

```powershell
git clone https://github.com/AliCodeMxzzi/fifa-2026-world-cup-prediction.git
cd fifa-2026-world-cup-prediction
pip install -r requirements.txt
$env:PYTHONIOENCODING='utf-8'
python wc2026_simulation.py
start wc2026_report.html
```

**macOS / Linux**

```bash
git clone https://github.com/AliCodeMxzzi/fifa-2026-world-cup-prediction.git
cd fifa-2026-world-cup-prediction
pip install -r requirements.txt
PYTHONIOENCODING=utf-8 python3 wc2026_simulation.py
open wc2026_report.html    # macOS
# xdg-open wc2026_report.html   # Linux
```

---

## What's in Git vs created locally

After `git clone`, you get **source code + starter data**. Everything else is downloaded or generated on **your machine** the first time you run the script.

### Included in the repository

| File | Purpose |
|------|---------|
| `wc2026_simulation.py` | Main pipeline |
| `wc2026_scheduler.py` | Pre-kickoff automation (FotMob + Task Scheduler) |
| `install_scheduler.ps1` | One-click Windows scheduler setup |
| `requirements.txt` | Python dependencies |
| `expected_lineups.json` | Starter projected XIs for all 48 teams (updated over time via FotMob or manual edits) |
| `market_odds.csv` | Sample Polymarket prices — enough to demo value bets; refreshed automatically in `--live` mode |
| `README.md`, `LICENSE`, `.gitignore` | Docs and license |

### Created on first run (not in Git — listed in `.gitignore`)

| File | How it appears |
|------|----------------|
| `international_results.csv` | Auto-downloaded from GitHub (~49k matches) |
| `squad_lists.pdf` | Auto-downloaded from FIFA |
| `squad_market_values.json` | Scraped from Transfermarkt (slow; cached) |
| `squad_xi_values.json` | Scraped from Transfermarkt per-player pages (slow; cached) |
| `wc2026_report.html` | Full-run HTML dashboard |
| `wc2026_live_report.html` | Live-mode HTML dashboard |
| `wc2026_match_predictions.csv` | Match probabilities |
| `wc2026_value_bets.csv` | Fair odds vs market |
| `wc2026_bet_slip.csv` | BET / LEAN recommendations |
| `wc2026_backtest.csv` | 2018 & 2022 validation |

**First full run needs internet** and takes ~2–5 minutes while caches are built. Later runs are faster.

### Created when you use the scheduler (not in Git)

| File | How it appears |
|------|----------------|
| `wc2026_fixture_schedule.json` | `python wc2026_scheduler.py refresh` — FotMob match IDs + UTC kickoffs |
| `wc2026_scheduler_state.json` | Written by `daemon` mode to avoid duplicate triggers |

### Personal / optional (not in Git)

| File | Notes |
|------|-------|
| `paper_trade_log.csv` | Created when you log bets; stays on your machine only |

### What a new clone does *not* need to set up manually

- Python packages → `pip install -r requirements.txt`
- Match history, FIFA squads, Transfermarkt values → first `python wc2026_simulation.py`
- Polymarket odds for upcoming games → `python wc2026_simulation.py --live` (or included sample CSV for demo)
- Confirmed lineups near kickoff → `--live` / scheduler pulls from FotMob

### Optional one-time setup (your environment)

| Goal | Action |
|------|--------|
| **Auto T-60 runs (Windows)** | `.\install_scheduler.ps1` (needs Task Scheduler access; uses your local Python path) |
| **Auto T-60 runs (macOS/Linux)** | `python wc2026_scheduler.py daemon` (keep terminal open) |
| **Custom market prices** | Edit `market_odds.csv` if not using `--live` auto-fetch |
| **Override lineups** | Edit `expected_lineups.json` before a run |
| **UTF-8 on Windows** | `$env:PYTHONIOENCODING='utf-8'` in PowerShell (or set permanently in your profile) |

No API keys, accounts, or secrets are required — FotMob and Polymarket Gamma API are used without authentication.

---

```
Fifa 2026 World Cup Prediction/
│
├── wc2026_simulation.py      # Main script — entire pipeline (~3,100 lines)
├── wc2026_scheduler.py       # Pre-kickoff scheduler (FotMob + Task Scheduler)
├── install_scheduler.ps1     # One-click Windows scheduler install
├── README.md                 # This file
├── LICENSE                   # MIT License
├── requirements.txt          # Python dependencies
│
├── ── INPUT / CACHE DATA ──────────────────────────────────────────
├── international_results.csv # ~49k international matches (auto-downloaded)
├── squad_lists.pdf           # FIFA official squad lists (auto-downloaded)
├── squad_market_values.json  # Cached Transfermarkt full-squad € values
├── squad_xi_values.json      # Cached per-player values + XI totals
├── expected_lineups.json     # Projected / confirmed starting XIs (in repo; FotMob updates locally)
├── market_odds.csv           # Sample Polymarket prices (in repo; auto-updated in --live)
│
├── ── AUTO-DOWNLOADED CACHE (local only, .gitignore) ─────────────
├── international_results.csv # ~49k international matches
├── squad_lists.pdf           # FIFA official squad lists
├── squad_market_values.json  # Transfermarkt full-squad € values
├── squad_xi_values.json      # Per-player values + XI totals
├── wc2026_fixture_schedule.json  # FotMob kickoff times (scheduler)
├── wc2026_scheduler_state.json # Daemon trigger state
│
├── ── GENERATED OUTPUTS (local only, .gitignore) ─────────────────
├── wc2026_report.html        # Full-run HTML dashboard
├── wc2026_live_report.html   # Live-mode HTML dashboard
├── wc2026_match_predictions.csv
├── wc2026_value_bets.csv
├── wc2026_bet_slip.csv
├── wc2026_backtest.csv
└── paper_trade_log.csv       # Personal bet log (never committed)
```

### File-by-file reference

#### `wc2026_simulation.py`

Main pipeline script. Also includes live mode (`--live`), FotMob lineup fetch (`--fetch-lineups`), and Polymarket odds fetch (`--fetch-odds`). Contains:

- Configuration constants (weights, thresholds, URLs)
- Official 2026 group draw (`GROUPS` A–L, 48 teams)
- Elo system with competition-weighted K-factors
- Squad strength from FIFA PDF + Transfermarkt
- Poisson match engine
- Group stage + knockout tournament simulator
- Value-bet and bet-slip logic
- 2018/2022 backtest
- HTML report generator
- `main` entry point (`if __name__ == "__main__"`)

#### `wc2026_scheduler.py`

Companion script for pre-kickoff automation. Fetches FotMob fixture times, installs Windows Task Scheduler jobs at kickoff − 60 min, or runs as a polling daemon. Each trigger runs `wc2026_simulation.py --live`.

#### `international_results.csv`

Cached copy of [Mart Jürisoo's international results dataset](https://github.com/martj42/international_results).

| Column | Description |
|--------|-------------|
| `date` | Match date |
| `home_team`, `away_team` | Team names (dataset spelling) |
| `home_score`, `away_score` | Goals (`NaN` for unplayed 2026 WC fixtures) |
| `tournament` | e.g. `FIFA World Cup`, `Friendly`, `UEFA Euro qualification` |
| `city`, `country` | Venue |
| `neutral` | `TRUE` / `FALSE` |

Used for Elo training, fixture loading, and backtests. Delete to force re-download.

#### `squad_lists.pdf`

Official FIFA squad list PDF. Parsed with `pdfplumber` to get player rosters per nation. Downloaded once from FIFA's CDN and cached locally.

#### `squad_market_values.json`

Total Transfermarkt market value (€) per nation's full World Cup squad. Example:

```json
{
  "France": 1520000000.0,
  "England": 1360000000.0,
  ...
}
```

Delete to re-scrape Transfermarkt (slow; respect rate limits).

#### `squad_xi_values.json`

Per-player market values scraped from Transfermarkt, keyed by team. Used to sum **starting XI** value when `expected_lineups.json` names are matched to players.

#### `expected_lineups.json`

Projected starting elevens for all 48 teams. Structure per team:

```json
{
  "Argentina": {
    "source": "ESPN (squad minus projected bench)",
    "starters": ["Emiliano Martínez", "Nahuel Molina", ...]
  }
}
```

- **17 teams:** ESPN projected benches (starters = squad minus bench)
- **31 teams:** Goal.com projections
- Manual fixes applied where needed (e.g. Argentina GK, Senegal)
- **FotMob confirmed XIs** merged automatically in `--live` mode or via the scheduler

Edit this file when you want to override auto-fetched lineups or before kickoff if FotMob has not published yet.

#### `market_odds.csv` *(starter in repo; refreshed in live mode)*

Prediction-market or bookmaker prices for value bets and bet slip.

| Column | Description |
|--------|-------------|
| `date` | `YYYY-MM-DD` |
| `home_team`, `away_team` | Must match canonical names in the script |
| `market_home_decimal` | Home win price |
| `market_draw_decimal` | Draw price |
| `market_away_decimal` | Away win price |
| `notes` | Optional (e.g. `Polymarket Jun 11`) |

**Price formats supported:**

- **Polymarket-style:** `0.69` (= 69¢ implied probability)
- **Decimal odds:** `1.85` (= 54% implied)

The script auto-detects: values between 0 and 1 are treated as contract prices; values ≥ 1 as decimal odds.

#### `paper_trade_log.csv` *(user-maintained)*

Template for logging real wagers. Fill in after each bet:

```
date, match, pick, tier, market_price, stake_units, model_prob, edge, result, pnl_units, notes
```

Use this to track whether the model actually beats the market over the tournament.

#### Generated CSV / HTML outputs

See [Output files](#output-files) below.

---

## Pipeline (8 steps)

When you run `python wc2026_simulation.py`:

| Step | Action |
|------|--------|
| **1/8** | Load `international_results.csv` (download if missing) |
| **2/8** | **Backtest** 2018 & 2022 WC group stages (Elo-only, no look-ahead) |
| **3/8** | Compute **historical Elo** (2018–2026) + **form Elo** (last 12 months) |
| **4/8** | Build **squad strength** (FIFA PDF + Transfermarkt + expected XIs) |
| **5/8** | Initialise **Poisson match engine**; print key matchup previews |
| **6/8** | Predict all upcoming WC fixtures; value bets; **bet slip** |
| **7/8** | Run **10,000 Monte Carlo** full-tournament simulations |
| **8/8** | Generate **`wc2026_report.html`** |

---

## Model methodology

### 1. Team strength (four components)

Final blended rating drives expected goals in every match:

| Component | Weight | Source |
|-----------|--------|--------|
| Historical Elo | 35% | International matches 2018-01-01 → 2026-12-31 |
| Form Elo | 20% | Same matches, last 12 months only |
| Full squad value | 15% | Transfermarkt 26-man squad total → Elo scale |
| Starting XI value | 30% | Sum of expected starters' Transfermarkt values → Elo scale |

Squad € values are log-scaled and mapped to an Elo-like range (~1200–2100) so they blend with Elo ratings.

### 2. Elo system

- **Initial rating:** 1500 (or FIFA ranking prior for teams with little history)
- **K-factor:** 40, multiplied by match importance:

| Competition type | K multiplier |
|------------------|--------------|
| World Cup qualifiers | 1.50× |
| Continental tournaments (Euro, Copa, AFCON, etc.) | 1.30× |
| Continental qualifiers | 1.20× |
| Nations League | 1.15× |
| Friendlies | 0.70× |
| Other | 1.00× |

- **Home advantage in Elo:** +100 rating points for home team (in Elo updates only)
- Unplayed fixtures (`NaN` scores) are excluded from Elo calculation

### 3. Poisson match engine

From the strength gap between teams:

1. Compute expected goals (xG) for each side from rating differential
2. Apply **tournament defensiveness** factor (×0.85 — WC matches score less)
3. Apply **host boost** (+0.25 xG) for USA / Mexico / Canada at non-neutral venues
4. Clip xG to [0.20, 4.5]
5. Sum Poisson probabilities over scorelines 0–0 through 9–9 for win/draw/loss

Knockout ties: extra time (28% of normal xG) then penalty shootout with slight favourite bias.

### 4. Tournament simulation

For each of 10,000 runs:

1. Simulate all 12 group stages (3 matches per team)
2. Rank groups; select **8 best third-place** teams (points → GD → goals)
3. Build Round of 32 from FIFA bracket template
4. Simulate knockout rounds (no draws after group stage)
5. Track how often each team reaches R32, R16, QF, SF, Final, Wins

### 5. Value bets & bet slip

**Value flag** (broad): model probability exceeds vig-adjusted market implied by ≥ **5%**.

**Bet slip tiers** (strict, for real money):

| Tier | Model prob | Vig-adjusted edge | Stake |
|------|------------|-------------------|-------|
| **BET** | ≥ 58% | ≥ 8% | Quarter-Kelly, cap 2% bankroll |
| **LEAN** | ≥ 55% | ≥ 6% | Flat 1% bankroll |
| **SKIP** | Below thresholds | — | No bet |

Additional rules:

- **Vig removal:** when all three outcomes are priced, implied probs are normalised to sum to 100%
- **Matchday cap:** total exposure scaled down if > 8% bankroll on one calendar day
- **Horizon:** bet slip only includes fixtures in the **next 3 days** from run date

---

## Data sources

| Data | Source | Cached as |
|------|--------|-----------|
| International results | [martj42/international_results](https://github.com/martj42/international_results) | `international_results.csv` |
| Squad lists | [FIFA SquadLists PDF](https://fdp.fifa.org/assetspublic/ce281/pdf/SquadLists-English.pdf) | `squad_lists.pdf` |
| Squad market values | [Transfermarkt WC 2026](https://www.transfermarkt.com/weltmeisterschaft/teilnehmer/pokalwettbewerb/FIWC/saison_id/2025) | `squad_market_values.json` |
| Player values | Transfermarkt per-player pages | `squad_xi_values.json` |
| Expected lineups | ESPN + Goal.com (repo) + FotMob (live) | `expected_lineups.json` |
| Market prices | Polymarket Gamma API (`--live`) or manual edit | `market_odds.csv` |
| Fixture times / match IDs | FotMob API | `wc2026_fixture_schedule.json` |
| Confirmed lineups (match day) | FotMob API | merged into `expected_lineups.json` |

---

## Configuration

All tunables are at the top of `wc2026_simulation.py` (lines ~48–114):

```python
NUM_SIMULATIONS = 10_000
ELO_HIST_WEIGHT = 0.35
ELO_FORM_WEIGHT = 0.20
SQUAD_FULL_WEIGHT = 0.15
SQUAD_XI_WEIGHT = 0.30
HOST_ADVANTAGE_GOALS = 0.25
TOURNAMENT_DEFENSIVENESS = 0.85
VALUE_EDGE_THRESHOLD = 0.05
BET_TIER_STRONG_PROB = 0.58
BET_TIER_STRONG_EDGE = 0.08
BET_MAX_STAKE_PCT = 0.02
MATCHDAY_EXPOSURE_CAP = 0.08
DEFAULT_BANKROLL = 100.0
```

**Groups A–L** and **FIFA rankings** (April 2026) are hard-coded in the script. Update `GROUPS` if the draw changes.

---

## Output files

### `wc2026_match_predictions.csv`

One row per upcoming fixture:

- Win/draw/loss probabilities
- Fair decimal odds (`1 / probability`)
- Model pick and confidence
- Expected goals
- Best value bet label and edge (if any)

### `wc2026_value_bets.csv`

Three rows per fixture (home / draw / away):

- Model prob, fair odds, American odds
- Market decimal, vig-adjusted implied, edge
- `is_value_bet` boolean (≥ 5% edge)

### `wc2026_bet_slip.csv`

Actionable recommendations only (BET / LEAN, next 3 days):

- Pick, tier, market price, edge, EV%, Kelly, suggested stake in units

### `wc2026_backtest.csv`

96 rows (48 per tournament): 2018 + 2022 group-stage predictions vs actual results.

- Per-match Brier score and log-loss
- Used to sanity-check the Elo/Poisson core before trusting 2026 picks

### `wc2026_report.html`

Self-contained dashboard sections:

1. Tournament summary (champion odds, dark horse, etc.)
2. **Bet slip** (next 3 days)
3. **Backtest** (2018/2022 metrics + calibration)
4. Value bets (all flagged edges)
5. Match predictions by date
6. Team strength rankings
7. Top 15 contenders
8. Group advancement probabilities
9. Full tournament distribution table
10. Methodology

No server required — double-click to open.

---

## Prediction markets & betting workflow

### Before match day

1. **Easiest:** `python wc2026_simulation.py --live` (or let the scheduler run it at T-60)
2. Or edit **`market_odds.csv`** / **`expected_lineups.json`** manually, then run the script
3. Read **`wc2026_bet_slip.csv`** or the Bet Slip section in the HTML report

### Pre-bet checklist (printed in terminal)

1. Re-run with fresh `market_odds.csv`
2. Confirm starting XIs and injuries
3. **BET tier** at full suggested stake; **LEAN** at half or skip
4. Stay under daily exposure cap (8% bankroll)
5. Log every wager in `paper_trade_log.csv`

### Suggested bankroll mapping

Default display uses **100 units** = your full bankroll:

| Tier | Stake |
|------|-------|
| BET | 2 units (2%) |
| LEAN | 1 unit (1%) |
| Max per day | 8 units (8%) |

Scale to your actual bankroll (e.g. $500 bankroll → 1 unit = $5).

### What *not* to bet on blindly

- **Draw** picks (poor calibration in backtest)
- Value on an **underdog** when model favourite is the other side (warning shown)
- High edge but **low model probability** (e.g. Morocco vs Brazil — filtered out by design)
- Anything below BET/LEAN thresholds

---

## Backtest (2018 & 2022)

Validates the **Elo + Poisson core only** — squad/XI data is excluded to prevent look-ahead bias (2026 Transfermarkt values did not exist in 2018).

| Setting | Value |
|---------|-------|
| Tournaments | 2018, 2022 World Cups |
| Matches | First 48 group games per year (chronological) |
| Training cutoff | All matches strictly before tournament kickoff |
| Strength model | 64% historical Elo + 36% form Elo (renormalised from 35%/20%) |

**Typical combined results:**

| Metric | Approx. value | Interpretation |
|--------|---------------|----------------|
| Brier score | ~0.615 | Better than random (~0.67) |
| Pick accuracy | ~53% | Slightly above 33% random baseline |
| Favourite accuracy (≥55%) | ~55% | Marginal |
| Simulated flat-bet ROI | Negative | Model not proven profitable |

The 2026 live model adds squad/XI components that were **not** backtested on past World Cups.

---

## HTML report

File: `wc2026_report.html`

Dark-themed, single-page report with sticky navigation. Regenerated every run. Safe to share (no API keys; all data embedded).

---

## Maintenance & refreshing data

| Goal | Action |
|------|--------|
| Fresh match results | `python wc2026_simulation.py --refresh-data` or delete `international_results.csv` |
| Fresh Polymarket odds | `python wc2026_simulation.py --live` or `--fetch-odds` |
| Fresh FotMob lineups | `python wc2026_simulation.py --live` or `--fetch-lineups` |
| Re-scrape Transfermarkt | Delete `squad_market_values.json` and `squad_xi_values.json` |
| Re-download FIFA squads | Delete `squad_lists.pdf` |
| Refresh scheduler fixtures | `python wc2026_scheduler.py refresh` |
| Re-install T-60 tasks | `python wc2026_scheduler.py install --force` |
| Update lineups manually | Edit `expected_lineups.json` |
| Update market prices manually | Edit `market_odds.csv` |
| Reset bet tracking | Clear rows in `paper_trade_log.csv` (keep header) |

After deleting caches, the next run will re-download/scrape (slower).

---

## Troubleshooting

### Unicode / emoji errors on Windows

```powershell
$env:PYTHONIOENCODING='utf-8'
python wc2026_simulation.py
```

### Scheduler tasks not created (Windows)

- Run PowerShell as a user with permission to create scheduled tasks
- Ensure `python` is on PATH (`python --version`)
- Re-run: `python wc2026_scheduler.py install --force`
- Alternative: `python wc2026_scheduler.py daemon` (no admin required)

### No value bets / empty bet slip

- Ensure `market_odds.csv` has prices filled in for upcoming matches
- Bet slip only shows picks in the **next 3 days** that pass strict BET/LEAN filters
- Many edges are flagged in `wc2026_value_bets.csv` but filtered out of the slip

### Squad scrape warnings (`FontBBox`)

Harmless `pdfplumber` font warnings; parsing still works.

### Transfermarkt scrape fails

Uses cached JSON if present. If both network and cache fail, squad weight falls back to FIFA ranking priors.

### Team name mismatches

Canonical names are defined in `GROUPS`. The script maps variants via `NAME_MAP` (e.g. `Korea Republic` → `South Korea`). If a market row doesn't match, that fixture won't get value analysis — align names with `wc2026_match_predictions.csv`.

### Wrong player values (€190m for height)

Delete `squad_xi_values.json` and re-run. A fix ensures only cells containing `€` are parsed as market value.

---

## Known limitations

- **Not proven profitable** — backtest shows signal but negative simulated ROI at fair odds
- **Squad/XI component untested** on historical World Cups
- **No injury model** — FotMob lineups help near kickoff; check team news for late changes
- **No line movement / closing line** tracking
- **Host advantage** only for 2026 hosts (not Russia 2018, Qatar 2022 in backtest)
- **Draw calibration** is weak
- **Monte Carlo** uses same match engine as predictions but does not condition on group-stage results when simulating the full tournament from scratch each run
- **Polymarket auto-fetch** covers ~7 days ahead; older or unlisted markets still need manual `market_odds.csv` rows
- **Scheduler** uses your machine's local timezone and Python path — re-run `install` after moving the project folder

---

## Architecture overview

```
international_results.csv ──┐
expected_lineups.json ──────┤
squad_lists.pdf ────────────┼──► wc2026_simulation.py ──► CSV outputs
squad_*_values.json ────────┤                          └──► wc2026_report.html
market_odds.csv ────────────┘

Internal flow:

  Data load → Backtest (Elo-only)
           → Elo (hist + form)
           → Squad strength (full + XI)
           → TeamStrengthModel (blend)
           → PoissonMatchEngine
                ├─► Fixture predictions + value bets + bet slip
                └─► Monte Carlo tournament (10k × full bracket)
```

### Main classes in `wc2026_simulation.py`

| Class | Role |
|-------|------|
| `EloSystem` | Rating updates from match history |
| `SquadStrengthSystem` | FIFA PDF + Transfermarkt + XI values |
| `TeamStrengthModel` | 4-way blend into single rating |
| `PoissonMatchEngine` | xG, win/draw/loss probs, match simulation |
| `FixturePrediction` | One upcoming match forecast |
| `MatchValueAnalysis` | Fair odds + market comparison |
| `BetRecommendation` | Actionable BET/LEAN row |
| `BacktestSummary` | 2018/2022 validation metrics |
| `TeamTournamentRecord` | Monte Carlo advancement counts |

---

## 2026 groups (reference)

| Group | Teams |
|-------|-------|
| A | Mexico, South Africa, South Korea, Czechia |
| B | Canada, Switzerland, Qatar, Bosnia Herzegovina |
| C | Brazil, Morocco, Haiti, Scotland |
| D | United States, Paraguay, Australia, Turkey |
| E | Germany, Curacao, Ivory Coast, Ecuador |
| F | Netherlands, Japan, Tunisia, Sweden |
| G | Belgium, Egypt, Iran, New Zealand |
| H | Spain, Cape Verde, Saudi Arabia, Uruguay |
| I | France, Senegal, Iraq, Norway |
| J | Argentina, Algeria, Austria, Jordan |
| K | Portugal, DR Congo, Uzbekistan, Colombia |
| L | England, Croatia, Ghana, Panama |

---

## License & attribution

### Software license

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for the full text.

You are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of this software, provided the copyright notice and permission notice are included in all copies or substantial portions.

### Third-party data

This repository contains code only. Cached and downloaded data are created locally when you run the script and are **not** covered by this license:

| Data | Source | Notes |
|------|--------|-------|
| International match results | [Mart Jürisoo / martj42](https://github.com/martj42/international_results) | Auto-downloaded on first run |
| Squad lists | [FIFA](https://www.fifa.com) | Official PDF |
| Market values | [Transfermarkt](https://www.transfermarkt.com) | Scraped for research; respect their terms |
| Expected lineups | ESPN, Goal.com | Curated manually |

Use scraped and third-party data responsibly and for personal/research purposes only.

### Disclaimer

This project is for research and entertainment. It is **not financial advice**. Prediction markets involve risk of loss. Gamble only what you can afford to lose.
