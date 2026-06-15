"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          FIFA WORLD CUP 2026 — MONTE CARLO SIMULATION FRAMEWORK           ║
║                                                                            ║
║  • Poisson regression match model calibrated on international results      ║
║  • Historical + 12-month form Elo with competition weighting               ║
║  • Starting-XI market values from Transfermarkt player data                  ║
║  • 48-team / 12-group format with 8-best-third-place advancement           ║
║  • Host-nation home-advantage adjustment (USA, Mexico, Canada)             ║
║  • 10,000 Monte Carlo tournament simulations                               ║
║  • Full probability output: group exit, R32, R16, QF, SF, Final, Winner    ║
║  • Fair odds + value-bet analysis vs market_odds.csv                       ║
║  • Self-contained HTML report (wc2026_report.html)                       ║
╚══════════════════════════════════════════════════════════════════════════════╝

Requirements:
    pip install numpy pandas scipy requests tqdm tabulate beautifulsoup4 pdfplumber

Data source (auto-downloaded on first run):
    Mart Jürisoo's "International football results from 1872 to 2024+"
    https://github.com/martj42/international_results
"""

import argparse
import html as html_module
import json
import math
import random
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson
from tqdm import tqdm

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

NUM_SIMULATIONS = 10_000
RANDOM_SEED = 2026

# Elo parameters
ELO_INITIAL = 1500
ELO_K = 40
ELO_HOME_ADV = 100
ELO_START_DATE = "2018-01-01"
ELO_END_DATE = "2026-12-31"

# Match-importance multipliers applied to K-factor
MATCH_WEIGHT_WC_QUAL = 1.50
MATCH_WEIGHT_CONTINENTAL = 1.30   # Euros, Copa América, AFCON, Asian Cup, Gold Cup
MATCH_WEIGHT_CONTINENTAL_QUAL = 1.20
MATCH_WEIGHT_NATIONS_LEAGUE = 1.15
MATCH_WEIGHT_FRIENDLY = 0.70
MATCH_WEIGHT_DEFAULT = 1.00

# Team strength blend (four components, must sum to 1.0)
ELO_HIST_WEIGHT = 0.35       # 2018–2026 weighted Elo
ELO_FORM_WEIGHT = 0.20       # last-12-months form Elo
SQUAD_FULL_WEIGHT = 0.15     # full 26-man squad market value
SQUAD_XI_WEIGHT = 0.30       # expected starting XI market value
FORM_ELO_MONTHS = 12
STARTING_XI_SIZE = 11

# Squad data sources
SQUAD_PDF_URL = (
    "https://fdp.fifa.org/assetspublic/ce281/pdf/SquadLists-English.pdf"
)
SQUAD_PDF_CACHE = Path("squad_lists.pdf")
SQUAD_VALUES_CACHE = Path("squad_market_values.json")
SQUAD_XI_CACHE = Path("squad_xi_values.json")
EXPECTED_LINEUPS_JSON = Path("expected_lineups.json")
TRANSFERMARKT_BASE = "https://www.transfermarkt.com"
TRANSFERMARKT_WC_URL = (
    "https://www.transfermarkt.com/weltmeisterschaft/teilnehmer/"
    "pokalwettbewerb/FIWC/saison_id/2025"
)
MATCH_PREDICTIONS_CSV = Path("wc2026_match_predictions.csv")
VALUE_BETS_CSV = Path("wc2026_value_bets.csv")
MARKET_ODDS_CSV = Path("market_odds.csv")
REPORT_HTML = Path("wc2026_report.html")
BACKTEST_CSV = Path("wc2026_backtest.csv")
BACKTEST_YEARS = (2018, 2022)
BACKTEST_GROUP_MATCHES = 48     # 32-team format group stage
BACKTEST_HIST_LOOKBACK_YEARS = 8
BACKTEST_MIN_CONFIDENCE = 0.55  # for favourite-accuracy / simulated betting
VALUE_EDGE_THRESHOLD = 0.05   # flag bets with ≥5 pp edge vs market
BET_SLIP_CSV = Path("wc2026_bet_slip.csv")
PAPER_TRADE_LOG = Path("paper_trade_log.csv")
# Stricter tiers for real-money recommendations (informed by 2018/22 backtest)
BET_TIER_MIN_PROB = 0.55        # minimum model probability to consider
BET_TIER_STRONG_PROB = 0.58     # BET tier — higher confidence
BET_TIER_MIN_EDGE = 0.06        # LEAN tier — vig-adjusted edge
BET_TIER_STRONG_EDGE = 0.08     # BET tier edge
BET_KELLY_FRACTION = 0.25       # quarter-Kelly stake sizing
BET_MAX_STAKE_PCT = 0.02        # cap 2% of bankroll per wager
BET_LEAN_STAKE_PCT = 0.01       # flat 1% for LEAN tier
DEFAULT_BANKROLL = 100.0        # units for stake display
MATCHDAY_EXPOSURE_CAP = 0.08    # max 8% bankroll across one calendar day
MIN_PROB_FLOOR = 0.01         # avoid infinite odds for near-certain outcomes

# Poisson model parameters
HOST_ADVANTAGE_GOALS = 0.25          # extra expected goals for host nations
TOURNAMENT_DEFENSIVENESS = 0.85      # tournament matches are lower-scoring
AVG_GOALS_PER_TEAM = 1.30           # baseline expected goals per team

# Data
DATA_URL = (
    "https://raw.githubusercontent.com/martj42/international_results/"
    "master/results.csv"
)
DATA_CACHE = Path("international_results.csv")
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_MAX_AGE_HOURS = 4          # auto-refresh match CSV in --live mode if older
LIVE_REPORT_HTML = Path("wc2026_live_report.html")
FOTMOB_API_BASE = "https://www.fotmob.com/api/data"
FIXTURE_SCHEDULE_JSON = Path("wc2026_fixture_schedule.json")
SCHEDULER_STATE_JSON = Path("wc2026_scheduler_state.json")
WC2026_FIRST_DATE = date(2026, 6, 11)
WC2026_LAST_DATE = date(2026, 7, 19)
PREKICKOFF_MINUTES = 60          # run pipeline this many minutes before kickoff
LINEUP_FETCH_MAX_HOURS = 3       # try fetching lineups up to 3h before kickoff

# ─────────────────────────────────────────────────────────────────────────────
# 2. ACTUAL 2026 WORLD CUP DRAW  (Groups A–L, FIFA seeding order)
# ─────────────────────────────────────────────────────────────────────────────

GROUPS: Dict[str, List[str]] = {
    "A": ["Mexico", "South Africa", "South Korea", "Czechia"],
    "B": ["Canada", "Switzerland", "Qatar", "Bosnia Herzegovina"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["United States", "Paraguay", "Australia", "Turkey"],
    "E": ["Germany", "Curacao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Tunisia", "Sweden"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "DR Congo", "Uzbekistan", "Colombia"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}

ALL_TEAMS = [team for grp in GROUPS.values() for team in grp]
HOST_NATIONS = {"United States", "Mexico", "Canada"}

# FIFA Rankings (April 2026 update) — used as Elo prior when no history
FIFA_RANKINGS: Dict[str, int] = {
    "France": 1, "Spain": 2, "Argentina": 3, "England": 4,
    "Portugal": 5, "Brazil": 6, "Netherlands": 7, "Morocco": 8,
    "Belgium": 9, "Germany": 10, "Croatia": 11, "Colombia": 13,
    "Senegal": 14, "Mexico": 15, "United States": 16, "Uruguay": 17,
    "Japan": 18, "Switzerland": 19, "Iran": 21, "Turkey": 22,
    "Ecuador": 23, "Austria": 24, "South Korea": 25, "Australia": 27,
    "Canada": 30, "Norway": 32, "Egypt": 33, "Ivory Coast": 39,
    "Algeria": 36, "Sweden": 37, "Paraguay": 38, "Tunisia": 40,
    "Czechia": 41, "Scotland": 43, "Qatar": 53, "DR Congo": 55,
    "Iraq": 56, "Saudi Arabia": 58, "South Africa": 60,
    "Uzbekistan": 62, "Panama": 53, "Ghana": 65, "Jordan": 68,
    "Cape Verde": 70, "Bosnia Herzegovina": 71, "Haiti": 79,
    "Curacao": 81, "New Zealand": 88,
}

# Map dataset country names → our standardised names
NAME_MAP = {
    "United States": ["United States", "USA"],
    "South Korea": ["South Korea", "Korea Republic"],
    "Ivory Coast": ["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"],
    "Turkey": ["Turkey", "Türkiye", "Turkiye"],
    "Czechia": ["Czechia", "Czech Republic", "Czechoslovakia"],
    "Bosnia Herzegovina": ["Bosnia Herzegovina", "Bosnia and Herzegovina",
                           "Bosnia-Herzegovina"],
    "DR Congo": ["DR Congo", "Congo DR", "Dem. Rep. of the Congo",
                  "Zaire", "Congo"],
    "Cape Verde": ["Cape Verde", "Cabo Verde"],
    "Curacao": ["Curacao", "Curaçao"],
}

# Reverse lookup: dataset name → our canonical name
REVERSE_NAME_MAP: Dict[str, str] = {}
for canonical, aliases in NAME_MAP.items():
    for alias in aliases:
        REVERSE_NAME_MAP[alias] = canonical

# FIFA squad PDF / Transfermarkt → canonical team names
FIFA_SQUAD_NAME_MAP: Dict[str, str] = {
    "Bosnia And Herzegovina": "Bosnia Herzegovina",
    "Cabo Verde": "Cape Verde",
    "Congo DR": "DR Congo",
    "Côte D'Ivoire": "Ivory Coast",
    "Cote D'Ivoire": "Ivory Coast",
    "Curaçao": "Curacao",
    "Czechia": "Czechia",
    "Korea Republic": "South Korea",
    "Türkiye": "Turkey",
    "Turkiye": "Turkey",
    "USA": "United States",
}

TRANSFERMARKT_NAME_MAP: Dict[str, str] = {
    "Bosnia-Herzegovina": "Bosnia Herzegovina",
    "Curaçao": "Curacao",
    "Democratic Republic of the Congo": "DR Congo",
    "Ivory Coast": "Ivory Coast",
    "Turkiye": "Turkey",
}

# Polymarket search / question text aliases (API uses different spellings)
POLYMARKET_SEARCH_NAMES: Dict[str, List[str]] = {
    "South Korea": ["Korea Republic", "South Korea"],
    "United States": ["United States", "USA"],
    "Bosnia Herzegovina": ["Bosnia and Herzegovina", "Bosnia-Herzegovina",
                           "Bosnia Herzegovina"],
    "Ivory Coast": ["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"],
    "Turkey": ["Turkey", "Türkiye", "Turkiye"],
    "DR Congo": ["DR Congo", "Congo DR", "Democratic Republic of the Congo"],
    "Cape Verde": ["Cape Verde", "Cabo Verde"],
    "Curacao": ["Curacao", "Curaçao"],
}

FOTMOB_TO_CANONICAL: Dict[str, str] = {
    "Turkiye": "Turkey",
    "Bosnia and Herzegovina": "Bosnia Herzegovina",
    "Korea Republic": "South Korea",
    "USA": "United States",
    "Curaçao": "Curacao",
    "Curacao": "Curacao",
    "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "Democratic Republic of the Congo": "DR Congo",
    "Cabo Verde": "Cape Verde",
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. ROUND-OF-32 BRACKET STRUCTURE
#    FIFA's predetermined bracket for the 2026 format.
#    Winners of each R32 tie feed into the R16 bracket in fixed positions.
#    Notation: 1A = winner of Group A, 2A = runner-up of Group A,
#              3x = a third-place qualifier (assigned dynamically).
# ─────────────────────────────────────────────────────────────────────────────

# R32 matchups: (team_slot_home, team_slot_away)
# We use a structure that keeps the bracket balanced and mirrors FIFA's
# design principle of separating top seeds into opposite halves.

R32_TEMPLATE = [
    # ── LEFT HALF (leads to SF1) ──
    ("1A", "3_CDL"),   # R32-1
    ("2C", "2D"),      # R32-2
    ("1E", "3_BFG"),   # R32-3
    ("2G", "2H"),      # R32-4
    ("1B", "3_AEI"),   # R32-5
    ("2A", "2F"),      # R32-6
    ("1D", "3_CHJ"),   # R32-7
    ("2E", "2B"),      # R32-8
    # ── RIGHT HALF (leads to SF2) ──
    ("1H", "3_DFK"),   # R32-9
    ("2K", "2L"),      # R32-10
    ("1I", "3_EGL"),   # R32-11
    ("2I", "2J"),      # R32-12
    ("1F", "3_BHK"),   # R32-13
    ("2L", "2G"),      # R32-14  (adjusted – uses 2nd-best available)
    ("1C", "3_AIJ"),   # R32-15
    ("2H", "2K"),      # R32-16  (adjusted)
]

# Simplified: we pair group winners vs 3rd-place / runners-up using FIFA's
# published table.  The exact 3rd-place assignment depends on WHICH groups
# produce the 8 qualifying 3rd-place teams.  We handle this dynamically below.


# ─────────────────────────────────────────────────────────────────────────────
# 4. DATA LOADING & PREPROCESSING
# ─────────────────────────────────────────────────────────────────────────────

def download_data(force: bool = False) -> pd.DataFrame:
    """Download (or load cached) international match results."""
    stale = False
    if DATA_CACHE.exists() and not force:
        age_h = (datetime.now().timestamp() - DATA_CACHE.stat().st_mtime) / 3600
        stale = age_h > DATA_MAX_AGE_HOURS

    if DATA_CACHE.exists() and not force and not stale:
        print(f"[DATA] Loading cached results from {DATA_CACHE}")
        df = pd.read_csv(DATA_CACHE, parse_dates=["date"])
    else:
        if stale:
            print(f"[DATA] Cache older than {DATA_MAX_AGE_HOURS}h — refreshing …")
        else:
            print("[DATA] Downloading from GitHub …")
        try:
            import requests
            r = requests.get(DATA_URL, timeout=30)
            r.raise_for_status()
            DATA_CACHE.write_bytes(r.content)
            df = pd.read_csv(DATA_CACHE, parse_dates=["date"])
            print(f"[DATA] Saved {len(df):,} matches to {DATA_CACHE}")
        except Exception as e:
            if DATA_CACHE.exists():
                print(f"[DATA] Download failed ({e}); using stale cache.")
                df = pd.read_csv(DATA_CACHE, parse_dates=["date"])
            else:
                print(f"[DATA] Download failed ({e}). Generating synthetic Elo "
                      "ratings from FIFA rankings.")
                return pd.DataFrame()
    return df


def canonicalise(name: str) -> str:
    """Map a dataset team name to our canonical name."""
    return REVERSE_NAME_MAP.get(name, name)


def form_elo_start_date(months: int = FORM_ELO_MONTHS) -> str:
    """Return the start date for the form-Elo window."""
    return (pd.Timestamp.today() - pd.DateOffset(months=months)).strftime(
        "%Y-%m-%d")


def model_description() -> str:
    """Short summary of the four-component strength model."""
    return (
        f"{ELO_HIST_WEIGHT:.0%} hist Elo + {ELO_FORM_WEIGHT:.0%} form Elo "
        f"+ {SQUAD_FULL_WEIGHT:.0%} squad + {SQUAD_XI_WEIGHT:.0%} starting XI"
    )


def match_importance(tournament: str) -> float:
    """Return K-factor multiplier based on competition type."""
    if not isinstance(tournament, str) or not tournament.strip():
        return MATCH_WEIGHT_DEFAULT

    t = tournament.lower()

    if "friendly" in t:
        return MATCH_WEIGHT_FRIENDLY
    if "world cup qualification" in t or "world cup qual" in t:
        return MATCH_WEIGHT_WC_QUAL
    if any(k in t for k in (
        "uefa euro qualification", "copa américa qualification",
        "copa america qualification", "african cup of nations qualification",
        "afc asian cup qualification", "gold cup qualification",
        "concacaf nations league qualification",
    )):
        return MATCH_WEIGHT_CONTINENTAL_QUAL
    if any(k in t for k in (
        "uefa euro", "copa américa", "copa america",
        "african cup of nations", "afc asian cup", "gold cup",
        "arab cup", "aff championship",
    )):
        return MATCH_WEIGHT_CONTINENTAL
    if "nations league" in t:
        return MATCH_WEIGHT_NATIONS_LEAGUE

    return MATCH_WEIGHT_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────
# 5. ELO RATING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class EloSystem:
    """Computes Elo ratings from historical international match results."""

    def __init__(self, k: float = ELO_K, home_adv: float = ELO_HOME_ADV,
                 initial: float = ELO_INITIAL):
        self.k = k
        self.home_adv = home_adv
        self.initial = initial
        self.ratings: Dict[str, float] = defaultdict(lambda: self.initial)
        self.match_count: Dict[str, int] = defaultdict(int)

    def expected(self, ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def result_score(self, goals_a: int, goals_b: int) -> Tuple[float, float]:
        if goals_a > goals_b:
            return 1.0, 0.0
        elif goals_a < goals_b:
            return 0.0, 1.0
        return 0.5, 0.5

    def update(self, team_a: str, team_b: str, goals_a: int, goals_b: int,
               neutral: bool = False, k_multiplier: float = 1.0):
        ra = self.ratings[team_a] + (0 if neutral else self.home_adv)
        rb = self.ratings[team_b]

        ea = self.expected(ra, rb)
        sa, sb = self.result_score(goals_a, goals_b)

        # Goal-difference multiplier (FIFA-style)
        gd = abs(goals_a - goals_b)
        if gd <= 1:
            g = 1.0
        elif gd == 2:
            g = 1.5
        else:
            g = (11.0 + gd) / 8.0

        effective_k = self.k * k_multiplier
        self.ratings[team_a] += effective_k * g * (sa - ea)
        self.ratings[team_b] += effective_k * g * (sb - (1 - ea))
        self.match_count[team_a] += 1
        self.match_count[team_b] += 1

    def compute_from_dataframe(self, df: pd.DataFrame,
                               start_date: str = ELO_START_DATE,
                               end_date: str = ELO_END_DATE):
        """Build Elo ratings from matches between start_date and end_date."""

        # Drop unplayed fixtures (e.g. scheduled 2026 WC matches with no score yet)
        df = (df.dropna(subset=["home_score", "away_score"])
                .sort_values("date")
                .copy())

        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        df = df[(df["date"] >= start) & (df["date"] <= end)]

        for _, row in df.iterrows():
            home = canonicalise(row["home_team"])
            away = canonicalise(row["away_team"])
            neutral = bool(row.get("neutral", False))
            weight = match_importance(row.get("tournament", ""))
            self.update(home, away, int(row["home_score"]),
                        int(row["away_score"]), neutral,
                        k_multiplier=weight)

    def get_rating(self, team: str) -> float:
        return self.ratings[team]

    def fill_missing_from_rankings(self, teams: List[str],
                                   rankings: Dict[str, int]):
        """For teams without enough history, estimate Elo from FIFA ranking."""
        for team in teams:
            if self.match_count.get(team, 0) < 10:
                rank = rankings.get(team, 100)
                # Rough mapping: rank 1 → ~2050, rank 100 → ~1300
                estimated_elo = 2100 - (rank * 8)
                self.ratings[team] = max(estimated_elo, 1200)


# ─────────────────────────────────────────────────────────────────────────────
# 5b. SQUAD STRENGTH (FIFA squads + Transfermarkt market values)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerValue:
    name: str
    position: str
    market_value: float


class SquadStrengthSystem:
    """Evaluates team strength from confirmed World Cup squads."""

    def __init__(self):
        self.market_values: Dict[str, float] = {}
        self.xi_market_values: Dict[str, float] = {}
        self.squad_sizes: Dict[str, int] = {}
        self.players: Dict[str, List[PlayerValue]] = {}
        self.lineup_sources: Dict[str, str] = {}
        self.ratings: Dict[str, float] = {}
        self.xi_ratings: Dict[str, float] = {}

    @staticmethod
    def _parse_market_value(text: str) -> float:
        s = text.replace("\xa0", "").replace("€", "").strip().lower()
        num = float(re.sub(r"[^0-9.]", "", s) or 0)
        if "bn" in s:
            return num * 1e9
        if "m" in s:
            return num * 1e6
        if "k" in s:
            return num * 1e3
        return num

    def _canonical_squad_name(self, name: str) -> str:
        return FIFA_SQUAD_NAME_MAP.get(name, REVERSE_NAME_MAP.get(name, name))

    def load_fifa_squads(self) -> Dict[str, int]:
        """Parse official FIFA squad PDF and return player counts per team."""
        if not SQUAD_PDF_CACHE.exists():
            print(f"[SQUAD] Downloading FIFA squad lists …")
            try:
                import requests
                r = requests.get(SQUAD_PDF_URL, timeout=60)
                r.raise_for_status()
                SQUAD_PDF_CACHE.write_bytes(r.content)
            except Exception as e:
                print(f"[SQUAD] FIFA PDF download failed ({e}).")
                return {}

        try:
            import pdfplumber
        except ImportError:
            print("[SQUAD] pdfplumber not installed — skipping squad PDF parse.")
            return {}

        squads: Dict[str, int] = {}
        current_team: Optional[str] = None

        with pdfplumber.open(SQUAD_PDF_CACHE) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    header = re.match(r"^(.+?)\s*\(([A-Z]{3})\)\s*$", line.strip())
                    if header and not re.match(r"^\d+\s+(GK|DF|MF|FW)", line):
                        raw_name = header.group(1).strip()
                        current_team = self._canonical_squad_name(raw_name)
                        squads.setdefault(current_team, 0)
                        continue

                    if current_team and re.match(r"^\d+\s+(GK|DF|MF|FW)\s+", line):
                        squads[current_team] += 1

        self.squad_sizes = squads
        return squads

    def fetch_market_values(self, teams: List[str]) -> Dict[str, float]:
        """Fetch squad market values from Transfermarkt (cached locally)."""
        if SQUAD_VALUES_CACHE.exists():
            cached = json.loads(SQUAD_VALUES_CACHE.read_text(encoding="utf-8"))
            values = {k: float(v) for k, v in cached.items()}
            self.market_values = {t: values.get(t, 0.0) for t in teams}
            return self.market_values

        print("[SQUAD] Fetching Transfermarkt squad market values …")
        try:
            import requests
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            }
            r = requests.get(TRANSFERMARKT_WC_URL, headers=headers, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            scraped: Dict[str, float] = {}
            for tr in soup.select("table.items tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 8:
                    continue
                team_link = tds[1].find("a")
                if not team_link:
                    continue
                raw_name = team_link.get("title") or team_link.get_text(strip=True)
                canon = TRANSFERMARKT_NAME_MAP.get(raw_name, raw_name)
                canon = REVERSE_NAME_MAP.get(canon, canon)
                scraped[canon] = self._parse_market_value(
                    tds[6].get_text(strip=True))

            SQUAD_VALUES_CACHE.write_text(
                json.dumps(scraped, indent=2), encoding="utf-8")
            self.market_values = {t: scraped.get(t, 0.0) for t in teams}
            return self.market_values

        except Exception as e:
            print(f"[SQUAD] Transfermarkt fetch failed ({e}).")
            return {}

    @staticmethod
    def _values_to_ratings(values: Dict[str, float],
                           teams: List[str]) -> Dict[str, float]:
        """Map market values to pseudo-Elo on a log scale."""
        positive = [values.get(t, 0.0) for t in teams if values.get(t, 0.0) > 0]
        if not positive:
            return {t: max(2100 - FIFA_RANKINGS.get(t, 100) * 8, 1200)
                    for t in teams}

        log_vals = [math.log10(max(v, 1e6)) for v in positive]
        min_log, max_log = min(log_vals), max(log_vals)
        span = max(max_log - min_log, 0.01)
        ratings: Dict[str, float] = {}

        for team in teams:
            mv = values.get(team, 0.0)
            if mv <= 0:
                ratings[team] = max(2100 - FIFA_RANKINGS.get(team, 100) * 8, 1200)
                continue
            log_mv = math.log10(max(mv, 1e6))
            norm = (log_mv - min_log) / span
            ratings[team] = 1250 + norm * 850

        return ratings

    def compute_ratings(self, teams: List[str]) -> Dict[str, float]:
        """Convert full-squad market values into pseudo-Elo ratings."""
        self.ratings = self._values_to_ratings(self.market_values, teams)
        return self.ratings

    def compute_xi_ratings(self, teams: List[str]) -> Dict[str, float]:
        """Convert starting-XI market values into pseudo-Elo ratings."""
        self.xi_ratings = self._values_to_ratings(self.xi_market_values, teams)
        return self.xi_ratings

    @staticmethod
    def _parse_player_position(cell_text: str) -> str:
        if "Goalkeeper" in cell_text:
            return "GK"
        if any(k in cell_text for k in (
            "Back", "Sweeper", "Defender",
        )):
            return "DF"
        if any(k in cell_text for k in (
            "Midfield", "midfield",
        )):
            return "MF"
        return "FW"

    @staticmethod
    def _parse_player_mv(text: str) -> float:
        return SquadStrengthSystem._parse_market_value(text)

    def _canonical_tm_team(self, name: str) -> str:
        canon = TRANSFERMARKT_NAME_MAP.get(name, name)
        return REVERSE_NAME_MAP.get(canon, canon)

    def _fetch_team_kader_urls(self) -> Dict[str, str]:
        """Map canonical team names to Transfermarkt full-squad URLs."""
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            )
        }
        r = requests.get(TRANSFERMARKT_WC_URL, headers=headers, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        urls: Dict[str, str] = {}
        for tr in soup.select("table.items tbody tr"):
            link = tr.find("a", href=re.compile(r"/startseite/verein/"))
            if not link:
                continue
            raw_name = link.get("title") or link.get_text(strip=True)
            canon = self._canonical_tm_team(raw_name)
            href = link["href"]
            parts = href.strip("/").split("/")
            if len(parts) < 4:
                continue
            slug, _, verein_id = parts[0], parts[1], parts[-1]
            urls[canon] = (
                f"{TRANSFERMARKT_BASE}/{slug}/kader/verein/"
                f"{verein_id}/plus/1"
            )
        return urls

    def _parse_kader_page(self, html: str) -> List[PlayerValue]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        players: List[PlayerValue] = []

        for tr in soup.select("table.items tbody tr"):
            tds = tr.find_all("td")
            # Full squad rows have ~13 cells; skip detail sub-rows
            if len(tds) < 10:
                continue
            name_cell = tds[1]
            name_link = name_cell.find("a")
            if not name_link:
                continue
            name = name_link.get_text(strip=True)
            cell_text = name_cell.get_text(" ", strip=True)
            position = self._parse_player_position(cell_text)

            market_value = 0.0
            for td in reversed(tds):
                text = td.get_text(strip=True).replace("\xa0", "")
                if "€" in text:
                    market_value = self._parse_player_mv(text)
                    break

            if market_value > 0:
                players.append(PlayerValue(name, position, market_value))

        return players

    @staticmethod
    def _normalize_player_name(name: str) -> str:
        import unicodedata
        name = unicodedata.normalize("NFKD", name)
        name = name.encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

    def _match_lineup_player(self, lineup_name: str,
                             players: List[PlayerValue]
                             ) -> Optional[PlayerValue]:
        """Fuzzy-match an expected lineup name to a squad player."""
        target = self._normalize_player_name(lineup_name)
        if not target:
            return None

        best: Optional[PlayerValue] = None
        best_score = 0.0

        for player in players:
            pname = self._normalize_player_name(player.name)
            if target == pname:
                return player

            score = 0.0
            if target in pname or pname in target:
                score = 0.85
            else:
                t_parts = target.split()
                p_parts = pname.split()
                if t_parts and p_parts and t_parts[-1] == p_parts[-1]:
                    score = 0.75
                elif t_parts and p_parts and t_parts[0] == p_parts[0]:
                    score = 0.65

            if score > best_score:
                best_score = score
                best = player

        return best if best_score >= 0.65 else None

    def _compute_lineup_value(self, lineup_names: List[str],
                              players: List[PlayerValue]) -> float:
        """Sum market values for matched expected starters."""
        total = 0.0
        matched = 0
        for name in lineup_names:
            player = self._match_lineup_player(name, players)
            if player:
                total += player.market_value
                matched += 1

        if matched < min(8, len(lineup_names)):
            return self._select_starting_xi(players)
        return total

    @staticmethod
    def _select_starting_xi(players: List[PlayerValue]) -> float:
        """Estimate starting XI value: best GK + top 10 outfield players."""
        if not players:
            return 0.0

        gks = [p for p in players if p.position == "GK"]
        outfield = [p for p in players if p.position != "GK"]

        xi: List[PlayerValue] = []
        if gks:
            xi.append(max(gks, key=lambda p: p.market_value))
        outfield.sort(key=lambda p: p.market_value, reverse=True)
        xi.extend(outfield[:STARTING_XI_SIZE - len(xi)])

        return sum(p.market_value for p in xi)

    def apply_expected_lineups(self, teams: List[str]) -> int:
        """Apply ESPN / Goal.com expected lineups to XI market values."""
        if not EXPECTED_LINEUPS_JSON.exists():
            return 0

        data = json.loads(
            EXPECTED_LINEUPS_JSON.read_text(encoding="utf-8"))
        applied = 0

        for team in teams:
            entry = data.get(team)
            if not entry:
                continue
            starters = entry.get("starters", [])
            players = self.players.get(team, [])
            if not starters or not players:
                continue

            self.xi_market_values[team] = self._compute_lineup_value(
                starters, players)
            self.lineup_sources[team] = entry.get("source", "expected lineup")
            applied += 1

        return applied

    def fetch_player_values(self, teams: List[str]) -> Dict[str, float]:
        """Scrape per-player values and derive starting-XI market values."""
        if SQUAD_XI_CACHE.exists():
            cached = json.loads(SQUAD_XI_CACHE.read_text(encoding="utf-8"))
            self.players = {
                team: [PlayerValue(**p) for p in plist]
                for team, plist in cached.get("players", {}).items()
            }
            self.xi_market_values = {
                t: float(v) for t, v in cached.get("xi_values", {}).items()
            }
            return self.xi_market_values

        print("[SQUAD] Fetching per-player market values for starting XI …")
        try:
            import requests
            import time

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            }
            kader_urls = self._fetch_team_kader_urls()
            fetched = 0

            for team in teams:
                url = kader_urls.get(team)
                if not url:
                    continue
                r = requests.get(url, headers=headers, timeout=30)
                r.raise_for_status()
                players = self._parse_kader_page(r.text)
                if players:
                    self.players[team] = players
                    self.xi_market_values[team] = self._select_starting_xi(
                        players)
                    fetched += 1
                time.sleep(0.25)

            cache_payload = {
                "players": {
                    team: [p.__dict__ for p in plist]
                    for team, plist in self.players.items()
                },
                "xi_values": self.xi_market_values,
            }
            SQUAD_XI_CACHE.write_text(
                json.dumps(cache_payload, indent=2), encoding="utf-8")
            print(f"[SQUAD] Built starting-XI values for {fetched} teams.")
            return self.xi_market_values

        except Exception as e:
            print(f"[SQUAD] Player-value fetch failed ({e}). "
                  "Falling back to squad totals.")
            for team in teams:
                full = self.market_values.get(team, 0.0)
                # Rough proxy: starters ≈ 55% of total squad value
                self.xi_market_values[team] = full * 0.55
            return self.xi_market_values

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, ELO_INITIAL)

    def get_xi_rating(self, team: str) -> float:
        return self.xi_ratings.get(team, ELO_INITIAL)

    def build(self, teams: List[str]) -> "SquadStrengthSystem":
        squads = self.load_fifa_squads()
        if squads:
            total_players = sum(squads.get(t, 0) for t in teams)
            print(f"[SQUAD] Parsed {total_players} players across "
                  f"{len(squads)} national teams from FIFA PDF.")
        self.fetch_market_values(teams)
        self.fetch_player_values(teams)
        applied = self.apply_expected_lineups(teams)
        if applied:
            print(f"[SQUAD] Applied expected lineups for {applied} teams "
                  f"(ESPN + Goal.com).")
        self.compute_ratings(teams)
        self.compute_xi_ratings(teams)
        return self


class TeamStrengthModel:
    """Blends historical Elo, form Elo, and squad (full + starting XI)."""

    def __init__(self, hist_elo: EloSystem, form_elo: EloSystem,
                 squad: SquadStrengthSystem,
                 hist_weight: float = ELO_HIST_WEIGHT,
                 form_weight: float = ELO_FORM_WEIGHT,
                 squad_weight: float = SQUAD_FULL_WEIGHT,
                 xi_weight: float = SQUAD_XI_WEIGHT):
        self.hist_elo = hist_elo
        self.form_elo = form_elo
        self.squad = squad
        total = hist_weight + form_weight + squad_weight + xi_weight
        self.hist_weight = hist_weight / total
        self.form_weight = form_weight / total
        self.squad_weight = squad_weight / total
        self.xi_weight = xi_weight / total

    def get_rating(self, team: str) -> float:
        return (
            self.hist_weight * self.hist_elo.get_rating(team)
            + self.form_weight * self.form_elo.get_rating(team)
            + self.squad_weight * self.squad.get_rating(team)
            + self.xi_weight * self.squad.get_xi_rating(team)
        )

    def get_elo(self, team: str) -> float:
        """Blended historical + form Elo (for display)."""
        elo_total = self.hist_weight + self.form_weight
        if elo_total <= 0:
            return ELO_INITIAL
        return (
            (self.hist_weight / elo_total) * self.hist_elo.get_rating(team)
            + (self.form_weight / elo_total) * self.form_elo.get_rating(team)
        )

    def get_hist_elo(self, team: str) -> float:
        return self.hist_elo.get_rating(team)

    def get_form_elo(self, team: str) -> float:
        return self.form_elo.get_rating(team)

    def get_squad_elo(self, team: str) -> float:
        """Blended full-squad + starting-XI rating (for display)."""
        squad_total = self.squad_weight + self.xi_weight
        if squad_total <= 0:
            return ELO_INITIAL
        return (
            (self.squad_weight / squad_total) * self.squad.get_rating(team)
            + (self.xi_weight / squad_total) * self.squad.get_xi_rating(team)
        )

    def get_xi_elo(self, team: str) -> float:
        return self.squad.get_xi_rating(team)


# ─────────────────────────────────────────────────────────────────────────────
# 6. POISSON MATCH ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class PoissonMatchEngine:
    """Simulates matches using a Poisson model driven by blended team strength."""

    def __init__(self, strength: Union[EloSystem, TeamStrengthModel],
                 avg_goals: float = AVG_GOALS_PER_TEAM,
                 host_boost: float = HOST_ADVANTAGE_GOALS,
                 tournament_factor: float = TOURNAMENT_DEFENSIVENESS):
        self.strength = strength
        self.avg_goals = avg_goals
        self.host_boost = host_boost
        self.tournament_factor = tournament_factor

    def _team_rating(self, team: str) -> float:
        if hasattr(self.strength, "get_rating"):
            return self.strength.get_rating(team)
        return self.strength.get_rating(team)

    def expected_goals(self, team_a: str, team_b: str,
                       neutral: bool = False
                       ) -> Tuple[float, float]:
        """Calculate expected goals for each team using strength difference."""
        ra = self._team_rating(team_a)
        rb = self._team_rating(team_b)
        diff = (ra - rb) / 400.0

        lambda_a = self.avg_goals * (10 ** (diff / 2.0))
        lambda_b = self.avg_goals * (10 ** (-diff / 2.0))

        lambda_a *= self.tournament_factor
        lambda_b *= self.tournament_factor

        if not neutral:
            if team_a in HOST_NATIONS:
                lambda_a += self.host_boost
            if team_b in HOST_NATIONS:
                lambda_b += self.host_boost

        lambda_a = np.clip(lambda_a, 0.20, 4.5)
        lambda_b = np.clip(lambda_b, 0.20, 4.5)

        return float(lambda_a), float(lambda_b)

    def simulate_match(self, team_a: str, team_b: str,
                       allow_draw: bool = True, neutral: bool = False
                       ) -> Tuple[int, int, Optional[str]]:
        """Simulate a single match.  Returns (goals_a, goals_b, winner).
        If allow_draw is False, uses extra-time / penalties logic."""

        la, lb = self.expected_goals(team_a, team_b, neutral=neutral)
        goals_a = np.random.poisson(la)
        goals_b = np.random.poisson(lb)

        if goals_a != goals_b or allow_draw:
            winner = team_a if goals_a > goals_b else (
                team_b if goals_b > goals_a else None)
            return goals_a, goals_b, winner

        # Extra time: 30 min ≈ 1/3 of normal time, slightly lower intensity
        et_la = la * 0.28
        et_lb = lb * 0.28
        et_a = np.random.poisson(et_la)
        et_b = np.random.poisson(et_lb)
        goals_a += et_a
        goals_b += et_b

        if goals_a != goals_b:
            winner = team_a if goals_a > goals_b else team_b
            return goals_a, goals_b, winner

        # Penalty shootout: ~50/50 with slight favourite bias
        p_a = 0.5 + 0.03 * np.clip((la - lb), -1, 1)
        winner = team_a if random.random() < p_a else team_b
        return goals_a, goals_b, winner

    def match_win_probabilities(self, team_a: str, team_b: str,
                                neutral: bool = False
                                ) -> Dict[str, float]:
        """Analytical win/draw/loss probabilities (for diagnostics)."""
        la, lb = self.expected_goals(team_a, team_b, neutral=neutral)
        max_goals = 10
        p_win_a = 0.0
        p_draw = 0.0
        p_win_b = 0.0
        for i in range(max_goals):
            for j in range(max_goals):
                p = poisson.pmf(i, la) * poisson.pmf(j, lb)
                if i > j:
                    p_win_a += p
                elif i == j:
                    p_draw += p
                else:
                    p_win_b += p
        return {"win_a": p_win_a, "draw": p_draw, "win_b": p_win_b}


# ─────────────────────────────────────────────────────────────────────────────
# 7. GROUP STAGE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GroupResult:
    team: str
    points: int = 0
    gf: int = 0        # goals for
    ga: int = 0        # goals against
    gd: int = 0        # goal difference
    wins: int = 0
    draws: int = 0
    losses: int = 0
    group: str = ""
    position: int = 0  # 1st, 2nd, 3rd, 4th


def simulate_group(group_name: str, teams: List[str],
                   engine: PoissonMatchEngine) -> List[GroupResult]:
    """Simulate a round-robin group stage (each pair plays once)."""
    results = {t: GroupResult(team=t, group=group_name) for t in teams}

    # Generate all pairings
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            ta, tb = teams[i], teams[j]
            ga, gb, _ = engine.simulate_match(ta, tb, allow_draw=True)

            results[ta].gf += ga
            results[ta].ga += gb
            results[tb].gf += gb
            results[tb].ga += ga

            if ga > gb:
                results[ta].points += 3
                results[ta].wins += 1
                results[tb].losses += 1
            elif gb > ga:
                results[tb].points += 3
                results[tb].wins += 1
                results[ta].losses += 1
            else:
                results[ta].points += 1
                results[ta].draws += 1
                results[tb].points += 1
                results[tb].draws += 1

    # Compute GD
    for r in results.values():
        r.gd = r.gf - r.ga

    # Sort by: points → GD → GF → random tiebreak
    standings = sorted(
        results.values(),
        key=lambda r: (r.points, r.gd, r.gf, random.random()),
        reverse=True
    )

    for pos, r in enumerate(standings, 1):
        r.position = pos

    return standings


def select_best_third_places(all_thirds: List[GroupResult], n: int = 8
                              ) -> List[GroupResult]:
    """Select the 8 best third-place teams across all 12 groups."""
    sorted_thirds = sorted(
        all_thirds,
        key=lambda r: (r.points, r.gd, r.gf, random.random()),
        reverse=True
    )
    return sorted_thirds[:n]


# ─────────────────────────────────────────────────────────────────────────────
# 8. KNOCKOUT BRACKET SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

def build_r32_matchups(group_standings: Dict[str, List[GroupResult]],
                       qualified_thirds: List[GroupResult]
                       ) -> List[Tuple[str, str]]:
    """Build Round-of-32 matchups based on FIFA's bracket template.

    The bracket is designed so that:
    - Group winners play 3rd-place or runners-up from other groups
    - Spain/Argentina are in opposite halves; France/England likewise
    - The bracket is fixed once group positions are known

    We use a simplified but faithful mapping.
    """
    # Collect winners and runners-up
    winners = {}   # e.g. {"A": "Mexico", ...}
    runners = {}
    for grp, standings in group_standings.items():
        winners[grp] = standings[0].team
        runners[grp] = standings[1].team

    # Map third-place teams by their source group
    thirds_by_group = {r.group: r.team for r in qualified_thirds}
    third_teams = list(thirds_by_group.values())

    # Build R32 bracket (16 matches → 16 winners go to R16)
    # Template based on FIFA's published bracket structure:
    matchups = [
        # ── LEFT HALF ──
        (winners["A"],  runners["C"]),    # M1
        (winners["D"],  runners["B"]),    # M2
        (winners["E"],  runners["G"]),    # M3
        (winners["H"],  runners["F"]),    # M4
        (winners["B"],  runners["A"]),    # M5
        (winners["C"],  runners["D"]),    # M6
        (winners["F"],  runners["E"]),    # M7
        (winners["G"],  runners["H"]),    # M8
        # ── RIGHT HALF ──
        (winners["I"],  runners["K"]),    # M9
        (winners["L"],  runners["J"]),    # M10
        (winners["J"],  runners["L"]),    # M11
        (winners["K"],  runners["I"]),    # M12
    ]

    # Assign the 8 third-place teams to the remaining 4 slots
    # (group winners who haven't been assigned an opponent yet get thirds)
    # We pair thirds with the strongest available group winners as underdogs
    random.shuffle(third_teams)
    third_matchups = []
    for i, t3 in enumerate(third_teams[:4]):
        # Third-place teams face runners-up from the opposite bracket half
        partner_runners = [runners[g] for g in "ABCDEFGHIJKL"
                           if runners[g] not in [m[1] for m in matchups]
                           and runners[g] not in [m[1] for m in third_matchups]]
        if partner_runners:
            third_matchups.append((t3, partner_runners[0]))

    # Remaining thirds face each other or available runners
    remaining_thirds = third_teams[4:]
    for i in range(0, len(remaining_thirds), 2):
        if i + 1 < len(remaining_thirds):
            third_matchups.append((remaining_thirds[i], remaining_thirds[i+1]))

    matchups.extend(third_matchups)

    # Ensure we have exactly 16 matchups → pad or trim
    # In rare edge cases, fill any gaps
    all_qualified = set()
    for w in winners.values():
        all_qualified.add(w)
    for r_val in runners.values():
        all_qualified.add(r_val)
    for t in third_teams:
        all_qualified.add(t)

    used = set()
    for a, b in matchups:
        used.add(a)
        used.add(b)

    unused = list(all_qualified - used)
    random.shuffle(unused)

    # Fill to 16 matchups
    while len(matchups) < 16 and len(unused) >= 2:
        matchups.append((unused.pop(), unused.pop()))

    return matchups[:16]


def simulate_knockout_round(matchups: List[Tuple[str, str]],
                            engine: PoissonMatchEngine
                            ) -> List[str]:
    """Simulate a knockout round.  Returns list of winners."""
    winners = []
    for team_a, team_b in matchups:
        _, _, winner = engine.simulate_match(team_a, team_b, allow_draw=False)
        winners.append(winner)
    return winners


def make_next_round_matchups(winners: List[str]) -> List[Tuple[str, str]]:
    """Pair winners sequentially for the next knockout round."""
    matchups = []
    for i in range(0, len(winners) - 1, 2):
        matchups.append((winners[i], winners[i + 1]))
    return matchups


# ─────────────────────────────────────────────────────────────────────────────
# 9. FULL TOURNAMENT SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TeamTournamentRecord:
    group_exit: int = 0
    r32: int = 0           # reached R32 (= advanced from group)
    r16: int = 0
    qf: int = 0
    sf: int = 0
    final: int = 0
    winner: int = 0


def simulate_tournament(engine: PoissonMatchEngine
                        ) -> Dict[str, str]:
    """Simulate one full tournament.  Returns dict of team → deepest round."""
    progress: Dict[str, str] = {t: "Group Stage" for t in ALL_TEAMS}

    # ── GROUP STAGE ──
    group_standings: Dict[str, List[GroupResult]] = {}
    all_thirds: List[GroupResult] = []

    for grp_name, teams in GROUPS.items():
        standings = simulate_group(grp_name, teams, engine)
        group_standings[grp_name] = standings
        # Third-place team
        all_thirds.append(standings[2])

    # Determine qualifiers
    qualified_thirds = select_best_third_places(all_thirds, n=8)
    qualified_third_teams = {r.team for r in qualified_thirds}

    for grp_name, standings in group_standings.items():
        for s in standings:
            if s.position <= 2:
                progress[s.team] = "R32"
            elif s.team in qualified_third_teams:
                progress[s.team] = "R32"
            else:
                progress[s.team] = "Group Stage"

    # ── ROUND OF 32 ──
    r32_matchups = build_r32_matchups(group_standings, qualified_thirds)
    r32_winners = simulate_knockout_round(r32_matchups, engine)
    for w in r32_winners:
        progress[w] = "R16"

    # ── ROUND OF 16 ──
    r16_matchups = make_next_round_matchups(r32_winners)
    r16_winners = simulate_knockout_round(r16_matchups, engine)
    for w in r16_winners:
        progress[w] = "QF"

    # ── QUARTER-FINALS ──
    qf_matchups = make_next_round_matchups(r16_winners)
    qf_winners = simulate_knockout_round(qf_matchups, engine)
    for w in qf_winners:
        progress[w] = "SF"

    # ── SEMI-FINALS ──
    sf_matchups = make_next_round_matchups(qf_winners)
    sf_winners = simulate_knockout_round(sf_matchups, engine)
    for w in sf_winners:
        progress[w] = "Final"

    # ── FINAL ──
    if len(sf_winners) >= 2:
        _, _, champion = engine.simulate_match(
            sf_winners[0], sf_winners[1], allow_draw=False)
        progress[champion] = "Winner"

    return progress


# ─────────────────────────────────────────────────────────────────────────────
# 10. MONTE CARLO ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def run_monte_carlo(engine: PoissonMatchEngine, n_sims: int = NUM_SIMULATIONS
                    ) -> Dict[str, TeamTournamentRecord]:
    """Run N tournament simulations and aggregate results."""
    records: Dict[str, TeamTournamentRecord] = {
        t: TeamTournamentRecord() for t in ALL_TEAMS
    }

    stage_order = {
        "Group Stage": 0,
        "R32": 1,
        "R16": 2,
        "QF": 3,
        "SF": 4,
        "Final": 5,
        "Winner": 6,
    }

    for _ in tqdm(range(n_sims), desc="Simulating tournaments",
                  unit="sim", ncols=80):
        result = simulate_tournament(engine)

        for team, stage in result.items():
            level = stage_order.get(stage, 0)
            if level >= 1:
                records[team].r32 += 1
            if level >= 2:
                records[team].r16 += 1
            if level >= 3:
                records[team].qf += 1
            if level >= 4:
                records[team].sf += 1
            if level >= 5:
                records[team].final += 1
            if level >= 6:
                records[team].winner += 1
            if level == 0:
                records[team].group_exit += 1

    return records


# ─────────────────────────────────────────────────────────────────────────────
# 11. WORLD CUP MATCH PREDICTIONS (for upcoming fixtures)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FixturePrediction:
    date: str
    home_team: str
    away_team: str
    city: str
    country: str
    neutral: bool
    p_home_win: float
    p_draw: float
    p_away_win: float
    pick: str
    pick_prob: float
    home_xg: float
    away_xg: float


@dataclass
class OutcomeOdds:
    label: str
    model_prob: float
    fair_decimal: float
    fair_american: int
    min_decimal_5pct_edge: float
    market_decimal: Optional[float] = None
    market_implied: Optional[float] = None
    edge: Optional[float] = None
    is_value: bool = False


@dataclass
class MatchValueAnalysis:
    date: str
    home_team: str
    away_team: str
    city: str
    neutral: bool
    home: OutcomeOdds
    draw: OutcomeOdds
    away: OutcomeOdds
    best_value: Optional[OutcomeOdds] = None


@dataclass
class BetRecommendation:
    date: str
    home_team: str
    away_team: str
    city: str
    pick: str
    tier: str                   # BET, LEAN, or SKIP
    model_prob: float
    market_price: float         # Polymarket-style 0–1 price
    market_implied: float       # vig-adjusted implied probability
    edge: float
    ev_pct: float               # expected return as % of stake
    kelly_full: float
    stake_pct: float            # % of bankroll
    stake_units: float
    fair_decimal: float
    warnings: List[str] = field(default_factory=list)


@dataclass
class SimulationSummary:
    champion: str
    champion_pct: float
    finalist: str
    finalist_pct: float
    host_best: str
    host_pct: float
    dark_horse: str
    dark_horse_pct: float
    matches_processed: int
    n_simulations: int


def prob_to_decimal(prob: float, min_prob: float = MIN_PROB_FLOOR) -> float:
    """Convert model probability to fair decimal odds."""
    return round(1.0 / max(prob, min_prob), 2)


def prob_to_american(prob: float, min_prob: float = MIN_PROB_FLOOR) -> int:
    """Convert model probability to fair American odds."""
    p = max(prob, min_prob)
    if p >= 0.5:
        return int(round(-100 * p / (1 - p)))
    return int(round(100 * (1 - p) / p))


def decimal_to_implied(decimal_odds: float) -> float:
    """Implied probability from decimal odds (before vig removal)."""
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


def min_profitable_decimal(model_prob: float,
                           edge: float = VALUE_EDGE_THRESHOLD) -> float:
    """Minimum decimal odds needed for a positive-EV bet at given edge."""
    return prob_to_decimal(max(model_prob - edge, MIN_PROB_FLOOR))


def create_market_odds_template(predictions: List[FixturePrediction],
                                path: Path = MARKET_ODDS_CSV) -> Path:
    """Create a template CSV for pasting bookmaker / prediction-market prices."""
    if path.exists():
        return path

    rows = [{
        "date": p.date,
        "home_team": p.home_team,
        "away_team": p.away_team,
        "market_home_decimal": "",
        "market_draw_decimal": "",
        "market_away_decimal": "",
        "notes": "Paste decimal odds (e.g. 1.85) or Polymarket price (0.65)",
    } for p in predictions]

    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def load_market_odds() -> Optional[pd.DataFrame]:
    """Load optional market odds for value-bet comparison."""
    if not MARKET_ODDS_CSV.exists():
        return None

    df = pd.read_csv(MARKET_ODDS_CSV)
    required = {"date", "home_team", "away_team"}
    if not required.issubset(df.columns):
        return None

    for col in ("market_home_decimal", "market_draw_decimal",
                "market_away_decimal"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _lookup_market_odds(market_df: Optional[pd.DataFrame],
                        pred: FixturePrediction
                        ) -> Tuple[Optional[float], Optional[float],
                                   Optional[float]]:
    if market_df is None:
        return None, None, None

    match = market_df[
        (market_df["date"].astype(str) == pred.date)
        & (market_df["home_team"] == pred.home_team)
        & (market_df["away_team"] == pred.away_team)
    ]
    if match.empty:
        return None, None, None

    row = match.iloc[0]

    def _price(col: str) -> Optional[float]:
        if col not in row or pd.isna(row[col]):
            return None
        val = float(row[col])
        # Polymarket-style 0–1 prices → convert to decimal odds
        if 0 < val < 1:
            return 1.0 / val
        return val

    return (_price("market_home_decimal"),
            _price("market_draw_decimal"),
            _price("market_away_decimal"))


def _vig_free_implied_probs(
        market_decimals: Tuple[Optional[float], Optional[float],
                               Optional[float]],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Remove bookmaker overround when all three outcomes are priced."""
    implied = [
        decimal_to_implied(m) if m and m > 1.0 else None
        for m in market_decimals
    ]
    if all(p is not None for p in implied):
        total = sum(implied)  # type: ignore[arg-type]
        if total > 0:
            return tuple(p / total for p in implied)  # type: ignore[misc]
    return tuple(implied)  # type: ignore[return-value]


def _build_outcome_odds(label: str, model_prob: float,
                        market_decimal: Optional[float],
                        vig_free_implied: Optional[float] = None) -> OutcomeOdds:
    fair_dec = prob_to_decimal(model_prob)
    outcome = OutcomeOdds(
        label=label,
        model_prob=model_prob,
        fair_decimal=fair_dec,
        fair_american=prob_to_american(model_prob),
        min_decimal_5pct_edge=min_profitable_decimal(model_prob),
    )

    implied = vig_free_implied
    if implied is None and market_decimal and market_decimal > 1.0:
        implied = decimal_to_implied(market_decimal)

    if implied is not None and market_decimal and market_decimal > 1.0:
        edge = model_prob - implied
        outcome.market_decimal = market_decimal
        outcome.market_implied = implied
        outcome.edge = edge
        outcome.is_value = edge >= VALUE_EDGE_THRESHOLD

    return outcome


def analyze_match_values(pred: FixturePrediction,
                         market_df: Optional[pd.DataFrame] = None
                         ) -> MatchValueAnalysis:
    """Compute fair odds and optional value edges for one fixture."""
    m_home, m_draw, m_away = _lookup_market_odds(market_df, pred)
    adj_home, adj_draw, adj_away = _vig_free_implied_probs(
        (m_home, m_draw, m_away))

    home = _build_outcome_odds(pred.home_team, pred.p_home_win, m_home,
                               adj_home)
    draw = _build_outcome_odds("Draw", pred.p_draw, m_draw, adj_draw)
    away = _build_outcome_odds(pred.away_team, pred.p_away_win, m_away,
                               adj_away)

    value_outcomes = [o for o in (home, draw, away) if o.is_value]
    best_value = max(value_outcomes, key=lambda o: o.edge or 0) if (
        value_outcomes) else None

    return MatchValueAnalysis(
        date=pred.date,
        home_team=pred.home_team,
        away_team=pred.away_team,
        city=pred.city,
        neutral=pred.neutral,
        home=home,
        draw=draw,
        away=away,
        best_value=best_value,
    )


def analyze_all_value_bets(
        predictions: List[FixturePrediction],
        market_df: Optional[pd.DataFrame] = None) -> List[MatchValueAnalysis]:
    return [analyze_match_values(p, market_df) for p in predictions]


def save_value_bets(analyses: List[MatchValueAnalysis],
                    predictions: List[FixturePrediction],
                    path: Path = VALUE_BETS_CSV) -> Path:
    """Export fair odds and value-bet flags for every match outcome."""
    rows = []
    for pred, analysis in zip(predictions, analyses):
        for outcome in (analysis.home, analysis.draw, analysis.away):
            rows.append({
                "date": pred.date,
                "home_team": pred.home_team,
                "away_team": pred.away_team,
                "outcome": outcome.label,
                "model_prob": round(outcome.model_prob, 4),
                "fair_decimal": outcome.fair_decimal,
                "fair_american": outcome.fair_american,
                "min_decimal_5pct_edge": round(
                    outcome.min_decimal_5pct_edge, 2),
                "market_decimal": outcome.market_decimal or "",
                "market_implied": round(outcome.market_implied, 4)
                if outcome.market_implied else "",
                "edge": round(outcome.edge, 4) if outcome.edge else "",
                "is_value_bet": outcome.is_value,
            })

    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _classify_bet_tier(model_prob: float, edge: float) -> str:
    if (model_prob >= BET_TIER_STRONG_PROB
            and edge >= BET_TIER_STRONG_EDGE):
        return "BET"
    if model_prob >= BET_TIER_MIN_PROB and edge >= BET_TIER_MIN_EDGE:
        return "LEAN"
    return "SKIP"


def _kelly_binary(model_prob: float, market_price: float) -> float:
    """Full Kelly fraction for a Polymarket YES buy at market_price."""
    if market_price <= 0 or market_price >= 1:
        return 0.0
    return max(0.0, (model_prob - market_price) / (1.0 - market_price))


def _bet_warnings(pred: FixturePrediction, pick: str,
                  model_prob: float, edge: float) -> List[str]:
    warnings: List[str] = []
    if pick == "Draw":
        warnings.append("Draw — poorly calibrated in backtest; prefer sides")
    if pred.pick != pick:
        warnings.append(f"Value on {pick} but model pick is {pred.pick}")
    if model_prob < BET_TIER_STRONG_PROB:
        warnings.append(f"Model prob {model_prob:.0%} below {BET_TIER_STRONG_PROB:.0%} BET bar")
    if edge < BET_TIER_STRONG_EDGE:
        warnings.append(f"Edge {edge:.0%} below {BET_TIER_STRONG_EDGE:.0%} BET bar")
    return warnings


def build_bet_recommendations(
        predictions: List[FixturePrediction],
        analyses: List[MatchValueAnalysis],
        bankroll: float = DEFAULT_BANKROLL,
        horizon_days: int = 3,
) -> List[BetRecommendation]:
    """Build actionable BET/LEAN recommendations for upcoming fixtures."""
    today = date.today()
    horizon = today + timedelta(days=horizon_days)
    recs: List[BetRecommendation] = []

    for pred, analysis in zip(predictions, analyses):
        match_day = date.fromisoformat(pred.date)
        if match_day < today or match_day > horizon:
            continue
        if not analysis.best_value:
            continue

        bet = analysis.best_value
        if bet.edge is None or bet.market_implied is None:
            continue
        if not bet.market_decimal:
            continue

        tier = _classify_bet_tier(bet.model_prob, bet.edge)
        if tier == "SKIP":
            continue

        if bet.market_decimal >= 1.0:
            market_price = 1.0 / bet.market_decimal
        else:
            market_price = bet.market_decimal

        kelly_full = _kelly_binary(bet.model_prob, market_price)
        if tier == "BET":
            stake_pct = min(kelly_full * BET_KELLY_FRACTION, BET_MAX_STAKE_PCT)
        else:
            stake_pct = min(BET_LEAN_STAKE_PCT,
                            kelly_full * BET_KELLY_FRACTION,
                            BET_MAX_STAKE_PCT)
        stake_pct = max(stake_pct, 0.005) if stake_pct > 0 else 0.0
        ev_pct = ((bet.model_prob - market_price) / market_price * 100
                  if market_price > 0 else 0.0)

        recs.append(BetRecommendation(
            date=pred.date,
            home_team=pred.home_team,
            away_team=pred.away_team,
            city=pred.city,
            pick=bet.label,
            tier=tier,
            model_prob=bet.model_prob,
            market_price=market_price,
            market_implied=bet.market_implied,
            edge=bet.edge,
            ev_pct=ev_pct,
            kelly_full=kelly_full,
            stake_pct=stake_pct,
            stake_units=round(stake_pct * bankroll, 2),
            fair_decimal=bet.fair_decimal,
            warnings=_bet_warnings(pred, bet.label, bet.model_prob, bet.edge),
        ))

    tier_order = {"BET": 0, "LEAN": 1}
    recs.sort(key=lambda r: (r.date, tier_order.get(r.tier, 9), -r.edge))
    return recs


def _cap_matchday_stakes(recs: List[BetRecommendation],
                         bankroll: float = DEFAULT_BANKROLL
                         ) -> List[BetRecommendation]:
    """Scale down stakes if a single day exceeds MATCHDAY_EXPOSURE_CAP."""
    by_date: Dict[str, List[BetRecommendation]] = defaultdict(list)
    for r in recs:
        by_date[r.date].append(r)

    capped: List[BetRecommendation] = []
    for day, day_recs in by_date.items():
        total_pct = sum(r.stake_pct for r in day_recs)
        if total_pct > MATCHDAY_EXPOSURE_CAP and total_pct > 0:
            scale = MATCHDAY_EXPOSURE_CAP / total_pct
            for r in day_recs:
                r.stake_pct = round(r.stake_pct * scale, 4)
                r.stake_units = round(r.stake_pct * bankroll, 2)
                r.warnings = list(r.warnings) + [
                    f"Stakes scaled ×{scale:.2f} for {day} exposure cap"]
        capped.extend(day_recs)
    capped.sort(key=lambda r: (r.date, r.tier != "BET", -r.edge))
    return capped


def save_bet_slip(recs: List[BetRecommendation],
                  path: Path = BET_SLIP_CSV) -> Path:
    rows = [{
        "date": r.date,
        "match": f"{r.home_team} vs {r.away_team}",
        "city": r.city,
        "pick": r.pick,
        "tier": r.tier,
        "model_prob": round(r.model_prob, 4),
        "market_price": round(r.market_price, 4),
        "market_implied_vigfree": round(r.market_implied, 4),
        "edge": round(r.edge, 4),
        "ev_pct_of_stake": round(r.ev_pct, 2),
        "kelly_full": round(r.kelly_full, 4),
        "stake_pct_bankroll": round(r.stake_pct * 100, 2),
        "stake_units": r.stake_units,
        "fair_decimal": r.fair_decimal,
        "warnings": "; ".join(r.warnings),
    } for r in recs]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def create_paper_trade_template(path: Path = PAPER_TRADE_LOG) -> Path:
    if path.exists():
        return path
    pd.DataFrame(columns=[
        "date", "match", "pick", "tier", "market_price", "stake_units",
        "model_prob", "edge", "result", "pnl_units", "notes",
    ]).to_csv(path, index=False)
    return path


def print_bet_slip(recs: List[BetRecommendation],
                   bankroll: float = DEFAULT_BANKROLL):
    """Print actionable bet recommendations for the next few matchdays."""
    print("\n" + "=" * 78)
    print("   BET SLIP — Actionable Recommendations (next 3 days)")
    print("=" * 78)
    print(f"   Tiers: BET = prob≥{BET_TIER_STRONG_PROB:.0%} & edge≥"
          f"{BET_TIER_STRONG_EDGE:.0%}  |  LEAN = prob≥{BET_TIER_MIN_PROB:.0%} "
          f"& edge≥{BET_TIER_MIN_EDGE:.0%}")
    print(f"   Stakes: quarter-Kelly (BET) or {BET_LEAN_STAKE_PCT:.0%} flat "
          f"(LEAN), cap {BET_MAX_STAKE_PCT:.0%}/bet, "
          f"{MATCHDAY_EXPOSURE_CAP:.0%}/day · Bankroll {bankroll:.0f} units")
    print("=" * 78)

    if not recs:
        print("\n  No BET/LEAN picks pass filters for upcoming fixtures.")
        print("  → Refresh market_odds.csv and re-run before kickoff.")
        print("  → Do NOT force bets; skipping is the default.")
    else:
        bet_n = sum(1 for r in recs if r.tier == "BET")
        lean_n = sum(1 for r in recs if r.tier == "LEAN")
        print(f"\n  {len(recs)} recommendation(s): {bet_n} BET, {lean_n} LEAN\n")
        for r in recs:
            tag = "★ BET" if r.tier == "BET" else "○ LEAN"
            print(f"  {tag}  {r.date}  {r.home_team} vs {r.away_team}")
            print(f"       Pick: {r.pick}  |  Model {r.model_prob * 100:.1f}%  "
                  f"vs Market {r.market_price * 100:.1f}%  "
                  f"(+{r.edge * 100:.1f}% edge, EV +{r.ev_pct:.1f}%)")
            print(f"       Stake: {r.stake_units:.2f} units "
                  f"({r.stake_pct * 100:.2f}% bankroll)  "
                  f"|  Fair odds {r.fair_decimal}")
            if r.warnings:
                print(f"       ⚠ {' · '.join(r.warnings)}")
            print()

    print_pre_bet_checklist(recs)
    print(f"\n  Bet slip CSV: {BET_SLIP_CSV}")
    print(f"  Log results:  {PAPER_TRADE_LOG}")
    print("=" * 78)


def print_pre_bet_checklist(recs: List[BetRecommendation]):
    """Print last-minute checks before placing real money."""
    print("\n  ── Pre-Bet Checklist (do this before every wager) ──")
    checks = [
        "Re-run script with fresh market_odds.csv (prices move fast)",
        "Confirm starting XIs & injuries (ESPN / official team news)",
        "Only stake BET tier with full size; LEAN at half size or skip",
        "Never exceed daily exposure cap across all bets",
        "Log every bet in paper_trade_log.csv to track real performance",
    ]
    if recs:
        draws = [r for r in recs if r.pick == "Draw"]
        if draws:
            checks.append("Draw picks flagged — consider skipping those")
        disagrees = [r for r in recs if "model pick" in " ".join(r.warnings).lower()]
        if disagrees:
            checks.append("Some value picks disagree with model favourite — extra caution")
    for i, item in enumerate(checks, 1):
        print(f"    {i}. {item}")


def _render_bet_slip_html(recs: List[BetRecommendation]) -> str:
    if not recs:
        return (
            '<div class="info-box"><p><strong>No BET/LEAN picks</strong> for the '
            'next 3 days with current filters. Refresh <code>market_odds.csv</code> '
            'before kickoff. Skipping is the correct play when no edge clears the bar.</p></div>'
        )

    cards = '<div class="card-grid value-grid">'
    for r in recs:
        cls = "bet-card-strong" if r.tier == "BET" else "bet-card-lean"
        warn_html = ""
        if r.warnings:
            warn_html = (
                '<div class="bet-warn">⚠ '
                + _esc(" · ".join(r.warnings)) + "</div>"
            )
        cards += (
            f'<div class="value-card {cls}">'
            f'<div class="bet-tier">{_esc(r.tier)}</div>'
            f'<div class="value-date">{_esc(r.date)} · {_esc(r.city)}</div>'
            f'<div class="value-match">{_esc(r.home_team)} vs '
            f'{_esc(r.away_team)}</div>'
            f'<div class="value-pick">{_esc(r.pick)}</div>'
            f'<div class="value-stats">Model {r.model_prob * 100:.1f}% vs '
            f'Market {r.market_price * 100:.1f}% (vig-adj)</div>'
            f'<div class="value-edge">+{r.edge * 100:.1f}% edge · '
            f'EV +{r.ev_pct:.1f}% · Stake {r.stake_units:.2f}u '
            f'({r.stake_pct * 100:.2f}%)</div>'
            f'{warn_html}</div>'
        )
    cards += "</div>"

    checklist = (
        '<div class="info-box" style="margin-top:1rem"><strong>Before you bet:</strong> '
        '<ol style="margin:0.5rem 0 0 1.25rem">'
        '<li>Re-run with updated Polymarket prices</li>'
        '<li>Check confirmed lineups & injuries</li>'
        '<li>BET tier only at full stake; LEAN at half or skip</li>'
        '<li>Log every wager in <code>paper_trade_log.csv</code></li>'
        '</ol></div>'
    )
    return cards + checklist


def load_wc_fixtures(df: pd.DataFrame,
                     from_date: Optional[date] = None) -> pd.DataFrame:
    """Return unplayed FIFA World Cup 2026 fixtures from from_date onward."""
    if from_date is None:
        from_date = date.today()

    fixtures = df[
        (df["tournament"] == "FIFA World Cup")
        & (df["date"] >= pd.Timestamp(from_date))
        & (df["home_score"].isna() | df["away_score"].isna())
    ].copy()

    return fixtures.sort_values("date")


def predict_wc_fixtures(engine: PoissonMatchEngine,
                        fixtures: pd.DataFrame) -> List[FixturePrediction]:
    """Compute win/draw/loss probabilities for each upcoming World Cup match."""
    predictions: List[FixturePrediction] = []

    for _, row in fixtures.iterrows():
        home = canonicalise(row["home_team"])
        away = canonicalise(row["away_team"])
        neutral = bool(row.get("neutral", False))
        probs = engine.match_win_probabilities(home, away, neutral=neutral)
        la, lb = engine.expected_goals(home, away, neutral=neutral)

        outcomes = [
            (home, probs["win_a"]),
            ("Draw", probs["draw"]),
            (away, probs["win_b"]),
        ]
        pick, pick_prob = max(outcomes, key=lambda x: x[1])

        predictions.append(FixturePrediction(
            date=row["date"].strftime("%Y-%m-%d"),
            home_team=home,
            away_team=away,
            city=str(row.get("city", "")),
            country=str(row.get("country", "")),
            neutral=neutral,
            p_home_win=probs["win_a"],
            p_draw=probs["draw"],
            p_away_win=probs["win_b"],
            pick=pick,
            pick_prob=pick_prob,
            home_xg=la,
            away_xg=lb,
        ))

    return predictions


def save_match_predictions(
        predictions: List[FixturePrediction],
        analyses: Optional[List[MatchValueAnalysis]] = None,
        path: Path = MATCH_PREDICTIONS_CSV) -> Path:
    """Write match predictions to CSV for prediction-market use."""
    rows = []
    for i, p in enumerate(predictions):
        row = {
            "date": p.date,
            "home_team": p.home_team,
            "away_team": p.away_team,
            "city": p.city,
            "country": p.country,
            "neutral_venue": p.neutral,
            "p_home_win": round(p.p_home_win, 4),
            "p_draw": round(p.p_draw, 4),
            "p_away_win": round(p.p_away_win, 4),
            "fair_home_decimal": prob_to_decimal(p.p_home_win),
            "fair_draw_decimal": prob_to_decimal(p.p_draw),
            "fair_away_decimal": prob_to_decimal(p.p_away_win),
            "model_pick": p.pick,
            "pick_probability": round(p.pick_prob, 4),
            "home_xg": round(p.home_xg, 3),
            "away_xg": round(p.away_xg, 3),
        }
        if analyses:
            a = analyses[i]
            row["value_bet"] = a.best_value.label if a.best_value else ""
            row["value_edge"] = round(a.best_value.edge, 4) if (
                a.best_value and a.best_value.edge) else ""
        rows.append(row)

    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def print_match_predictions(predictions: List[FixturePrediction],
                            strength: TeamStrengthModel):
    """Print formatted win/draw/loss probabilities for upcoming WC matches."""
    if not predictions:
        print("\n  No upcoming World Cup fixtures found.")
        return

    print("\n" + "=" * 110)
    print("   WORLD CUP 2026 — MATCH PREDICTIONS (Win / Draw / Loss)")
    print(f"   Model: {model_description()}")
    print("=" * 110)
    print(f"  {'Date':<12}{'Match':<38}{'Home%':>7}{'Draw%':>7}"
          f"{'Away%':>7}{'Pick':>16}{'Conf%':>7}")
    print("-" * 110)

    current_date = ""
    for p in predictions:
        if p.date != current_date:
            if current_date:
                print("-" * 110)
            current_date = p.date

        match_label = f"{p.home_team} vs {p.away_team}"
        venue = " (N)" if p.neutral else ""
        print(f"  {p.date:<12}{match_label + venue:<38}"
              f"{p.p_home_win * 100:>6.1f}%"
              f"{p.p_draw * 100:>6.1f}%"
              f"{p.p_away_win * 100:>6.1f}%"
              f"{p.pick:>16}"
              f"{p.pick_prob * 100:>6.1f}%")

    print("=" * 110)
    print(f"  Saved to {MATCH_PREDICTIONS_CSV}")
    print("  (N) = neutral venue — no host-nation goal boost applied")
    print("=" * 110)

    print("\n  ── Starting XI Market Values (Top 10) ──")
    squad_ranked = sorted(
        ALL_TEAMS, key=lambda t: strength.squad.xi_market_values.get(t, 0),
        reverse=True)[:10]
    for i, team in enumerate(squad_ranked, 1):
        xi_mv = strength.squad.xi_market_values.get(team, 0)
        print(f"  {i:>2}. {team:<22} XI €{xi_mv / 1e6:>7.1f}m  "
              f"Form {strength.get_form_elo(team):.0f}  "
              f"XI {strength.get_xi_elo(team):.0f}  "
              f"Blend {strength.get_rating(team):.0f}")


# ─────────────────────────────────────────────────────────────────────────────
# 12. WORLD CUP BACKTEST (2018 & 2022 group stages)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestMatchResult:
    year: int
    date: str
    home_team: str
    away_team: str
    actual: str
    p_home: float
    p_draw: float
    p_away: float
    model_pick: str
    pick_prob: float
    pick_correct: bool
    brier: float
    log_loss: float


@dataclass
class BacktestSummary:
    year: int
    cutoff_date: str
    matches: int
    training_matches: int
    brier_score: float
    log_loss: float
    pick_accuracy: float
    favorite_accuracy: float
    favorite_count: int
    simulated_bets: int
    simulated_wins: int
    simulated_roi: float
    calibration: List[Tuple[str, int, float, float]]  # bin, n, pred_avg, actual_rate
    results: List[BacktestMatchResult]


class EloBlendStrength:
    """Elo-only strength for historical backtests (no squad look-ahead)."""

    def __init__(self, hist_elo: EloSystem, form_elo: EloSystem):
        elo_total = ELO_HIST_WEIGHT + ELO_FORM_WEIGHT
        self.hist_elo = hist_elo
        self.form_elo = form_elo
        self.hist_weight = ELO_HIST_WEIGHT / elo_total
        self.form_weight = ELO_FORM_WEIGHT / elo_total

    def get_rating(self, team: str) -> float:
        return (self.hist_weight * self.hist_elo.get_rating(team)
                + self.form_weight * self.form_elo.get_rating(team))


def _match_actual_outcome(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "home"
    if home_score < away_score:
        return "away"
    return "draw"


def _get_wc_group_matches(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Return group-stage World Cup matches for a given year."""
    played = df.dropna(subset=["home_score", "away_score"])
    wc = played[
        (played["tournament"] == "FIFA World Cup")
        & (played["date"].dt.year == year)
    ].sort_values("date")
    return wc.head(BACKTEST_GROUP_MATCHES).copy()


def _tournament_cutoff(df: pd.DataFrame, year: int) -> pd.Timestamp:
    wc = df[
        (df["tournament"] == "FIFA World Cup")
        & (df["date"].dt.year == year)
    ]
    if wc.empty:
        return pd.Timestamp(f"{year}-06-01")
    return wc["date"].min().normalize()


def build_backtest_strength(df: pd.DataFrame,
                            cutoff: pd.Timestamp,
                            teams: List[str]) -> EloBlendStrength:
    """Train Elo ratings using only matches strictly before cutoff."""
    played = df.dropna(subset=["home_score", "away_score"]).copy()
    end_date = (cutoff - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    hist_start = max(
        pd.Timestamp("2014-01-01"),
        cutoff - pd.DateOffset(years=BACKTEST_HIST_LOOKBACK_YEARS),
    )
    form_start = (cutoff - pd.DateOffset(months=FORM_ELO_MONTHS)).strftime(
        "%Y-%m-%d")

    hist_elo = EloSystem()
    form_elo = EloSystem()
    hist_elo.compute_from_dataframe(
        played, start_date=str(hist_start.date()), end_date=end_date)
    form_elo.compute_from_dataframe(
        played, start_date=form_start, end_date=end_date)

    hist_elo.fill_missing_from_rankings(teams, FIFA_RANKINGS)
    form_elo.fill_missing_from_rankings(teams, FIFA_RANKINGS)
    return EloBlendStrength(hist_elo, form_elo)


def _calibration_bins(results: List[BacktestMatchResult]
                      ) -> List[Tuple[str, int, float, float]]:
    """Bin favourite win probability vs actual win rate."""
    bins = [
        ("50–55%", 0.50, 0.55),
        ("55–65%", 0.55, 0.65),
        ("65–75%", 0.65, 0.75),
        ("75%+", 0.75, 1.01),
    ]
    rows: List[Tuple[str, int, float, float]] = []

    for label, lo, hi in bins:
        fav_probs: List[float] = []
        fav_wins: List[float] = []
        for r in results:
            probs = {"home": r.p_home, "draw": r.p_draw, "away": r.p_away}
            fav_side = max(probs, key=probs.get)
            fav_p = probs[fav_side]
            if lo <= fav_p < hi:
                fav_probs.append(fav_p)
                actual_side = {"home": r.home_team, "draw": "Draw",
                               "away": r.away_team}[r.actual]
                fav_wins.append(1.0 if fav_side == r.actual else 0.0)

        if fav_probs:
            rows.append((label, len(fav_probs),
                         float(np.mean(fav_probs)),
                         float(np.mean(fav_wins))))

    return rows


def backtest_world_cup(df: pd.DataFrame, year: int) -> BacktestSummary:
    """Backtest group-stage predictions for one World Cup year."""
    group = _get_wc_group_matches(df, year)
    if group.empty:
        raise ValueError(f"No World Cup {year} matches in dataset.")

    cutoff = _tournament_cutoff(df, year)
    teams = sorted(set(group["home_team"].map(canonicalise))
                   | set(group["away_team"].map(canonicalise)))

    strength = build_backtest_strength(df, cutoff, teams)
    engine = PoissonMatchEngine(strength)

    end_train = cutoff - pd.Timedelta(days=1)
    hist_start = max(
        pd.Timestamp("2014-01-01"),
        cutoff - pd.DateOffset(years=BACKTEST_HIST_LOOKBACK_YEARS),
    )
    played = df.dropna(subset=["home_score", "away_score"])
    training_n = len(played[
        (played["date"] >= hist_start) & (played["date"] <= end_train)])

    results: List[BacktestMatchResult] = []
    sim_bets = sim_wins = 0
    sim_profit = 0.0
    favorite_hits = favorite_total = 0

    for _, row in group.iterrows():
        home = canonicalise(row["home_team"])
        away = canonicalise(row["away_team"])
        neutral = bool(row.get("neutral", False))
        hs, aws = int(row["home_score"]), int(row["away_score"])
        actual = _match_actual_outcome(hs, aws)

        probs = engine.match_win_probabilities(home, away, neutral=neutral)
        p_home, p_draw, p_away = probs["win_a"], probs["draw"], probs["win_b"]

        outcomes = [(home, p_home), ("Draw", p_draw), (away, p_away)]
        pick, pick_prob = max(outcomes, key=lambda x: x[1])
        pick_side = "home" if pick == home else (
            "away" if pick == away else "draw")
        pick_correct = pick_side == actual

        y = [1.0 if actual == "home" else 0.0,
             1.0 if actual == "draw" else 0.0,
             1.0 if actual == "away" else 0.0]
        p_vec = [p_home, p_draw, p_away]
        brier = sum((p - o) ** 2 for p, o in zip(p_vec, y))
        p_correct = max(p_home if actual == "home" else 0,
                        p_draw if actual == "draw" else 0,
                        p_away if actual == "away" else 0)
        log_loss = -math.log(max(p_correct, 1e-15))

        fav_p = max(p_home, p_draw, p_away)
        if fav_p >= BACKTEST_MIN_CONFIDENCE:
            favorite_total += 1
            fav_side = ("home" if fav_p == p_home else
                        "draw" if fav_p == p_draw else "away")
            if fav_side == actual:
                favorite_hits += 1
            sim_bets += 1
            if fav_side == actual:
                sim_wins += 1
                sim_profit += (1.0 / fav_p) - 1.0
            else:
                sim_profit -= 1.0

        results.append(BacktestMatchResult(
            year=year,
            date=row["date"].strftime("%Y-%m-%d"),
            home_team=home,
            away_team=away,
            actual=actual,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            model_pick=pick,
            pick_prob=pick_prob,
            pick_correct=pick_correct,
            brier=brier,
            log_loss=log_loss,
        ))

    n = len(results)
    return BacktestSummary(
        year=year,
        cutoff_date=cutoff.strftime("%Y-%m-%d"),
        matches=n,
        training_matches=training_n,
        brier_score=float(np.mean([r.brier for r in results])),
        log_loss=float(np.mean([r.log_loss for r in results])),
        pick_accuracy=sum(r.pick_correct for r in results) / n,
        favorite_accuracy=(favorite_hits / favorite_total
                           if favorite_total else 0.0),
        favorite_count=favorite_total,
        simulated_bets=sim_bets,
        simulated_wins=sim_wins,
        simulated_roi=sim_profit / sim_bets if sim_bets else 0.0,
        calibration=_calibration_bins(results),
        results=results,
    )


def run_world_cup_backtests(df: pd.DataFrame) -> Dict[int, BacktestSummary]:
    """Run backtests for all configured World Cup years."""
    return {year: backtest_world_cup(df, year) for year in BACKTEST_YEARS}


def save_backtest_results(summaries: Dict[int, BacktestSummary],
                          path: Path = BACKTEST_CSV) -> Path:
    rows = []
    for summary in summaries.values():
        for r in summary.results:
            rows.append({
                "year": r.year,
                "date": r.date,
                "home_team": r.home_team,
                "away_team": r.away_team,
                "actual": r.actual,
                "p_home_win": round(r.p_home, 4),
                "p_draw": round(r.p_draw, 4),
                "p_away_win": round(r.p_away, 4),
                "model_pick": r.model_pick,
                "pick_prob": round(r.pick_prob, 4),
                "pick_correct": r.pick_correct,
                "brier": round(r.brier, 4),
                "log_loss": round(r.log_loss, 4),
            })
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def print_backtest_report(summaries: Dict[int, BacktestSummary]):
    """Print backtest metrics to terminal."""
    print("\n" + "=" * 78)
    print("   WORLD CUP BACKTEST — 2018 & 2022 GROUP STAGES")
    print("   (Point-in-time Elo + form only — no squad data to avoid look-ahead)")
    print("=" * 78)

    combined_brier: List[float] = []
    combined_ll: List[float] = []
    combined_correct = 0
    combined_n = 0

    for year in sorted(summaries):
        s = summaries[year]
        combined_brier.extend([r.brier for r in s.results])
        combined_ll.extend([r.log_loss for r in s.results])
        combined_correct += sum(r.pick_correct for r in s.results)
        combined_n += s.matches

        print(f"\n  ── {year} World Cup (cutoff {s.cutoff_date}) ──")
        print(f"  Group matches tested:     {s.matches}")
        print(f"  Training matches used:    {s.training_matches:,}")
        print(f"  Brier score (lower=better): {s.brier_score:.4f}")
        print(f"  Log loss (lower=better):    {s.log_loss:.4f}")
        print(f"  Pick accuracy:            {s.pick_accuracy * 100:.1f}%")
        print(f"  Favourite accuracy (≥{BACKTEST_MIN_CONFIDENCE:.0%}): "
              f"{s.favorite_accuracy * 100:.1f}% "
              f"({s.favorite_count} matches)")
        print(f"  Simulated flat bets:      {s.simulated_bets} bets, "
              f"{s.simulated_wins} wins, "
              f"ROI {s.simulated_roi * 100:+.1f}%")
        print("  Calibration (favourite win prob → actual):")
        for label, n, pred, actual in s.calibration:
            print(f"    {label:8} n={n:2}  predicted {pred * 100:5.1f}%  "
                  f"actual {actual * 100:5.1f}%")

    if combined_n:
        print(f"\n  ── Combined ({combined_n} group matches) ──")
        print(f"  Brier score:     {np.mean(combined_brier):.4f}")
        print(f"  Log loss:        {np.mean(combined_ll):.4f}")
        print(f"  Pick accuracy:   {combined_correct / combined_n * 100:.1f}%")
        print("  (Random baseline ≈ 33% pick accuracy; Brier < 0.67 beats uniform)")

    print(f"\n  Full results: {BACKTEST_CSV}")
    print("=" * 78)


def _render_backtest_html(summaries: Dict[int, BacktestSummary]) -> str:
    if not summaries:
        return "<p>No backtest data.</p>"

    combined = [r for s in summaries.values() for r in s.results]
    n = len(combined)
    brier = np.mean([r.brier for r in combined]) if combined else 0
    acc = sum(r.pick_correct for r in combined) / n if n else 0

    summary_cards = (
        f'<div class="stats-grid">'
        f'<div class="stat-card"><div class="label">Matches Tested</div>'
        f'<div class="value">{n}</div>'
        f'<div class="detail">2018 + 2022 group stages</div></div>'
        f'<div class="stat-card"><div class="label">Brier Score</div>'
        f'<div class="value">{brier:.3f}</div>'
        f'<div class="detail">Lower is better (&lt;0.67 beats random)</div></div>'
        f'<div class="stat-card"><div class="label">Pick Accuracy</div>'
        f'<div class="value">{acc * 100:.1f}%</div>'
        f'<div class="detail">Highest-probability outcome</div></div>'
        f'</div>'
    )

    year_sections = ""
    for year in sorted(summaries):
        s = summaries[year]
        cal_rows = ""
        for label, cnt, pred, actual in s.calibration:
            cal_rows += (
                f"<tr><td>{_esc(label)}</td><td>{cnt}</td>"
                f"<td>{pred * 100:.1f}%</td><td>{actual * 100:.1f}%</td></tr>"
            )
        year_sections += (
            f'<h3>{year} World Cup</h3>'
            f'<p class="venue">Cutoff {_esc(s.cutoff_date)} · '
            f'{s.training_matches:,} training matches · '
            f'Brier {s.brier_score:.3f} · Pick acc {s.pick_accuracy * 100:.1f}% · '
            f'Sim ROI {s.simulated_roi * 100:+.1f}% '
            f'({s.simulated_bets} flat bets @ fair odds, fav ≥55%)</p>'
            f'<table class="data-table"><tr><th>Prob bin</th><th>n</th>'
            f'<th>Predicted</th><th>Actual</th></tr>{cal_rows}</table>'
        )

    worst = sorted(combined, key=lambda r: r.log_loss, reverse=True)[:8]
    miss_rows = ""
    for r in worst:
        actual_label = (r.home_team if r.actual == "home" else
                        r.away_team if r.actual == "away" else "Draw")
        miss_rows += (
            f"<tr><td>{r.year}</td><td>{_esc(r.home_team)} vs "
            f"{_esc(r.away_team)}</td>"
            f"<td>{_esc(r.model_pick)} ({r.pick_prob * 100:.0f}%)</td>"
            f"<td>{_esc(actual_label)}</td>"
            f"<td>{'✓' if r.pick_correct else '✗'}</td></tr>"
        )

    return (
        f'{summary_cards}'
        f'<div class="info-box" style="margin-bottom:1.25rem">'
        f'<p>Backtest uses <strong>historical + form Elo only</strong> '
        f'(trained strictly before each tournament). Squad/lineup data is '
        f'excluded to prevent look-ahead bias. 2026 predictions use the full '
        f'four-component model.</p></div>'
        f'{year_sections}'
        f'<h3 style="margin-top:1.5rem">Biggest Surprises (highest log-loss)</h3>'
        f'<table class="data-table"><tr><th>Year</th><th>Match</th>'
        f'<th>Model pick</th><th>Actual</th><th>OK?</th></tr>'
        f'{miss_rows}</table>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 13. HTML REPORT
# ─────────────────────────────────────────────────────────────────────────────

def _esc(text: object) -> str:
    return html_module.escape(str(text))


def _pct_bar(pct: float, color: str = "#3b82f6") -> str:
    width = min(max(pct, 0), 100)
    return (f'<div class="bar-track"><div class="bar-fill" '
            f'style="width:{width:.1f}%;background:{color}"></div></div>')


def _prob_cells(outcome: OutcomeOdds) -> str:
    value_cls = ' class="value-cell"' if outcome.is_value else ""
    edge = (f'<span class="edge-tag">+{outcome.edge * 100:.1f}% edge</span>'
            if outcome.is_value and outcome.edge else "")
    market = ""
    if outcome.market_decimal:
        market = (f'<div class="market-line">Market {_esc(outcome.market_decimal)}'
                  f' ({outcome.market_implied * 100:.1f}%)</div>')
    return (
        f'<td{value_cls}>'
        f'<div class="prob-main">{outcome.model_prob * 100:.1f}%</div>'
        f'<div class="odds-line">Fair {_esc(outcome.fair_decimal)} '
        f'({outcome.fair_american:+d})</div>'
        f'<div class="odds-line subtle">Min EV+5%: '
        f'{_esc(outcome.min_decimal_5pct_edge)}</div>'
        f'{market}{edge}</td>'
    )


def build_simulation_summary(records: Dict[str, TeamTournamentRecord],
                             n_sims: int,
                             matches_processed: int) -> SimulationSummary:
    champ = max(ALL_TEAMS, key=lambda t: records[t].winner)
    finalist = max(ALL_TEAMS, key=lambda t: records[t].final)
    host_best = max(HOST_NATIONS, key=lambda t: records[t].winner)
    bottom_20 = sorted(ALL_TEAMS,
                       key=lambda t: FIFA_RANKINGS.get(t, 100))[-20:]
    dark_horse = max(bottom_20, key=lambda t: records[t].winner)

    return SimulationSummary(
        champion=champ,
        champion_pct=records[champ].winner / n_sims * 100,
        finalist=finalist,
        finalist_pct=records[finalist].final / n_sims * 100,
        host_best=host_best,
        host_pct=records[host_best].winner / n_sims * 100,
        dark_horse=dark_horse,
        dark_horse_pct=records[dark_horse].winner / n_sims * 100,
        matches_processed=matches_processed,
        n_simulations=n_sims,
    )


def generate_html_report(
        strength: TeamStrengthModel,
        predictions: List[FixturePrediction],
        value_analyses: List[MatchValueAnalysis],
        records: Dict[str, TeamTournamentRecord],
        summary: SimulationSummary,
        backtest_results: Optional[Dict[int, BacktestSummary]] = None,
        bet_recommendations: Optional[List[BetRecommendation]] = None,
        live_mode: bool = False,
        path: Path = REPORT_HTML,
) -> Path:
    """Write a self-contained HTML dashboard with all simulation outputs."""
    n_sims = summary.n_simulations
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    has_market = any(
        a.best_value for a in value_analyses
    ) or any(
        o.market_decimal
        for a in value_analyses
        for o in (a.home, a.draw, a.away)
    )

    def _sim_pct(count: int) -> float:
        return count / n_sims * 100 if n_sims else 0.0

    # Top contenders (by sim wins, or strength rating in live mode)
    if n_sims:
        top_teams = sorted(ALL_TEAMS,
                           key=lambda t: records[t].winner,
                           reverse=True)[:15]
        max_win = _sim_pct(records[top_teams[0]].winner) if top_teams else 1
    else:
        top_teams = sorted(ALL_TEAMS,
                           key=lambda t: strength.get_rating(t),
                           reverse=True)[:15]
        max_win = strength.get_rating(top_teams[0]) if top_teams else 1

    contender_rows = ""
    for i, team in enumerate(top_teams, 1):
        if n_sims:
            win_pct = _sim_pct(records[team].winner)
            bar_w = win_pct / max_win * 100 if max_win else 0
            pct_label = f"{win_pct:.2f}%"
        else:
            rating = strength.get_rating(team)
            bar_w = rating / max_win * 100 if max_win else 0
            pct_label = f"{rating:.0f}"
        host = ' <span class="host-badge">HOST</span>' if team in HOST_NATIONS else ""
        contender_rows += (
            f'<div class="contender-row">'
            f'<span class="rank">{i}</span>'
            f'<span class="team-name">{_esc(team)}{host}</span>'
            f'<div class="bar-track wide"><div class="bar-fill gold" '
            f'style="width:{bar_w:.1f}%"></div></div>'
            f'<span class="pct-label">{pct_label}</span></div>'
        )

    # Team strength table
    strength_rows = ""
    ratings = sorted(
        [(t, strength.get_rating(t), strength.get_hist_elo(t),
          strength.get_form_elo(t), strength.get_xi_elo(t),
          strength.squad.xi_market_values.get(t, 0) / 1e6)
         for t in ALL_TEAMS],
        key=lambda x: -x[1],
    )
    for i, (team, blend, hist_r, form_r, xi_r, xi_mv) in enumerate(ratings, 1):
        host = ' <span class="host-badge">HOST</span>' if team in HOST_NATIONS else ""
        strength_rows += (
            f"<tr><td>{i}</td><td>{_esc(team)}{host}</td>"
            f"<td><strong>{blend:.0f}</strong></td>"
            f"<td>{hist_r:.0f}</td><td>{form_r:.0f}</td>"
            f"<td>{xi_r:.0f}</td><td>€{xi_mv:.1f}m</td></tr>"
        )

    # Value bets highlight
    value_hits = [
        (a, a.best_value) for a in value_analyses if a.best_value
    ]
    if value_hits:
        value_section = '<div class="card-grid value-grid">'
        for analysis, bet in value_hits:
            value_section += (
                f'<div class="value-card">'
                f'<div class="value-date">{_esc(analysis.date)}</div>'
                f'<div class="value-match">{_esc(analysis.home_team)} vs '
                f'{_esc(analysis.away_team)}</div>'
                f'<div class="value-pick">{_esc(bet.label)}</div>'
                f'<div class="value-stats">Model {bet.model_prob * 100:.1f}% vs '
                f'Market {bet.market_implied * 100:.1f}%</div>'
                f'<div class="value-edge">+{bet.edge * 100:.1f}% edge · '
                f'Fair odds {bet.fair_decimal}</div></div>'
            )
        value_section += "</div>"
    else:
        value_section = (
            '<div class="info-box">'
            '<p><strong>No value bets flagged yet.</strong> Paste bookmaker or '
            'Polymarket prices into <code>market_odds.csv</code> and re-run. '
            'Bets are flagged when model probability exceeds market implied '
            f'probability by ≥{VALUE_EDGE_THRESHOLD * 100:.0f} percentage points.</p>'
            '<p>Until then, compare model <em>fair odds</em> in the match table '
            'below to prices on your prediction market.</p></div>'
        )

    # Match predictions by date
    match_sections = ""
    by_date: Dict[str, List[Tuple[FixturePrediction, MatchValueAnalysis]]] = {}
    for pred, analysis in zip(predictions, value_analyses):
        by_date.setdefault(pred.date, []).append((pred, analysis))

    for match_date in sorted(by_date.keys()):
        match_sections += f'<h3 class="date-heading">{_esc(match_date)}</h3>'
        match_sections += '<div class="match-grid">'
        for pred, analysis in by_date[match_date]:
            venue = (f'{_esc(pred.city)}, {_esc(pred.country)}'
                     if pred.city else "TBD")
            neutral_tag = ('<span class="tag neutral">Neutral</span>'
                           if pred.neutral else
                           '<span class="tag home">Home boost</span>')
            pick_cls = "pick-strong" if pred.pick_prob >= 0.55 else "pick-lean"

            match_sections += (
                f'<div class="match-card">'
                f'<div class="match-header">'
                f'<span class="teams">{_esc(pred.home_team)} '
                f'<span class="vs">vs</span> {_esc(pred.away_team)}</span>'
                f'{neutral_tag}</div>'
                f'<div class="venue">{venue}</div>'
                f'<div class="xg-line">xG: {pred.home_xg:.2f} – '
                f'{pred.away_xg:.2f}</div>'
                f'<table class="mini-odds"><tr>'
                f'<th>{_esc(pred.home_team)}</th><th>Draw</th>'
                f'<th>{_esc(pred.away_team)}</th></tr><tr>'
                f'{_prob_cells(analysis.home)}'
                f'{_prob_cells(analysis.draw)}'
                f'{_prob_cells(analysis.away)}'
                f'</tr></table>'
                f'<div class="model-pick {pick_cls}">Pick: <strong>'
                f'{_esc(pred.pick)}</strong> ({pred.pick_prob * 100:.1f}%)</div>'
                f'</div>'
            )
        match_sections += "</div>"

    # Group probabilities
    group_sections = ""
    if n_sims:
        for grp in sorted(GROUPS.keys()):
            teams = GROUPS[grp]
            group_sections += f'<div class="group-card"><h4>Group {grp}</h4><table>'
            group_sections += (
                "<tr><th>Team</th><th>Advance</th><th>Exit</th><th>R16</th></tr>"
            )
            for team in sorted(teams, key=lambda t: records[t].r32,
                               reverse=True):
                r = records[team]
                adv = _sim_pct(r.r32)
                ex = _sim_pct(r.group_exit)
                r16 = _sim_pct(r.r16)
                group_sections += (
                    f"<tr><td>{_esc(team)}</td>"
                    f"<td>{adv:.1f}%</td><td>{ex:.1f}%</td><td>{r16:.1f}%</td></tr>"
                )
            group_sections += "</table></div>"
    else:
        group_sections = (
            '<p class="info-box">Group advancement probabilities require a full '
            'simulation run (<code>python wc2026_simulation.py</code>).</p>'
        )

    # Full tournament table
    team_group = {t: g for g, teams in GROUPS.items() for t in teams}
    tourney_rows = ""
    if n_sims:
        for team in sorted(ALL_TEAMS, key=lambda t: records[t].winner,
                           reverse=True):
            r = records[team]
            host = ' <span class="host-badge">HOST</span>' if team in HOST_NATIONS else ""
            tourney_rows += (
                f"<tr><td>{_esc(team)}{host}</td><td>{team_group[team]}</td>"
                f"<td>{_sim_pct(r.r32):.1f}%</td>"
                f"<td>{_sim_pct(r.r16):.1f}%</td>"
                f"<td>{_sim_pct(r.qf):.1f}%</td>"
                f"<td>{_sim_pct(r.sf):.1f}%</td>"
                f"<td>{_sim_pct(r.final):.1f}%</td>"
                f"<td><strong>{_sim_pct(r.winner):.2f}%</strong></td>"
                f"<td>{strength.get_rating(team):.0f}</td></tr>"
            )
    else:
        tourney_rows = (
            '<tr><td colspan="9" style="color:var(--muted)">'
            'Run full simulation for tournament distribution.</td></tr>'
        )

    backtest_section = (
        _render_backtest_html(backtest_results)
        if backtest_results else
        '<p class="info-box">Backtest not run.</p>'
    )

    bet_slip_section = _render_bet_slip_html(bet_recommendations or [])

    live_banner = ""
    if live_mode:
        live_banner = (
            '<div class="info-box" style="margin-bottom:1.25rem;border-color:#22c55e">'
            '<strong>Live update mode</strong> — Match data and Polymarket odds '
            f'refreshed at {_esc(generated)}. Tournament Monte Carlo skipped; '
            'run <code>python wc2026_simulation.py</code> (no flags) for champion odds.'
            '</div>'
        )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FIFA World Cup 2026 — Prediction Report</title>
<style>
:root {{
  --bg: #0c1222; --surface: #151d2e; --surface2: #1c2740;
  --border: #2a3655; --text: #e8edf5; --muted: #8b9cb8;
  --accent: #3b82f6; --gold: #f59e0b; --green: #22c55e;
  --red: #ef4444; --purple: #a855f7;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5;
}}
.hero {{
  background: linear-gradient(135deg, #1e3a5f 0%, #0c1222 60%, #1a0a2e 100%);
  padding: 2.5rem 2rem; border-bottom: 1px solid var(--border);
}}
.hero h1 {{ font-size: 1.85rem; font-weight: 700; margin-bottom: 0.35rem; }}
.hero .sub {{ color: var(--muted); font-size: 0.95rem; }}
.hero .meta {{ margin-top: 1rem; font-size: 0.8rem; color: var(--muted); }}
.nav {{
  display: flex; flex-wrap: wrap; gap: 0.5rem; padding: 1rem 2rem;
  background: var(--surface); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
}}
.nav a {{
  color: var(--muted); text-decoration: none; font-size: 0.82rem;
  padding: 0.35rem 0.75rem; border-radius: 6px; border: 1px solid var(--border);
}}
.nav a:hover {{ color: var(--text); border-color: var(--accent); }}
.container {{ max-width: 1280px; margin: 0 auto; padding: 1.5rem 2rem 3rem; }}
section {{ margin-bottom: 2.5rem; }}
h2 {{
  font-size: 1.25rem; margin-bottom: 1rem; padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--accent); display: inline-block;
}}
h3.date-heading {{
  font-size: 1rem; color: var(--gold); margin: 1.5rem 0 0.75rem;
}}
.stats-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem; margin-bottom: 1.5rem;
}}
.stat-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.1rem;
}}
.stat-card .label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; }}
.stat-card .value {{ font-size: 1.5rem; font-weight: 700; margin-top: 0.25rem; }}
.stat-card .detail {{ font-size: 0.82rem; color: var(--muted); }}
.card-grid {{ display: grid; gap: 1rem; }}
.value-grid {{ grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }}
.value-card {{
  background: linear-gradient(135deg, #14532d 0%, var(--surface) 100%);
  border: 1px solid #22c55e55; border-radius: 12px; padding: 1rem;
}}
.value-pick {{ font-size: 1.2rem; font-weight: 700; color: var(--green); }}
.value-edge {{ font-size: 0.85rem; color: #86efac; margin-top: 0.35rem; }}
.bet-card-strong {{ border-color: #22c55e; background: linear-gradient(135deg, #14532d 0%, var(--surface) 100%); }}
.bet-card-lean {{ border-color: #eab30888; background: linear-gradient(135deg, #422006 0%, var(--surface) 100%); }}
.bet-tier {{ font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em; color: var(--gold); margin-bottom: 0.25rem; }}
.bet-warn {{ font-size: 0.78rem; color: #fbbf24; margin-top: 0.5rem; }}
.info-box {{
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.25rem; color: var(--muted); font-size: 0.9rem;
}}
.info-box code {{ background: var(--bg); padding: 0.15rem 0.4rem; border-radius: 4px; }}
.match-grid {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 1rem;
}}
.match-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1rem;
}}
.match-header {{ display: flex; justify-content: space-between; align-items: center; }}
.teams {{ font-weight: 600; font-size: 0.95rem; }}
.vs {{ color: var(--muted); font-weight: 400; }}
.venue {{ font-size: 0.78rem; color: var(--muted); margin: 0.35rem 0; }}
.xg-line {{ font-size: 0.8rem; color: var(--purple); margin-bottom: 0.6rem; }}
.tag {{
  font-size: 0.65rem; padding: 0.15rem 0.45rem; border-radius: 4px;
  text-transform: uppercase; font-weight: 600;
}}
.tag.neutral {{ background: #334155; color: #94a3b8; }}
.tag.home {{ background: #422006; color: var(--gold); }}
.mini-odds {{ width: 100%; font-size: 0.78rem; border-collapse: collapse; }}
.mini-odds th, .mini-odds td {{
  padding: 0.4rem 0.3rem; text-align: center; border-top: 1px solid var(--border);
}}
.mini-odds th {{ color: var(--muted); font-weight: 500; }}
.prob-main {{ font-weight: 700; font-size: 0.95rem; }}
.odds-line {{ color: var(--muted); font-size: 0.72rem; }}
.odds-line.subtle {{ font-size: 0.68rem; }}
.market-line {{ color: var(--gold); font-size: 0.72rem; margin-top: 0.15rem; }}
.value-cell {{ background: #14532d33; }}
.edge-tag {{
  display: inline-block; background: var(--green); color: #052e16;
  font-size: 0.65rem; font-weight: 700; padding: 0.1rem 0.35rem;
  border-radius: 4px; margin-top: 0.2rem;
}}
.model-pick {{
  margin-top: 0.6rem; font-size: 0.85rem; padding: 0.4rem 0.6rem;
  border-radius: 6px; background: var(--surface2);
}}
.pick-strong {{ border-left: 3px solid var(--green); }}
.pick-lean {{ border-left: 3px solid var(--gold); }}
.contender-row {{
  display: grid; grid-template-columns: 28px 140px 1fr 60px;
  align-items: center; gap: 0.75rem; margin-bottom: 0.5rem;
}}
.bar-track {{
  height: 8px; background: var(--surface2); border-radius: 4px; overflow: hidden;
}}
.bar-track.wide {{ height: 12px; }}
.bar-fill {{ height: 100%; border-radius: 4px; background: var(--accent); }}
.bar-fill.gold {{ background: linear-gradient(90deg, var(--gold), #fbbf24); }}
.pct-label {{ font-size: 0.82rem; font-weight: 600; text-align: right; }}
.host-badge {{
  font-size: 0.6rem; background: #422006; color: var(--gold);
  padding: 0.1rem 0.35rem; border-radius: 3px; margin-left: 0.25rem;
}}
.data-table {{
  width: 100%; border-collapse: collapse; font-size: 0.82rem;
  background: var(--surface); border-radius: 12px; overflow: hidden;
}}
.data-table th, .data-table td {{
  padding: 0.55rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border);
}}
.data-table th {{ background: var(--surface2); color: var(--muted); font-weight: 600; }}
.data-table tr:hover td {{ background: var(--surface2); }}
.groups-grid {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 1rem;
}}
.group-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 0.75rem;
}}
.group-card h4 {{ color: var(--accent); margin-bottom: 0.5rem; }}
.group-card table {{ width: 100%; font-size: 0.78rem; }}
.group-card th {{ color: var(--muted); text-align: left; padding: 0.2rem 0; }}
.group-card td {{ padding: 0.2rem 0; }}
.methodology {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.25rem; font-size: 0.88rem; color: var(--muted);
}}
.methodology ul {{ margin: 0.5rem 0 0 1.25rem; }}
.methodology li {{ margin-bottom: 0.3rem; }}
.footer {{ text-align: center; color: var(--muted); font-size: 0.78rem; padding: 2rem; }}
@media (max-width: 700px) {{
  .container {{ padding: 1rem; }}
  .contender-row {{ grid-template-columns: 24px 1fr 60px; }}
  .contender-row .bar-track {{ display: none; }}
}}
</style>
</head>
<body>
<header class="hero">
  <h1>FIFA World Cup 2026 — {"Live Prediction Update" if live_mode else "Prediction Report"}</h1>
  <p class="sub">48 Teams · 12 Groups · {summary.n_simulations:,} Monte Carlo Simulations</p>
  <p class="meta">Generated {_esc(generated)} · Model: {_esc(model_description())}</p>
</header>
<nav class="nav">
  <a href="#summary">Summary</a>
  <a href="#betslip">Bet Slip</a>
  <a href="#backtest">Backtest</a>
  <a href="#value">Value Bets</a>
  <a href="#matches">Match Predictions</a>
  <a href="#strength">Team Strength</a>
  <a href="#contenders">Top Contenders</a>
  <a href="#groups">Groups</a>
  <a href="#tournament">Tournament Odds</a>
  <a href="#method">Methodology</a>
</nav>
<div class="container">

{live_banner}

<section id="summary">
  <h2>Tournament Summary</h2>
  <div class="stats-grid">
    <div class="stat-card"><div class="label">Most Likely Champion</div>
      <div class="value">{_esc(summary.champion)}</div>
      <div class="detail">{summary.champion_pct:.1f}% win probability</div></div>
    <div class="stat-card"><div class="label">Most Likely Finalist</div>
      <div class="value">{_esc(summary.finalist)}</div>
      <div class="detail">{summary.finalist_pct:.1f}% reach final</div></div>
    <div class="stat-card"><div class="label">Best Host Nation</div>
      <div class="value">{_esc(summary.host_best)}</div>
      <div class="detail">{summary.host_pct:.1f}% win probability</div></div>
    <div class="stat-card"><div class="label">Dark Horse</div>
      <div class="value">{_esc(summary.dark_horse)}</div>
      <div class="detail">{summary.dark_horse_pct:.2f}% win probability</div></div>
    <div class="stat-card"><div class="label">Elo Matches Used</div>
      <div class="value">{summary.matches_processed:,}</div>
      <div class="detail">{ELO_START_DATE} to {ELO_END_DATE}</div></div>
    <div class="stat-card"><div class="label">Fixtures Predicted</div>
      <div class="value">{len(predictions)}</div>
      <div class="detail">Group stage matches</div></div>
  </div>
</section>

<section id="betslip">
  <h2>Bet Slip — Next 3 Days</h2>
  <p style="color:var(--muted);font-size:0.88rem;margin-bottom:1rem">
    BET = model ≥{BET_TIER_STRONG_PROB:.0%} &amp; vig-adjusted edge ≥{BET_TIER_STRONG_EDGE:.0%}.
    LEAN = ≥{BET_TIER_MIN_PROB:.0%} &amp; ≥{BET_TIER_MIN_EDGE:.0%}.
    Stakes use quarter-Kelly capped at {BET_MAX_STAKE_PCT:.0%} per bet.
  </p>
  {bet_slip_section}
</section>

<section id="backtest">
  <h2>Model Backtest — 2018 &amp; 2022 World Cups</h2>
  {backtest_section}
</section>

<section id="value">
  <h2>Value Bets vs Market</h2>
  {value_section}
</section>

<section id="matches">
  <h2>Match Predictions — Win / Draw / Loss</h2>
  <p style="color:var(--muted);font-size:0.88rem;margin-bottom:1rem">
    Fair decimal odds = 1 ÷ model probability. Compare to your prediction market;
    bet when market odds exceed the <em>Min EV+5%</em> threshold.
  </p>
  {match_sections}
</section>

<section id="strength">
  <h2>Team Strength Rankings</h2>
  <table class="data-table">
    <tr><th>#</th><th>Team</th><th>Blend</th><th>Hist</th><th>Form</th><th>XI Elo</th><th>XI Value</th></tr>
    {strength_rows}
  </table>
</section>

<section id="contenders">
  <h2>Top 15 Contenders</h2>
  {contender_rows}
</section>

<section id="groups">
  <h2>Group Stage — Advancement Probabilities</h2>
  <div class="groups-grid">{group_sections}</div>
</section>

<section id="tournament">
  <h2>Full Tournament Distribution</h2>
  <table class="data-table">
    <tr><th>Team</th><th>Grp</th><th>Advance</th><th>R16</th><th>QF</th>
    <th>SF</th><th>Final</th><th>Win</th><th>Rating</th></tr>
    {tourney_rows}
  </table>
</section>

<section id="method">
  <h2>Methodology</h2>
  <div class="methodology">
    <ul>
      <li>Historical Elo ({ELO_HIST_WEIGHT:.0%}): {ELO_START_DATE}–{ELO_END_DATE} with competition weights</li>
      <li>Form Elo ({ELO_FORM_WEIGHT:.0%}): last {FORM_ELO_MONTHS} months, same weighting rules</li>
      <li>Full squad value ({SQUAD_FULL_WEIGHT:.0%}): Transfermarkt 26-man squad totals</li>
      <li>Starting XI value ({SQUAD_XI_WEIGHT:.0%}): ESPN projected XIs + Goal.com for other teams</li>
      <li>Poisson match model with xG from strength differential</li>
      <li>Host advantage: +{HOST_ADVANTAGE_GOALS} xG for USA, Mexico, Canada (non-neutral venues only)</li>
      <li>Tournament defensiveness factor: ×{TOURNAMENT_DEFENSIVENESS}</li>
      <li>Value bets flagged when edge ≥ {VALUE_EDGE_THRESHOLD * 100:.0f}% vs prices in market_odds.csv</li>
    </ul>
  </div>
</section>

</div>
<footer class="footer">
  FIFA World Cup 2026 Monte Carlo Simulator · Not financial advice · Gamble responsibly
</footer>
</body>
</html>"""

    path.write_text(page, encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 13. TERMINAL REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def print_elo_ratings(strength: TeamStrengthModel, teams: List[str]):
    """Print blended strength ratings for all 48 teams."""
    ratings = [(t, strength.get_rating(t), strength.get_hist_elo(t),
                strength.get_form_elo(t), strength.get_xi_elo(t))
               for t in teams]
    ratings.sort(key=lambda x: -x[1])

    print("\n" + "=" * 92)
    print("         TEAM STRENGTH — ALL 48 WORLD CUP TEAMS")
    print(f"         ({model_description()})")
    print("=" * 92)
    print(f"  {'Rank':<6}{'Team':<20}{'Blend':>7}{'Hist':>7}{'Form':>7}"
          f"{'XI':>7}{'XI€m':>8}")
    print("-" * 92)
    for i, (team, blend, hist_r, form_r, xi_r) in enumerate(ratings, 1):
        host = " 🏠" if team in HOST_NATIONS else ""
        xi_mv = strength.squad.xi_market_values.get(team, 0) / 1e6
        print(f"  {i:<6}{team:<20}{blend:>7.0f}{hist_r:>7.0f}{form_r:>7.0f}"
              f"{xi_r:>7.0f}{xi_mv:>8.1f}{host}")
    print("=" * 92)


def print_group_probabilities(records: Dict[str, TeamTournamentRecord],
                              n_sims: int):
    """Print group-stage advancement probabilities."""
    print("\n" + "=" * 72)
    print("      GROUP STAGE — PROBABILITY OF ADVANCING TO KNOCKOUT ROUND")
    print("=" * 72)

    for grp_name in sorted(GROUPS.keys()):
        teams = GROUPS[grp_name]
        print(f"\n  ┌─── Group {grp_name} ───────────────────────────────"
              "──────────────────┐")
        print(f"  │ {'Team':<25}{'Advance %':>10}{'Exit %':>10}"
              f"{'Win R32 %':>12}│")
        print(f"  ├───────────────────────────────────────────────"
              "──────────────────┤")
        for team in sorted(teams,
                           key=lambda t: records[t].r32 / n_sims,
                           reverse=True):
            r = records[team]
            adv = r.r32 / n_sims * 100
            ex = r.group_exit / n_sims * 100
            r16 = r.r16 / n_sims * 100
            print(f"  │ {team:<25}{adv:>9.1f}%{ex:>9.1f}%{r16:>11.1f}%│")
        print(f"  └───────────────────────────────────────────────"
              "──────────────────┘")


def print_tournament_probabilities(records: Dict[str, TeamTournamentRecord],
                                   n_sims: int,
                                   strength: TeamStrengthModel):
    """Print full probability distribution for all teams."""
    print("\n" + "=" * 100)
    print("         FULL TOURNAMENT PROBABILITY DISTRIBUTION"
          f"  ({n_sims:,} simulations)")
    print("=" * 100)
    print(f"  {'Team':<22}{'Group':>6}{'Adv%':>7}{'R16%':>7}"
          f"{'QF%':>7}{'SF%':>7}{'Final%':>8}{'Win%':>8}{'Elo':>7}")
    print("-" * 100)

    # Find each team's group
    team_group = {}
    for g, teams in GROUPS.items():
        for t in teams:
            team_group[t] = g

    # Sort by win probability
    sorted_teams = sorted(
        ALL_TEAMS,
        key=lambda t: records[t].winner / n_sims,
        reverse=True
    )

    for team in sorted_teams:
        r = records[team]
        grp = team_group[team]
        adv = r.r32 / n_sims * 100
        r16 = r.r16 / n_sims * 100
        qf = r.qf / n_sims * 100
        sf = r.sf / n_sims * 100
        fn = r.final / n_sims * 100
        win = r.winner / n_sims * 100
        elo_r = strength.get_rating(team)
        marker = " ★" if team in HOST_NATIONS else ""
        print(f"  {team + marker:<22}{grp:>6}{adv:>7.1f}{r16:>7.1f}"
              f"{qf:>7.1f}{sf:>7.1f}{fn:>8.1f}{win:>8.2f}{elo_r:>7.0f}")

    print("=" * 100)


def print_top_contenders(records: Dict[str, TeamTournamentRecord],
                         n_sims: int, top_n: int = 15):
    """Print a focused view of the top contenders."""
    print("\n" + "=" * 60)
    print(f"         TOP {top_n} CONTENDERS — WIN PROBABILITY")
    print("=" * 60)

    sorted_teams = sorted(
        ALL_TEAMS,
        key=lambda t: records[t].winner / n_sims,
        reverse=True
    )[:top_n]

    max_win = records[sorted_teams[0]].winner / n_sims * 100

    for i, team in enumerate(sorted_teams, 1):
        r = records[team]
        win_pct = r.winner / n_sims * 100
        bar_len = int(win_pct / max_win * 30) if max_win > 0 else 0
        bar = "█" * bar_len
        host = " ★" if team in HOST_NATIONS else ""
        print(f"  {i:>2}. {team + host:<22} {win_pct:>6.2f}%  {bar}")

    print("=" * 60)
    print("  ★ = Host nation (receives home advantage boost)")


def print_key_matchup(engine: PoissonMatchEngine, ta: str, tb: str):
    """Print analytical probabilities for a specific matchup."""
    probs = engine.match_win_probabilities(ta, tb)
    la, lb = engine.expected_goals(ta, tb)
    print(f"  {ta} vs {tb}:")
    print(f"    xG: {la:.2f} – {lb:.2f}  |  "
          f"Win: {probs['win_a']:.1%}  Draw: {probs['draw']:.1%}  "
          f"Loss: {probs['win_b']:.1%}")


def print_value_bet_summary(analyses: List[MatchValueAnalysis],
                            has_market_file: bool):
    """Print value-bet highlights to terminal."""
    print("\n" + "=" * 72)
    print("   VALUE BET ANALYSIS — Fair Odds & Market Comparison")
    print("=" * 72)

    if not has_market_file:
        print(f"  No {MARKET_ODDS_CSV} found — created template.")
        print("  Paste decimal odds (1.85) or Polymarket prices (0.65) and re-run.")
    else:
        hits = [a for a in analyses if a.best_value]
        if hits:
            print(f"  {len(hits)} value bet(s) flagged "
                  f"(edge ≥ {VALUE_EDGE_THRESHOLD * 100:.0f}%):")
            for a in hits:
                b = a.best_value
                print(f"    {a.date}  {a.home_team} vs {a.away_team}: "
                      f"{b.label}  model {b.model_prob * 100:.1f}% vs "
                      f"market {b.market_implied * 100:.1f}%  "
                      f"(+{b.edge * 100:.1f}% edge)")
        else:
            print("  No value bets above threshold with current market prices.")

    print(f"  Full fair-odds export: {VALUE_BETS_CSV}")
    print("=" * 72)


# ─────────────────────────────────────────────────────────────────────────────
# 14. LIVE DATA, LINEUPS & CLI
# ─────────────────────────────────────────────────────────────────────────────

def canonical_from_fotmob(name: str) -> str:
    """Map FotMob team name to our canonical GROUPS name."""
    if name in ALL_TEAMS:
        return name
    mapped = FOTMOB_TO_CANONICAL.get(name, name)
    if mapped in ALL_TEAMS:
        return mapped
    return canonicalise(REVERSE_NAME_MAP.get(mapped, mapped))


def _fotmob_get(url: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        import requests
        r = requests.get(
            url, params=params, timeout=20,
            headers={"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36")},
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"[FOTMOB] Request failed ({url}): {exc}")
        return None


def fetch_fotmob_day_matches(day: date) -> List[dict]:
    """Return World Cup matches on a calendar day from FotMob."""
    data = _fotmob_get(f"{FOTMOB_API_BASE}/matches",
                       {"date": day.strftime("%Y%m%d")})
    if not data:
        return []

    fixtures: List[dict] = []
    for league in data.get("leagues", []):
        if "World Cup" not in (league.get("name") or ""):
            continue
        for m in league.get("matches", []):
            home_raw = m.get("home", {}).get("name", "")
            away_raw = m.get("away", {}).get("name", "")
            status = m.get("status", {})
            utc_time = status.get("utcTime")
            kickoff = pd.Timestamp(utc_time) if utc_time else None
            fixtures.append({
                "match_id": int(m["id"]),
                "date": day.isoformat(),
                "home_team": canonical_from_fotmob(home_raw),
                "away_team": canonical_from_fotmob(away_raw),
                "home_raw": home_raw,
                "away_raw": away_raw,
                "kickoff_utc": kickoff.isoformat() if kickoff else "",
                "started": bool(status.get("started")),
                "finished": bool(status.get("finished")),
                "group": league.get("name", ""),
            })
    return fixtures


def fetch_fotmob_world_cup_schedule(
        start: date = WC2026_FIRST_DATE,
        end: date = WC2026_LAST_DATE,
) -> List[dict]:
    """Build full WC schedule with FotMob match IDs and UTC kickoffs."""
    schedule: List[dict] = []
    day = start
    while day <= end:
        schedule.extend(fetch_fotmob_day_matches(day))
        day += timedelta(days=1)
    schedule.sort(key=lambda x: x.get("kickoff_utc", x["date"]))
    return schedule


def save_fixture_schedule(schedule: List[dict],
                          path: Path = FIXTURE_SCHEDULE_JSON) -> Path:
    path.write_text(json.dumps(schedule, indent=2), encoding="utf-8")
    return path


def load_fixture_schedule(
        path: Path = FIXTURE_SCHEDULE_JSON) -> List[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_fotmob_match_lineups(match_id: int) -> Optional[dict]:
    """Return confirmed starters/bench for both teams from FotMob."""
    data = _fotmob_get(f"{FOTMOB_API_BASE}/matchDetails",
                       {"matchId": match_id})
    if not data:
        return None

    lineup = data.get("content", {}).get("lineup", {})
    home = lineup.get("homeTeam", {})
    away = lineup.get("awayTeam", {})
    home_starters = home.get("starters") or []
    away_starters = away.get("starters") or []

    if len(home_starters) < 11 and len(away_starters) < 11:
        return None

    def _names(players: list, subs: list) -> Tuple[List[str], List[str]]:
        starters = [p.get("name", "").strip() for p in players
                    if p.get("name")]
        bench = [p.get("name", "").strip() for p in (subs or [])
                 if p.get("name")]
        return starters[:11], bench

    home_names, home_bench = _names(home_starters, home.get("subs"))
    away_names, away_bench = _names(away_starters, away.get("subs"))

    if len(home_names) < 11 and len(away_names) < 11:
        return None

    general = data.get("general", {})
    home_team = canonical_from_fotmob(
        general.get("homeTeam", {}).get("name") or home.get("name", ""))
    away_team = canonical_from_fotmob(
        general.get("awayTeam", {}).get("name") or away.get("name", ""))

    return {
        "match_id": match_id,
        "home_team": home_team,
        "away_team": away_team,
        "home": {
            "starters": home_names,
            "bench": home_bench,
            "formation": home.get("formation", ""),
        },
        "away": {
            "starters": away_names,
            "bench": away_bench,
            "formation": away.get("formation", ""),
        },
        "source": "FotMob confirmed",
    }


def update_expected_lineups_from_fotmob(
        match_lineups: dict,
        path: Path = EXPECTED_LINEUPS_JSON,
) -> List[str]:
    """Merge FotMob lineups into expected_lineups.json. Returns updated teams."""
    if not path.exists():
        data: dict = {}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    updated: List[str] = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    for side in ("home", "away"):
        team = match_lineups[f"{side}_team"]
        info = match_lineups[side]
        starters = info.get("starters", [])
        if len(starters) < 11:
            continue
        data[team] = {
            "source": f"FotMob confirmed ({ts})",
            "formation": info.get("formation", ""),
            "starters": starters,
            "bench": info.get("bench", []),
        }
        updated.append(team)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return updated


def refresh_lineups_for_upcoming(
        schedule: Optional[List[dict]] = None,
        hours_ahead: float = LINEUP_FETCH_MAX_HOURS,
) -> int:
    """Fetch FotMob lineups for matches starting within hours_ahead."""
    if schedule is None:
        schedule = load_fixture_schedule()
        if not schedule:
            print("[FOTMOB] Building fixture schedule …")
            schedule = fetch_fotmob_world_cup_schedule()
            save_fixture_schedule(schedule)

    now = pd.Timestamp.now(tz="UTC")
    updated_matches = 0

    for fix in schedule:
        if fix.get("finished"):
            continue
        kickoff_raw = fix.get("kickoff_utc")
        if not kickoff_raw:
            continue
        kickoff = pd.Timestamp(kickoff_raw)
        if kickoff.tzinfo is None:
            kickoff = kickoff.tz_localize("UTC")
        hours_until = (kickoff - now).total_seconds() / 3600
        if hours_until < -0.5 or hours_until > hours_ahead:
            continue

        match_id = int(fix["match_id"])
        lineups = fetch_fotmob_match_lineups(match_id)
        if not lineups:
            print(f"  [FOTMOB] No confirmed XI yet: {fix['home_team']} vs "
                  f"{fix['away_team']} (id {match_id})")
            continue

        teams = update_expected_lineups_from_fotmob(lineups)
        if teams:
            updated_matches += 1
            print(f"  [FOTMOB] Updated lineups: {', '.join(teams)} "
                  f"({fix['home_team']} vs {fix['away_team']})")

    return updated_matches


def refresh_lineups_for_match(match_id: int) -> bool:
    """Fetch and save lineups for a single FotMob match ID."""
    lineups = fetch_fotmob_match_lineups(match_id)
    if not lineups:
        return False
    teams = update_expected_lineups_from_fotmob(lineups)
    if teams:
        print(f"[FOTMOB] Updated: {', '.join(teams)}")
    return bool(teams)


def _polymarket_search_names(team: str) -> List[str]:
    return POLYMARKET_SEARCH_NAMES.get(team, [team])


def _parse_polymarket_prices(raw) -> List[float]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [float(p) for p in raw]


def _polymarket_yes_price(market: dict) -> Optional[float]:
    """Extract live Yes price from a Polymarket binary market."""
    prices = _parse_polymarket_prices(market.get("outcomePrices"))
    if not prices:
        return None

    yes = prices[0]
    # Skip clearly settled markets
    if market.get("closed") and (yes >= 0.995 or yes <= 0.005):
        return None

    bid = market.get("bestBid")
    ask = market.get("bestAsk")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2.0
    if ask is not None and 0.0 < float(ask) < 1.0:
        return float(ask)
    if 0.005 < yes < 0.995:
        return yes
    return None


def _event_matches_fixture(event: dict, home: str, away: str,
                           match_date: str) -> bool:
    title = (event.get("title") or "").lower()
    date_str = match_date  # YYYY-MM-DD
    home_names = [n.lower() for n in _polymarket_search_names(home)]
    away_names = [n.lower() for n in _polymarket_search_names(away)]
    has_home = any(n in title for n in home_names)
    has_away = any(n in title for n in away_names)
    if not (has_home and has_away and " vs" in title):
        return False
    slug = (event.get("slug") or "")
    return date_str in slug or date_str.replace("-", "") in slug or (
        "fifwc" in slug or "fif-" in slug)


def fetch_polymarket_fixture_odds(home: str, away: str,
                                  match_date: str) -> Tuple[
                                      Optional[float], Optional[float],
                                      Optional[float]]:
    """Return (home, draw, away) Polymarket prices for one fixture."""
    try:
        import requests
    except ImportError:
        return None, None, None

    queries = [
        f"{home} {away}",
        f"{_polymarket_search_names(home)[0]} "
        f"{_polymarket_search_names(away)[0]}",
    ]

    event = None
    for q in queries:
        try:
            r = requests.get(
                f"{POLYMARKET_GAMMA_URL}/public-search",
                params={"q": q},
                timeout=15,
            )
            r.raise_for_status()
        except Exception:
            continue

        for ev in r.json().get("events", []):
            if _event_matches_fixture(ev, home, away, match_date):
                event = ev
                break
        if event:
            break

    if not event:
        return None, None, None

    home_p = draw_p = away_p = None
    home_names = [n.lower() for n in _polymarket_search_names(home)]
    away_names = [n.lower() for n in _polymarket_search_names(away)]

    for market in event.get("markets", []):
        q = (market.get("question") or "").lower()
        price = _polymarket_yes_price(market)
        if price is None:
            continue
        if "draw" in q:
            draw_p = price
        elif "win on" in q:
            if any(n in q for n in home_names):
                home_p = price
            elif any(n in q for n in away_names):
                away_p = price

    return home_p, draw_p, away_p


def update_market_odds_from_polymarket(
        predictions: List[FixturePrediction],
        horizon_days: int = 7,
        path: Path = MARKET_ODDS_CSV) -> int:
    """Fetch live Polymarket prices and update market_odds.csv."""
    create_market_odds_template(predictions, path)
    df = pd.read_csv(path)
    today = date.today()
    horizon = today + timedelta(days=horizon_days)
    updated = 0
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    for pred in predictions:
        match_day = date.fromisoformat(pred.date)
        if match_day < today or match_day > horizon:
            continue

        home_p, draw_p, away_p = fetch_polymarket_fixture_odds(
            pred.home_team, pred.away_team, pred.date)
        if home_p is None and draw_p is None and away_p is None:
            continue

        mask = (
            (df["date"].astype(str) == pred.date)
            & (df["home_team"] == pred.home_team)
            & (df["away_team"] == pred.away_team)
        )
        if not mask.any():
            continue

        if home_p is not None:
            df.loc[mask, "market_home_decimal"] = round(home_p, 4)
        if draw_p is not None:
            df.loc[mask, "market_draw_decimal"] = round(draw_p, 4)
        if away_p is not None:
            df.loc[mask, "market_away_decimal"] = round(away_p, 4)
        df.loc[mask, "notes"] = f"Polymarket live {ts}"
        updated += 1
        print(f"  [ODDS] {pred.date} {pred.home_team} vs {pred.away_team}: "
              f"{home_p or '-'} / {draw_p or '-'} / {away_p or '-'}")

    df.to_csv(path, index=False)
    return updated


def build_strength_model(df: pd.DataFrame) -> TeamStrengthModel:
    """Compute Elo + squad strength from match dataframe."""
    hist_elo = EloSystem()
    form_elo = EloSystem()
    form_start = form_elo_start_date()

    if not df.empty:
        hist_elo.compute_from_dataframe(df)
        form_elo.compute_from_dataframe(df, start_date=form_start)
    hist_elo.fill_missing_from_rankings(ALL_TEAMS, FIFA_RANKINGS)
    form_elo.fill_missing_from_rankings(ALL_TEAMS, FIFA_RANKINGS)

    squad_system = SquadStrengthSystem().build(ALL_TEAMS)
    return TeamStrengthModel(hist_elo, form_elo, squad_system)


def empty_tournament_records() -> Dict[str, TeamTournamentRecord]:
    return {t: TeamTournamentRecord() for t in ALL_TEAMS}


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FIFA World Cup 2026 prediction & simulation framework",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live mode: refresh match data + Polymarket odds, skip "
             "backtest & Monte Carlo (~30s)",
    )
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="Force re-download international_results.csv from GitHub",
    )
    parser.add_argument(
        "--fetch-odds",
        action="store_true",
        help="Fetch Polymarket prices into market_odds.csv (next 7 days)",
    )
    parser.add_argument(
        "--fetch-lineups",
        action="store_true",
        help="Fetch confirmed lineups from FotMob (matches starting soon)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full pipeline with backtest + 10k Monte Carlo (default)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_cli_args(argv)
    live_mode = args.live
    force_data = args.refresh_data or live_mode
    fetch_odds = args.fetch_odds or live_mode
    fetch_lineups = args.fetch_lineups or live_mode
    full_sim = args.full or (not live_mode and not fetch_lineups)

    np.random.seed(RANDOM_SEED)
    random.seed(RANDOM_SEED)

    n_steps = 8 if full_sim else 4
    print("╔══════════════════════════════════════════════════════════════╗")
    if live_mode:
        print("║   FIFA WORLD CUP 2026 — LIVE UPDATE MODE                  ║")
        print("║   Refresh data · Polymarket odds · Match predictions      ║")
    else:
        print("║      FIFA WORLD CUP 2026 — MONTE CARLO SIMULATOR          ║")
        print("║      48 Teams • 12 Groups • 10,000 Simulations            ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    step = 1
    print(f"\n[{step}/{n_steps}] Loading international match data …")
    df = download_data(force=force_data)
    step += 1

    backtest_results: Optional[Dict[int, BacktestSummary]] = None
    if full_sim:
        print(f"[{step}/{n_steps}] Backtesting model on 2018 & 2022 group stages …")
        backtest_results = run_world_cup_backtests(df)
        save_backtest_results(backtest_results)
        print_backtest_report(backtest_results)
        step += 1
    else:
        print("       (Skipping backtest in live mode)")

    print(f"[{step}/{n_steps}] Computing Elo + squad strength …")
    if fetch_lineups:
        print("       Fetching confirmed lineups from FotMob …")
        n_lineups = refresh_lineups_for_upcoming()
        print(f"       Lineups updated for {n_lineups} match(es)")
    strength = build_strength_model(df)
    if full_sim:
        print_elo_ratings(strength, ALL_TEAMS)
    step += 1

    engine = PoissonMatchEngine(strength)

    if full_sim:
        print(f"\n[{step}/{n_steps}] Initialising Poisson match engine …")
        print("\n  ── Key Opening-Match Previews ──")
        print_key_matchup(engine, "Mexico", "South Africa")
        print_key_matchup(engine, "United States", "Paraguay")
        print_key_matchup(engine, "Canada", "Bosnia Herzegovina")
        print_key_matchup(engine, "Brazil", "Morocco")
        step += 1

    print(f"\n[{step}/{n_steps}] Predicting upcoming World Cup matches …")
    wc_fixtures = load_wc_fixtures(df)
    match_predictions = predict_wc_fixtures(engine, wc_fixtures)

    if fetch_odds:
        print("       Fetching live Polymarket odds …")
        n_odds = update_market_odds_from_polymarket(match_predictions)
        print(f"       Updated {n_odds} fixture(s) in {MARKET_ODDS_CSV}")

    had_market = MARKET_ODDS_CSV.exists()
    create_market_odds_template(match_predictions)
    market_df = load_market_odds()
    value_analyses = analyze_all_value_bets(match_predictions, market_df)

    bet_recommendations = _cap_matchday_stakes(build_bet_recommendations(
        match_predictions, value_analyses))
    save_bet_slip(bet_recommendations)
    create_paper_trade_template()

    save_match_predictions(match_predictions, value_analyses)
    save_value_bets(value_analyses, match_predictions)
    print_match_predictions(match_predictions, strength)
    print_value_bet_summary(value_analyses, had_market)
    print_bet_slip(bet_recommendations)
    step += 1

    records: Dict[str, TeamTournamentRecord]
    sim_summary: SimulationSummary
    if full_sim:
        print(f"\n[{step}/{n_steps}] Running {NUM_SIMULATIONS:,} "
              f"Monte Carlo simulations …")
        records = run_monte_carlo(engine, NUM_SIMULATIONS)
        print("\n  Generating tournament probability reports …\n")
        print_group_probabilities(records, NUM_SIMULATIONS)
        print_tournament_probabilities(records, NUM_SIMULATIONS, strength)
        print_top_contenders(records, NUM_SIMULATIONS)
        step += 1

        played_count = 0
        if not df.empty:
            played = df.dropna(subset=["home_score", "away_score"])
            played = played[(played["date"] >= ELO_START_DATE)
                            & (played["date"] <= ELO_END_DATE)]
            played_count = len(played)
        sim_summary = build_simulation_summary(
            records, NUM_SIMULATIONS, played_count)
        report_path = REPORT_HTML
    else:
        records = empty_tournament_records()
        sim_summary = SimulationSummary(
            champion="— (run full sim)",
            champion_pct=0.0,
            finalist="—",
            finalist_pct=0.0,
            host_best="—",
            host_pct=0.0,
            dark_horse="—",
            dark_horse_pct=0.0,
            matches_processed=0,
            n_simulations=0,
        )
        report_path = LIVE_REPORT_HTML

    print(f"\n[{step}/{n_steps}] Building HTML report …")
    generated_path = generate_html_report(
        strength, match_predictions, value_analyses, records, sim_summary,
        backtest_results=backtest_results,
        bet_recommendations=bet_recommendations,
        live_mode=live_mode,
        path=report_path,
    )

    print("\n" + "=" * 60)
    if full_sim:
        print("         SIMULATION SUMMARY")
        print("=" * 60)
        print(f"  Most likely champion:  {sim_summary.champion} "
              f"({sim_summary.champion_pct:.1f}%)")
        print(f"  Most likely finalist:  {sim_summary.finalist} "
              f"({sim_summary.finalist_pct:.1f}%)")
    else:
        print("         LIVE UPDATE COMPLETE")
        print("=" * 60)
        print(f"  Updated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("  Tournament sim skipped — use default run for champion odds")

    print("\n  ── Output Files ──")
    print(f"  HTML report:       {generated_path.resolve()}")
    print(f"  Bet slip:          {BET_SLIP_CSV.resolve()}")
    print(f"  Match predictions: {MATCH_PREDICTIONS_CSV.resolve()}")
    if full_sim:
        print(f"  Backtest results:  {BACKTEST_CSV.resolve()}")
    print("=" * 60)
    if live_mode:
        print("  Done. Open wc2026_live_report.html for fresh picks.")
    else:
        print("  Done. Open wc2026_report.html in your browser.")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# 15. MAIN EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    raise SystemExit(main())
