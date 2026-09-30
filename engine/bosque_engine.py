import os
import json
import math
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd


ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"
STATE_FILE = ENGINE_DIR / "state.json"
JOURNAL_FILE = ENGINE_DIR / "trade_journal.json"
FORWARD_FILE = ENGINE_DIR / "forward_test.json"
BACKTEST_FILE = ENGINE_DIR / "backtest_results.json"
NEWS_CACHE_FILE = ENGINE_DIR / "news_cache.json"


TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

FOREX_FACTORY_JSON_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
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


MIN_SCORE = 70


# =========================================================
# XAUUSD PIP MODEL
#
# 1 pip = 0.10 price movement
# =========================================================

PIP_SIZE = 0.10


MIN_RISK_PIPS = 25

MAX_RISK_PIPS = 80


TP1_PIPS = 60

MIN_TP2_PIPS = 120

TP3_PIPS = 180


MIN_RR = 2.0


# =========================================================
# NEWS SETTINGS
# =========================================================

NEWS_CACHE_MINUTES = 30

NEWS_MAX_STALE_MINUTES = 240


NEWS_BLOCK_BEFORE_MINUTES = 30

NEWS_BLOCK_AFTER_MINUTES = 20


NEWS_CAUTION_BEFORE_MINUTES = 60

NEWS_CAUTION_AFTER_MINUTES = 30


# =========================================================
# SIGNAL COOLDOWN
# =========================================================

SIGNAL_COOLDOWN_MINUTES = 30


# =========================================================
# MALAYSIA TIME
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
# BASIC HELPERS
# =========================================================

def now_my():

    return datetime.now(
        timezone.utc
    ).astimezone(
        MY_TZ
    )


def now_utc():

    return datetime.now(
        timezone.utc
    )


def clean_for_json(value):

    if isinstance(
        value,
        dict
    ):

        return {
            str(k):
            clean_for_json(v)
            for k, v in value.items()
        }

    if isinstance(
        value,
        (list, tuple)
    ):

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

        if (
            math.isnan(value)
            or
            math.isinf(value)
        ):

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

    tmp.replace(
        path
    )


def load_json(
    path
):

    if not path.exists():

        return {}

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(
                f
            )

    except Exception:

        return {}


def load_previous_dashboard():

    return load_json(
        DASHBOARD_FILE
    )


def parse_dt(
    value
):

    if (
        value is None
        or
        value == ""
    ):

        return None

    try:

        ts = pd.to_datetime(
            value,
            utc=True,
            errors="coerce"
        )

        if pd.isna(
            ts
        ):

            return None

        return ts.to_pydatetime()

    except Exception:

        return None


def price_to_pips(
    distance
):

    return abs(
        float(distance)
    ) / PIP_SIZE


def pips_to_price(
    pips
):

    return (
        float(pips)
        *
        PIP_SIZE
    )


def round_price(
    price
):

    return round(
        float(price),
        2
    )


def age_minutes(
    dt,
    reference=None
):

    reference = (
        reference
        or
        now_utc()
    )

    if dt is None:

        return None

    return (

        reference

        -

        dt.astimezone(
            timezone.utc
        )

    ).total_seconds() / 60.0


# =========================================================
# PERSISTENT STATE
#
# GitHub runner is temporary.
#
# dashboard_data.json is used as backup persistence because
# it is already committed by the dashboard workflow.
# =========================================================

def load_state(
    previous_dashboard=None
):

    state = load_json(
        STATE_FILE
    )

    if state:

        return state

    previous_dashboard = (

        previous_dashboard

        or

        load_previous_dashboard()
    )

    dashboard_state = (

        previous_dashboard.get(
            "_persistent_state",
            {}
        )

        if isinstance(
            previous_dashboard,
            dict
        )

        else

        {}
    )

    if isinstance(
        dashboard_state,
        dict
    ):

        return dashboard_state.copy()

    return {}


def save_state(
    state
):

    save_json_atomic(
        STATE_FILE,
        state
    )


# =========================================================
# SESSION
#
# Session is NOT a hard signal gate.
# =========================================================

def get_session(
    dt=None
):

    dt = (
        dt
        or
        now_my()
    )

    hour = dt.hour

    for (
        name,
        start,
        end
    ) in SESSIONS:

        if (
            start
            <= hour
            <
            end
        ):

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
#
# ONE request per scan.
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
            "JSON",

        "timezone":
            "UTC"
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
        utc=True,
        errors="coerce"
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
# REMOVE INCOMPLETE M5
# =========================================================

def remove_incomplete_candle(
    df
):

    if df.empty:

        return df

    last_time = pd.to_datetime(
        df.iloc[-1][
            "datetime"
        ],
        utc=True
    ).to_pydatetime()

    elapsed = (

        now_utc()

        -

        last_time

    ).total_seconds()

    if elapsed < 300:

        return df.iloc[
            :-1
        ].copy()

    return df.copy()


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

    out = x.resample(
        f"{minutes}min"
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

    return (
        out
        .dropna()
        .reset_index()
    )


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
        left
        +
        right
        +
        1
    ):

        return highs

    for i in range(
        left,
        len(df) - right
    ):

        value = float(
            df.iloc[i][
                "high"
            ]
        )

        left_values = df.iloc[
            i-left:i
        ]["high"]

        right_values = df.iloc[
            i+1:i+right+1
        ]["high"]

        if (

            value
            >
            float(
                left_values.max()
            )

            and

            value
            >=
            float(
                right_values.max()
            )

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
        left
        +
        right
        +
        1
    ):

        return lows

    for i in range(
        left,
        len(df) - right
    ):

        value = float(
            df.iloc[i][
                "low"
            ]
        )

        left_values = df.iloc[
            i-left:i
        ]["low"]

        right_values = df.iloc[
            i+1:i+right+1
        ]["low"]

        if (

            value
            <
            float(
                left_values.min()
            )

            and

            value
            <=
            float(
                right_values.min()
            )

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

        old_high = highs[-2][
            "price"
        ]

        new_high = highs[-1][
            "price"
        ]

        old_low = lows[-2][
            "price"
        ]

        new_low = lows[-1][
            "price"
        ]

        if (
            new_high > old_high

            and

            new_low > old_low
        ):

            direction = "BULLISH"

        elif (
            new_high < old_high

            and

            new_low < old_low
        ):

            direction = "BEARISH"

    current_close = float(
        df.iloc[-1][
            "close"
        ]
    )

    if highs:

        latest_high = highs[-1][
            "price"
        ]

        if (
            current_close
            >
            latest_high
        ):

            bos = "BULLISH BOS"

    if lows:

        latest_low = lows[-1][
            "price"
        ]

        if (
            current_close
            <
            latest_low
        ):

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
        x.iloc[-1][
            "close"
        ]
    )

    width = (
        high - low
    )

    location = (

        (
            close - low
        )
        /
        width

        if width > 0

        else

        0.5
    )

    is_range = (

        0.20
        <=
        location
        <=
        0.80

        or

        width
        <
        close * 0.01
    )

    return {

        "is_range":
            bool(
                is_range
            ),

        "high":
            round_price(
                high
            ),

        "low":
            round_price(
                low
            ),

        "width":
            round_price(
                width
            ),

        "location":
            round(
                location,
                4
            )
    }


# =========================================================
# PREMIUM / DISCOUNT
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
    ) / 2.0

    close = float(
        x.iloc[-1][
            "close"
        ]
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
# LIQUIDITY SWEEP
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
            df.iloc[-1][
                "high"
            ]
        )

        current_close = float(
            df.iloc[-1][
                "close"
            ]
        )

        if (
            current_high
            >
            swing_high

            and

            current_close
            <
            swing_high
        ):

            result[
                "buy_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = round_price(
                swing_high
            )

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
            df.iloc[-1][
                "low"
            ]
        )

        current_close = float(
            df.iloc[-1][
                "close"
            ]
        )

        if (
            current_low
            <
            swing_low

            and

            current_close
            >
            swing_low
        ):

            result[
                "sell_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = round_price(
                swing_low
            )

            result[
                "description"
            ] = (
                "SELL-SIDE LIQUIDITY SWEPT"
            )

    return result


# =========================================================
# REAL LIQUIDITY LEVELS
#
# PDH
# PDL
# Asia High
# Asia Low
# Current Session High
# Current Session Low
# =========================================================

def calculate_liquidity_levels(
    m5,
    reference_my
):

    x = m5.copy()

    x["datetime"] = pd.to_datetime(
        x["datetime"],
        utc=True
    )

    x["datetime_my"] = (

        x["datetime"]
        .dt
        .tz_convert(
            MY_TZ
        )
    )

    x["local_date"] = (
        x["datetime_my"]
        .dt
        .date
    )

    today = (
        reference_my.date()
    )

    previous_dates = sorted(

        d

        for d in (
            x["local_date"]
            .dropna()
            .unique()
        )

        if d < today
    )

    pdh = None

    pdl = None

    if previous_dates:

        prev_date = (
            previous_dates[-1]
        )

        prev = x[
            x["local_date"]
            ==
            prev_date
        ]

        if not prev.empty:

            pdh = round_price(
                prev["high"].max()
            )

            pdl = round_price(
                prev["low"].min()
            )

    current_day = x[
        x["local_date"]
        ==
        today
    ].copy()

    asia = current_day[

        (
            current_day[
                "datetime_my"
            ].dt.hour
            >= 7
        )

        &

        (
            current_day[
                "datetime_my"
            ].dt.hour
            < 15
        )
    ]

    asia_high = None

    asia_low = None

    if not asia.empty:

        asia_high = round_price(
            asia["high"].max()
        )

        asia_low = round_price(
            asia["low"].min()
        )

    session = get_session(
        reference_my
    )

    session_high = None

    session_low = None

    if session == "ASIAN":

        session_rows = asia

    elif session == "LONDON":

        session_rows = current_day[

            (
                current_day[
                    "datetime_my"
                ].dt.hour
                >= 15
            )

            &

            (
                current_day[
                    "datetime_my"
                ].dt.hour
                < 20
            )
        ]

    elif session == "NEW YORK":

        hour = reference_my.hour

        if hour >= 20:

            session_rows = current_day[

                (
                    current_day[
                        "datetime_my"
                    ].dt.hour
                    >= 20
                )

                &

                (
                    current_day[
                        "datetime_my"
                    ].dt.hour
                    < 23
                )
            ]

        else:

            yesterday = (
                today
                -
                timedelta(
                    days=1
                )
            )

            session_rows = x[

                (
                    x["local_date"]
                    ==
                    yesterday
                )

                &

                (
                    x[
                        "datetime_my"
                    ].dt.hour
                    >= 20
                )
            ].copy()

            early_today = current_day[

                current_day[
                    "datetime_my"
                ].dt.hour
                < 1
            ]

            session_rows = pd.concat(
                [
                    session_rows,
                    early_today
                ],
                ignore_index=True
            )

    else:

        session_rows = (
            pd.DataFrame()
        )

    if not session_rows.empty:

        session_high = round_price(
            session_rows[
                "high"
            ].max()
        )

        session_low = round_price(
            session_rows[
                "low"
            ].min()
        )

    return {

        "pdh":
            pdh,

        "pdl":
            pdl,

        "asia_high":
            asia_high,

        "asia_low":
            asia_low,

        "session_high":
            session_high,

        "session_low":
            session_low
    }


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

    closes = (

        df["close"]
        .tail(
            lookback + 1
        )
        .tolist()
    )

    up = 0

    down = 0

    for i in range(
        1,
        len(closes)
    ):

        if (
            closes[i]
            >
            closes[i-1]
        ):

            up += 1

        elif (
            closes[i]
            <
            closes[i-1]
        ):

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
        body
        /
        full_range
    )

    bullish = (

        float(c["close"])
        >
        float(c["open"])

        and

        ratio >= 0.55
    )

    bearish = (

        float(c["close"])
        <
        float(c["open"])

        and

        ratio >= 0.55
    )

    return {

        "bullish":
            bool(
                bullish
            ),

        "bearish":
            bool(
                bearish
            )
    }


# =========================================================
# ATR / VOLATILITY
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
        x["close"]
        .shift(1)
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
        tr
        .rolling(
            period
        )
        .mean()
        .iloc[-1]
    )

    if pd.isna(
        atr
    ):

        return None

    return float(
        atr
    )


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
    m15
):

    h1_direction = (
        h1[
            "direction"
        ]
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

    range_info = (
        analyze_range(
            m15
        )
    )

    direction = None

    opportunity = (
        "NO VALID SETUP"
    )

    reason = []


    if range_info[
        "is_range"
    ]:

        if (
            m15_liquidity[
                "sell_side_sweep"
            ]

            and

            range_info[
                "location"
            ] is not None

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
            ] is not None

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


    if direction is None:

        if (
            h1_direction
            ==
            "BULLISH"

            and

            m15_pd[
                "zone"
            ]
            ==
            "DISCOUNT"
        ):

            direction = "BUY"

            opportunity = (
                "BUY PULLBACK"
            )

            reason.append(
                "H1 bullish + M15 discount"
            )

        elif (
            h1_direction
            ==
            "BEARISH"

            and

            m15_pd[
                "zone"
            ]
            ==
            "PREMIUM"
        ):

            direction = "SELL"

            opportunity = (
                "SELL PULLBACK"
            )

            reason.append(
                "H1 bearish + M15 premium"
            )


    if direction is None:

        if (
            m15_structure[
                "bos"
            ]
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
            m15_structure[
                "bos"
            ]
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

    bos = structure[
        "bos"
    ]


    if expected_direction == "BUY":

        bos_ok = (
            bos
            ==
            "BULLISH BOS"
        )

        candle_ok = (

            candle[
                "bullish"
            ]

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
                bool(
                    confirmed
                ),

            "bos":
                bos,

            "candle":
                bool(
                    candle_ok
                ),

            "momentum":
                momentum_data[
                    "direction"
                ],

            "reason":
                (
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
            bos
            ==
            "BEARISH BOS"
        )

        candle_ok = (

            candle[
                "bearish"
            ]

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
                bool(
                    confirmed
                ),

            "bos":
                bos,

            "candle":
                bool(
                    candle_ok
                ),

            "momentum":
                momentum_data[
                    "direction"
                ],

            "reason":
                (
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


    if h1[
        "direction"
    ] in [
        "BULLISH",
        "BEARISH"
    ]:

        score += 15


    if h1[
        "bos"
    ]:

        score += 5


    if m15_setup[
        "valid"
    ]:

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


    if m15_setup[
        "structure"
    ].get(
        "bos"
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


    direction = (
        m15_setup[
            "direction"
        ]
    )

    zone = (
        m15_setup[
            "pd"
        ][
            "zone"
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
#
# BUY = SL BELOW ENTRY
# SELL = SL ABOVE ENTRY
# =========================================================

def create_trade_plan(
    direction,
    m5,
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
        m5.iloc[-1][
            "close"
        ]
    )

    structure = analyze_structure(
        m5
    )

    swing_low = (
        structure[
            "swing_low"
        ]
    )

    swing_high = (
        structure[
            "swing_high"
        ]
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


    if direction == "BUY":

        if swing_low is None:

            return {

                "valid":
                    False,

                "reason":
                    "BUY requires swing low",

                "entry":
                    round_price(
                        entry
                    )
            }

        if float(
            swing_low
        ) >= entry:

            return {

                "valid":
                    False,

                "reason":
                    (
                        "BUY invalid: "
                        "swing low is not below entry"
                    ),

                "entry":
                    round_price(
                        entry
                    ),

                "swing_low":
                    round_price(
                        swing_low
                    )
            }

        sl = (

            float(
                swing_low
            )

            -

            buffer
        )

    else:

        if swing_high is None:

            return {

                "valid":
                    False,

                "reason":
                    "SELL requires swing high",

                "entry":
                    round_price(
                        entry
                    )
            }

        if float(
            swing_high
        ) <= entry:

            return {

                "valid":
                    False,

                "reason":
                    (
                        "SELL invalid: "
                        "swing high is not above entry"
                    ),

                "entry":
                    round_price(
                        entry
                    ),

                "swing_high":
                    round_price(
                        swing_high
                    )
            }

        sl = (

            float(
                swing_high
            )

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
        risk_pips
        <
        MIN_RISK_PIPS

        or

        risk_pips
        >
        MAX_RISK_PIPS
    ):

        return {

            "valid":
                False,

            "reason":
                (
                    f"Risk {risk_pips:.1f} "
                    f"pips outside "
                    f"{MIN_RISK_PIPS}-"
                    f"{MAX_RISK_PIPS}"
                ),

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
                )
        }


    tp2_pips = max(
        MIN_TP2_PIPS,
        risk_pips * 2.0
    )

    tp3_pips = max(
        TP3_PIPS,
        risk_pips * 3.0
    )


    if direction == "BUY":

        tp1 = (
            entry
            +
            pips_to_price(
                TP1_PIPS
            )
        )

        tp2 = (
            entry
            +
            pips_to_price(
                tp2_pips
            )
        )

        tp3 = (
            entry
            +
            pips_to_price(
                tp3_pips
            )
        )

        geometry_ok = (
            sl
            <
            entry
            <
            tp1
            <
            tp2
            <
            tp3
        )

    else:

        tp1 = (
            entry
            -
            pips_to_price(
                TP1_PIPS
            )
        )

        tp2 = (
            entry
            -
            pips_to_price(
                tp2_pips
            )
        )

        tp3 = (
            entry
            -
            pips_to_price(
                tp3_pips
            )
        )

        geometry_ok = (
            sl
            >
            entry
            >
            tp1
            >
            tp2
            >
            tp3
        )


    if not geometry_ok:

        return {

            "valid":
                False,

            "reason":
                (
                    "TRADE PLAN "
                    "GEOMETRY INVALID"
                )
        }


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

        "geometry":
            "VALID",

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
                tp1
            ),

        "tp2":
            round_price(
                tp2
            ),

        "tp3":
            round_price(
                tp3
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

        "risk_level":
            (
                "LOW"

                if risk_pips <= 40

                else

                "MEDIUM"
            )
    }


# =========================================================
# FOREX FACTORY REAL NEWS
# =========================================================

def normalize_ff_event(
    event
):

    if not isinstance(
        event,
        dict
    ):

        return None


    country = str(
        event.get(
            "country",
            ""
        )
    ).strip().upper()


    impact = str(
        event.get(
            "impact",
            ""
        )
    ).strip().title()


    title = str(
        event.get(
            "title",
            ""
        )
    ).strip()


    event_dt = parse_dt(
        event.get(
            "date"
        )
    )


    if (
        not title
        or
        not country
    ):

        return None


    return {

        "title":
            title,

        "country":
            country,

        "impact":
            impact,

        "date":
            (
                event_dt.isoformat()

                if event_dt

                else

                None
            ),

        "forecast":
            event.get(
                "forecast"
            ),

        "previous":
            event.get(
                "previous"
            )
    }


def get_cached_news(
    previous_dashboard
):

    candidates = []


    local_cache = load_json(
        NEWS_CACHE_FILE
    )

    if local_cache:

        candidates.append(
            local_cache
        )


    dashboard_cache = (

        previous_dashboard.get(
            "_news_cache",
            {}
        )

        if isinstance(
            previous_dashboard,
            dict
        )

        else

        {}
    )


    if dashboard_cache:

        candidates.append(
            dashboard_cache
        )


    best = None

    best_time = None


    for cache in candidates:

        fetched_at = parse_dt(
            cache.get(
                "fetched_at"
            )
        )

        if fetched_at is None:

            continue

        if (
            best_time is None

            or

            fetched_at > best_time
        ):

            best = cache

            best_time = fetched_at


    return (
        best
        or
        {}
    )


def fetch_forexfactory_calendar(
    previous_dashboard
):

    reference = now_utc()


    cached = get_cached_news(
        previous_dashboard
    )


    cached_at = parse_dt(
        cached.get(
            "fetched_at"
        )
    )


    cached_age = age_minutes(
        cached_at,
        reference
    )


    if (
        cached

        and

        cached_age is not None

        and

        cached_age
        <=
        NEWS_CACHE_MINUTES
    ):

        return {

            "source":
                "FOREX FACTORY",

            "feed_status":
                "CACHE",

            "fetched_at":
                cached_at.isoformat(),

            "age_minutes":
                round(
                    cached_age,
                    1
                ),

            "events":
                cached.get(
                    "events",
                    []
                ),

            "error":
                None
        }


    try:

        response = requests.get(

            FOREX_FACTORY_JSON_URL,

            headers={

                "User-Agent":
                    "BosqueForexAI/5.1",

                "Accept":
                    "application/json"
            },

            timeout=20
        )


        response.raise_for_status()


        raw = response.json()


        if not isinstance(
            raw,
            list
        ):

            raise RuntimeError(
                (
                    "Forex Factory feed "
                    "did not return a list"
                )
            )


        events = []


        for item in raw:

            event = normalize_ff_event(
                item
            )

            if event:

                events.append(
                    event
                )


        if len(events) == 0:

            raise RuntimeError(
                (
                    "Forex Factory feed "
                    "returned zero "
                    "parseable events"
                )
            )


        fetched_at = (
            reference.isoformat()
        )


        cache = {

            "source":
                "FOREX FACTORY",

            "fetched_at":
                fetched_at,

            "events":
                events
        }


        save_json_atomic(
            NEWS_CACHE_FILE,
            cache
        )


        return {

            "source":
                "FOREX FACTORY",

            "feed_status":
                "LIVE",

            "fetched_at":
                fetched_at,

            "age_minutes":
                0.0,

            "events":
                events,

            "error":
                None
        }


    except Exception as exc:


        if (
            cached

            and

            cached_age is not None

            and

            cached_age
            <=
            NEWS_MAX_STALE_MINUTES
        ):

            return {

                "source":
                    "FOREX FACTORY",

                "feed_status":
                    "STALE CACHE",

                "fetched_at":
                    (
                        cached_at.isoformat()

                        if cached_at

                        else

                        None
                    ),

                "age_minutes":
                    round(
                        cached_age,
                        1
                    ),

                "events":
                    cached.get(
                        "events",
                        []
                    ),

                "error":
                    str(
                        exc
                    )
            }


        return {

            "source":
                "FOREX FACTORY",

            "feed_status":
                "ERROR",

            "fetched_at":
                (
                    cached_at.isoformat()

                    if cached_at

                    else

                    None
                ),

            "age_minutes":
                (
                    round(
                        cached_age,
                        1
                    )

                    if cached_age
                    is not None

                    else

                    None
                ),

            "events":
                [],

            "error":
                str(
                    exc
                )
        }


def evaluate_news_filter(
    feed,
    reference=None
):

    reference = (
        reference
        or
        now_utc()
    )


    reference = (
        reference
        .astimezone(
            timezone.utc
        )
    )


    feed_status = feed.get(
        "feed_status",
        "ERROR"
    )


    events = feed.get(
        "events",
        []
    )


    # =====================================================
    # FAIL CLOSED
    #
    # If we genuinely do not have calendar data,
    # News Filter = BLOCK.
    # =====================================================

    if feed_status == "ERROR":

        return {

            "ok":
                False,

            "status":
                "BLOCK",

            "filter":
                "BLOCK",

            "high_impact":
                "UNKNOWN",

            "event":
                None,

            "event_time":
                None,

            "minutes_to_news":
                None,

            "minutes_since_news":
                None,

            "reason":
                "NEWS FEED UNAVAILABLE",

            "feed_status":
                feed_status,

            "source":
                feed.get(
                    "source"
                ),

            "feed_age_minutes":
                feed.get(
                    "age_minutes"
                ),

            "error":
                feed.get(
                    "error"
                )
        }


    relevant = []


    for event in events:


        if (
            str(
                event.get(
                    "country",
                    ""
                )
            ).upper()
            !=
            "USD"
        ):

            continue


        if (
            str(
                event.get(
                    "impact",
                    ""
                )
            ).title()
            !=
            "High"
        ):

            continue


        event_dt = parse_dt(
            event.get(
                "date"
            )
        )


        if event_dt is None:

            continue


        diff = (

            event_dt

            -

            reference

        ).total_seconds() / 60.0


        relevant.append({

            **event,

            "_dt":
                event_dt,

            "_diff":
                diff
        })


    relevant.sort(

        key=lambda x:

        abs(
            x[
                "_diff"
            ]
        )
    )


    block_event = None

    caution_event = None


    for event in relevant:

        diff = event[
            "_diff"
        ]


        if (
            -NEWS_BLOCK_AFTER_MINUTES
            <=
            diff
            <=
            NEWS_BLOCK_BEFORE_MINUTES
        ):

            block_event = event

            break


        in_pre_caution = (

            NEWS_BLOCK_BEFORE_MINUTES
            <
            diff
            <=
            NEWS_CAUTION_BEFORE_MINUTES
        )


        in_post_caution = (

            -NEWS_CAUTION_AFTER_MINUTES
            <=
            diff
            <
            -NEWS_BLOCK_AFTER_MINUTES
        )


        if (
            caution_event is None

            and

            (
                in_pre_caution

                or

                in_post_caution
            )
        ):

            caution_event = event


    # =====================================================
    # BLOCK
    # =====================================================

    if block_event:

        diff = block_event[
            "_diff"
        ]


        if diff >= 0:

            reason = (
                "HIGH IMPACT USD "
                "NEWS APPROACHING"
            )

            minutes_to_news = round(
                diff,
                1
            )

            minutes_since_news = None

        else:

            reason = (
                "POST-NEWS COOLDOWN"
            )

            minutes_to_news = None

            minutes_since_news = round(
                abs(diff),
                1
            )


        return {

            "ok":
                False,

            "status":
                "BLOCK",

            "filter":
                "BLOCK",

            "high_impact":
                "YES",

            "event":
                block_event[
                    "title"
                ],

            "event_time":
                block_event[
                    "_dt"
                ].isoformat(),

            "minutes_to_news":
                minutes_to_news,

            "minutes_since_news":
                minutes_since_news,

            "reason":
                reason,

            "feed_status":
                feed_status,

            "source":
                feed.get(
                    "source"
                ),

            "feed_age_minutes":
                feed.get(
                    "age_minutes"
                ),

            "error":
                feed.get(
                    "error"
                )
        }


    # =====================================================
    # CAUTION
    # =====================================================

    if caution_event:

        diff = caution_event[
            "_diff"
        ]


        return {

            "ok":
                True,

            "status":
                "CAUTION",

            "filter":
                "PASS",

            "high_impact":
                "YES",

            "event":
                caution_event[
                    "title"
                ],

            "event_time":
                caution_event[
                    "_dt"
                ].isoformat(),

            "minutes_to_news":
                (
                    round(
                        diff,
                        1
                    )

                    if diff >= 0

                    else

                    None
                ),

            "minutes_since_news":
                (
                    round(
                        abs(diff),
                        1
                    )

                    if diff < 0

                    else

                    None
                ),

            "reason":
                (
                    "HIGH IMPACT USD "
                    "NEWS NEARBY"
                ),

            "feed_status":
                feed_status,

            "source":
                feed.get(
                    "source"
                ),

            "feed_age_minutes":
                feed.get(
                    "age_minutes"
                ),

            "error":
                feed.get(
                    "error"
                )
        }


    # =====================================================
    # CLEAR
    # =====================================================

    next_event = None


    future_events = [

        e

        for e in relevant

        if e[
            "_diff"
        ] > 0
    ]


    if future_events:

        next_event = min(

            future_events,

            key=lambda x:
            x[
                "_diff"
            ]
        )


    return {

        "ok":
            True,

        "status":
            "CLEAR",

        "filter":
            "PASS",

        "high_impact":
            (
                "YES"

                if next_event

                else

                "CLEAR"
            ),

        "event":
            (
                next_event[
                    "title"
                ]

                if next_event

                else

                None
            ),

        "event_time":
            (
                next_event[
                    "_dt"
                ].isoformat()

                if next_event

                else

                None
            ),

        "minutes_to_news":
            (
                round(
                    next_event[
                        "_diff"
                    ],
                    1
                )

                if next_event

                else

                None
            ),

        "minutes_since_news":
            None,

        "reason":
            (
                "NO HIGH IMPACT USD "
                "NEWS IN BLOCK WINDOW"
            ),

        "feed_status":
            feed_status,

        "source":
            feed.get(
                "source"
            ),

        "feed_age_minutes":
            feed.get(
                "age_minutes"
            ),

        "error":
            feed.get(
                "error"
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
# SIGNAL DUPLICATE / COOLDOWN
# =========================================================

def is_duplicate_or_cooldown(
    state,
    direction,
    opportunity,
    signal_id,
    timestamp
):

    if (
        signal_id

        and

        signal_id
        ==
        state.get(
            "last_signal_id"
        )
    ):

        return True


    last_time = parse_dt(
        state.get(
            "last_signal_sent"
        )
    )


    same_setup = (

        state.get(
            "last_signal_direction"
        )
        ==
        direction

        and

        state.get(
            "last_signal_opportunity"
        )
        ==
        opportunity
    )


    if (
        last_time

        and

        same_setup
    ):

        minutes = (

            timestamp.astimezone(
                timezone.utc
            )

            -

            last_time

        ).total_seconds() / 60.0


        if (
            0
            <=
            minutes
            <
            SIGNAL_COOLDOWN_MINUTES
        ):

            return True


    return False


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

        f"bot{TELEGRAM_BOT_TOKEN}/"

        "sendMessage"
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


    news_text = (
        news_result.get(
            "status",
            "UNKNOWN"
        )
    )


    return (

        "👑 BOSQUE FOREX AI\n\n"

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
        f"{volatility.get('atr_pips')} "
        f"pips\n"

        f"📰 News: "
        f"{news_text}\n\n"

        "⚠️ Manual confirmation required."
    )


# =========================================================
# JOURNAL
#
# dashboard_data.json is used as persistence backup.
# =========================================================

def load_journal(
    previous_dashboard=None
):

    local = load_json(
        JOURNAL_FILE
    )


    if (
        isinstance(
            local,
            dict
        )

        and

        isinstance(
            local.get(
                "trades"
            ),
            list
        )
    ):

        return local


    previous_dashboard = (

        previous_dashboard

        or

        load_previous_dashboard()
    )


    embedded = (

        previous_dashboard.get(
            "_journal_store",
            {}
        )

        if isinstance(
            previous_dashboard,
            dict
        )

        else

        {}
    )


    if (
        isinstance(
            embedded,
            dict
        )

        and

        isinstance(
            embedded.get(
                "trades"
            ),
            list
        )
    ):

        return embedded


    return {

        "version":
            "3.0",

        "symbol":
            SYMBOL,

        "created_at":
            now_my().isoformat(),

        "trades":
            []
    }


def save_journal(
    journal
):

    save_json_atomic(
        JOURNAL_FILE,
        journal
    )


def journal_signal(
    journal,
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

        return journal


    if not signal.get(
        "new_signal",
        False
    ):

        return journal


    if not plan.get(
        "valid",
        False
    ):

        return journal


    signal_id = signal.get(
        "id"
    )


    if not signal_id:

        return journal


    for trade in journal[
        "trades"
    ]:

        if trade.get(
            "signal_id"
        ) == signal_id:

            return journal


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


    save_journal(
        journal
    )


    return journal


# =========================================================
# JOURNAL OUTCOME
#
# WIN  = TP2
# LOSS = SL
#
# TP1 is tracked.
# No automatic BE rule is invented.
#
# If SL and TP are both inside same M5 candle,
# conservative assumption = SL first.
# =========================================================

def evaluate_trade_against_candle(
    trade,
    candle
):

    if trade.get(
        "result"
    ) != "OPEN":

        return False


    direction = trade.get(
        "direction"
    )


    entry = float(
        trade[
            "entry"
        ]
    )

    sl = float(
        trade[
            "sl"
        ]
    )

    tp1 = float(
        trade[
            "tp1"
        ]
    )

    tp2 = float(
        trade[
            "tp2"
        ]
    )

    tp3 = float(
        trade[
            "tp3"
        ]
    )


    high = float(
        candle[
            "high"
        ]
    )

    low = float(
        candle[
            "low"
        ]
    )


    candle_time = pd.to_datetime(
        candle[
            "datetime"
        ],
        utc=True
    ).to_pydatetime()


    trade_time = parse_dt(
        trade.get(
            "timestamp"
        )
    )


    if (
        trade_time

        and

        candle_time
        <=
        trade_time
    ):

        return False


    before = json.dumps(
        clean_for_json(
            trade
        ),
        sort_keys=True
    )


    if direction == "BUY":

        sl_hit = (
            low <= sl
        )

        tp1_hit = (
            high >= tp1
        )

        tp2_hit = (
            high >= tp2
        )

        tp3_hit = (
            high >= tp3
        )


        if sl_hit:

            trade.update({

                "result":
                    "LOSS",

                "result_pips":
                    round(
                        -price_to_pips(
                            entry - sl
                        ),
                        1
                    ),

                "result_r":
                    -1.0,

                "closed_at":
                    candle_time.isoformat(),

                "close_reason":
                    "SL HIT"
            })

        else:

            if tp1_hit:

                trade[
                    "tp1_hit"
                ] = True


            if tp3_hit:

                trade[
                    "tp3_hit"
                ] = True


            if tp2_hit:

                trade[
                    "tp2_hit"
                ] = True


                result_pips = (
                    price_to_pips(
                        tp2 - entry
                    )
                )


                trade.update({

                    "result":
                        "WIN",

                    "result_pips":
                        round(
                            result_pips,
                            1
                        ),

                    "result_r":
                        round(

                            result_pips

                            /

                            max(
                                float(
                                    trade[
                                        "risk_pips"
                                    ]
                                ),
                                0.0001
                            ),

                            3
                        ),

                    "closed_at":
                        candle_time.isoformat(),

                    "close_reason":
                        "TP2 HIT"
                })


    elif direction == "SELL":

        sl_hit = (
            high >= sl
        )

        tp1_hit = (
            low <= tp1
        )

        tp2_hit = (
            low <= tp2
        )

        tp3_hit = (
            low <= tp3
        )


        if sl_hit:

            trade.update({

                "result":
                    "LOSS",

                "result_pips":
                    round(
                        -price_to_pips(
                            sl - entry
                        ),
                        1
                    ),

                "result_r":
                    -1.0,

                "closed_at":
                    candle_time.isoformat(),

                "close_reason":
                    "SL HIT"
            })

        else:

            if tp1_hit:

                trade[
                    "tp1_hit"
                ] = True


            if tp3_hit:

                trade[
                    "tp3_hit"
                ] = True


            if tp2_hit:

                trade[
                    "tp2_hit"
                ] = True


                result_pips = (
                    price_to_pips(
                        entry - tp2
                    )
                )


                trade.update({

                    "result":
                        "WIN",

                    "result_pips":
                        round(
                            result_pips,
                            1
                        ),

                    "result_r":
                        round(

                            result_pips

                            /

                            max(
                                float(
                                    trade[
                                        "risk_pips"
                                    ]
                                ),
                                0.0001
                            ),

                            3
                        ),

                    "closed_at":
                        candle_time.isoformat(),

                    "close_reason":
                        "TP2 HIT"
                })


    after = json.dumps(
        clean_for_json(
            trade
        ),
        sort_keys=True
    )


    return (
        before
        !=
        after
    )


def update_journal_outcomes(
    journal,
    m5
):

    changed = False


    for trade in journal.get(
        "trades",
        []
    ):


        if trade.get(
            "result"
        ) != "OPEN":

            continue


        trade_time = parse_dt(
            trade.get(
                "timestamp"
            )
        )


        if trade_time is None:

            continue


        future = m5[

            m5["datetime"]

            >

            pd.Timestamp(
                trade_time
            )
        ]


        for _, candle in (
            future.iterrows()
        ):

            this_changed = (
                evaluate_trade_against_candle(
                    trade,
                    candle
                )
            )


            if this_changed:

                changed = True


            if trade.get(
                "result"
            ) != "OPEN":

                break


    if changed:

        save_journal(
            journal
        )


    return journal


# =========================================================
# JOURNAL STATISTICS
# =========================================================

def calculate_journal_stats(
    journal
):

    trades = journal.get(
        "trades",
        []
    )


    if not isinstance(
        trades,
        list
    ):

        trades = []


    open_trades = [

        t

        for t in trades

        if t.get(
            "result"
        ) == "OPEN"
    ]


    closed = [

        t

        for t in trades

        if t.get(
            "result"
        ) in [
            "WIN",
            "LOSS",
            "BE"
        ]
    ]


    wins = [

        t

        for t in closed

        if t.get(
            "result"
        ) == "WIN"
    ]


    losses = [

        t

        for t in closed

        if t.get(
            "result"
        ) == "LOSS"
    ]


    breakeven = [

        t

        for t in closed

        if t.get(
            "result"
        ) == "BE"
    ]


    result_r = []

    result_pips = []


    for trade in closed:


        if (
            trade.get(
                "result_r"
            )
            is not None
        ):

            try:

                result_r.append(
                    float(
                        trade[
                            "result_r"
                        ]
                    )
                )

            except Exception:

                pass


        if (
            trade.get(
                "result_pips"
            )
            is not None
        ):

            try:

                result_pips.append(
                    float(
                        trade[
                            "result_pips"
                        ]
                    )
                )

            except Exception:

                pass


    closed_count = len(
        closed
    )


    win_rate = (

        len(wins)

        /

        closed_count

        *

        100.0

        if closed_count

        else

        0.0
    )


    total_r = sum(
        result_r
    )


    average_r = (

        total_r

        /

        len(
            result_r
        )

        if result_r

        else

        0.0
    )


    total_pips = sum(
        result_pips
    )


    average_pips = (

        total_pips

        /

        len(
            result_pips
        )

        if result_pips

        else

        0.0
    )


    gross_profit_r = sum(

        value

        for value in result_r

        if value > 0
    )


    gross_loss_r = abs(

        sum(

            value

            for value in result_r

            if value < 0
        )
    )


    profit_factor = (

        gross_profit_r

        /

        gross_loss_r

        if gross_loss_r > 0

        else

        0.0
    )


    equity = 0.0

    peak = 0.0

    max_drawdown_r = 0.0


    for value in result_r:

        equity += value

        peak = max(
            peak,
            equity
        )

        max_drawdown_r = max(

            max_drawdown_r,

            peak - equity
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


    latest_trade = (

        trades[-1]

        if trades

        else

        None
    )


    latest_closed_trade = (

        closed[-1]

        if closed

        else

        None
    )


    return {

        "status":
            "ACTIVE",

        "tracking_model":
            (
                "TP1 TRACKED; "
                "TP2 = WIN/EXIT; "
                "SL = LOSS; "
                "NO AUTOMATIC BE RULE"
            ),

        "total_signals":
            len(
                trades
            ),

        "open_trades":
            len(
                open_trades
            ),

        "closed_trades":
            closed_count,

        "wins":
            len(
                wins
            ),

        "losses":
            len(
                losses
            ),

        "breakeven":
            len(
                breakeven
            ),

        "win_rate":
            round(
                win_rate,
                2
            ),

        "total_pips":
            round(
                total_pips,
                1
            ),

        "average_pips":
            round(
                average_pips,
                1
            ),

        "total_r":
            round(
                total_r,
                3
            ),

        "average_r":
            round(
                average_r,
                3
            ),

        "profit_factor":
            round(
                profit_factor,
                3
            ),

        "max_drawdown_r":
            round(
                max_drawdown_r,
                3
            ),

        "max_consecutive_losses":
            max_consecutive_losses,

        "latest_trade":
            latest_trade,

        "latest_closed_trade":
            latest_closed_trade,

        "last_updated":
            now_my().isoformat()
    }


# =========================================================
# BACKTEST STATUS
#
# IMPORTANT:
#
# This live engine does NOT pretend historical
# 2022-2025 testing has been run.
# =========================================================

def load_backtest_status():

    data = load_json(
        BACKTEST_FILE
    )


    if (
        isinstance(
            data,
            dict
        )

        and

        data.get(
            "status"
        ) in [
            "COMPLETED",
            "RUNNING",
            "FAILED"
        ]
    ):

        return data


    return {

        "status":
            "NOT RUN",

        "development_period":
            (
                "2022-2024 — "
                "DATASET REQUIRED"
            ),

        "out_of_sample":
            (
                "2025 — NOT RUN"
            ),

        "forward":
            (
                "2026 — "
                "LIVE JOURNAL ACTIVE"
            ),

        "note":
            (
                "No historical backtest "
                "result has been generated "
                "by this live engine."
            )
    }


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
            (
                f"Score {score} "
                f"< {MIN_SCORE}"
            )
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


    if (
        plan.get(
            "valid",
            False
        )

        and

        plan.get(
            "rr_tp2",
            0
        )
        <
        MIN_RR
    ):

        reasons.append(
            "RR TP2 below 1:2"
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
                        "Unknown news state"
                    )
                )
            )
        )


    return {

        "valid":
            len(
                reasons
            ) == 0,

        "reasons":
            reasons
    }


# =========================================================
# MAIN
# =========================================================

def main():

    timestamp_my = now_my()

    timestamp_utc = (
        timestamp_my
        .astimezone(
            timezone.utc
        )
    )


    previous_dashboard = (
        load_previous_dashboard()
    )


    # =====================================================
    # ONE TWELVE DATA REQUEST
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
    # JOURNAL
    # =====================================================

    journal = load_journal(
        previous_dashboard
    )


    journal = (
        update_journal_outcomes(
            journal,
            m5
        )
    )


    # =====================================================
    # MTF
    # =====================================================

    m15 = aggregate(
        m5,
        15
    )


    h1 = aggregate(
        m5,
        60
    )


    if (
        len(m15) < 50

        or

        len(h1) < 30
    ):

        raise RuntimeError(
            "Not enough MTF data"
        )


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
    # REGIME
    # =====================================================

    h1_range = analyze_range(
        h1
    )


    if h1_structure[
        "direction"
    ] in [
        "BULLISH",
        "BEARISH"
    ]:

        market_mode = (
            "TRENDING"
        )

    elif h1_range[
        "is_range"
    ]:

        market_mode = (
            "RANGING"
        )

    else:

        market_mode = (
            "NEUTRAL"
        )


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


    liquidity_levels = (
        calculate_liquidity_levels(
            m5,
            timestamp_my
        )
    )


    volatility = (
        volatility_analysis(
            m5
        )
    )


    session = get_session(
        timestamp_my
    )


    session_ok = (
        is_preferred_session(
            session
        )
    )


    # =====================================================
    # REAL NEWS
    # =====================================================

    news_feed = (
        fetch_forexfactory_calendar(
            previous_dashboard
        )
    )


    news_result = (
        evaluate_news_filter(
            news_feed,
            timestamp_utc
        )
    )


    # =====================================================
    # SETUP
    # =====================================================

    setup = detect_m15_setup(
        h1_structure,
        m15
    )


    # =====================================================
    # M5 CONFIRMATION
    # =====================================================

    m5_confirm = (
        m5_confirmation(
            m5,
            setup[
                "direction"
            ]
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

        setup[
            "direction"
        ],

        m5,

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
        gate[
            "valid"
        ]
    )


    score_ok = (
        score
        >=
        MIN_SCORE
    )


    setup_ok = (
        setup[
            "valid"
        ]
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
            "valid",
            False
        )

        and

        plan.get(
            "rr_tp2",
            0
        )
        >=
        MIN_RR
    )


    news_ok = (
        news_result[
            "ok"
        ]
    )


    # =====================================================
    # SIGNAL
    # =====================================================

    signal = {

        "active":
            False,

        "new_signal":
            False,

        "telegram_sent":
            False,

        "id":
            None,

        "direction":
            setup[
                "direction"
            ],

        "opportunity":
            setup[
                "opportunity"
            ],

        "score":
            score,

        "timestamp":
            timestamp_my.isoformat(),

        "status":
            "WAIT",

        "gate_reasons":
            gate[
                "reasons"
            ]
    }


    state = load_state(
        previous_dashboard
    )


    # =====================================================
    # VALID SIGNAL
    # =====================================================

    if valid_signal:


        signal_id = make_signal_id(

            setup[
                "direction"
            ],

            setup[
                "opportunity"
            ],

            plan[
                "entry"
            ],

            plan[
                "sl"
            ],

            plan[
                "tp2"
            ]
        )


        signal.update({

            "active":
                True,

            "id":
                signal_id,

            "status":
                "VALID",

            "entry":
                plan[
                    "entry"
                ],

            "sl":
                plan[
                    "sl"
                ],

            "tp1":
                plan[
                    "tp1"
                ],

            "tp2":
                plan[
                    "tp2"
                ],

            "tp3":
                plan[
                    "tp3"
                ],

            "risk_pips":
                plan[
                    "risk_pips"
                ],

            "rr_tp2":
                plan[
                    "rr_tp2"
                ]
        })


        duplicate = (
            is_duplicate_or_cooldown(

                state,

                setup[
                    "direction"
                ],

                setup[
                    "opportunity"
                ],

                signal_id,

                timestamp_my
            )
        )


        if not duplicate:


            message = format_telegram(

                setup[
                    "direction"
                ],

                setup[
                    "opportunity"
                ],

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


            signal[
                "new_signal"
            ] = True


            signal[
                "telegram_sent"
            ] = bool(
                sent
            )


            # Only mark Telegram as sent
            # when Telegram really accepts it.

            if sent:


                state.update({

                    "last_signal_id":
                        signal_id,

                    "last_signal_sent":
                        timestamp_my.isoformat(),

                    "last_signal_direction":
                        setup[
                            "direction"
                        ],

                    "last_signal_opportunity":
                        setup[
                            "opportunity"
                        ],

                    "last_telegram_success":
                        True
                })


                save_state(
                    state
                )


    # =====================================================
    # RECORD JOURNAL SIGNAL
    # =====================================================

    journal = journal_signal(

        journal,

        signal,

        setup,

        m5_confirm,

        plan,

        score,

        session,

        news_result,

        timestamp_my
    )


    journal = (
        update_journal_outcomes(
            journal,
            m5
        )
    )


    journal_stats = (
        calculate_journal_stats(
            journal
        )
    )


    forward_stats = {

        **journal_stats,

        "status":
            "FORWARD TESTING",

        "symbol":
            SYMBOL
    }


    save_json_atomic(
        FORWARD_FILE,
        forward_stats
    )


    # =====================================================
    # BACKTEST
    # =====================================================

    backtest = (
        load_backtest_status()
    )


    # =====================================================
    # DASHBOARD STATUS
    # =====================================================

    if valid_signal:

        dashboard_status = (
            "VALID SIGNAL"
        )

    elif not news_ok:

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
    # EXECUTABLE PLAN ONLY
    # =====================================================

    if valid_signal:

        dashboard_plan = plan

    else:

        dashboard_plan = {

            "valid":
                False,

            "status":
                "NO EXECUTABLE PLAN",

            "reason":
                (
                    "; ".join(
                        gate[
                            "reasons"
                        ]
                    )

                    if gate[
                        "reasons"
                    ]

                    else

                    "Signal gate not passed"
                )
        }


    # =====================================================
    # NEWS DASHBOARD
    # =====================================================

    dashboard_news = {

        "status":
            news_result[
                "status"
            ],

        "high_impact":
            news_result[
                "high_impact"
            ],

        "minutes_to_news":
            news_result[
                "minutes_to_news"
            ],

        "minutes_since_news":
            news_result[
                "minutes_since_news"
            ],

        "filter":
            news_result[
                "filter"
            ],

        "event":
            news_result[
                "event"
            ],

        "event_time":
            news_result[
                "event_time"
            ],

        "reason":
            news_result[
                "reason"
            ],

        "feed_status":
            news_result[
                "feed_status"
            ],

        "source":
            news_result[
                "source"
            ],

        "feed_age_minutes":
            news_result[
                "feed_age_minutes"
            ],

        "error":
            news_result[
                "error"
            ],

        "block_before_minutes":
            NEWS_BLOCK_BEFORE_MINUTES,

        "block_after_minutes":
            NEWS_BLOCK_AFTER_MINUTES
    }


    # =====================================================
    # JOURNAL DASHBOARD
    # =====================================================

    dashboard_journal = {

        "status":
            "ACTIVE",

        "enabled":
            True,

        "file":
            JOURNAL_FILE.name,

        "symbol":
            SYMBOL,

        **journal_stats
    }


    # =====================================================
    # EXPECTANCY UI COMPATIBILITY
    # =====================================================

    expectancy = {

        "status":
            "FORWARD TESTING",

        "trades":
            journal_stats[
                "closed_trades"
            ],

        "win_rate":
            journal_stats[
                "win_rate"
            ],

        "average_r":
            journal_stats[
                "average_r"
            ],

        "profit_factor":
            journal_stats[
                "profit_factor"
            ],

        "expectancy_r":
            journal_stats[
                "average_r"
            ],

        "max_drawdown":
            journal_stats[
                "max_drawdown_r"
            ],

        "max_consecutive_losses":
            journal_stats[
                "max_consecutive_losses"
            ]
    }


    # =====================================================
    # DASHBOARD
    # =====================================================

    dashboard = {

        "engine": {

            "name":
                "BOSQUE FOREX AI",

            "version":
                "SCALPING V5.1 AUDITED",

            "symbol":
                SYMBOL,

            "timeframe":
                "H1 → M15 → M5",

            "timestamp":
                timestamp_my.isoformat(),

            "status":
                dashboard_status,

            "twelve_data_requests_this_scan":
                1,

            "news_source_requests_this_scan":
                (
                    0

                    if news_feed[
                        "feed_status"
                    ] == "CACHE"

                    else

                    1
                )
        },


        "latest_price":
            round_price(
                float(
                    m5.iloc[-1][
                        "close"
                    ]
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
                h1_range
        },


        "volatility":
            volatility,


        "pd":
            pd_data,


        "liquidity": {

            **liquidity,

            **liquidity_levels
        },


        "news":
            dashboard_news,


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


        "signal":
            signal,


        "plan":
            dashboard_plan,


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

            # INFORMATIONAL ONLY
            "session":
                session_ok,

            "session_blocking":
                False
        },


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
                news_result[
                    "reason"
                ]
        },


        "signal_gate": {

            "valid":
                valid_signal,

            "score":
                score,

            "minimum_score":
                MIN_SCORE,

            "reasons":
                gate[
                    "reasons"
                ]
        },


        "risk_engine": {

            "status":
                (
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


        "news_audit":
            dashboard_news,


        "invalidation": {

            "status":
                (
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

                (
                    "High-impact USD news block "
                    "overrides technical signal"
                )
            ]
        },


        "sop": {

            "news_filter":
                (
                    "PASS"

                    if news_ok

                    else

                    "BLOCK"
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

            # HONEST:
            # not implemented yet
            "fresh_zone":
                "NOT IMPLEMENTED",

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
        # REAL JOURNAL
        # =================================================

        "journal":
            dashboard_journal,


        # Frontend compatibility
        "trading_journal":
            dashboard_journal,


        # Frontend compatibility
        "expectancy":
            expectancy,


        # =================================================
        # REAL FORWARD TEST FROM LIVE SIGNAL JOURNAL
        # =================================================

        "forward_test":
            forward_stats,


        # =================================================
        # HONEST BACKTEST STATUS
        # =================================================

        "backtest":
            backtest,


        # =================================================
        # IMPLEMENTATION AUDIT
        # =================================================

        "implementation_status": {

            "live_engine":
                "ACTIVE",

            "telegram":
                (
                    "CONFIGURED"

                    if (
                        TELEGRAM_BOT_TOKEN
                        and
                        TELEGRAM_CHAT_ID
                    )

                    else

                    "NOT CONFIGURED"
                ),

            "journal":
                "ACTIVE",

            "forward_test":
                "ACTIVE",

            "news_filter":
                (
                    "ACTIVE"

                    if news_feed[
                        "feed_status"
                    ] != "ERROR"

                    else

                    "ERROR"
                ),

            "fresh_zone":
                "NOT IMPLEMENTED",

            "daily_loss_lock":
                "NOT CONFIGURED",

            "historical_backtest":
                backtest.get(
                    "status",
                    "NOT RUN"
                )
        },


        # =================================================
        # PERSISTENCE BACKUPS
        #
        # Important for GitHub Actions.
        # =================================================

        "_persistent_state":
            state,


        "_journal_store":
            journal,


        "_news_cache": {

            "source":
                news_feed[
                    "source"
                ],

            "fetched_at":
                news_feed[
                    "fetched_at"
                ],

            "events":
                news_feed[
                    "events"
                ]
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
    # OUTPUT
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