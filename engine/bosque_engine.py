import os
import json
import math
import hashlib
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd


# =========================================================
# BOSQUE FOREX AI
# SCALPING ENGINE V5
#
# H1 -> M15 -> M5
#
# V5 OBJECTIVES
# ---------------------------------------------------------
# - Twelve Data M5
# - Local M15 / H1 aggregation
# - H1 structure
# - M15 setup
# - M5 confirmation
# - Liquidity sweep
# - Premium / Discount
# - Market regime
# - ATR volatility
# - Risk engine
# - REAL NEWS FILTER
# - USD/XAUUSD event relevance
# - PRE-NEWS BLOCK
# - POST-NEWS COOLDOWN
# - News cache
# - News stale protection
# - Session context
# - Telegram alerts
# - Signal journal
# - Automatic journal outcome tracking
# - Forward-test statistics
# - Directional SL/TP validation
# - Strict valid-signal gating
# - Backtest / OOS foundation
# =========================================================


# =========================================================
# PATHS
# =========================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"

STATE_FILE = ENGINE_DIR / "state.json"

JOURNAL_FILE = ENGINE_DIR / "trade_journal.json"

BACKTEST_FILE = ENGINE_DIR / "backtest_results.json"

FORWARD_FILE = ENGINE_DIR / "forward_test.json"

NEWS_CACHE_FILE = ENGINE_DIR / "news_cache.json"


# =========================================================
# TWELVE DATA
# =========================================================

TWELVEDATA_URL = (
    "https://api.twelvedata.com/time_series"
)

API_KEY = os.getenv(
    "TWELVEDATA_API_KEY",
    ""
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

SYMBOL = "XAU/USD"

INTERVAL = "5min"

OUTPUT_SIZE = 500


# =========================================================
# NEWS
# =========================================================
#
# IMPORTANT:
#
# News calendar is intentionally separated from
# Twelve Data market-data request.
#
# This protects the Twelve Data quota.
#
# Primary calendar:
# ForexFactory calendar page.
#
# If calendar cannot be retrieved:
# NEWS FEED = STALE / UNKNOWN
#
# The engine will NOT silently assume CLEAR.
# =========================================================

NEWS_CALENDAR_URL = (
    "https://www.forexfactory.com/calendar"
)

NEWS_CACHE_MINUTES = 15

NEWS_STALE_MINUTES = 60

NEWS_PRE_BLOCK_MINUTES = 30

NEWS_POST_COOLDOWN_MINUTES = 15

NEWS_CAUTION_BEFORE_MINUTES = 60

NEWS_CAUTION_AFTER_MINUTES = 30


# =========================================================
# NEWS IMPACT
# =========================================================
#
# USD events that can materially affect XAUUSD.
#
# We intentionally use keyword classification instead
# of relying only on colour/HTML classes.
# =========================================================

HIGH_IMPACT_KEYWORDS = [

    "non-farm payroll",

    "nonfarm payroll",

    "nfp",

    "consumer price index",

    "cpi",

    "core cpi",

    "personal consumption expenditures",

    "core pce",

    "pce price index",

    "fomc",

    "federal funds rate",

    "interest rate decision",

    "fed interest rate",

    "fed rate",

    "powell",

    "fomc press conference",

    "fomc statement",

    "fomc minutes",

    "unemployment rate",

    "initial jobless claims",

    "adp non-farm",

    "adp employment",

    "retail sales",

    "gross domestic product",

    "gdp",

    "ism manufacturing",

    "ism services",

    "ism non-manufacturing",

    "producer price index",

    "ppi",

    "core ppi",

    "durable goods",

    "consumer confidence"
]


MEDIUM_IMPACT_KEYWORDS = [

    "jolts",

    "industrial production",

    "housing starts",

    "building permits",

    "existing home sales",

    "new home sales",

    "trade balance",

    "personal income",

    "personal spending",

    "wholesale inventories",

    "factory orders",

    "pending home sales",

    "michigan consumer sentiment",

    "chicago pmi"
]


# =========================================================
# RISK / SCORING
# =========================================================

MIN_SCORE = 70

# XAUUSD
#
# 1 pip = 0.10 price
#
# Example:
#
# 4156.50 -> 4157.50
# = 10 pips

PIP_SIZE = 0.10

MIN_RISK_PIPS = 25

MAX_RISK_PIPS = 80

TP1_PIPS = 60

MIN_TP2_PIPS = 120

TP3_PIPS = 180

MIN_RR = 2.0


# =========================================================
# SESSION
# Malaysia UTC+8
#
# Session is informational only.
# It NEVER blocks a valid signal.
# =========================================================

MY_TZ = timezone(
    timedelta(hours=8)
)

SESSIONS = [

    ("ASIAN", 7, 15),

    ("LONDON", 15, 20),

    ("NEW YORK", 20, 23),

    ("NEW YORK", 0, 1),
]


# =========================================================
# GLOBAL
# =========================================================

CURRENT_H1 = None


# =========================================================
# BASIC HELPERS
# =========================================================

def now_my():

    return datetime.now(
        timezone.utc
    ).astimezone(
        MY_TZ
    )


def clean_for_json(value):

    if isinstance(value, dict):

        return {
            str(k): clean_for_json(v)
            for k, v in value.items()
        }

    if isinstance(value, list):

        return [
            clean_for_json(v)
            for v in value
        ]

    if isinstance(
        value,
        pd.Timestamp
    ):

        return value.isoformat()

    if isinstance(
        value,
        datetime
    ):

        return value.isoformat()

    if isinstance(
        value,
        float
    ):

        if math.isnan(value):

            return None

        if math.isinf(value):

            return None

    return value


def save_json_atomic(
    path,
    data
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = path.with_suffix(
        ".tmp"
    )

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            clean_for_json(data),
            f,
            ensure_ascii=False,
            indent=2
        )

    tmp.replace(path)


def load_json(path):

    if not path.exists():

        return {}

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def load_state():

    return load_json(
        STATE_FILE
    )


def save_state(state):

    save_json_atomic(
        STATE_FILE,
        state
    )


def price_to_pips(
    distance
):

    return abs(
        distance
    ) / PIP_SIZE


def pips_to_price(
    pips
):

    return (
        pips * PIP_SIZE
    )


def round_price(
    price
):

    return round(
        float(price),
        2
    )


def parse_timestamp(
    value
):

    if not value:

        return None

    try:

        dt = pd.to_datetime(
            value,
            utc=True
        )

        return dt.to_pydatetime()

    except Exception:

        return None


def normalize_text(
    value
):

    if value is None:

        return ""

    text = str(
        value
    ).strip().lower()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text


# =========================================================
# SESSION
# =========================================================

def get_session(
    dt=None
):

    dt = dt or now_my()

    hour = dt.hour

    for (
        name,
        start,
        end
    ) in SESSIONS:

        if start <= hour < end:

            return name

    return "OFF SESSION"


def is_preferred_session(
    session
):

    return session in [

        "LONDON",

        "NEW YORK"
    ]


# =========================================================
# TWELVE DATA
# =========================================================

def fetch_m5():

    if not API_KEY:

        raise RuntimeError(
            "TWELVEDATA_API_KEY missing"
        )

    params = {

        "symbol":
            SYMBOL,

        "interval":
            INTERVAL,

        "outputsize":
            OUTPUT_SIZE,

        "apikey":
            API_KEY,

        "format":
            "JSON"
    }

    response = requests.get(
        TWELVEDATA_URL,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if "values" not in data:

        raise RuntimeError(
            f"Twelve Data error: {data}"
        )

    df = pd.DataFrame(
        data["values"]
    )

    if df.empty:

        raise RuntimeError(
            "No market data returned"
        )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True
    )

    for col in [

        "open",

        "high",

        "low",

        "close"

    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=[

            "datetime",

            "open",

            "high",

            "low",

            "close"
        ]
    )

    df = df.sort_values(
        "datetime"
    )

    df = df.drop_duplicates(
        "datetime"
    )

    df = df.reset_index(
        drop=True
    )

    return df


# =========================================================
# REMOVE INCOMPLETE CANDLE
# =========================================================

def remove_incomplete_candle(
    df
):

    if df.empty:

        return df

    last_time = df.iloc[-1][
        "datetime"
    ]

    if last_time.tzinfo is None:

        last_time = last_time.replace(
            tzinfo=timezone.utc
        )

    now_utc = datetime.now(
        timezone.utc
    )

    elapsed = (
        now_utc - last_time
    ).total_seconds()

    if elapsed < 300:

        return df.iloc[
            :-1
        ].copy()

    return df


# =========================================================
# AGGREGATION
# =========================================================

def aggregate(
    df,
    minutes
):

    x = df.copy()

    x["datetime"] = pd.to_datetime(
        x["datetime"],
        utc=True
    )

    x = x.set_index(
        "datetime"
    )

    rule = f"{minutes}min"

    out = x.resample(
        rule
    ).agg({

        "open":
            "first",

        "high":
            "max",

        "low":
            "min",

        "close":
            "last"
    })

    out = out.dropna()

    out = out.reset_index()

    return out


# =========================================================
# SWINGS
# =========================================================

def find_swing_highs(
    df,
    left=2,
    right=2
):

    highs = []

    if len(df) < (
        left + right + 1
    ):

        return highs

    for i in range(
        left,
        len(df) - right
    ):

        value = float(
            df.iloc[i]["high"]
        )

        left_values = df.iloc[
            i-left:i
        ]["high"]

        right_values = df.iloc[
            i+1:i+right+1
        ]["high"]

        if (
            value >
            left_values.max()

            and

            value >=
            right_values.max()
        ):

            highs.append({

                "index":
                    i,

                "price":
                    value
            })

    return highs


def find_swing_lows(
    df,
    left=2,
    right=2
):

    lows = []

    if len(df) < (
        left + right + 1
    ):

        return lows

    for i in range(
        left,
        len(df) - right
    ):

        value = float(
            df.iloc[i]["low"]
        )

        left_values = df.iloc[
            i-left:i
        ]["low"]

        right_values = df.iloc[
            i+1:i+right+1
        ]["low"]

        if (
            value <
            left_values.min()

            and

            value <=
            right_values.min()
        ):

            lows.append({

                "index":
                    i,

                "price":
                    value
            })

    return lows


# =========================================================
# STRUCTURE
# =========================================================

def analyze_structure(
    df
):

    highs = find_swing_highs(
        df
    )

    lows = find_swing_lows(
        df
    )

    direction = "RANGE"

    bos = None

    if (
        len(highs) >= 2

        and

        len(lows) >= 2
    ):

        h1 = highs[-2]["price"]

        h2 = highs[-1]["price"]

        l1 = lows[-2]["price"]

        l2 = lows[-1]["price"]

        if (
            h2 > h1

            and

            l2 > l1
        ):

            direction = "BULLISH"

        elif (
            h2 < h1

            and

            l2 < l1
        ):

            direction = "BEARISH"

    current_close = float(
        df.iloc[-1]["close"]
    )

    if highs:

        latest_high = highs[-1][
            "price"
        ]

        if current_close > latest_high:

            bos = "BULLISH BOS"

    if lows:

        latest_low = lows[-1][
            "price"
        ]

        if current_close < latest_low:

            bos = "BEARISH BOS"

    return {

        "direction":
            direction,

        "bos":
            bos,

        "swing_high":
            (
                highs[-1]["price"]
                if highs
                else None
            ),

        "swing_low":
            (
                lows[-1]["price"]
                if lows
                else None
            ),

        "swing_highs_count":
            len(highs),

        "swing_lows_count":
            len(lows)
    }


# =========================================================
# RANGE
# =========================================================

def analyze_range(
    df,
    lookback=20
):

    if len(df) < lookback:

        return {

            "is_range":
                False,

            "high":
                None,

            "low":
                None,

            "width":
                None,

            "location":
                None
        }

    x = df.tail(
        lookback
    )

    high = float(
        x["high"].max()
    )

    low = float(
        x["low"].min()
    )

    close = float(
        x.iloc[-1]["close"]
    )

    width = high - low

    location = (
        (close - low) / width
        if width > 0
        else 0.5
    )

    is_range = (

        0.20 <= location <= 0.80

        or

        width < close * 0.01
    )

    return {

        "is_range":
            bool(is_range),

        "high":
            high,

        "low":
            low,

        "width":
            width,

        "location":
            location
    }


# =========================================================
# PD ZONE
# =========================================================

def pd_zone(
    df,
    lookback=20
):

    if len(df) < 5:

        return {

            "zone":
                "UNKNOWN",

            "equilibrium":
                None,

            "high":
                None,

            "low":
                None
        }

    x = df.tail(
        lookback
    )

    high = float(
        x["high"].max()
    )

    low = float(
        x["low"].min()
    )

    equilibrium = (
        high + low
    ) / 2

    close = float(
        x.iloc[-1]["close"]
    )

    if close > equilibrium:

        zone = "PREMIUM"

    elif close < equilibrium:

        zone = "DISCOUNT"

    else:

        zone = "EQUILIBRIUM"

    return {

        "zone":
            zone,

        "equilibrium":
            round_price(
                equilibrium
            ),

        "high":
            round_price(
                high
            ),

        "low":
            round_price(
                low
            )
    }


# =========================================================
# LIQUIDITY
# =========================================================

def liquidity_analysis(
    df
):

    highs = find_swing_highs(
        df
    )

    lows = find_swing_lows(
        df
    )

    result = {

        "buy_side_sweep":
            False,

        "sell_side_sweep":
            False,

        "swept_level":
            None,

        "description":
            "NONE"
    }

    if highs:

        swing_high = highs[-1][
            "price"
        ]

        current_high = float(
            df.iloc[-1]["high"]
        )

        current_close = float(
            df.iloc[-1]["close"]
        )

        if (
            current_high > swing_high

            and

            current_close < swing_high
        ):

            result[
                "buy_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = swing_high

            result[
                "description"
            ] = (
                "BUY-SIDE LIQUIDITY SWEPT"
            )

    if lows:

        swing_low = lows[-1][
            "price"
        ]

        current_low = float(
            df.iloc[-1]["low"]
        )

        current_close = float(
            df.iloc[-1]["close"]
        )

        if (
            current_low < swing_low

            and

            current_close > swing_low
        ):

            result[
                "sell_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = swing_low

            result[
                "description"
            ] = (
                "SELL-SIDE LIQUIDITY SWEPT"
            )

    return result


# =========================================================
# MOMENTUM
# =========================================================

def momentum(
    df,
    lookback=6
):

    if len(df) < (
        lookback + 1
    ):

        return {

            "direction":
                "NEUTRAL",

            "strength":
                0
        }

    closes = df[
        "close"
    ].tail(
        lookback + 1
    ).tolist()

    up = 0

    down = 0

    for i in range(
        1,
        len(closes)
    ):

        if closes[i] > closes[i-1]:

            up += 1

        elif closes[i] < closes[i-1]:

            down += 1

    if up >= 4:

        return {

            "direction":
                "BULLISH",

            "strength":
                up
        }

    if down >= 4:

        return {

            "direction":
                "BEARISH",

            "strength":
                down
        }

    return {

        "direction":
            "NEUTRAL",

        "strength":
            max(
                up,
                down
            )
    }


# =========================================================
# CANDLE CONFIRMATION
# =========================================================

def candle_confirmation(
    df
):

    c = df.iloc[-1]

    body = abs(
        float(c["close"])
        -
        float(c["open"])
    )

    full_range = (
        float(c["high"])
        -
        float(c["low"])
    )

    if full_range <= 0:

        return {

            "bullish":
                False,

            "bearish":
                False
        }

    ratio = (
        body / full_range
    )

    bullish = (
        c["close"] > c["open"]

        and

        ratio >= 0.55
    )

    bearish = (
        c["close"] < c["open"]

        and

        ratio >= 0.55
    )

    return {

        "bullish":
            bool(bullish),

        "bearish":
            bool(bearish)
    }


# =========================================================
# ATR
# =========================================================

def calculate_atr(
    df,
    period=14
):

    if len(df) < (
        period + 1
    ):

        return None

    x = df.copy()

    prev_close = (
        x["close"].shift(1)
    )

    tr1 = (
        x["high"]
        -
        x["low"]
    )

    tr2 = abs(
        x["high"]
        -
        prev_close
    )

    tr3 = abs(
        x["low"]
        -
        prev_close
    )

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(
        axis=1
    )

    atr = (
        tr.rolling(period)
        .mean()
        .iloc[-1]
    )

    if pd.isna(atr):

        return None

    return float(atr)


def volatility_analysis(
    df
):

    atr = calculate_atr(
        df
    )

    if atr is None:

        return {

            "atr":
                None,

            "atr_pips":
                None,

            "condition":
                "UNKNOWN"
        }

    atr_pips = price_to_pips(
        atr
    )

    if atr_pips < 25:

        condition = "LOW"

    elif atr_pips <= 70:

        condition = "NORMAL"

    else:

        condition = "HIGH"

    return {

        "atr":
            round_price(
                atr
            ),

        "atr_pips":
            round(
                atr_pips,
                1
            ),

        "condition":
            condition
    }


# =========================================================
# M15 SETUP
# =========================================================

def detect_m15_setup(
    h1,
    m15,
    m5
):

    h1_direction = (
        h1["direction"]
    )

    m15_structure = (
        analyze_structure(
            m15
        )
    )

    m15_pd = pd_zone(
        m15
    )

    m15_liquidity = (
        liquidity_analysis(
            m15
        )
    )

    direction = None

    opportunity = (
        "NO VALID SETUP"
    )

    reason = []

    range_info = analyze_range(
        m15
    )

    # -----------------------------------------------------
    # RANGE REVERSAL
    # -----------------------------------------------------

    if range_info["is_range"]:

        if (
            m15_liquidity[
                "sell_side_sweep"
            ]

            and

            range_info[
                "location"
            ] <= 0.25
        ):

            direction = "BUY"

            opportunity = (
                "BUY RANGE REVERSAL"
            )

            reason.append(
                "M15 range low + sell-side sweep"
            )

        elif (
            m15_liquidity[
                "buy_side_sweep"
            ]

            and

            range_info[
                "location"
            ] >= 0.75
        ):

            direction = "SELL"

            opportunity = (
                "SELL RANGE REVERSAL"
            )

            reason.append(
                "M15 range high + buy-side sweep"
            )

    # -----------------------------------------------------
    # LIQUIDITY SWEEP
    # -----------------------------------------------------

    if direction is None:

        if m15_liquidity[
            "sell_side_sweep"
        ]:

            direction = "BUY"

            opportunity = (
                "BUY LIQUIDITY SWEEP"
            )

            reason.append(
                "M15 sell-side liquidity sweep"
            )

        elif m15_liquidity[
            "buy_side_sweep"
        ]:

            direction = "SELL"

            opportunity = (
                "SELL LIQUIDITY SWEEP"
            )

            reason.append(
                "M15 buy-side liquidity sweep"
            )

    # -----------------------------------------------------
    # TREND PULLBACK
    # -----------------------------------------------------

    if direction is None:

        if (
            h1_direction == "BULLISH"

            and

            m15_pd["zone"]
            == "DISCOUNT"
        ):

            direction = "BUY"

            opportunity = (
                "BUY PULLBACK"
            )

            reason.append(
                "H1 bullish + M15 discount"
            )

        elif (
            h1_direction == "BEARISH"

            and

            m15_pd["zone"]
            == "PREMIUM"
        ):

            direction = "SELL"

            opportunity = (
                "SELL PULLBACK"
            )

            reason.append(
                "H1 bearish + M15 premium"
            )

    # -----------------------------------------------------
    # BREAKOUT
    # -----------------------------------------------------

    if direction is None:

        if (
            m15_structure["bos"]
            ==
            "BULLISH BOS"
        ):

            direction = "BUY"

            opportunity = (
                "BUY BREAKOUT RETEST"
            )

            reason.append(
                "M15 bullish BOS"
            )

        elif (
            m15_structure["bos"]
            ==
            "BEARISH BOS"
        ):

            direction = "SELL"

            opportunity = (
                "SELL BREAKOUT RETEST"
            )

            reason.append(
                "M15 bearish BOS"
            )

    return {

        "direction":
            direction,

        "opportunity":
            opportunity,

        "valid":
            direction is not None,

        "reason":
            reason,

        "pd":
            m15_pd,

        "liquidity":
            m15_liquidity,

        "structure":
            m15_structure,

        "range":
            range_info
    }


# =========================================================
# M5 CONFIRMATION
# =========================================================

def m5_confirmation(
    df,
    expected_direction
):

    structure = analyze_structure(
        df
    )

    momentum_data = momentum(
        df
    )

    candle = candle_confirmation(
        df
    )

    bos = structure["bos"]

    if expected_direction == "BUY":

        bos_ok = (
            bos == "BULLISH BOS"
        )

        candle_ok = (
            candle["bullish"]

            and

            momentum_data[
                "direction"
            ]
            ==
            "BULLISH"
        )

        confirmed = (
            bos_ok
            or
            candle_ok
        )

        return {

            "confirmed":
                bool(confirmed),

            "bos":
                bos,

            "candle":
                candle_ok,

            "momentum":
                momentum_data[
                    "direction"
                ],

            "reason": (

                "M5 bullish BOS"

                if bos_ok

                else

                "M5 bullish candle + momentum"

                if candle_ok

                else

                "NO CONFIRMATION"
            )
        }

    if expected_direction == "SELL":

        bos_ok = (
            bos == "BEARISH BOS"
        )

        candle_ok = (
            candle["bearish"]

            and

            momentum_data[
                "direction"
            ]
            ==
            "BEARISH"
        )

        confirmed = (
            bos_ok
            or
            candle_ok
        )

        return {

            "confirmed":
                bool(confirmed),

            "bos":
                bos,

            "candle":
                candle_ok,

            "momentum":
                momentum_data[
                    "direction"
                ],

            "reason": (

                "M5 bearish BOS"

                if bos_ok

                else

                "M5 bearish candle + momentum"

                if candle_ok

                else

                "NO CONFIRMATION"
            )
        }

    return {

        "confirmed":
            False,

        "bos":
            bos,

        "candle":
            False,

        "momentum":
            momentum_data[
                "direction"
            ],

        "reason":
            "NO DIRECTION"
    }


# =========================================================
# SCORE
# =========================================================

def calculate_score(
    h1,
    m15_setup,
    m5_confirm,
    session,
    volatility
):

    score = 0

    if h1["direction"] in [

        "BULLISH",

        "BEARISH"

    ]:

        score += 15

    if h1["bos"]:

        score += 5

    if m15_setup["valid"]:

        score += 10

    if (
        m15_setup[
            "liquidity"
        ].get(
            "buy_side_sweep"
        )

        or

        m15_setup[
            "liquidity"
        ].get(
            "sell_side_sweep"
        )
    ):

        score += 10

    if (
        m15_setup[
            "structure"
        ].get(
            "bos"
        )
    ):

        score += 10

    if m5_confirm[
        "confirmed"
    ]:

        score += 15

    if m5_confirm[
        "bos"
    ]:

        score += 10

    if m5_confirm[
        "candle"
    ]:

        score += 10

    zone = m15_setup[
        "pd"
    ]["zone"]

    direction = (
        m15_setup[
            "direction"
        ]
    )

    if (
        direction == "BUY"

        and

        zone == "DISCOUNT"
    ):

        score += 5

    elif (
        direction == "SELL"

        and

        zone == "PREMIUM"
    ):

        score += 5

    if is_preferred_session(
        session
    ):

        score += 5

    if (
        volatility[
            "condition"
        ]
        ==
        "NORMAL"
    ):

        score += 5

    return min(
        score,
        100
    )


# =========================================================
# TRADE PLAN
# =========================================================

def create_trade_plan(
    direction,
    m5,
    score,
    volatility
):

    if direction not in [

        "BUY",

        "SELL"

    ]:

        return {

            "valid":
                False,

            "reason":
                "NO VALID DIRECTION"
        }

    entry = float(
        m5.iloc[-1]["close"]
    )

    structure = analyze_structure(
        m5
    )

    swing_low = (
        structure["swing_low"]
    )

    swing_high = (
        structure["swing_high"]
    )

    atr = volatility.get(
        "atr"
    )

    buffer = 0.20

    if atr:

        buffer = max(
            0.20,
            float(atr) * 0.20
        )

    # -----------------------------------------------------
    # BUY
    # -----------------------------------------------------

    if direction == "BUY":

        if swing_low is None:

            return {

                "valid":
                    False,

                "reason":
                    "BUY requires swing low"
            }

        if float(swing_low) >= entry:

            return {

                "valid":
                    False,

                "reason":
                    (
                        "BUY invalid: "
                        "swing low is not below entry"
                    ),

                "entry":
                    round_price(entry),

                "swing_low":
                    round_price(swing_low)
            }

        sl = (
            float(swing_low)
            -
            buffer
        )

    # -----------------------------------------------------
    # SELL
    # -----------------------------------------------------

    else:

        if swing_high is None:

            return {

                "valid":
                    False,

                "reason":
                    "SELL requires swing high"
            }

        if float(swing_high) <= entry:

            return {

                "valid":
                    False,

                "reason":
                    (
                        "SELL invalid: "
                        "swing high is not above entry"
                    ),

                "entry":
                    round_price(entry),

                "swing_high":
                    round_price(swing_high)
            }

        sl = (
            float(swing_high)
            +
            buffer
        )

    risk_price = abs(
        entry - sl
    )

    risk_pips = price_to_pips(
        risk_price
    )

    if (
        risk_pips < MIN_RISK_PIPS

        or

        risk_pips > MAX_RISK_PIPS
    ):

        return {

            "valid":
                False,

            "reason":
                (
                    f"Risk {risk_pips:.1f} pips "
                    f"outside "
                    f"{MIN_RISK_PIPS}-"
                    f"{MAX_RISK_PIPS}"
                ),

            "entry":
                round_price(entry),

            "sl":
                round_price(sl),

            "risk_pips":
                round(
                    risk_pips,
                    1
                )
        }

    if direction == "BUY":

        tp1_price = (
            entry
            +
            pips_to_price(
                TP1_PIPS
            )
        )

    else:

        tp1_price = (
            entry
            -
            pips_to_price(
                TP1_PIPS
            )
        )

    tp2_pips = max(
        MIN_TP2_PIPS,
        risk_pips * 2
    )

    tp3_pips = max(
        TP3_PIPS,
        risk_pips * 3
    )

    if direction == "BUY":

        tp2_price = (
            entry
            +
            pips_to_price(
                tp2_pips
            )
        )

        tp3_price = (
            entry
            +
            pips_to_price(
                tp3_pips
            )
        )

    else:

        tp2_price = (
            entry
            -
            pips_to_price(
                tp2_pips
            )
        )

        tp3_price = (
            entry
            -
            pips_to_price(
                tp3_pips
            )
        )

    rr_tp1 = (
        TP1_PIPS
        /
        risk_pips
    )

    rr_tp2 = (
        tp2_pips
        /
        risk_pips
    )

    rr_tp3 = (
        tp3_pips
        /
        risk_pips
    )

    geometry_ok = True

    if direction == "BUY":

        if not (

            sl < entry

            and

            tp1_price > entry

            and

            tp2_price > entry

            and

            tp3_price > entry

        ):

            geometry_ok = False

    elif direction == "SELL":

        if not (

            sl > entry

            and

            tp1_price < entry

            and

            tp2_price < entry

            and

            tp3_price < entry

        ):

            geometry_ok = False

    if not geometry_ok:

        return {

            "valid":
                False,

            "reason":
                "TRADE PLAN GEOMETRY INVALID"
        }

    if rr_tp2 < MIN_RR:

        return {

            "valid":
                False,

            "reason":
                "TP2 RR below minimum"
        }

    return {

        "valid":
            True,

        "direction":
            direction,

        "entry":
            round_price(
                entry
            ),

        "sl":
            round_price(
                sl
            ),

        "risk_pips":
            round(
                risk_pips,
                1
            ),

        "tp1":
            round_price(
                tp1_price
            ),

        "tp2":
            round_price(
                tp2_price
            ),

        "tp3":
            round_price(
                tp3_price
            ),

        "tp1_pips":
            round(
                TP1_PIPS,
                1
            ),

        "tp2_pips":
            round(
                tp2_pips,
                1
            ),

        "tp3_pips":
            round(
                tp3_pips,
                1
            ),

        "rr_tp1":
            round(
                rr_tp1,
                2
            ),

        "rr_tp2":
            round(
                rr_tp2,
                2
            ),

        "rr_tp3":
            round(
                rr_tp3,
                2
            ),

        "risk_level": (

            "LOW"

            if risk_pips <= 40

            else

            "MEDIUM"
        ),

        "geometry":
            "VALID"
    }


# =========================================================
# NEWS ENGINE
# =========================================================

def classify_news_event(
    event_name,
    impact=None
):

    name = normalize_text(
        event_name
    )

    impact_text = normalize_text(
        impact
    )

    # Explicit high impact from calendar.
    if (
        "high" in impact_text

        or

        "red" in impact_text

        or

        "3" == impact_text

    ):

        return "HIGH"

    for keyword in HIGH_IMPACT_KEYWORDS:

        if keyword in name:

            return "HIGH"

    for keyword in MEDIUM_IMPACT_KEYWORDS:

        if keyword in name:

            return "MEDIUM"

    return "LOW"


def is_xau_relevant_event(
    currency,
    event_name
):

    currency = (
        str(currency)
        .strip()
        .upper()
    )

    name = normalize_text(
        event_name
    )

    # USD is the main macro driver
    # for this news filter.
    if currency == "USD":

        return True

    # Gold can react to certain global
    # central-bank / geopolitical events,
    # but we do not block them automatically
    # unless explicitly classified.
    global_keywords = [

        "federal reserve",

        "powell",

        "fomc",

        "interest rate",

        "central bank"
    ]

    for keyword in global_keywords:

        if keyword in name:

            return True

    return False


def parse_news_datetime(
    value
):

    if value is None:

        return None

    text = str(
        value
    ).strip()

    if not text:

        return None

    # Direct ISO parsing first.
    try:

        dt = pd.to_datetime(
            text,
            utc=True
        )

        if not pd.isna(dt):

            return dt.to_pydatetime()

    except Exception:

        pass

    return None


def extract_calendar_rows(
    html
):

    """
    Attempts to parse ForexFactory's calendar
    using pandas HTML tables.

    This is intentionally defensive because
    website HTML can change.
    """

    rows = []

    try:

        tables = pd.read_html(
            html
        )

    except Exception:

        tables = []

    for table in tables:

        if table.empty:

            continue

        columns = [
            normalize_text(
                c
            )
            for c in table.columns
        ]

        table_text = " ".join(
            columns
        )

        # We only want calendar-like tables.
        if not any(
            token in table_text
            for token in [
                "currency",
                "impact",
                "event",
                "actual",
                "forecast",
                "previous"
            ]
        ):

            continue

        for _, row in table.iterrows():

            values = {

                normalize_text(
                    col
                ):
                row[col]

                for col in table.columns
            }

            event_name = ""

            currency = ""

            impact = ""

            for key, value in values.items():

                if (
                    "event" in key
                    or
                    "detail" in key
                ):

                    event_name = str(
                        value
                    )

                elif (
                    "currency" in key
                ):

                    currency = str(
                        value
                    )

                elif (
                    "impact" in key
                ):

                    impact = str(
                        value
                    )

            if not event_name:

                # Fallback:
                # Search entire row.
                event_name = " ".join(
                    str(v)
                    for v in values.values()
                )

            if not event_name:

                continue

            rows.append({

                "currency":
                    currency,

                "event":
                    event_name,

                "impact":
                    classify_news_event(
                        event_name,
                        impact
                    )
            })

    return rows


def parse_calendar_with_regex(
    html
):

    """
    Secondary defensive parser.

    This does not guarantee complete extraction,
    but can recover event names/currency from
    common HTML structures.
    """

    rows = []

    # Remove scripts/styles.
    text = re.sub(
        r"<script.*?</script>",
        " ",
        html,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    text_lower = text.lower()

    currencies = [
        "USD"
    ]

    for currency in currencies:

        # Find windows around USD.
        for match in re.finditer(
            currency.lower(),
            text_lower
        ):

            start = max(
                0,
                match.start() - 300
            )

            end = min(
                len(text),
                match.end() + 500
            )

            window = text[
                start:end
            ]

            window_lower = (
                window.lower()
            )

            for keyword in (
                HIGH_IMPACT_KEYWORDS
                +
                MEDIUM_IMPACT_KEYWORDS
            ):

                if keyword in window_lower:

                    rows.append({

                        "currency":
                            currency,

                        "event":
                            keyword,

                        "impact":
                            classify_news_event(
                                keyword
                            )
                    })

    return rows


def fetch_news_calendar():

    now = now_my()

    cache = load_json(
        NEWS_CACHE_FILE
    )

    cached_at = parse_timestamp(
        cache.get(
            "cached_at"
        )
    )

    # -----------------------------------------------------
    # USE CACHE
    # -----------------------------------------------------

    if cached_at:

        age = (
            datetime.now(
                timezone.utc
            )
            -
            cached_at
        ).total_seconds() / 60

        if age <= NEWS_CACHE_MINUTES:

            return {

                "status":
                    "CACHE",

                "source":
                    cache.get(
                        "source",
                        "ForexFactory"
                    ),

                "events":
                    cache.get(
                        "events",
                        []
                    ),

                "cached_at":
                    cache.get(
                        "cached_at"
                    ),

                "age_minutes":
                    round(
                        age,
                        1
                    ),

                "error":
                    None
            }

    # -----------------------------------------------------
    # FETCH
    # -----------------------------------------------------

    try:

        response = requests.get(

            NEWS_CALENDAR_URL,

            params={

                "range":
                    "today"
            },

            headers={

                "User-Agent":
                    (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "Chrome/120 Safari/537.36"
                    )
            },

            timeout=15
        )

        response.raise_for_status()

        html = response.text

        rows = extract_calendar_rows(
            html
        )

        # Regex fallback.
        if not rows:

            rows = parse_calendar_with_regex(
                html
            )

        # Keep only relevant USD/XAU events.
        filtered = []

        seen = set()

        for row in rows:

            currency = (
                row.get(
                    "currency",
                    ""
                )
                .strip()
                .upper()
            )

            event = (
                row.get(
                    "event",
                    ""
                )
                .strip()
            )

            impact = (
                row.get(
                    "impact",
                    "LOW"
                )
                .upper()
            )

            if not event:

                continue

            if not is_xau_relevant_event(
                currency,
                event
            ):

                continue

            if impact not in [

                "HIGH",

                "MEDIUM"

            ]:

                continue

            key = (
                currency,
                normalize_text(event),
                impact
            )

            if key in seen:

                continue

            seen.add(key)

            filtered.append({

                "currency":
                    currency,

                "event":
                    event,

                "impact":
                    impact
            })

        # Save cache.
        cache_data = {

            "status":
                "OK",

            "source":
                "ForexFactory",

            "cached_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "events":
                filtered,

            "error":
                None
        }

        save_json_atomic(
            NEWS_CACHE_FILE,
            cache_data
        )

        return {

            "status":
                "LIVE",

            "source":
                "ForexFactory",

            "events":
                filtered,

            "cached_at":
                cache_data[
                    "cached_at"
                ],

            "age_minutes":
                0,

            "error":
                None
        }

    except Exception as exc:

        # -------------------------------------------------
        # FALLBACK TO LAST CACHE
        # -------------------------------------------------

        if cache:

            cached_at = parse_timestamp(
                cache.get(
                    "cached_at"
                )
            )

            age_minutes = None

            if cached_at:

                age_minutes = (
                    datetime.now(
                        timezone.utc
                    )
                    -
                    cached_at
                ).total_seconds()
                / 60

            return {

                "status":
                    "STALE",

                "source":
                    cache.get(
                        "source",
                        "ForexFactory"
                    ),

                "events":
                    cache.get(
                        "events",
                        []
                    ),

                "cached_at":
                    cache.get(
                        "cached_at"
                    ),

                "age_minutes":
                    (
                        round(
                            age_minutes,
                            1
                        )
                        if age_minutes
                        is not None
                        else
                        None
                    ),

                "error":
                    str(exc)
            }

        return {

            "status":
                "ERROR",

            "source":
                "ForexFactory",

            "events":
                [],

            "cached_at":
                None,

            "age_minutes":
                None,

            "error":
                str(exc)
        }


def infer_event_datetime(
    event,
    reference_time
):

    """
    Calendar HTML can differ by version.

    This function attempts to locate a datetime
    if available. It also accepts explicit datetime
    fields if future calendar parsing provides them.
    """

    for key in [

        "datetime",

        "timestamp",

        "event_datetime",

        "time"

    ]:

        value = event.get(
            key
        )

        if value:

            dt = parse_news_datetime(
                value
            )

            if dt:

                return dt

    return None


def news_window_status(
    event_time,
    now_utc
):

    if event_time is None:

        return {

            "status":
                "UNKNOWN",

            "minutes":
                None,

            "block":
                True,

            "reason":
                "Event time unavailable"
        }

    diff_minutes = (
        event_time
        -
        now_utc
    ).total_seconds()
    / 60

    # -----------------------------------------------------
    # PRE-NEWS
    # -----------------------------------------------------

    if (
        0
        <=
        diff_minutes
        <=
        NEWS_PRE_BLOCK_MINUTES
    ):

        return {

            "status":
                "BLOCK",

            "minutes":
                round(
                    diff_minutes,
                    1
                ),

            "block":
                True,

            "reason":
                "HIGH IMPACT NEWS APPROACHING"
        }

    # -----------------------------------------------------
    # CAUTION BEFORE
    # -----------------------------------------------------

    if (
        NEWS_PRE_BLOCK_MINUTES
        <
        diff_minutes
        <=
        NEWS_CAUTION_BEFORE_MINUTES
    ):

        return {

            "status":
                "CAUTION",

            "minutes":
                round(
                    diff_minutes,
                    1
                ),

            "block":
                False,

            "reason":
                "HIGH IMPACT NEWS NEARBY"
        }

    # -----------------------------------------------------
    # POST NEWS
    # -----------------------------------------------------

    minutes_after = (
        -diff_minutes
    )

    if (
        0
        <
        minutes_after
        <=
        NEWS_POST_COOLDOWN_MINUTES
    ):

        return {

            "status":
                "BLOCK",

            "minutes":
                round(
                    minutes_after,
                    1
                ),

            "block":
                True,

            "reason":
                "POST-NEWS COOLDOWN"
        }

    # -----------------------------------------------------
    # POST NEWS CAUTION
    # -----------------------------------------------------

    if (
        NEWS_POST_COOLDOWN_MINUTES
        <
        minutes_after
        <=
        NEWS_CAUTION_AFTER_MINUTES
    ):

        return {

            "status":
                "CAUTION",

            "minutes":
                round(
                    minutes_after,
                    1
                ),

            "block":
                False,

            "reason":
                "POST-NEWS VOLATILITY WINDOW"
        }

    return {

        "status":
            "CLEAR",

        "minutes":
            round(
                diff_minutes,
                1
            ),

        "block":
            False,

        "reason":
            "Outside news window"
    }


def evaluate_news_filter(
    calendar_data,
    now=None
):

    now = now or now_my()

    now_utc = (
        now.astimezone(
            timezone.utc
        )
    )

    feed_status = (
        calendar_data.get(
            "status",
            "ERROR"
        )
    )

    events = (
        calendar_data.get(
            "events",
            []
        )
    )

    # -----------------------------------------------------
    # FEED ERROR
    # -----------------------------------------------------
    #
    # We do NOT silently pass.
    # -----------------------------------------------------

    if feed_status == "ERROR":

        return {

            "ok":
                False,

            "status":
                "BLOCK",

            "feed_status":
                "ERROR",

            "source":
                calendar_data.get(
                    "source"
                ),

            "event":
                None,

            "currency":
                None,

            "impact":
                None,

            "minutes":
                None,

            "reason":
                "NEWS FEED UNAVAILABLE",

            "error":
                calendar_data.get(
                    "error"
                )
        }

    # -----------------------------------------------------
    # STALE FEED
    # -----------------------------------------------------

    age = calendar_data.get(
        "age_minutes"
    )

    if (
        feed_status == "STALE"

        and

        (
            age is None

            or

            age > NEWS_STALE_MINUTES
        )
    ):

        return {

            "ok":
                False,

            "status":
                "BLOCK",

            "feed_status":
                "STALE",

            "source":
                calendar_data.get(
                    "source"
                ),

            "event":
                None,

            "currency":
                None,

            "impact":
                None,

            "minutes":
                None,

            "reason":
                "NEWS FEED TOO STALE",

            "error":
                calendar_data.get(
                    "error"
                )
        }

    # -----------------------------------------------------
    # FIND RELEVANT EVENTS
    # -----------------------------------------------------

    best = None

    caution_events = []

    for event in events:

        impact = (
            event.get(
                "impact",
                "LOW"
            )
            .upper()
        )

        if impact != "HIGH":

            continue

        event_time = infer_event_datetime(
            event,
            now
        )

        # If event time isn't available,
        # do not automatically block.
        #
        # But mark it as unknown.
        if event_time is None:

            event_copy = dict(
                event
            )

            event_copy[
                "window_status"
            ] = "UNKNOWN"

            caution_events.append(
                event_copy
            )

            continue

        window = news_window_status(
            event_time,
            now_utc
        )

        event_copy = dict(
            event
        )

        event_copy[
            "datetime"
        ] = event_time.isoformat()

        event_copy[
            "window_status"
        ] = window[
            "status"
        ]

        event_copy[
            "minutes"
        ] = window[
            "minutes"
        ]

        event_copy[
            "window_reason"
        ] = window[
            "reason"
        ]

        if window["block"]:

            best = event_copy

            break

        if (
            window["status"]
            ==
            "CAUTION"
        ):

            caution_events.append(
                event_copy
            )

    # -----------------------------------------------------
    # BLOCK
    # -----------------------------------------------------

    if best:

        return {

            "ok":
                False,

            "status":
                "BLOCK",

            "feed_status":
                feed_status,

            "source":
                calendar_data.get(
                    "source"
                ),

            "event":
                best.get(
                    "event"
                ),

            "currency":
                best.get(
                    "currency"
                ),

            "impact":
                best.get(
                    "impact"
                ),

            "minutes":
                best.get(
                    "minutes"
                ),

            "event_datetime":
                best.get(
                    "datetime"
                ),

            "reason":
                best.get(
                    "window_reason"
                ),

            "error":
                calendar_data.get(
                    "error"
                )
        }

    # -----------------------------------------------------
    # CAUTION
    # -----------------------------------------------------

    if caution_events:

        nearest = min(

            caution_events,

            key=lambda x:
            abs(
                float(
                    x.get(
                        "minutes",
                        999999
                    )
                )
            )
        )

        return {

            "ok":
                True,

            "status":
                "CAUTION",

            "feed_status":
                feed_status,

            "source":
                calendar_data.get(
                    "source"
                ),

            "event":
                nearest.get(
                    "event"
                ),

            "currency":
                nearest.get(
                    "currency"
                ),

            "impact":
                nearest.get(
                    "impact"
                ),

            "minutes":
                nearest.get(
                    "minutes"
                ),

            "event_datetime":
                nearest.get(
                    "datetime"
                ),

            "reason":
                nearest.get(
                    "window_reason",
                    "HIGH IMPACT NEWS NEARBY"
                ),

            "error":
                calendar_data.get(
                    "error"
                )
        }

    # -----------------------------------------------------
    # UNKNOWN EVENTS
    # -----------------------------------------------------

    if caution_events and feed_status in [
        "STALE"
    ]:

        return {

            "ok":
                False,

            "status":
                "BLOCK",

            "feed_status":
                feed_status,

            "source":
                calendar_data.get(
                    "source"
                ),

            "event":
                None,

            "currency":
                None,

            "impact":
                None,

            "minutes":
                None,

            "event_datetime":
                None,

            "reason":
                "NEWS CALENDAR STALE",

            "error":
                calendar_data.get(
                    "error"
                )
        }

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    return {

        "ok":
            True,

        "status":
            "PASS",

        "feed_status":
            feed_status,

        "source":
            calendar_data.get(
                "source"
            ),

        "event":
            None,

        "currency":
            None,

        "impact":
            None,

        "minutes":
            None,

        "event_datetime":
            None,

        "reason":
            "NO HIGH IMPACT XAUUSD NEWS IN BLOCK WINDOW",

        "error":
            calendar_data.get(
                "error"
            )
    }


# =========================================================
# NEWS SUMMARY
# =========================================================

def build_news_summary(
    calendar_data,
    news_result
):

    events = (
        calendar_data.get(
            "events",
            []
        )
    )

    high_events = [

        e for e in events

        if e.get(
            "impact"
        ) == "HIGH"
    ]

    return {

        "status":
            news_result.get(
                "status"
            ),

        "filter":
            (
                "PASS"
                if news_result.get(
                    "ok"
                )
                else
                "BLOCK"
            ),

        "feed_status":
            calendar_data.get(
                "status"
            ),

        "source":
            calendar_data.get(
                "source"
            ),

        "event":
            news_result.get(
                "event"
            ),

        "currency":
            news_result.get(
                "currency"
            ),

        "impact":
            news_result.get(
                "impact"
            ),

        "minutes_to_event":
            news_result.get(
                "minutes"
            ),

        "reason":
            news_result.get(
                "reason"
            ),

        "events_detected":
            len(events),

        "high_impact_events":
            len(high_events),

        "cache_age_minutes":
            calendar_data.get(
                "age_minutes"
            )
    }


# =========================================================
# SIGNAL ID
# =========================================================

def make_signal_id(
    direction,
    opportunity,
    entry,
    sl,
    tp2
):

    raw = (

        f"{direction}|"

        f"{opportunity}|"

        f"{round(entry, 2)}|"

        f"{round(sl, 2)}|"

        f"{round(tp2, 2)}"
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:16]


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        return False

    if not TELEGRAM_CHAT_ID:

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        return response.ok

    except Exception:

        return False


def format_telegram(
    direction,
    opportunity,
    score,
    plan,
    session,
    pd_data,
    volatility,
    news_result
):

    preferred = (

        "YES"

        if is_preferred_session(
            session
        )

        else

        "NO"
    )

    news_status = (
        news_result.get(
            "status",
            "UNKNOWN"
        )
    )

    news_event = (
        news_result.get(
            "event"
        )
        or
        "NONE"
    )

    news_minutes = (
        news_result.get(
            "minutes"
        )
    )

    news_line = (
        f"{news_status}"
    )

    if news_minutes is not None:

        news_line += (
            f" ({news_minutes} min)"
        )

    return (

        "👑 BOSQUE FOREX AI V5\n\n"

        f"🚨 {direction} "
        f"{opportunity}\n"

        f"⭐ Score: "
        f"{score}/100\n\n"

        f"📍 Entry: "
        f"{plan['entry']}\n"

        f"🛑 SL: "
        f"{plan['sl']}\n"

        f"🎯 TP1: "
        f"{plan['tp1']} "
        f"({plan['tp1_pips']} pips)\n"

        f"🎯 TP2: "
        f"{plan['tp2']} "
        f"({plan['tp2_pips']} pips)\n"

        f"🎯 TP3: "
        f"{plan['tp3']} "
        f"({plan['tp3_pips']} pips)\n\n"

        f"💰 Risk: "
        f"{plan['risk_pips']} pips\n"

        f"📊 RR TP2: "
        f"1:{plan['rr_tp2']}\n"

        f"🌍 Session: "
        f"{session}\n"

        f"⭐ Preferred Session: "
        f"{preferred}\n"

        f"📐 PD: "
        f"{pd_data['zone']}\n"

        f"🌡 ATR: "
        f"{volatility.get('atr_pips')} pips\n"

        f"📰 News: "
        f"{news_line}\n"

        f"📢 Event: "
        f"{news_event}\n\n"

        "⚠️ Manual confirmation required."
    )


# =========================================================
# JOURNAL
# =========================================================

def load_journal():

    data = load_json(
        JOURNAL_FILE
    )

    if not data:

        data = {

            "version":
                "3.0",

            "symbol":
                SYMBOL,

            "created_at":
                now_my().isoformat(),

            "trades":
                []
        }

    if "trades" not in data:

        data["trades"] = []

    return data


def journal_signal(
    signal,
    setup,
    m5_confirm,
    plan,
    score,
    session,
    news_result,
    timestamp
):

    if not signal.get(
        "active",
        False
    ):

        return

    if not plan.get(
        "valid",
        False
    ):

        return

    journal = load_journal()

    signal_id = signal.get(
        "id"
    )

    for trade in journal[
        "trades"
    ]:

        if trade.get(
            "signal_id"
        ) == signal_id:

            return

    trade = {

        "signal_id":
            signal_id,

        "timestamp":
            timestamp.isoformat(),

        "symbol":
            SYMBOL,

        "direction":
            signal.get(
                "direction"
            ),

        "opportunity":
            signal.get(
                "opportunity"
            ),

        "score":
            score,

        "session":
            session,

        "preferred_session":
            is_preferred_session(
                session
            ),

        "news_status":
            news_result.get(
                "status"
            ),

        "news_event":
            news_result.get(
                "event"
            ),

        "news_currency":
            news_result.get(
                "currency"
            ),

        "news_impact":
            news_result.get(
                "impact"
            ),

        "entry":
            plan.get(
                "entry"
            ),

        "sl":
            plan.get(
                "sl"
            ),

        "tp1":
            plan.get(
                "tp1"
            ),

        "tp2":
            plan.get(
                "tp2"
            ),

        "tp3":
            plan.get(
                "tp3"
            ),

        "risk_pips":
            plan.get(
                "risk_pips"
            ),

        "tp1_pips":
            plan.get(
                "tp1_pips"
            ),

        "tp2_pips":
            plan.get(
                "tp2_pips"
            ),

        "tp3_pips":
            plan.get(
                "tp3_pips"
            ),

        "rr_tp2":
            plan.get(
                "rr_tp2"
            ),

        "result":
            "OPEN",

        "result_pips":
            None,

        "result_r":
            None,

        "closed_at":
            None,

        "close_reason":
            None,

        "tp1_hit":
            False,

        "tp2_hit":
            False,

        "tp3_hit":
            False,

        "setup_reason":
            setup.get(
                "reason",
                []
            ),

        "m5_confirmation":
            m5_confirm.get(
                "reason"
            )
    }

    journal[
        "trades"
    ].append(
        trade
    )

    save_json_atomic(
        JOURNAL_FILE,
        journal
    )


# =========================================================
# JOURNAL OUTCOME ENGINE
# =========================================================

def evaluate_trade_against_candle(
    trade,
    candle
):

    if trade.get(
        "result"
    ) != "OPEN":

        return trade

    direction = trade.get(
        "direction"
    )

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp1 = float(
        trade["tp1"]
    )

    tp2 = float(
        trade["tp2"]
    )

    tp3 = float(
        trade["tp3"]
    )

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    candle_time = candle[
        "datetime"
    ]

    if isinstance(
        candle_time,
        pd.Timestamp
    ):

        candle_time = (
            candle_time
            .to_pydatetime()
        )

    trade_time = parse_timestamp(
        trade.get(
            "timestamp"
        )
    )

    if (
        trade_time

        and

        candle_time <= trade_time
    ):

        return trade

    if direction == "BUY":

        if low <= sl:

            trade[
                "result"
            ] = "LOSS"

            trade[
                "result_pips"
            ] = round(
                price_to_pips(
                    entry - sl
                ) * -1,
                1
            )

            trade[
                "result_r"
            ] = -1.0

            trade[
                "closed_at"
            ] = candle_time.isoformat()

            trade[
                "close_reason"
            ] = "SL HIT"

            return trade

        if high >= tp1:

            trade[
                "tp1_hit"
            ] = True

        if high >= tp2:

            trade[
                "tp2_hit"
            ] = True

            trade[
                "result"
            ] = "WIN"

            trade[
                "result_pips"
            ] = round(
                price_to_pips(
                    tp2 - entry
                ),
                1
            )

            trade[
                "result_r"
            ] = round(
                trade[
                    "result_pips"
                ]
                /
                trade[
                    "risk_pips"
                ],
                3
            )

            trade[
                "closed_at"
            ] = candle_time.isoformat()

            trade[
                "close_reason"
            ] = "TP2 HIT"

            return trade

        if high >= tp3:

            trade[
                "tp3_hit"
            ] = True

    elif direction == "SELL":

        if high >= sl:

            trade[
                "result"
            ] = "LOSS"

            trade[
                "result_pips"
            ] = round(
                price_to_pips(
                    sl - entry
                ) * -1,
                1
            )

            trade[
                "result_r"
            ] = -1.0

            trade[
                "closed_at"
            ] = candle_time.isoformat()

            trade[
                "close_reason"
            ] = "SL HIT"

            return trade

        if low <= tp1:

            trade[
                "tp1_hit"
            ] = True

        if low <= tp2:

            trade[
                "tp2_hit"
            ] = True

            trade[
                "result"
            ] = "WIN"

            trade[
                "result_pips"
            ] = round(
                price_to_pips(
                    entry - tp2
                ),
                1
            )

            trade[
                "result_r"
            ] = round(
                trade[
                    "result_pips"
                ]
                /
                trade[
                    "risk_pips"
                ],
                3
            )

            trade[
                "closed_at"
            ] = candle_time.isoformat()

            trade[
                "close_reason"
            ] = "TP2 HIT"

            return trade

        if low <= tp3:

            trade[
                "tp3_hit"
            ] = True

    return trade


def update_journal_outcomes(
    m5
):

    journal = load_journal()

    trades = journal.get(
        "trades",
        []
    )

    if not trades:

        return journal

    changed = False

    for trade in trades:

        if trade.get(
            "result"
        ) != "OPEN":

            continue

        trade_time = parse_timestamp(
            trade.get(
                "timestamp"
            )
        )

        if trade_time is None:

            continue

        trade_time_ts = pd.Timestamp(
            trade_time
        )

        candles = m5[
            m5["datetime"]
            >
            trade_time_ts
        ]

        for _, candle in candles.iterrows():

            old_result = trade.get(
                "result"
            )

            trade = (
                evaluate_trade_against_candle(
                    trade,
                    candle
                )
            )

            if trade.get(
                "result"
            ) != old_result:

                changed = True

                break

    if changed:

        save_json_atomic(
            JOURNAL_FILE,
            journal
        )

    return journal


# =========================================================
# FORWARD TEST
# =========================================================

def calculate_forward_stats():

    journal = load_journal()

    trades = journal.get(
        "trades",
        []
    )

    closed = [

        x for x in trades

        if x.get(
            "result"
        ) in [

            "WIN",

            "LOSS",

            "BE"
        ]
    ]

    wins = [

        x for x in closed

        if x.get(
            "result"
        ) == "WIN"
    ]

    losses = [

        x for x in closed

        if x.get(
            "result"
        ) == "LOSS"
    ]

    breakeven = [

        x for x in closed

        if x.get(
            "result"
        ) == "BE"
    ]

    total = len(
        closed
    )

    result_rs = [

        float(
            x["result_r"]
        )

        for x in closed

        if x.get(
            "result_r"
        ) is not None
    ]

    result_pips = [

        float(
            x["result_pips"]
        )

        for x in closed

        if x.get(
            "result_pips"
        ) is not None
    ]

    total_r = (

        sum(result_rs)

        if result_rs

        else

        0.0
    )

    average_r = (

        sum(result_rs)
        /
        len(result_rs)

        if result_rs

        else

        None
    )

    total_pips = (

        sum(result_pips)

        if result_pips

        else

        0.0
    )

    average_pips = (

        sum(result_pips)
        /
        len(result_pips)

        if result_pips

        else

        None
    )

    win_rate = (

        len(wins)
        /
        total
        *
        100

        if total

        else

        None
    )

    equity = 0.0

    peak = 0.0

    max_drawdown = 0.0

    for r in result_rs:

        equity += r

        peak = max(
            peak,
            equity
        )

        drawdown = (
            peak - equity
        )

        max_drawdown = max(
            max_drawdown,
            drawdown
        )

    current_losses = 0

    max_consecutive_losses = 0

    for trade in closed:

        if trade.get(
            "result"
        ) == "LOSS":

            current_losses += 1

            max_consecutive_losses = max(
                max_consecutive_losses,
                current_losses
            )

        else:

            current_losses = 0

    open_trades = len([

        x for x in trades

        if x.get(
            "result"
        ) == "OPEN"
    ])

    data = {

        "status":
            "FORWARD TESTING",

        "symbol":
            SYMBOL,

        "total_signals":
            len(trades),

        "open_trades":
            open_trades,

        "closed_trades":
            total,

        "wins":
            len(wins),

        "losses":
            len(losses),

        "breakeven":
            len(breakeven),

        "win_rate":
            (
                round(
                    win_rate,
                    2
                )

                if win_rate is not None

                else

                None
            ),

        "total_pips":
            round(
                total_pips,
                1
            ),

        "average_pips":
            (
                round(
                    average_pips,
                    1
                )

                if average_pips is not None

                else

                None
            ),

        "total_r":
            round(
                total_r,
                3
            ),

        "average_r":
            (
                round(
                    average_r,
                    3
                )

                if average_r is not None

                else

                None
            ),

        "max_drawdown_r":
            round(
                max_drawdown,
                3
            ),

        "max_consecutive_losses":
            max_consecutive_losses,

        "last_updated":
            now_my().isoformat()
    }

    save_json_atomic(
        FORWARD_FILE,
        data
    )

    return data


# =========================================================
# BACKTEST FOUNDATION
# =========================================================

def load_backtest():

    data = load_json(
        BACKTEST_FILE
    )

    if not data:

        data = {

            "status":
                "READY",

            "symbol":
                SYMBOL,

            "development_period":
                "2022-2024",

            "out_of_sample":
                "2025",

            "forward":
                "2026",

            "runs":
                []
        }

    return data


# =========================================================
# SIGNAL GATE
# =========================================================

def validate_signal_gate(
    score,
    setup,
    m5_confirm,
    plan,
    news_result
):

    reasons = []

    if score < MIN_SCORE:

        reasons.append(
            f"Score {score} < {MIN_SCORE}"
        )

    if not setup.get(
        "valid",
        False
    ):

        reasons.append(
            "M15 setup invalid"
        )

    if not m5_confirm.get(
        "confirmed",
        False
    ):

        reasons.append(
            "M5 confirmation missing"
        )

    if not plan.get(
        "valid",
        False
    ):

        reasons.append(
            plan.get(
                "reason",
                "Trade plan invalid"
            )
        )

    if not news_result.get(
        "ok",
        False
    ):

        reasons.append(
            (
                "NEWS BLOCK: "
                +
                str(
                    news_result.get(
                        "reason",
                        "News filter BLOCK"
                    )
                )
            )
        )

    return {

        "valid":
            len(reasons) == 0,

        "reasons":
            reasons
    }


# =========================================================
# MAIN ENGINE
# =========================================================

def main():

    global CURRENT_H1

    timestamp = now_my()

    # =====================================================
    # FETCH M5
    # =====================================================

    m5 = fetch_m5()

    m5 = remove_incomplete_candle(
        m5
    )

    if len(m5) < 100:

        raise RuntimeError(
            "Not enough M5 candles"
        )

    # =====================================================
    # UPDATE JOURNAL
    # =====================================================

    journal = update_journal_outcomes(
        m5
    )

    forward_stats = (
        calculate_forward_stats()
    )

    # =====================================================
    # AGGREGATION
    # =====================================================

    m15 = aggregate(
        m5,
        15
    )

    h1 = aggregate(
        m5,
        60
    )

    CURRENT_H1 = h1

    if (
        len(m15) < 50

        or

        len(h1) < 30
    ):

        raise RuntimeError(
            "Not enough MTF data"
        )

    # =====================================================
    # STRUCTURE
    # =====================================================

    h1_structure = (
        analyze_structure(
            h1
        )
    )

    m15_structure = (
        analyze_structure(
            m15
        )
    )

    m5_structure = (
        analyze_structure(
            m5
        )
    )

    # =====================================================
    # MARKET REGIME
    # =====================================================

    regime_range = (
        analyze_range(
            h1
        )
    )

    if h1_structure[
        "direction"
    ] in [

        "BULLISH",

        "BEARISH"

    ]:

        market_mode = "TRENDING"

    elif regime_range[
        "is_range"
    ]:

        market_mode = "RANGING"

    else:

        market_mode = "NEUTRAL"

    # =====================================================
    # CONTEXT
    # =====================================================

    pd_data = pd_zone(
        m15
    )

    liquidity = (
        liquidity_analysis(
            m15
        )
    )

    volatility = (
        volatility_analysis(
            m5
        )
    )

    session = get_session(
        timestamp
    )

    session_ok = (
        is_preferred_session(
            session
        )
    )

    # =====================================================
    # REAL NEWS ENGINE
    # =====================================================

    calendar_data = (
        fetch_news_calendar()
    )

    news_result = (
        evaluate_news_filter(
            calendar_data,
            timestamp
        )
    )

    news_ok = (
        news_result[
            "ok"
        ]
    )

    news_summary = (
        build_news_summary(
            calendar_data,
            news_result
        )
    )

    # =====================================================
    # M15 SETUP
    # =====================================================

    setup = detect_m15_setup(
        h1_structure,
        m15,
        m5
    )

    # =====================================================
    # M5 CONFIRMATION
    # =====================================================

    m5_confirm = (
        m5_confirmation(
            m5,
            setup["direction"]
        )
    )

    # =====================================================
    # SCORE
    # =====================================================

    score = calculate_score(
        h1_structure,
        setup,
        m5_confirm,
        session,
        volatility
    )

    # =====================================================
    # TRADE PLAN
    # =====================================================

    plan = create_trade_plan(
        setup["direction"],
        m5,
        score,
        volatility
    )

    # =====================================================
    # SIGNAL GATE
    # =====================================================

    gate = validate_signal_gate(
        score,
        setup,
        m5_confirm,
        plan,
        news_result
    )

    valid_signal = (
        gate["valid"]
    )

    score_ok = (
        score >= MIN_SCORE
    )

    setup_ok = (
        setup["valid"]
    )

    m5_ok = (
        m5_confirm[
            "confirmed"
        ]
    )

    risk_ok = (
        plan.get(
            "valid",
            False
        )
    )

    rr_ok = (

        plan.get(
            "rr_tp2",
            0
        )

        >=

        MIN_RR
    )

    # =====================================================
    # SIGNAL
    # =====================================================

    signal = {

        "active":
            False,

        "id":
            None,

        "direction":
            setup["direction"],

        "opportunity":
            setup["opportunity"],

        "score":
            score,

        "timestamp":
            timestamp.isoformat(),

        "status":
            "WAIT",

        "gate_reasons":
            gate["reasons"],

        "news_status":
            news_result.get(
                "status"
            ),

        "news_event":
            news_result.get(
                "event"
            ),

        "news_minutes":
            news_result.get(
                "minutes"
            )
    }

    state = load_state()

    # =====================================================
    # ONLY VALID SIGNAL CAN BECOME ACTIVE
    # =====================================================

    if valid_signal:

        signal_id = make_signal_id(

            setup["direction"],

            setup["opportunity"],

            plan["entry"],

            plan["sl"],

            plan["tp2"]
        )

        signal.update({

            "active":
                True,

            "id":
                signal_id,

            "status":
                "VALID",

            "entry":
                plan["entry"],

            "sl":
                plan["sl"],

            "tp1":
                plan["tp1"],

            "tp2":
                plan["tp2"],

            "tp3":
                plan["tp3"],

            "risk_pips":
                plan["risk_pips"],

            "rr_tp2":
                plan["rr_tp2"]
        })

        last_signal = state.get(
            "last_signal_id"
        )

        if signal_id != last_signal:

            message = format_telegram(

                setup["direction"],

                setup["opportunity"],

                score,

                plan,

                session,

                pd_data,

                volatility,

                news_result
            )

            sent = send_telegram(
                message
            )

            if sent:

                state[
                    "last_signal_id"
                ] = signal_id

                state[
                    "last_signal_sent"
                ] = timestamp.isoformat()

                save_state(
                    state
                )

    # =====================================================
    # JOURNAL
    # =====================================================

    journal_signal(

        signal,

        setup,

        m5_confirm,

        plan,

        score,

        session,

        news_result,

        timestamp
    )

    forward_stats = (
        calculate_forward_stats()
    )

    # =====================================================
    # DASHBOARD STATUS
    # =====================================================

    if valid_signal:

        dashboard_status = (
            "VALID SIGNAL"
        )

    elif (
        news_result.get(
            "status"
        )
        ==
        "BLOCK"
    ):

        dashboard_status = (
            "WAIT — NEWS BLOCK"
        )

    elif score < MIN_SCORE:

        dashboard_status = (
            "WAIT — SCORE BELOW 70"
        )

    else:

        dashboard_status = (
            "WAIT — CONDITIONS NOT MET"
        )

    # =====================================================
    # SAFE PLAN DISPLAY
    # =====================================================

    dashboard_plan = (

        plan

        if plan.get(
            "valid",
            False
        )

        and

        valid_signal

        else

        {

            "valid":
                False,

            "status":
                "NO EXECUTABLE PLAN",

            "reason":
                (

                    plan.get(
                        "reason",
                        "Signal gate not passed"
                    )

                    if not valid_signal

                    else

                    plan.get(
                        "reason",
                        "Invalid trade plan"
                    )
                )
        }
    )

    # =====================================================
    # DASHBOARD
    # =====================================================

    dashboard = {

        "engine": {

            "name":
                "BOSQUE FOREX AI",

            "version":
                "SCALPING V5",

            "symbol":
                SYMBOL,

            "timeframe":
                "H1 → M15 → M5",

            "timestamp":
                timestamp.isoformat(),

            "status":
                dashboard_status
        },

        "latest_price":
            round_price(
                float(
                    m5.iloc[-1]["close"]
                )
            ),

        "session":
            session,

        "session_preferred":
            session_ok,

        "market_mode":
            market_mode,

        "regime": {

            "type":
                market_mode,

            "h1_direction":
                h1_structure[
                    "direction"
                ],

            "range":
                regime_range
        },

        "volatility":
            volatility,

        "pd":
            pd_data,

        "liquidity": {

            **liquidity,

            "pdh":
                None,

            "pdl":
                None,

            "asia_high":
                None,

            "asia_low":
                None,

            "session_high":
                None,

            "session_low":
                None
        },

        # =================================================
        # NEWS
        # =================================================

        "news":
            news_summary,

        # =================================================
        # OPPORTUNITY
        # =================================================

        "opportunity": {

            "type":
                setup[
                    "opportunity"
                ],

            "direction":
                setup[
                    "direction"
                ],

            "valid":
                valid_signal,

            "analysis_valid":
                setup[
                    "valid"
                ],

            "score":
                score,

            "minimum_score":
                MIN_SCORE,

            "status":
                dashboard_status
        },

        # =================================================
        # SIGNAL
        # =================================================

        "signal":
            signal,

        # =================================================
        # PLAN
        # =================================================

        "plan":
            dashboard_plan,

        # =================================================
        # POTENTIAL
        # =================================================

        "potential": {

            "tp1_pips":
                (
                    plan.get(
                        "tp1_pips"
                    )
                    if valid_signal
                    else
                    None
                ),

            "tp2_pips":
                (
                    plan.get(
                        "tp2_pips"
                    )
                    if valid_signal
                    else
                    None
                ),

            "tp3_pips":
                (
                    plan.get(
                        "tp3_pips"
                    )
                    if valid_signal
                    else
                    None
                ),

            "status":
                (
                    "VALID"
                    if valid_signal
                    else
                    "WAIT"
                )
        },

        # =================================================
        # MTF
        # =================================================

        "h1": {

            **h1_structure,

            "condition":
                market_mode
        },

        "m15": {

            **m15_structure,

            "setup":
                setup
        },

        "m5": {

            **m5_structure,

            "confirmation":
                m5_confirm
        },

        # =================================================
        # FILTERS
        # =================================================

        "filters": {

            "score":
                score_ok,

            "setup":
                setup_ok,

            "m5_confirmation":
                m5_ok,

            "risk":
                risk_ok,

            "rr":
                rr_ok,

            "news":
                news_ok,

            "session":
                session_ok,

            "session_blocking":
                False
        },

        # =================================================
        # CONFIRMATIONS
        # =================================================

        "confirmations": {

            "h1_bias":
                h1_structure[
                    "direction"
                ],

            "m15_setup":
                setup[
                    "opportunity"
                ],

            "m5_confirmation":
                m5_confirm[
                    "reason"
                ],

            "liquidity":
                liquidity[
                    "description"
                ],

            "pd_zone":
                pd_data[
                    "zone"
                ],

            "news":
                news_result.get(
                    "reason"
                )
        },

        # =================================================
        # SIGNAL GATE
        # =================================================

        "signal_gate": {

            "valid":
                valid_signal,

            "score":
                score,

            "minimum_score":
                MIN_SCORE,

            "reasons":
                gate["reasons"]
        },

        # =================================================
        # RISK ENGINE
        # =================================================

        "risk_engine": {

            "status": (

                "VALID"

                if risk_ok

                else

                "INVALID"
            ),

            "risk_pips":
                plan.get(
                    "risk_pips"
                ),

            "risk_level":
                plan.get(
                    "risk_level"
                ),

            "min_risk_pips":
                MIN_RISK_PIPS,

            "max_risk_pips":
                MAX_RISK_PIPS,

            "daily_loss_limit":
                "NOT CONFIGURED",

            "consecutive_loss_limit":
                "NOT CONFIGURED"
        },

        # =================================================
        # TRADE PLAN AUDIT
        # =================================================

        "trade_plan_audit": {

            "status":
                (
                    "PASS"

                    if plan.get(
                        "valid",
                        False
                    )

                    else

                    "FAIL"
                ),

            "direction":
                setup[
                    "direction"
                ],

            "entry":
                plan.get(
                    "entry"
                ),

            "sl":
                plan.get(
                    "sl"
                ),

            "tp1":
                plan.get(
                    "tp1"
                ),

            "tp2":
                plan.get(
                    "tp2"
                ),

            "tp3":
                plan.get(
                    "tp3"
                ),

            "geometry":
                plan.get(
                    "geometry"
                ),

            "reason":
                plan.get(
                    "reason"
                )
        },

        # =================================================
        # NEWS AUDIT
        # =================================================

        "news_audit": {

            "status":
                news_result.get(
                    "status"
                ),

            "filter":
                (
                    "PASS"
                    if news_result.get(
                        "ok"
                    )
                    else
                    "BLOCK"
                ),

            "feed":
                news_result.get(
                    "feed_status"
                ),

            "source":
                news_result.get(
                    "source"
                ),

            "currency":
                news_result.get(
                    "currency"
                ),

            "impact":
                news_result.get(
                    "impact"
                ),

            "event":
                news_result.get(
                    "event"
                ),

            "minutes":
                news_result.get(
                    "minutes"
                ),

            "reason":
                news_result.get(
                    "reason"
                ),

            "pre_block_minutes":
                NEWS_PRE_BLOCK_MINUTES,

            "post_cooldown_minutes":
                NEWS_POST_COOLDOWN_MINUTES,

            "stale_after_minutes":
                NEWS_STALE_MINUTES
        },

        # =================================================
        # INVALIDATION
        # =================================================

        "invalidation": {

            "status": (

                "VALID"

                if valid_signal

                else

                "WAIT"
            ),

            "conditions": [

                "M5 confirmation required",

                "Risk must remain valid",

                "RR TP2 must remain >= 1:2",

                "BUY SL must remain below Entry",

                "SELL SL must remain above Entry",

                "BUY TP levels must remain above Entry",

                "SELL TP levels must remain below Entry",

                "High-impact news block overrides technical signal",

                "Avoid invalidation after structure failure"
            ]
        },

        # =================================================
        # SOP
        # =================================================

        "sop": {

            "news_filter":
                (
                    "PASS"
                    if news_ok
                    else
                    "BLOCK"
                ),

            "news_status":
                news_result.get(
                    "status"
                ),

            "news_event":
                news_result.get(
                    "event"
                ),

            "session_filter":
                (
                    "PREFERRED"
                    if session_ok
                    else
                    "NON-PREFERRED"
                ),

            "session_blocking":
                "NO",

            "risk":
                plan.get(
                    "risk_level",
                    "UNKNOWN"
                ),

            "fresh_zone":
                (
                    "YES"
                    if setup["valid"]
                    else
                    "NO"
                ),

            "m15_setup":
                (
                    "YES"
                    if setup_ok
                    else
                    "NO"
                ),

            "m5_confirmation":
                (
                    "YES"
                    if m5_ok
                    else
                    "NO"
                )
        },

        # =================================================
        # JOURNAL
        # =================================================

        "journal": {

            "status":
                "ACTIVE",

            "file":
                JOURNAL_FILE.name,

            "total_signals":
                forward_stats[
                    "total_signals"
                ],

            "open_trades":
                forward_stats[
                    "open_trades"
                ],

            "closed_trades":
                forward_stats[
                    "closed_trades"
                ]
        },

        # =================================================
        # FORWARD TEST
        # =================================================

        "forward_test":
            forward_stats,

        # =================================================
        # BACKTEST
        # =================================================

        "backtest": {

            "status":
                "READY",

            "file":
                BACKTEST_FILE.name,

            "development_period":
                "2022-2024",

            "out_of_sample":
                "2025",

            "forward":
                "2026",

            "note":
                (
                    "Historical backtest runner "
                    "will be activated separately "
                    "after V5 live signal validation."
                )
        }
    }

    # =====================================================
    # SAVE DASHBOARD
    # =====================================================

    save_json_atomic(
        DASHBOARD_FILE,
        dashboard
    )

    # =====================================================
    # CONSOLE
    # =====================================================

    print(
        json.dumps(
            clean_for_json(
                dashboard
            ),
            indent=2,
            ensure_ascii=False
        )
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    main()