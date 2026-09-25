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
# SCALPING ENGINE V3
#
# FLOW:
# H1 CONTEXT
#     ↓
# M15 SETUP
#     ↓
# M5 CONFIRMATION
#     ↓
# SCORE
#     ↓
# RISK / RR
#     ↓
# TRADE PLAN
#     ↓
# TELEGRAM
#     ↓
# DASHBOARD
# =========================================================


# =========================================================
# PATHS
# =========================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"
STATE_FILE = ENGINE_DIR / "state.json"


# =========================================================
# API
# =========================================================

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# =========================================================
# MARKET
# =========================================================

SYMBOL = "XAU/USD"

INTERVAL = "5min"

# One Twelve Data request per scan.
OUTPUT_SIZE = 500


# =========================================================
# RISK / SCORING
# =========================================================

MIN_SCORE = 70

# XAUUSD pip model used by Bosque:
#
# 1 pip = 0.10 price movement
#
# Example:
#
# 2350.00 -> 2351.00
# = 10 pips
#
# 2350.00 -> 2355.00
# = 50 pips

PIP_SIZE = 0.10


# ---------------------------------------------------------
# Risk
# ---------------------------------------------------------

MIN_RISK_PIPS = 25
MAX_RISK_PIPS = 80

MIN_RR = 2.0


# ---------------------------------------------------------
# Targets
# ---------------------------------------------------------

TP1_PIPS = 60
MIN_TP2_PIPS = 120
TP3_PIPS = 180


# =========================================================
# TIMEZONE
# =========================================================

MY_TZ = timezone(
    timedelta(hours=8)
)


# =========================================================
# SESSION
# =========================================================

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
    return (
        datetime.now(timezone.utc)
        .astimezone(MY_TZ)
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

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, float):

        if math.isnan(value):
            return None

        if math.isinf(value):
            return None

    return value


def save_json_atomic(path, data):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = path.with_suffix(".tmp")

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


def load_state():

    if not STATE_FILE.exists():
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_state(state):

    save_json_atomic(
        STATE_FILE,
        state
    )


def price_to_pips(distance):

    return abs(
        float(distance)
    ) / PIP_SIZE


def pips_to_price(pips):

    return (
        float(pips) *
        PIP_SIZE
    )


def round_price(price):

    return round(
        float(price),
        2
    )


# =========================================================
# SESSION
# =========================================================

def get_session(dt=None):

    dt = dt or now_my()

    hour = dt.hour

    for name, start, end in SESSIONS:

        if start <= hour < end:
            return name

    return "OFF SESSION"


# =========================================================
# TWELVE DATA
# =========================================================

def fetch_m5():

    if not API_KEY:

        raise RuntimeError(
            "TWELVEDATA_API_KEY missing"
        )

    params = {

        "symbol": SYMBOL,

        "interval": INTERVAL,

        "outputsize": OUTPUT_SIZE,

        "apikey": API_KEY,

        "format": "JSON",

        "timezone": "UTC"
    }

    try:

        response = requests.get(
            TWELVEDATA_URL,
            params=params,
            timeout=30
        )

        response.raise_for_status()

    except requests.RequestException as exc:

        raise RuntimeError(
            f"Twelve Data request failed: {exc}"
        )

    try:

        data = response.json()

    except ValueError:

        raise RuntimeError(
            "Twelve Data returned invalid JSON"
        )

    if "values" not in data:

        message = data.get(
            "message",
            "Unknown Twelve Data error"
        )

        raise RuntimeError(
            f"Twelve Data error: {message}"
        )

    df = pd.DataFrame(
        data["values"]
    )

    if df.empty:

        raise RuntimeError(
            "No market data returned"
        )

    required_columns = [
        "datetime",
        "open",
        "high",
        "low",
        "close"
    ]

    missing = [
        col
        for col in required_columns
        if col not in df.columns
    ]

    if missing:

        raise RuntimeError(
            f"Missing columns: {missing}"
        )

    # -----------------------------------------------------
    # Datetime
    # -----------------------------------------------------

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True,
        errors="coerce"
    )

    # -----------------------------------------------------
    # OHLC
    # -----------------------------------------------------

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

def remove_incomplete_candle(df):

    if df.empty:
        return df

    last_time = df.iloc[-1]["datetime"]

    if not isinstance(
        last_time,
        pd.Timestamp
    ):
        last_time = pd.Timestamp(
            last_time
        )

    if last_time.tzinfo is None:

        last_time = last_time.tz_localize(
            "UTC"
        )

    now_utc = datetime.now(
        timezone.utc
    )

    elapsed = (
        now_utc -
        last_time.to_pydatetime()
    ).total_seconds()

    # -----------------------------------------------------
    # Keep only completed 5-minute candles.
    # -----------------------------------------------------

    if elapsed < 300:

        return df.iloc[:-1].copy()

    return df.copy()


# =========================================================
# AGGREGATION
# =========================================================

def aggregate(df, minutes):

    if df.empty:
        return df

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
        rule,
        label="left",
        closed="left"
    ).agg({

        "open": "first",

        "high": "max",

        "low": "min",

        "close": "last"
    })

    out = out.dropna()

    out = out.reset_index()

    return out


# =========================================================
# SWING HIGH
# =========================================================

def find_swing_highs(
    df,
    left=2,
    right=2
):

    highs = []

    if len(df) < (
        left +
        right +
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
            i - left:i
        ]["high"]

        right_values = df.iloc[
            i + 1:i + right + 1
        ]["high"]

        if (
            value > left_values.max()
            and
            value >= right_values.max()
        ):

            highs.append({

                "index": i,

                "price": value
            })

    return highs


# =========================================================
# SWING LOW
# =========================================================

def find_swing_lows(
    df,
    left=2,
    right=2
):

    lows = []

    if len(df) < (
        left +
        right +
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
            i - left:i
        ]["low"]

        right_values = df.iloc[
            i + 1:i + right + 1
        ]["low"]

        if (
            value < left_values.min()
            and
            value <= right_values.min()
        ):

            lows.append({

                "index": i,

                "price": value
            })

    return lows


# =========================================================
# STRUCTURE
# =========================================================

def analyze_structure(df):

    highs = find_swing_highs(df)

    lows = find_swing_lows(df)

    direction = "RANGE"

    bos = None

    latest_high = (
        highs[-1]["price"]
        if highs
        else None
    )

    latest_low = (
        lows[-1]["price"]
        if lows
        else None
    )

    # -----------------------------------------------------
    # HH / HL = bullish
    # LH / LL = bearish
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # BOS
    # -----------------------------------------------------

    current_close = float(
        df.iloc[-1]["close"]
    )

    if latest_high is not None:

        if current_close > latest_high:

            bos = "BULLISH BOS"

    if latest_low is not None:

        if current_close < latest_low:

            bos = "BEARISH BOS"

    return {

        "direction": direction,

        "bos": bos,

        "swing_high": latest_high,

        "swing_low": latest_low
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

            "is_range": False,

            "high": None,

            "low": None,

            "width": None,

            "location": None
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

    if width <= 0:

        location = 0.5

    else:

        location = (
            close - low
        ) / width

    # -----------------------------------------------------
    # Range definition
    #
    # Middle 60% = possible range
    # Very tight range = range
    # -----------------------------------------------------

    is_range = (

        0.20 <= location <= 0.80

        or

        width <
        close * 0.01
    )

    return {

        "is_range": bool(is_range),

        "high": round_price(high),

        "low": round_price(low),

        "width": round_price(width),

        "location": round(
            location,
            3
        )
    }


# =========================================================
# MARKET REGIME
# =========================================================

def determine_regime(
    h1_structure,
    h1_df
):

    direction = (
        h1_structure["direction"]
    )

    range_info = analyze_range(
        h1_df
    )

    if direction in [
        "BULLISH",
        "BEARISH"
    ]:

        return "TRENDING"

    if range_info["is_range"]:

        return "RANGING"

    return "NEUTRAL"


# =========================================================
# PREMIUM / DISCOUNT
# =========================================================

def pd_zone(
    df,
    lookback=20
):

    if len(df) < 2:

        return {

            "zone": "EQUILIBRIUM",

            "equilibrium": None,

            "high": None,

            "low": None
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

        "zone": zone,

        "equilibrium":
            round_price(
                equilibrium
            ),

        "high":
            round_price(high),

        "low":
            round_price(low)
    }


# =========================================================
# LIQUIDITY
# =========================================================

def liquidity_analysis(df):

    highs = find_swing_highs(df)

    lows = find_swing_lows(df)

    result = {

        "buy_side_sweep": False,

        "sell_side_sweep": False,

        "swept_level": None,

        "description": "NONE"
    }

    if len(highs) >= 1:

        swing_high = (
            highs[-1]["price"]
        )

        current_high = float(
            df.iloc[-1]["high"]
        )

        current_close = float(
            df.iloc[-1]["close"]
        )

        if (
            current_high >
            swing_high
            and
            current_close <
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
                "BUY-SIDE "
                "LIQUIDITY SWEPT"
            )

    if len(lows) >= 1:

        swing_low = (
            lows[-1]["price"]
        )

        current_low = float(
            df.iloc[-1]["low"]
        )

        current_close = float(
            df.iloc[-1]["close"]
        )

        if (
            current_low <
            swing_low
            and
            current_close >
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
                "SELL-SIDE "
                "LIQUIDITY SWEPT"
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

            "direction": "NEUTRAL",

            "strength": 0
        }

    closes = df["close"].tail(
        lookback + 1
    ).tolist()

    up = 0

    down = 0

    for i in range(
        1,
        len(closes)
    ):

        if closes[i] > closes[i - 1]:

            up += 1

        elif closes[i] < closes[i - 1]:

            down += 1

    if up >= 4:

        return {

            "direction": "BULLISH",

            "strength": up
        }

    if down >= 4:

        return {

            "direction": "BEARISH",

            "strength": down
        }

    return {

        "direction": "NEUTRAL",

        "strength":
            max(
                up,
                down
            )
    }


# =========================================================
# CANDLE CONFIRMATION
# =========================================================

def candle_confirmation(df):

    if df.empty:

        return {

            "bullish": False,

            "bearish": False,

            "body_ratio": 0
        }

    c = df.iloc[-1]

    open_price = float(
        c["open"]
    )

    high = float(
        c["high"]
    )

    low = float(
        c["low"]
    )

    close = float(
        c["close"]
    )

    body = abs(
        close - open_price
    )

    full_range = (
        high - low
    )

    if full_range <= 0:

        return {

            "bullish": False,

            "bearish": False,

            "body_ratio": 0
        }

    ratio = (
        body /
        full_range
    )

    bullish = (
        close > open_price
        and
        ratio >= 0.55
    )

    bearish = (
        close < open_price
        and
        ratio >= 0.55
    )

    return {

        "bullish": bool(
            bullish
        ),

        "bearish": bool(
            bearish
        ),

        "body_ratio": round(
            ratio,
            3
        )
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
        x["high"] -
        x["low"]
    )

    tr2 = abs(
        x["high"] -
        prev_close
    )

    tr3 = abs(
        x["low"] -
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
        .rolling(period)
        .mean()
        .iloc[-1]
    )

    if pd.isna(atr):

        return None

    return float(atr)


# =========================================================
# VOLATILITY
# =========================================================

def volatility_analysis(df):

    atr = calculate_atr(
        df
    )

    if atr is None:

        return {

            "atr": None,

            "atr_pips": None,

            "condition": "UNKNOWN"
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
            round_price(atr),

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
        h1["direction"]
    )

    m15_structure = (
        analyze_structure(m15)
    )

    m15_pd = pd_zone(
        m15
    )

    m15_liquidity = (
        liquidity_analysis(m15)
    )

    range_info = analyze_range(
        m15
    )

    direction = None

    opportunity = (
        "NO VALID SETUP"
    )

    reason = []

    # =====================================================
    # 1. RANGE REVERSAL
    # =====================================================

    if range_info["is_range"]:

        if (
            m15_liquidity[
                "sell_side_sweep"
            ]
            and
            range_info["location"]
            <= 0.25
        ):

            direction = "BUY"

            opportunity = (
                "BUY RANGE REVERSAL"
            )

            reason.append(
                "M15 range low + "
                "sell-side sweep"
            )

        elif (
            m15_liquidity[
                "buy_side_sweep"
            ]
            and
            range_info["location"]
            >= 0.75
        ):

            direction = "SELL"

            opportunity = (
                "SELL RANGE REVERSAL"
            )

            reason.append(
                "M15 range high + "
                "buy-side sweep"
            )

    # =====================================================
    # 2. LIQUIDITY SWEEP
    # =====================================================

    if direction is None:

        if m15_liquidity[
            "sell_side_sweep"
        ]:

            direction = "BUY"

            opportunity = (
                "BUY LIQUIDITY SWEEP"
            )

            reason.append(
                "M15 sell-side "
                "liquidity sweep"
            )

        elif m15_liquidity[
            "buy_side_sweep"
        ]:

            direction = "SELL"

            opportunity = (
                "SELL LIQUIDITY SWEEP"
            )

            reason.append(
                "M15 buy-side "
                "liquidity sweep"
            )

    # =====================================================
    # 3. TREND PULLBACK
    # =====================================================

    if direction is None:

        if (
            h1_direction ==
            "BULLISH"
            and
            m15_pd["zone"] ==
            "DISCOUNT"
        ):

            direction = "BUY"

            opportunity = (
                "BUY PULLBACK"
            )

            reason.append(
                "H1 bullish + "
                "M15 discount"
            )

        elif (
            h1_direction ==
            "BEARISH"
            and
            m15_pd["zone"] ==
            "PREMIUM"
        ):

            direction = "SELL"

            opportunity = (
                "SELL PULLBACK"
            )

            reason.append(
                "H1 bearish + "
                "M15 premium"
            )

    # =====================================================
    # 4. BREAKOUT
    # =====================================================

    if direction is None:

        if (
            m15_structure["bos"] ==
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
            m15_structure["bos"] ==
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

        "direction": direction,

        "opportunity": opportunity,

        "valid":
            direction is not None,

        "reason": reason,

        "pd": m15_pd,

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

    # =====================================================
    # BUY
    # =====================================================

    if expected_direction == "BUY":

        bos_ok = (
            bos ==
            "BULLISH BOS"
        )

        candle_ok = (

            candle["bullish"]

            and

            momentum_data[
                "direction"
            ] ==
            "BULLISH"
        )

        confirmed = (
            bos_ok
            or
            candle_ok
        )

        if bos_ok:

            reason = (
                "M5 bullish BOS"
            )

        elif candle_ok:

            reason = (
                "M5 bullish candle "
                "+ momentum"
            )

        else:

            reason = (
                "NO CONFIRMATION"
            )

        return {

            "confirmed":
                bool(confirmed),

            "bos": bos,

            "candle":
                bool(candle_ok),

            "momentum":
                momentum_data[
                    "direction"
                ],

            "reason": reason
        }

    # =====================================================
    # SELL
    # =====================================================

    if expected_direction == "SELL":

        bos_ok = (
            bos ==
            "BEARISH BOS"
        )

        candle_ok = (

            candle["bearish"]

            and

            momentum_data[
                "direction"
            ] ==
            "BEARISH"
        )

        confirmed = (
            bos_ok
            or
            candle_ok
        )

        if bos_ok:

            reason = (
                "M5 bearish BOS"
            )

        elif candle_ok:

            reason = (
                "M5 bearish candle "
                "+ momentum"
            )

        else:

            reason = (
                "NO CONFIRMATION"
            )

        return {

            "confirmed":
                bool(confirmed),

            "bos": bos,

            "candle":
                bool(candle_ok),

            "momentum":
                momentum_data[
                    "direction"
                ],

            "reason": reason
        }

    return {

        "confirmed": False,

        "bos": bos,

        "candle": False,

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
    # H1 CONTEXT
    # -----------------------------------------------------

    if h1["direction"] in [
        "BULLISH",
        "BEARISH"
    ]:

        score += 15

    if h1["bos"]:

        score += 5

    # -----------------------------------------------------
    # M15 SETUP
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

    if m15_setup[
        "structure"
    ].get("bos"):

        score += 10

    # -----------------------------------------------------
    # M5 CONFIRMATION
    # -----------------------------------------------------

    if m5_confirm[
        "confirmed"
    ]:

        score += 15

    if m5_confirm["bos"]:

        score += 10

    if m5_confirm["candle"]:

        score += 10

    # -----------------------------------------------------
    # PREMIUM / DISCOUNT
    # -----------------------------------------------------

    zone = (
        m15_setup[
            "pd"
        ]["zone"]
    )

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
    # SESSION
    # -----------------------------------------------------

    if session in [
        "LONDON",
        "NEW YORK"
    ]:

        score += 5

    # -----------------------------------------------------
    # VOLATILITY
    # -----------------------------------------------------

    if volatility[
        "condition"
    ] == "NORMAL":

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

    if not direction:

        return {

            "valid": False,

            "reason":
                "No direction"
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

    # -----------------------------------------------------
    # ATR BUFFER
    # -----------------------------------------------------

    atr = volatility.get(
        "atr"
    )

    buffer = 0.20

    if atr:

        buffer = max(
            0.20,
            atr * 0.20
        )

    # =====================================================
    # BUY
    # =====================================================

    if direction == "BUY":

        if swing_low is None:

            return {

                "valid": False,

                "reason":
                    "No valid M5 swing low"
            }

        sl = (
            float(swing_low)
            - buffer
        )

    # =====================================================
    # SELL
    # =====================================================

    else:

        if swing_high is None:

            return {

                "valid": False,

                "reason":
                    "No valid M5 swing high"
            }

        sl = (
            float(swing_high)
            + buffer
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
        risk_pips <
        MIN_RISK_PIPS

        or

        risk_pips >
        MAX_RISK_PIPS
    ):

        return {

            "valid": False,

            "reason": (
                f"Risk "
                f"{risk_pips:.1f} pips "
                f"outside "
                f"{MIN_RISK_PIPS}-"
                f"{MAX_RISK_PIPS}"
            )
        }

    # =====================================================
    # TP1
    # =====================================================

    tp1_price = (

        entry +
        pips_to_price(
            TP1_PIPS
        )

        if direction == "BUY"

        else

        entry -
        pips_to_price(
            TP1_PIPS
        )
    )

    # =====================================================
    # TP2
    # =====================================================

    tp2_pips = max(
        MIN_TP2_PIPS,
        risk_pips * 2
    )

    tp2_price = (

        entry +
        pips_to_price(
            tp2_pips
        )

        if direction == "BUY"

        else

        entry -
        pips_to_price(
            tp2_pips
        )
    )

    # =====================================================
    # TP3
    # =====================================================

    tp3_pips = max(
        TP3_PIPS,
        risk_pips * 3
    )

    tp3_price = (

        entry +
        pips_to_price(
            tp3_pips
        )

        if direction == "BUY"

        else

        entry -
        pips_to_price(
            tp3_pips
        )
    )

    # =====================================================
    # RR
    # =====================================================

    rr_tp1 = (
        TP1_PIPS /
        risk_pips
    )

    rr_tp2 = (
        tp2_pips /
        risk_pips
    )

    rr_tp3 = (
        tp3_pips /
        risk_pips
    )

    if rr_tp2 < MIN_RR:

        return {

            "valid": False,

            "reason":
                "TP2 RR below minimum"
        }

    return {

        "valid": True,

        "direction":
            direction,

        "entry":
            round_price(entry),

        "sl":
            round_price(sl),

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
        raw.encode(
            "utf-8"
        )
    ).hexdigest()[:16]


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

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

    except requests.RequestException:

        return False


# =========================================================
# TELEGRAM FORMAT
# =========================================================

def format_telegram(
    direction,
    opportunity,
    score,
    plan,
    session,
    pd_data,
    volatility
):

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

        f"📐 PD: "
        f"{pd_data['zone']}\n"

        f"🌡 ATR: "
        f"{volatility.get('atr_pips')} pips\n\n"

        "⚠️ Manual confirmation required."
    )


# =========================================================
# ERROR DASHBOARD
# =========================================================

def save_error_dashboard(
    error_message
):

    timestamp = now_my()

    dashboard = {

        "engine": {

            "name":
                "BOSQUE FOREX AI",

            "version":
                "SCALPING V3",

            "symbol":
                SYMBOL,

            "timeframe":
                "H1 → M15 → M5",

            "timestamp":
                timestamp.isoformat(),

            "status":
                "ERROR"
        },

        "latest_price":
            None,

        "session":
            get_session(
                timestamp
            ),

        "market_mode":
            "UNKNOWN",

        "error":
            str(error_message),

        "opportunity": {

            "type":
                "NO VALID SETUP",

            "direction":
                None,

            "valid":
                False,

            "score":
                0
        },

        "signal": {

            "active":
                False,

            "id":
                None
        },

        "plan": {

            "valid":
                False
        }
    }

    save_json_atomic(
        DASHBOARD_FILE,
        dashboard
    )


# =========================================================
# MAIN ENGINE
# =========================================================

def main():

    timestamp = now_my()

    try:

        # =================================================
        # FETCH M5
        # =================================================

        m5 = fetch_m5()

        m5 = remove_incomplete_candle(
            m5
        )

        if len(m5) < 100:

            raise RuntimeError(
                "Not enough M5 candles"
            )

        # =================================================
        # AGGREGATE
        # =================================================

        m15 = aggregate(
            m5,
            15
        )

        h1 = aggregate(
            m5,
            60
        )

        if len(m15) < 50:

            raise RuntimeError(
                "Not enough M15 candles"
            )

        if len(h1) < 30:

            raise RuntimeError(
                "Not enough H1 candles"
            )

        # =================================================
        # STRUCTURE
        # =================================================

        h1_structure = (
            analyze_structure(h1)
        )

        m15_structure = (
            analyze_structure(m15)
        )

        m5_structure = (
            analyze_structure(m5)
        )

        # =================================================
        # REGIME
        # =================================================

        regime_range = analyze_range(
            h1
        )

        market_mode = (
            determine_regime(
                h1_structure,
                h1
            )
        )

        # =================================================
        # PD
        # =================================================

        pd_data = pd_zone(
            m15
        )

        # =================================================
        # LIQUIDITY
        # =================================================

        liquidity = (
            liquidity_analysis(
                m15
            )
        )

        # =================================================
        # VOLATILITY
        # =================================================

        volatility = (
            volatility_analysis(
                m5
            )
        )

        # =================================================
        # SESSION
        # =================================================

        session = get_session(
            timestamp
        )

        # =================================================
        # M15 SETUP
        # =================================================

        setup = detect_m15_setup(
            h1_structure,
            m15
        )

        # =================================================
        # M5 CONFIRMATION
        # =================================================

        m5_confirm = (
            m5_confirmation(
                m5,
                setup["direction"]
            )
        )

        # =================================================
        # SCORE
        # =================================================

        score = calculate_score(

            h1_structure,

            setup,

            m5_confirm,

            session,

            volatility
        )

        # =================================================
        # TRADE PLAN
        # =================================================

        plan = create_trade_plan(

            setup["direction"],

            m5,

            score,

            volatility
        )

        # =================================================
        # FILTERS
        # =================================================

        news_ok = True

        session_ok = (
            session in [
                "LONDON",
                "NEW YORK"
            ]
        )

        setup_ok = (
            setup["valid"]
        )

        m5_ok = (
            m5_confirm["confirmed"]
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
            >= MIN_RR
        )

        score_ok = (
            score >= MIN_SCORE
        )

        # -------------------------------------------------
        # IMPORTANT:
        #
        # Session is displayed as a filter,
        # but is NOT a hard blocker.
        #
        # This allows Bosque to still detect
        # valid Asian-session setups.
        # -------------------------------------------------

        valid_signal = all([

            score_ok,

            setup_ok,

            m5_ok,

            risk_ok,

            rr_ok,

            news_ok
        ])

        # =================================================
        # SIGNAL
        # =================================================

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
                timestamp.isoformat()
        }

        state = load_state()

        # =================================================
        # VALID SIGNAL
        # =================================================

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

                "entry":
                    plan["entry"],

                "sl":
                    plan["sl"],

                "tp1":
                    plan["tp1"],

                "tp2":
                    plan["tp2"],

                "tp3":
                    plan["tp3"]
            })

            last_signal = state.get(
                "last_signal_id"
            )

            # -------------------------------------------------
            # Prevent duplicate Telegram alerts
            # -------------------------------------------------

            if signal_id != last_signal:

                message = format_telegram(

                    setup["direction"],

                    setup["opportunity"],

                    score,

                    plan,

                    session,

                    pd_data,

                    volatility
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

                    save_state(
                        state
                    )

        # =================================================
        # DASHBOARD
        # =================================================

        dashboard = {

            "engine": {

                "name":
                    "BOSQUE FOREX AI",

                "version":
                    "SCALPING V3",

                "symbol":
                    SYMBOL,

                "timeframe":
                    "H1 → M15 → M5",

                "timestamp":
                    timestamp.isoformat(),

                "status":
                    "ONLINE"
            },

            # ---------------------------------------------
            # PRICE
            # ---------------------------------------------

            "latest_price":
                round_price(
                    float(
                        m5.iloc[-1]["close"]
                    )
                ),

            # ---------------------------------------------
            # SESSION
            # ---------------------------------------------

            "session":
                session,

            # ---------------------------------------------
            # MARKET MODE
            # ---------------------------------------------

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

            # ---------------------------------------------
            # VOLATILITY
            # ---------------------------------------------

            "volatility":
                volatility,

            # ---------------------------------------------
            # PREMIUM / DISCOUNT
            # ---------------------------------------------

            "pd":
                pd_data,

            # ---------------------------------------------
            # LIQUIDITY
            # ---------------------------------------------

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

            # ---------------------------------------------
            # NEWS
            # ---------------------------------------------

            "news": {

                "status":
                    "NOT PROVIDED",

                "high_impact":
                    None,

                "minutes_to_news":
                    None,

                "filter":
                    "NOT CONNECTED"
            },

            # ---------------------------------------------
            # OPPORTUNITY
            # ---------------------------------------------

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

            # ---------------------------------------------
            # SIGNAL
            # ---------------------------------------------

            "signal":
                signal,

            # ---------------------------------------------
            # PLAN
            # ---------------------------------------------

            "plan":
                plan,

            # ---------------------------------------------
            # POTENTIAL
            # ---------------------------------------------

            "potential": {

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
                    )
            },

            # ---------------------------------------------
            # H1
            # ---------------------------------------------

            "h1": {

                **h1_structure,

                "condition":
                    market_mode
            },

            # ---------------------------------------------
            # M15
            # ---------------------------------------------

            "m15": {

                **m15_structure,

                "setup":
                    setup
            },

            # ---------------------------------------------
            # M5
            # ---------------------------------------------

            "m5": {

                **m5_structure,

                "confirmation":
                    m5_confirm
            },

            # ---------------------------------------------
            # FILTERS
            # ---------------------------------------------

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
                    session_ok
            },

            # ---------------------------------------------
            # CONFIRMATIONS
            # ---------------------------------------------

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

            # ---------------------------------------------
            # RISK ENGINE
            # ---------------------------------------------

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

            # ---------------------------------------------
            # INVALIDATION
            # ---------------------------------------------

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

                    "Avoid invalidation after "
                    "structure failure"
                ]
            },

            # ---------------------------------------------
            # SOP
            # ---------------------------------------------

            "sop": {

                "news_filter": (

                    "PASS"

                    if news_ok

                    else

                    "BLOCK"
                ),

                "session_filter": (

                    "PASS"

                    if session_ok

                    else

                    "BLOCK"
                ),

                "risk":
                    plan.get(
                        "risk_level",
                        "UNKNOWN"
                    ),

                "fresh_zone": (

                    "YES"

                    if setup_ok

                    else

                    "NO"
                ),

                "m15_setup": (

                    "YES"

                    if setup_ok

                    else

                    "NO"
                ),

                "m5_confirmation": (

                    "YES"

                    if m5_ok

                    else

                    "NO"
                )
            },

            # ---------------------------------------------
            # EXPECTANCY
            # ---------------------------------------------

            "expectancy": {

                "status":
                    "NOT TRACKED",

                "trades":
                    None,

                "win_rate":
                    None,

                "average_r":
                    None,

                "profit_factor":
                    None,

                "expectancy_r":
                    None,

                "max_drawdown":
                    None,

                "max_consecutive_losses":
                    None
            },

            # ---------------------------------------------
            # BACKTEST
            # ---------------------------------------------

            "backtest": {

                "status":
                    "NOT RUN",

                "development_period":
                    "2022-2024",

                "out_of_sample":
                    "2025",

                "forward":
                    "2026"
            }
        }

        # =================================================
        # SAVE DASHBOARD
        # =================================================

        save_json_atomic(

            DASHBOARD_FILE,

            dashboard
        )

        # =================================================
        # CONSOLE
        # =================================================

        print(
            json.dumps(

                clean_for_json(
                    dashboard
                ),

                indent=2,

                ensure_ascii=False
            )
        )

    except Exception as exc:

        # -------------------------------------------------
        # Never leave old dashboard data silently running.
        # -------------------------------------------------

        save_error_dashboard(
            exc
        )

        print(
            json.dumps({

                "engine":
                    "BOSQUE FOREX AI",

                "status":
                    "ERROR",

                "error":
                    str(exc)

            }, indent=2)
        )

        raise


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    main()