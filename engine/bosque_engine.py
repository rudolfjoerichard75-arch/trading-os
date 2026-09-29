import os
import json
import math
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd


# =========================================================
# BOSQUE FOREX AI
# SCALPING ENGINE V4
#
# H1 → M15 → M5
#
# FEATURES
# ---------------------------------------------------------
# - Twelve Data M5
# - ONE Twelve Data request per scan
# - Local M15 / H1 aggregation
# - H1 market structure
# - M15 setup
# - M5 confirmation
# - Liquidity sweep
# - Premium / Discount
# - Market regime
# - ATR volatility
# - Risk engine
# - News filter foundation
# - Session context
# - Session NEVER blocks valid signal
# - Telegram alerts
# - Signal journal
# - Automatic journal outcome tracking
# - Forward test statistics
# - Rolling backtest foundation
# - Profit factor
# - Total pips
# - Average R
# - Win rate
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
# ENGINE SETTINGS
# =========================================================

MIN_SCORE = 70

# XAUUSD
#
# 1 pip = 0.10 price
#
# Example:
#
# 4300.00 -> 4301.00
#
# = 10 pips

PIP_SIZE = 0.10


# =========================================================
# RISK
# =========================================================

MIN_RISK_PIPS = 25

MAX_RISK_PIPS = 80

TP1_PIPS = 60

MIN_TP2_PIPS = 120

TP3_PIPS = 180

MIN_RR = 2.0


# =========================================================
# TEST SETTINGS
# =========================================================

BACKTEST_ENABLED = True

BACKTEST_MIN_BARS = 150

BACKTEST_MAX_HOLD_BARS = 72

BACKTEST_ONE_TRADE_AT_A_TIME = True

FORWARD_MAX_HOLD_BARS = 72


# =========================================================
# MALAYSIA TIME
# =========================================================

MY_TZ = timezone(
    timedelta(hours=8)
)


# =========================================================
# SESSION
# =========================================================

SESSIONS = [

    (
        "ASIAN",
        7,
        15
    ),

    (
        "LONDON",
        15,
        20
    ),

    (
        "NEW YORK",
        20,
        23
    ),

    (
        "NEW YORK",
        0,
        1
    )
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


def clean_for_json(
    value
):

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
        list
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

            return json.load(f)

    except Exception:

        return {}


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


# =========================================================
# SESSION
# =========================================================

def get_session(
    dt=None
):

    dt = dt or now_my()

    hour = dt.hour

    for name, start, end in SESSIONS:

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

    return remove_incomplete_candle(
        df
    )


# =========================================================
# REMOVE INCOMPLETE CANDLE
# =========================================================

def remove_incomplete_candle(
    df
):

    if df.empty:

        return df

    last_time = df.iloc[-1]["datetime"]

    if last_time.tzinfo is None:

        last_time = last_time.replace(
            tzinfo=timezone.utc
        )

    now_utc = datetime.now(
        timezone.utc
    )

    elapsed = (
        now_utc
        -
        last_time
    ).total_seconds()

    if elapsed < 300:

        return df.iloc[:-1].copy()

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
            df.iloc[i]["high"]
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
            left_values.max()

            and

            value
            >=
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
            df.iloc[i]["low"]
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
            left_values.min()

            and

            value
            <=
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

    if highs:

        latest_high = highs[-1]["price"]

        close = float(
            df.iloc[-1]["close"]
        )

        if close > latest_high:

            bos = "BULLISH BOS"

    if lows:

        latest_low = lows[-1]["price"]

        close = float(
            df.iloc[-1]["close"]
        )

        if close < latest_low:

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
            )
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
# PREMIUM / DISCOUNT
# =========================================================

def pd_zone(
    df,
    lookback=20
):

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

        swing_high = highs[-1]["price"]

        current_high = float(
            df.iloc[-1]["high"]
        )

        current_close = float(
            df.iloc[-1]["close"]
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
            ] = swing_high

            result[
                "description"
            ] = (
                "BUY-SIDE LIQUIDITY SWEPT"
            )

    if lows:

        swing_low = lows[-1]["price"]

        current_low = float(
            df.iloc[-1]["low"]
        )

        current_close = float(
            df.iloc[-1]["close"]
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
# CANDLE
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

        c["close"]
        >
        c["open"]

        and

        ratio >= 0.55
    )

    bearish = (

        c["close"]
        <
        c["open"]

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

    previous_close = (
        df["close"]
        .shift(1)
    )

    tr1 = (
        df["high"]
        -
        df["low"]
    )

    tr2 = abs(
        df["high"]
        -
        previous_close
    )

    tr3 = abs(
        df["low"]
        -
        previous_close
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

    atr_value = (
        tr.rolling(
            period
        )
        .mean()
        .iloc[-1]
    )

    if pd.isna(
        atr_value
    ):

        return None

    return float(
        atr_value
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

    range_info = analyze_range(
        m15
    )

    direction = None

    opportunity = (
        "NO VALID SETUP"
    )

    reason = []

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

            h1["direction"]
            ==
            "BULLISH"

            and

            m15_pd["zone"]
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

            h1["direction"]
            ==
            "BEARISH"

            and

            m15_pd["zone"]
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

    # H1
    if h1[
        "direction"
    ] in [
        "BULLISH",
        "BEARISH"
    ]:

        score += 15

    if h1["bos"]:

        score += 5

    # M15
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

    # M5
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

    # PD
    direction = (
        m15_setup[
            "direction"
        ]
    )

    zone = (
        m15_setup[
            "pd"
        ]["zone"]
    )

    if (

        direction
        ==
        "BUY"

        and

        zone
        ==
        "DISCOUNT"

    ):

        score += 5

    elif (

        direction
        ==
        "SELL"

        and

        zone
        ==
        "PREMIUM"

    ):

        score += 5

    # Preferred session bonus ONLY.
    #
    # IMPORTANT:
    # This is NOT a hard gate.

    if is_preferred_session(
        session
    ):

        score += 5

    # Volatility

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
    volatility
):

    if not direction:

        return {

            "valid":
                False,

            "reason":
                "NO DIRECTION"
        }

    entry = float(
        m5.iloc[-1]["close"]
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
            atr * 0.20
        )

    if direction == "BUY":

        if swing_low is None:

            return {

                "valid":
                    False,

                "reason":
                    "NO SWING LOW"
            }

        sl = (
            float(swing_low)
            -
            buffer
        )

    else:

        if swing_high is None:

            return {

                "valid":
                    False,

                "reason":
                    "NO SWING HIGH"
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
                    f"Risk {risk_pips:.1f} pips "
                    f"outside "
                    f"{MIN_RISK_PIPS}-"
                    f"{MAX_RISK_PIPS}"
                )
        }

    tp2_pips = max(
        MIN_TP2_PIPS,
        risk_pips * 2
    )

    tp3_pips = max(
        TP3_PIPS,
        risk_pips * 3
    )

    tp1_price = (

        entry
        +
        pips_to_price(
            TP1_PIPS
        )

        if direction == "BUY"

        else

        entry
        -
        pips_to_price(
            TP1_PIPS
        )
    )

    tp2_price = (

        entry
        +
        pips_to_price(
            tp2_pips
        )

        if direction == "BUY"

        else

        entry
        -
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

        if direction == "BUY"

        else

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
            TP1_PIPS,

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
        )
    }


# =========================================================
# NEWS FILTER
# =========================================================

def evaluate_news_filter(
    news
):

    high_impact = str(
        news.get(
            "high_impact",
            ""
        )
    ).strip().upper()

    minutes = news.get(
        "minutes_to_news"
    )

    clear_states = {

        "CLEAR",
        "NONE",
        "NO",
        "NO HIGH IMPACT",
        "FALSE",
        "0"
    }

    blocked_states = {

        "BLOCK",
        "HIGH IMPACT",
        "YES",
        "TRUE",
        "1"
    }

    # CLEAR
    if high_impact in clear_states:

        return {

            "ok":
                True,

            "status":
                "PASS",

            "high_impact":
                "CLEAR",

            "minutes":
                (
                    minutes
                    if minutes is not None
                    else
                    "NOT PROVIDED"
                )
        }

    # BLOCK
    if high_impact in blocked_states:

        return {

            "ok":
                False,

            "status":
                "BLOCK",

            "high_impact":
                high_impact,

            "minutes":
                (
                    minutes
                    if minutes is not None
                    else
                    "NOT PROVIDED"
                )
        }

    # UNKNOWN
    return {

        "ok":
            False,

        "status":
            "BLOCK",

        "high_impact":
            (
                high_impact
                if high_impact
                else
                "NOT PROVIDED"
            ),

        "minutes":
            (
                minutes
                if minutes is not None
                else
                "NOT PROVIDED"
            )
    }


# =========================================================
# SIGNAL ID
# =========================================================

def make_signal_id(
    direction,
    opportunity,
    candle_time,
    entry,
    sl,
    tp2
):

    raw = (

        f"{direction}|"
        f"{opportunity}|"
        f"{candle_time}|"
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
        f"{volatility.get('atr_pips')} pips\n"

        f"📰 News: "
        f"{news_result['status']}\n\n"

        "⚠️ Manual confirmation required."
    )


# =========================================================
# JOURNAL
# =========================================================

def load_journal():

    journal = load_json(
        JOURNAL_FILE
    )

    if not journal:

        journal = {

            "version":
                "2.0",

            "symbol":
                SYMBOL,

            "trades":
                []
        }

    journal.setdefault(
        "trades",
        []
    )

    return journal


def journal_signal(
    signal,
    setup,
    m5_confirm,
    plan,
    score,
    session,
    news_result,
    timestamp,
    candle_time
):

    if not signal.get(
        "active",
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

        "signal_candle_time":
            candle_time.isoformat(),

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
            news_result[
                "status"
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

        "exit_price":
            None,

        "exit_reason":
            None,

        "bars_held":
            None,

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
# RESOLVE JOURNAL TRADE
# =========================================================

def resolve_trade(
    trade,
    df,
    max_bars=72
):

    if trade.get(
        "result"
    ) != "OPEN":

        return trade

    try:

        direction = trade[
            "direction"
        ]

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

        tp2 = float(
            trade[
                "tp2"
            ]
        )

        signal_time = pd.to_datetime(
            trade[
                "signal_candle_time"
            ],
            utc=True
        )

    except Exception:

        return trade

    times = pd.to_datetime(
        df["datetime"],
        utc=True
    )

    future = df[
        times
        >
        signal_time
    ].head(
        max_bars
    ).copy()

    if future.empty:

        return trade

    risk_pips = (

        price_to_pips(
            entry - sl
        )

        if direction == "BUY"

        else

        price_to_pips(
            sl - entry
        )
    )

    for bar_number, (
        index,
        row
    ) in enumerate(
        future.iterrows(),
        start=1
    ):

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        candle_time = pd.to_datetime(
            row["datetime"],
            utc=True
        )

        if direction == "BUY":

            sl_hit = (
                low <= sl
            )

            tp_hit = (
                high >= tp2
            )

            win_pips = price_to_pips(
                tp2 - entry
            )

            loss_pips = price_to_pips(
                entry - sl
            )

        else:

            sl_hit = (
                high >= sl
            )

            tp_hit = (
                low <= tp2
            )

            win_pips = price_to_pips(
                entry - tp2
            )

            loss_pips = price_to_pips(
                sl - entry
            )

        # Conservative rule:
        # If SL and TP are both touched
        # inside the same candle,
        # count SL first.

        if sl_hit:

            trade.update({

                "result":
                    "LOSS",

                "result_pips":
                    round(
                        -loss_pips,
                        1
                    ),

                "result_r":
                    -1.0,

                "closed_at":
                    candle_time.isoformat(),

                "exit_price":
                    round_price(
                        sl
                    ),

                "exit_reason":
                    (
                        "SL"
                        if not tp_hit
                        else
                        "SL_AND_TP_SAME_CANDLE_SL_FIRST"
                    ),

                "bars_held":
                    bar_number
            })

            return trade

        if tp_hit:

            trade.update({

                "result":
                    "WIN",

                "result_pips":
                    round(
                        win_pips,
                        1
                    ),

                "result_r":
                    round(
                        win_pips
                        /
                        max(
                            risk_pips,
                            0.0001
                        ),
                        3
                    ),

                "closed_at":
                    candle_time.isoformat(),

                "exit_price":
                    round_price(
                        tp2
                    ),

                "exit_reason":
                    "TP2",

                "bars_held":
                    bar_number
            })

            return trade

    # TIME EXIT

    if len(future) >= max_bars:

        row = future.iloc[-1]

        close = float(
            row["close"]
        )

        if direction == "BUY":

            movement = price_to_pips(
                close - entry
            )

        else:

            movement = price_to_pips(
                entry - close
            )

        if abs(movement) < 5:

            result = "BE"

        elif movement > 0:

            result = "WIN"

        else:

            result = "LOSS"

        trade.update({

            "result":
                result,

            "result_pips":
                round(
                    movement,
                    1
                ),

            "result_r":
                round(
                    movement
                    /
                    max(
                        risk_pips,
                        0.0001
                    ),
                    3
                ),

            "closed_at":
                pd.to_datetime(
                    row["datetime"],
                    utc=True
                ).isoformat(),

            "exit_price":
                round_price(
                    close
                ),

            "exit_reason":
                "TIME_EXIT",

            "bars_held":
                max_bars
        })

    return trade


# =========================================================
# UPDATE JOURNAL OUTCOMES
# =========================================================

def update_journal_outcomes(
    df
):

    journal = load_journal()

    changed = False

    for i, trade in enumerate(
        journal["trades"]
    ):

        before = json.dumps(
            trade,
            sort_keys=True
        )

        journal["trades"][i] = (
            resolve_trade(
                trade,
                df,
                FORWARD_MAX_HOLD_BARS
            )
        )

        after = json.dumps(
            journal["trades"][i],
            sort_keys=True
        )

        if before != after:

            changed = True

    if changed:

        save_json_atomic(
            JOURNAL_FILE,
            journal
        )

    return journal


# =========================================================
# STATISTICS
# =========================================================

def calculate_stats(
    trades
):

    closed = [

        trade

        for trade in trades

        if trade.get(
            "result"
        ) in [
            "WIN",
            "LOSS",
            "BE"
        ]
    ]

    wins = [

        trade

        for trade in closed

        if trade.get(
            "result"
        ) == "WIN"
    ]

    losses = [

        trade

        for trade in closed

        if trade.get(
            "result"
        ) == "LOSS"
    ]

    breakeven = [

        trade

        for trade in closed

        if trade.get(
            "result"
        ) == "BE"
    ]

    total = len(
        closed
    )

    result_pips = [

        float(
            trade.get(
                "result_pips"
            )
            or
            0
        )

        for trade in closed
    ]

    result_r = [

        float(
            trade.get(
                "result_r"
            )
        )

        for trade in closed

        if trade.get(
            "result_r"
        ) is not None
    ]

    gross_profit_r = sum(

        x

        for x in result_r

        if x > 0
    )

    gross_loss_r = abs(
        sum(

            x

            for x in result_r

            if x < 0
        )
    )

    profit_factor = (

        gross_profit_r
        /
        gross_loss_r

        if gross_loss_r > 0

        else

        None
    )

    return {

        "trades":
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
                    len(wins)
                    /
                    total
                    *
                    100,
                    2
                )

                if total

                else
                None
            ),

        "total_pips":
            round(
                sum(
                    result_pips
                ),
                1
            ),

        "average_pips":
            (
                round(
                    sum(
                        result_pips
                    )
                    /
                    len(
                        result_pips
                    ),
                    1
                )

                if result_pips

                else
                None
            ),

        "average_r":
            (
                round(
                    sum(result_r)
                    /
                    len(result_r),
                    3
                )

                if result_r

                else
                None
            ),

        "profit_factor":
            (
                round(
                    profit_factor,
                    3
                )

                if profit_factor is not None

                else
                None
            )
    }


# =========================================================
# FORWARD TEST
# =========================================================

def update_forward_test(
    df
):

    journal = update_journal_outcomes(
        df
    )

    stats = calculate_stats(
        journal[
            "trades"
        ]
    )

    open_trades = sum(

        trade.get(
            "result"
        )
        ==
        "OPEN"

        for trade in journal[
            "trades"
        ]
    )

    data = {

        "status":
            "FORWARD TESTING",

        "symbol":
            SYMBOL,

        **stats,

        "open_trades":
            open_trades,

        "last_updated":
            now_my().isoformat()
    }

    save_json_atomic(
        FORWARD_FILE,
        data
    )

    return data


# =========================================================
# BACKTEST SIGNAL ENGINE
# =========================================================

def generate_historical_signal(
    df,
    index
):

    if index < BACKTEST_MIN_BARS:

        return None

    window = df.iloc[
        :index + 1
    ].copy()

    m15 = aggregate(
        window,
        15
    )

    h1 = aggregate(
        window,
        60
    )

    if (

        len(m15) < 50

        or

        len(h1) < 30

    ):

        return None

    h1_structure = (
        analyze_structure(
            h1
        )
    )

    setup = detect_m15_setup(
        h1_structure,
        m15
    )

    confirmation = (
        m5_confirmation(
            window,
            setup[
                "direction"
            ]
        )
    )

    candle_time = pd.to_datetime(
        window.iloc[-1][
            "datetime"
        ],
        utc=True
    )

    local_time = (
        candle_time
        .to_pydatetime()
        .astimezone(
            MY_TZ
        )
    )

    current_session = get_session(
        local_time
    )

    current_volatility = (
        volatility_analysis(
            window
        )
    )

    current_score = calculate_score(

        h1_structure,

        setup,

        confirmation,

        current_session,

        current_volatility
    )

    trade_plan = create_trade_plan(

        setup[
            "direction"
        ],

        window,

        current_volatility
    )

    valid = all([

        current_score >= MIN_SCORE,

        setup[
            "valid"
        ],

        confirmation[
            "confirmed"
        ],

        trade_plan.get(
            "valid",
            False
        ),

        trade_plan.get(
            "rr_tp2",
            0
        )
        >=
        MIN_RR
    ])

    if not valid:

        return None

    return {

        "signal_id":
            make_signal_id(

                setup[
                    "direction"
                ],

                setup[
                    "opportunity"
                ],

                candle_time.isoformat(),

                trade_plan[
                    "entry"
                ],

                trade_plan[
                    "sl"
                ],

                trade_plan[
                    "tp2"
                ]
            ),

        "candle_time":
            candle_time.isoformat(),

        "direction":
            setup[
                "direction"
            ],

        "opportunity":
            setup[
                "opportunity"
            ],

        "score":
            current_score,

        "session":
            current_session,

        "entry":
            trade_plan[
                "entry"
            ],

        "sl":
            trade_plan[
                "sl"
            ],

        "tp2":
            trade_plan[
                "tp2"
            ],

        "risk_pips":
            trade_plan[
                "risk_pips"
            ]
    }


# =========================================================
# BACKTEST
# =========================================================

def run_backtest(
    df
):

    if not BACKTEST_ENABLED:

        return {

            "status":
                "DISABLED",

            "symbol":
                SYMBOL
        }

    if len(df) < (
        BACKTEST_MIN_BARS
        +
        30
    ):

        return {

            "status":
                "NOT ENOUGH DATA",

            "symbol":
                SYMBOL,

            "bars_used":
                len(df)
        }

    trades = []

    index = BACKTEST_MIN_BARS

    while index < (
        len(df) - 1
    ):

        signal = (
            generate_historical_signal(
                df,
                index
            )
        )

        if signal is None:

            index += 1

            continue

        future_df = df.iloc[
            index + 1:
        ].copy()

        trade = {

            "signal_id":
                signal[
                    "signal_id"
                ],

            "signal_candle_time":
                signal[
                    "candle_time"
                ],

            "direction":
                signal[
                    "direction"
                ],

            "opportunity":
                signal[
                    "opportunity"
                ],

            "score":
                signal[
                    "score"
                ],

            "session":
                signal[
                    "session"
                ],

            "entry":
                signal[
                    "entry"
                ],

            "sl":
                signal[
                    "sl"
                ],

            "tp2":
                signal[
                    "tp2"
                ],

            "risk_pips":
                signal[
                    "risk_pips"
                ],

            "result":
                "OPEN",

            "result_pips":
                None,

            "result_r":
                None,

            "closed_at":
                None,

            "exit_price":
                None,

            "exit_reason":
                None,

            "bars_held":
                None
        }

        trade = resolve_trade(

            trade,

            future_df,

            BACKTEST_MAX_HOLD_BARS
        )

        if trade.get(
            "result"
        ) != "OPEN":

            trades.append(
                trade
            )

        if (

            BACKTEST_ONE_TRADE_AT_A_TIME

            and

            trade.get(
                "closed_at"
            )

        ):

            close_time = pd.to_datetime(
                trade[
                    "closed_at"
                ],
                utc=True
            )

            all_times = pd.to_datetime(
                df[
                    "datetime"
                ],
                utc=True
            )

            closed_indexes = df.index[
                all_times
                <=
                close_time
            ]

            if len(
                closed_indexes
            ):

                index = (
                    int(
                        closed_indexes[-1]
                    )
                    +
                    1
                )

            else:

                index += 1

            continue

        index += 1

    stats = calculate_stats(
        trades
    )

    result = {

        "status":
            "COMPLETED",

        "symbol":
            SYMBOL,

        "timeframe":
            "M5",

        "strategy":
            "SCALPING V4 H1-M15-M5",

        "bars_used":
            len(df),

        "period_start":
            df.iloc[0][
                "datetime"
            ].isoformat(),

        "period_end":
            df.iloc[-1][
                "datetime"
            ].isoformat(),

        "min_score":
            MIN_SCORE,

        "min_rr":
            MIN_RR,

        "pip_size":
            PIP_SIZE,

        "stats":
            stats,

        "trades":
            trades,

        "note":
            (
                "Rolling replay of the "
                "already-fetched M5 dataset. "
                "No additional Twelve Data "
                "request is made."
            ),

        "last_updated":
            now_my().isoformat()
    }

    save_json_atomic(
        BACKTEST_FILE,
        result
    )

    return result


# =========================================================
# MAIN
# =========================================================

def main():

    timestamp = now_my()

    # =====================================================
    # ONE DATA REQUEST
    # =====================================================

    m5 = fetch_m5()

    if len(m5) < 100:

        raise RuntimeError(
            "Not enough M5 candles"
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

    h1_range = analyze_range(
        h1
    )

    if h1_structure[
        "direction"
    ] in [
        "BULLISH",
        "BEARISH"
    ]:

        market_mode = "TRENDING"

    elif h1_range[
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

    current_session = get_session(
        timestamp
    )

    preferred_session = (
        is_preferred_session(
            current_session
        )
    )

    # =====================================================
    # NEWS
    # =====================================================
    #
    # Current news layer:
    #
    # CLEAR = PASS
    #
    # NOT PROVIDED minutes:
    # does NOT block.
    #
    # BLOCK = BLOCK.
    #
    # ForexFactory layer can be connected
    # here later without changing the
    # rest of the engine.
    # =====================================================

    news = {

        "high_impact":
            "CLEAR",

        "minutes_to_news":
            None
    }

    news_result = (
        evaluate_news_filter(
            news
        )
    )

    # =====================================================
    # M15 SETUP
    # =====================================================

    setup = detect_m15_setup(
        h1_structure,
        m15
    )

    # =====================================================
    # M5 CONFIRMATION
    # =====================================================

    confirmation = (
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

        confirmation,

        current_session,

        volatility
    )

    # =====================================================
    # TRADE PLAN
    # =====================================================

    trade_plan = create_trade_plan(

        setup[
            "direction"
        ],

        m5,

        volatility
    )

    # =====================================================
    # HARD GATES
    # =====================================================

    filters = {

        "score":
            score >= MIN_SCORE,

        "setup":
            setup[
                "valid"
            ],

        "m5_confirmation":
            confirmation[
                "confirmed"
            ],

        "risk":
            trade_plan.get(
                "valid",
                False
            ),

        "rr":
            (
                trade_plan.get(
                    "rr_tp2",
                    0
                )
                >=
                MIN_RR
            ),

        "news":
            news_result[
                "ok"
            ],

        # Informational only.
        "session":
            preferred_session,

        # Explicitly disabled.
        "session_blocking":
            False
    }

    # =====================================================
    # SESSION NEVER BLOCKS
    # =====================================================

    valid_signal = all([

        filters[
            "score"
        ],

        filters[
            "setup"
        ],

        filters[
            "m5_confirmation"
        ],

        filters[
            "risk"
        ],

        filters[
            "rr"
        ],

        filters[
            "news"
        ]
    ])

    # =====================================================
    # CURRENT SIGNAL
    # =====================================================

    candle_time = pd.to_datetime(
        m5.iloc[-1][
            "datetime"
        ],
        utc=True
    )

    signal = {

        "active":
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
            timestamp.isoformat(),

        "candle_time":
            candle_time.isoformat()
    }

    state = load_json(
        STATE_FILE
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

            candle_time.isoformat(),

            trade_plan[
                "entry"
            ],

            trade_plan[
                "sl"
            ],

            trade_plan[
                "tp2"
            ]
        )

        signal.update({

            "active":
                True,

            "id":
                signal_id,

            "entry":
                trade_plan[
                    "entry"
                ],

            "sl":
                trade_plan[
                    "sl"
                ],

            "tp1":
                trade_plan[
                    "tp1"
                ],

            "tp2":
                trade_plan[
                    "tp2"
                ],

            "tp3":
                trade_plan[
                    "tp3"
                ]
        })

        # -------------------------------------------------
        # TELEGRAM DUPLICATE PROTECTION
        # -------------------------------------------------

        last_signal_id = state.get(
            "last_signal_id"
        )

        if signal_id != last_signal_id:

            message = format_telegram(

                setup[
                    "direction"
                ],

                setup[
                    "opportunity"
                ],

                score,

                trade_plan,

                current_session,

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
                ] = (
                    timestamp.isoformat()
                )

                save_json_atomic(
                    STATE_FILE,
                    state
                )

    # =====================================================
    # JOURNAL CURRENT SIGNAL
    # =====================================================

    journal_signal(

        signal,

        setup,

        confirmation,

        trade_plan,

        score,

        current_session,

        news_result,

        timestamp,

        candle_time
    )

    # =====================================================
    # UPDATE JOURNAL OUTCOMES
    # =====================================================

    forward_stats = (
        update_forward_test(
            m5
        )
    )

    # =====================================================
    # BACKTEST
    # =====================================================

    backtest_result = (
        run_backtest(
            m5
        )
    )

    # =====================================================
    # JOURNAL SUMMARY
    # =====================================================

    journal = load_journal()

    journal_stats = calculate_stats(
        journal[
            "trades"
        ]
    )

    open_trades = sum(

        trade.get(
            "result"
        )
        ==
        "OPEN"

        for trade in journal[
            "trades"
        ]
    )

    # =====================================================
    # DASHBOARD
    # =====================================================

    dashboard = {

        "engine": {

            "name":
                "BOSQUE FOREX AI",

            "version":
                "SCALPING V4",

            "symbol":
                SYMBOL,

            "timeframe":
                "H1 → M15 → M5",

            "timestamp":
                timestamp.isoformat(),

            "data_requests_this_scan":
                1
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
            current_session,

        "market_mode":
            market_mode,

        # =================================================
        # REGIME
        # =================================================

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

        # =================================================
        # VOLATILITY
        # =================================================

        "volatility":
            volatility,

        # =================================================
        # PD
        # =================================================

        "pd":
            pd_data,

        # =================================================
        # LIQUIDITY
        # =================================================

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

        "news": {

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
                    "minutes"
                ],

            "filter":
                news_result[
                    "status"
                ]
        },

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
                setup[
                    "valid"
                ],

            "score":
                score
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
            trade_plan,

        # =================================================
        # POTENTIAL
        # =================================================

        "potential": {

            "tp1_pips":
                trade_plan.get(
                    "tp1_pips"
                ),

            "tp2_pips":
                trade_plan.get(
                    "tp2_pips"
                ),

            "tp3_pips":
                trade_plan.get(
                    "tp3_pips"
                )
        },

        # =================================================
        # H1
        # =================================================

        "h1": {

            **h1_structure,

            "condition":
                market_mode
        },

        # =================================================
        # M15
        # =================================================

        "m15": {

            **m15_structure,

            "setup":
                setup
        },

        # =================================================
        # M5
        # =================================================

        "m5": {

            **m5_structure,

            "confirmation":
                confirmation
        },

        # =================================================
        # FILTERS
        # =================================================

        "filters": filters,

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
                confirmation[
                    "reason"
                ],

            "liquidity":
                liquidity[
                    "description"
                ],

            "pd_zone":
                pd_data[
                    "zone"
                ]
        },

        # =================================================
        # RISK ENGINE
        # =================================================

        "risk_engine": {

            "status": (

                "VALID"

                if filters[
                    "risk"
                ]

                else

                "INVALID"
            ),

            "risk_pips":
                trade_plan.get(
                    "risk_pips"
                ),

            "risk_level":
                trade_plan.get(
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

                "Avoid invalidation after structure failure"
            ]
        },

        # =================================================
        # SOP
        # =================================================

        "sop": {

            "news_filter": (

                "PASS"

                if news_result[
                    "ok"
                ]

                else

                "BLOCK"
            ),

            "session_filter": (

                "PREFERRED"

                if preferred_session

                else

                "NON-PREFERRED"
            ),

            "session_blocking":
                "NO",

            "risk":
                trade_plan.get(
                    "risk_level",
                    "UNKNOWN"
                ),

            "fresh_zone": (

                "YES"

                if setup[
                    "valid"
                ]

                else

                "NO"
            ),

            "m15_setup": (

                "YES"

                if filters[
                    "setup"
                ]

                else

                "NO"
            ),

            "m5_confirmation": (

                "YES"

                if filters[
                    "m5_confirmation"
                ]

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

            "open_trades":
                open_trades,

            **journal_stats
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
                backtest_result.get(
                    "status"
                ),

            "file":
                BACKTEST_FILE.name,

            "bars_used":
                backtest_result.get(
                    "bars_used"
                ),

            "period_start":
                backtest_result.get(
                    "period_start"
                ),

            "period_end":
                backtest_result.get(
                    "period_end"
                ),

            "stats":
                backtest_result.get(
                    "stats",
                    {}
                ),

            "note":
                backtest_result.get(
                    "note"
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