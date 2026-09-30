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
# SCALPING ENGINE V4.1
#
# H1 -> M15 -> M5
#
# V4.1 OBJECTIVES
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
# - News filter
# - Session context
# - Telegram alerts
# - Signal journal
# - Automatic journal outcome tracking
# - Forward-test statistics
# - Directional SL/TP validation
# - Strict valid-signal gating
# - Dashboard Journal Sync
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
# RISK / SCORING
# =========================================================

MIN_SCORE = 70

# =========================================================
# XAUUSD PIP MODEL
#
# 1 pip = 0.10 price movement
#
# Example:
#
# 4156.50 -> 4157.50
# = 10 pips
# =========================================================

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

    # -----------------------------------------------------
    # H1
    # -----------------------------------------------------

    if h1["direction"] in [
        "BULLISH",
        "BEARISH"
    ]:

        score += 15

    if h1["bos"]:

        score += 5

    # -----------------------------------------------------
    # M15
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # M5
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # PD
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # SESSION BONUS ONLY
    # -----------------------------------------------------

    if is_preferred_session(
        session
    ):

        score += 5

    # -----------------------------------------------------
    # VOLATILITY
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # RISK
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # TP
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # RR
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # FINAL GEOMETRY AUDIT
    # -----------------------------------------------------

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

    data = load_json(
        JOURNAL_FILE
    )

    if not data:

        data = {

            "version":
                "2.1",

            "symbol":
                SYMBOL,

            "created_at":
                now_my().isoformat(),

            "trades":
                []
        }

    if "trades" not in data:

        data["trades"] = []

    if "version" not in data:

        data["version"] = "2.1"

    if "symbol" not in data:

        data["symbol"] = SYMBOL

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

    if not signal_id:

        return

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

    # -----------------------------------------------------
    # BUY
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # SELL
    # -----------------------------------------------------

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

        trade_timestamp = pd.Timestamp(
            trade_time
        )

        candles = m5[
            m5["datetime"]
            >
            trade_timestamp
        ]

        for _, candle in candles.iterrows():

            old_state = (
                json.dumps(
                    trade,
                    sort_keys=True,
                    default=str
                )
            )

            evaluate_trade_against_candle(
                trade,
                candle
            )

            new_state = (
                json.dumps(
                    trade,
                    sort_keys=True,
                    default=str
                )
            )

            if old_state != new_state:

                changed = True

            if trade.get(
                "result"
            ) != "OPEN":

                break

    if changed:

        save_json_atomic(
            JOURNAL_FILE,
            journal
        )

    return journal


# =========================================================
# JOURNAL STATISTICS
# =========================================================

def calculate_journal_stats():

    journal = load_journal()

    trades = journal.get(
        "trades",
        []
    )

    if not isinstance(
        trades,
        list
    ):

        trades = []

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

    open_trades = [

        trade

        for trade in trades

        if trade.get(
            "result"
        ) == "OPEN"
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

    total_signals = len(
        trades
    )

    win_rate = (

        (
            len(wins)
            /
            total
        )
        *
        100

        if total > 0

        else

        0.0
    )

    result_pips = []

    for trade in closed:

        value = trade.get(
            "result_pips"
        )

        if value is None:

            continue

        try:

            result_pips.append(
                float(value)
            )

        except (
            TypeError,
            ValueError
        ):

            continue

    result_r = []

    for trade in closed:

        value = trade.get(
            "result_r"
        )

        if value is None:

            continue

        try:

            result_r.append(
                float(value)
            )

        except (
            TypeError,
            ValueError
        ):

            continue

    total_pips = (

        sum(result_pips)

        if result_pips

        else

        0.0
    )

    average_pips = (

        (
            sum(result_pips)
            /
            len(result_pips)
        )

        if result_pips

        else

        0.0
    )

    total_r = (

        sum(result_r)

        if result_r

        else

        0.0
    )

    average_r = (

        (
            sum(result_r)
            /
            len(result_r)
        )

        if result_r

        else

        0.0
    )

    # -----------------------------------------------------
    # EQUITY / DRAWDOWN
    # -----------------------------------------------------

    equity = 0.0

    peak = 0.0

    max_drawdown = 0.0

    for r in result_r:

        equity += r

        if equity > peak:

            peak = equity

        drawdown = (
            peak - equity
        )

        if drawdown > max_drawdown:

            max_drawdown = drawdown

    # -----------------------------------------------------
    # CONSECUTIVE LOSSES
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # LATEST TRADE
    # -----------------------------------------------------

    latest_trade = (

        trades[-1]

        if trades

        else

        None
    )

    latest_closed_trade = None

    for trade in reversed(
        closed
    ):

        latest_closed_trade = trade

        break

    stats = {

        "status":
            "ACTIVE",

        "symbol":
            SYMBOL,

        "total_signals":
            total_signals,

        "open_trades":
            len(open_trades),

        "closed_trades":
            total,

        "wins":
            len(wins),

        "losses":
            len(losses),

        "breakeven":
            len(breakeven),

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

        "max_drawdown_r":
            round(
                max_drawdown,
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

    return stats


# =========================================================
# FORWARD TEST
# =========================================================

def calculate_forward_stats():

    stats = calculate_journal_stats()

    data = {

        "status":
            "FORWARD TESTING",

        "symbol":
            SYMBOL,

        "total_signals":
            stats[
                "total_signals"
            ],

        "open_trades":
            stats[
                "open_trades"
            ],

        "closed_trades":
            stats[
                "closed_trades"
            ],

        "wins":
            stats[
                "wins"
            ],

        "losses":
            stats[
                "losses"
            ],

        "breakeven":
            stats[
                "breakeven"
            ],

        "win_rate":
            stats[
                "win_rate"
            ],

        "total_pips":
            stats[
                "total_pips"
            ],

        "average_pips":
            stats[
                "average_pips"
            ],

        "total_r":
            stats[
                "total_r"
            ],

        "average_r":
            stats[
                "average_r"
            ],

        "max_drawdown_r":
            stats[
                "max_drawdown_r"
            ],

        "max_consecutive_losses":
            stats[
                "max_consecutive_losses"
            ],

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
    news_ok
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

    if not news_ok:

        reasons.append(
            "News filter BLOCK"
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
    # FETCH
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
    # UPDATE EXISTING JOURNAL OUTCOMES FIRST
    # =====================================================

    journal = update_journal_outcomes(
        m5
    )

    forward_stats = (
        calculate_forward_stats()
    )

    journal_stats = (
        calculate_journal_stats()
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
    # NEWS
    # =====================================================

    news = {

        "status":
            "CLEAR",

        "high_impact":
            "CLEAR",

        "minutes_to_news":
            None,

        "filter":
            "PASS"
    }

    news_result = (
        evaluate_news_filter(
            news
        )
    )

    news_ok = (
        news_result["ok"]
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
        news_ok
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
            gate["reasons"]
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

    # =====================================================
    # RECALCULATE JOURNAL
    # =====================================================

    journal_stats = (
        calculate_journal_stats()
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
    # JOURNAL DASHBOARD DATA
    #
    # IMPORTANT:
    #
    # We expose the values directly inside
    # dashboard["journal"] so frontend does not
    # need to guess / calculate them.
    #
    # Zero is used instead of null for empty
    # statistics.
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

        "total_signals":
            int(
                journal_stats[
                    "total_signals"
                ]
            ),

        "open_trades":
            int(
                journal_stats[
                    "open_trades"
                ]
            ),

        "closed_trades":
            int(
                journal_stats[
                    "closed_trades"
                ]
            ),

        "wins":
            int(
                journal_stats[
                    "wins"
                ]
            ),

        "losses":
            int(
                journal_stats[
                    "losses"
                ]
            ),

        "breakeven":
            int(
                journal_stats[
                    "breakeven"
                ]
            ),

        "win_rate":
            float(
                journal_stats[
                    "win_rate"
                ]
            ),

        "total_pips":
            float(
                journal_stats[
                    "total_pips"
                ]
            ),

        "average_pips":
            float(
                journal_stats[
                    "average_pips"
                ]
            ),

        "total_r":
            float(
                journal_stats[
                    "total_r"
                ]
            ),

        "average_r":
            float(
                journal_stats[
                    "average_r"
                ]
            ),

        "max_drawdown_r":
            float(
                journal_stats[
                    "max_drawdown_r"
                ]
            ),

        "max_consecutive_losses":
            int(
                journal_stats[
                    "max_consecutive_losses"
                ]
            ),

        "latest_trade":
            journal_stats[
                "latest_trade"
            ],

        "latest_closed_trade":
            journal_stats[
                "latest_closed_trade"
            ],

        "last_updated":
            journal_stats[
                "last_updated"
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
                "SCALPING V4.1",

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
                ]
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
        #
        # THIS IS THE IMPORTANT FIX.
        # All values are directly exposed.
        # =================================================

        "journal":
            dashboard_journal,

        # =================================================
        # FRONTEND ALIAS
        #
        # Some dashboard versions may use
        # "trading_journal" instead of "journal".
        # =================================================

        "trading_journal":
            dashboard_journal,

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
                    "after V4 live signal validation."
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