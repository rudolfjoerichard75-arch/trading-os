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
# PRODUCTION SCALPING ENGINE
# H1 -> M15 -> M5
# QUOTA SAFE
# =========================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"
STATE_FILE = ENGINE_DIR / "state.json"

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

# ONE Twelve Data request per scan
OUTPUT_SIZE = 500

# =========================================================
# SETTINGS
# =========================================================

MIN_SCORE = 70

# XAUUSD
# 1 pip = 0.10 price movement
PIP_SIZE = 0.10

MIN_RISK_PIPS = 25
MAX_RISK_PIPS = 80

TP1_PIPS = 60
MIN_TP2_PIPS = 120
TP3_PIPS = 180

MIN_RR = 2.0

# =========================================================
# RISK PROTECTION
# =========================================================

MAX_CONSECUTIVE_LOSSES = 3
DAILY_LOSS_LIMIT_R = -3.0

# =========================================================
# NEWS SETTINGS
# =========================================================
#
# Twelve Data is NOT used for news.
# Until a dedicated news source is connected,
# news remains UNKNOWN and blocks NEW signals.
#

NEWS_BLOCK_MINUTES = 30

# =========================================================
# TIMEZONE
# =========================================================

MY_TZ = timezone(timedelta(hours=8))

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
    return datetime.now(timezone.utc).astimezone(MY_TZ)


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

        if math.isnan(value) or math.isinf(value):
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
    return abs(distance) / PIP_SIZE


def pips_to_price(pips):
    return pips * PIP_SIZE


def round_price(price):

    if price is None:
        return None

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


def session_is_preferred(session):

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
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": OUTPUT_SIZE,
        "apikey": API_KEY,
        "format": "JSON"
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
        df["datetime"]
    )

    for column in [
        "open",
        "high",
        "low",
        "close"
    ]:

        df[column] = pd.to_numeric(
            df[column],
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

def remove_incomplete_candle(df):

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
        now_utc - last_time
    ).total_seconds()

    if elapsed < 300:

        return df.iloc[:-1].copy()

    return df


# =========================================================
# AGGREGATION
# =========================================================

def aggregate(df, minutes):

    x = df.copy()

    x["datetime"] = pd.to_datetime(
        x["datetime"]
    )

    x = x.set_index(
        "datetime"
    )

    out = x.resample(
        f"{minutes}min"
    ).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    })

    out = out.dropna()

    return out.reset_index()


# =========================================================
# SWINGS
# =========================================================

def find_swing_highs(
    df,
    left=2,
    right=2
):

    highs = []

    if len(df) < left + right + 1:
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
            value > left_values.max()
            and
            value >= right_values.max()
        ):

            highs.append({
                "index": i,
                "price": value
            })

    return highs


def find_swing_lows(
    df,
    left=2,
    right=2
):

    lows = []

    if len(df) < left + right + 1:
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
            value < left_values.min()
            and
            value <= right_values.max()
            if False
            else
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

    if len(highs) >= 2 and len(lows) >= 2:

        previous_high = highs[-2]["price"]
        latest_high = highs[-1]["price"]

        previous_low = lows[-2]["price"]
        latest_low = lows[-1]["price"]

        if (
            latest_high > previous_high
            and
            latest_low > previous_low
        ):

            direction = "BULLISH"

        elif (
            latest_high < previous_high
            and
            latest_low < previous_low
        ):

            direction = "BEARISH"

    close = float(
        df.iloc[-1]["close"]
    )

    if highs:

        latest_high = highs[-1]["price"]

        if close > latest_high:
            bos = "BULLISH BOS"

    if lows:

        latest_low = lows[-1]["price"]

        if close < latest_low:
            bos = "BEARISH BOS"

    return {

        "direction": direction,

        "bos": bos,

        "swing_high": (
            highs[-1]["price"]
            if highs
            else None
        ),

        "swing_low": (
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

        "is_range": bool(
            is_range
        ),

        "high": high,

        "low": low,

        "width": width,

        "location": location
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

        "zone": zone,

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

def liquidity_analysis(df):

    highs = find_swing_highs(df)
    lows = find_swing_lows(df)

    result = {

        "buy_side_sweep": False,

        "sell_side_sweep": False,

        "swept_level": None,

        "description": "NONE"
    }

    if highs:

        level = highs[-1]["price"]

        current_high = float(
            df.iloc[-1]["high"]
        )

        current_close = float(
            df.iloc[-1]["close"]
        )

        if (
            current_high > level
            and
            current_close < level
        ):

            result[
                "buy_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = level

            result[
                "description"
            ] = (
                "BUY-SIDE LIQUIDITY SWEPT"
            )

    if lows:

        level = lows[-1]["price"]

        current_low = float(
            df.iloc[-1]["low"]
        )

        current_close = float(
            df.iloc[-1]["close"]
        )

        if (
            current_low < level
            and
            current_close > level
        ):

            result[
                "sell_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = level

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

    if len(df) < lookback + 1:

        return {
            "direction": "NEUTRAL",
            "strength": 0
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
# CANDLE
# =========================================================

def candle_confirmation(df):

    c = df.iloc[-1]

    open_price = float(
        c["open"]
    )

    close_price = float(
        c["close"]
    )

    high = float(
        c["high"]
    )

    low = float(
        c["low"]
    )

    body = abs(
        close_price -
        open_price
    )

    full_range = (
        high -
        low
    )

    if full_range <= 0:

        return {
            "bullish": False,
            "bearish": False
        }

    ratio = (
        body /
        full_range
    )

    return {

        "bullish":
            bool(
                close_price >
                open_price
                and
                ratio >= 0.55
            ),

        "bearish":
            bool(
                close_price <
                open_price
                and
                ratio >= 0.55
            )
    }


# =========================================================
# ATR
# =========================================================

def calculate_atr(
    df,
    period=14
):

    if len(df) < period + 1:
        return None

    previous_close = (
        df["close"].shift(1)
    )

    tr1 = (
        df["high"] -
        df["low"]
    )

    tr2 = abs(
        df["high"] -
        previous_close
    )

    tr3 = abs(
        df["low"] -
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

    atr = (
        tr.rolling(
            period
        ).mean().iloc[-1]
    )

    if pd.isna(atr):
        return None

    return float(atr)


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

    # -----------------------------------------------------
    # RANGE REVERSAL
    # -----------------------------------------------------

    if range_info["is_range"]:

        if (
            m15_liquidity[
                "sell_side_sweep"
            ]
            and
            range_info["location"] <= 0.25
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
            range_info["location"] >= 0.75
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
            m15_pd["zone"] == "DISCOUNT"
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
            m15_pd["zone"] == "PREMIUM"
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

        "direction": direction,

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

    structure = (
        analyze_structure(df)
    )

    momentum_data = (
        momentum(df)
    )

    candle = (
        candle_confirmation(df)
    )

    bos = structure["bos"]

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
            ]
            ==
            "BULLISH"
        )

        # STRONGER confirmation:
        # BOS OR candle+momentum
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
                bool(candle_ok),

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
            bos ==
            "BEARISH BOS"
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
                bool(candle_ok),

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

    # H1 context
    if h1["direction"] in [
        "BULLISH",
        "BEARISH"
    ]:

        score += 15

    if h1["bos"]:
        score += 5

    # M15 setup
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
        ].get("bos")
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

    # Session
    if session in [
        "LONDON",
        "NEW YORK"
    ]:

        score += 5

    # Volatility
    if (
        volatility[
            "condition"
        ] == "NORMAL"
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
            "valid": False,
            "reason": "NO DIRECTION"
        }

    entry = float(
        m5.iloc[-1]["close"]
    )

    structure = (
        analyze_structure(m5)
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
                "valid": False,
                "reason":
                    "NO SWING LOW"
            }

        sl = (
            float(swing_low)
            - buffer
        )

    else:

        if swing_high is None:

            return {
                "valid": False,
                "reason":
                    "NO SWING HIGH"
            }

        sl = (
            float(swing_high)
            + buffer
        )

    risk_price = abs(
        entry - sl
    )

    risk_pips = (
        price_to_pips(
            risk_price
        )
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

            "reason":
                (
                    f"Risk "
                    f"{risk_pips:.1f} pips "
                    f"outside "
                    f"{MIN_RISK_PIPS}-"
                    f"{MAX_RISK_PIPS}"
                ),

            "risk_pips":
                round(
                    risk_pips,
                    1
                )
        }

    tp1_pips = (
        TP1_PIPS
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

        tp1 = (
            entry +
            pips_to_price(
                tp1_pips
            )
        )

        tp2 = (
            entry +
            pips_to_price(
                tp2_pips
            )
        )

        tp3 = (
            entry +
            pips_to_price(
                tp3_pips
            )
        )

    else:

        tp1 = (
            entry -
            pips_to_price(
                tp1_pips
            )
        )

        tp2 = (
            entry -
            pips_to_price(
                tp2_pips
            )
        )

        tp3 = (
            entry -
            pips_to_price(
                tp3_pips
            )
        )

    rr_tp1 = (
        tp1_pips /
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
                "TP2 RR below minimum",

            "risk_pips":
                round(
                    risk_pips,
                    1
                )
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
            round_price(tp1),

        "tp2":
            round_price(tp2),

        "tp3":
            round_price(tp3),

        "tp1_pips":
            round(
                tp1_pips,
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
                else "MEDIUM"
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
        f"{round(entry,2)}|"
        f"{round(sl,2)}|"
        f"{round(tp2,2)}"
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:16]


# =========================================================
# RISK STATE
# =========================================================

def risk_protection(
    state,
    timestamp
):

    today = (
        timestamp.date()
        .isoformat()
    )

    if state.get(
        "risk_date"
    ) != today:

        state[
            "risk_date"
        ] = today

        state[
            "daily_r"
        ] = 0.0

        state[
            "consecutive_losses"
        ] = 0

    daily_r = float(
        state.get(
            "daily_r",
            0.0
        )
    )

    consecutive = int(
        state.get(
            "consecutive_losses",
            0
        )
    )

    daily_ok = (
        daily_r >
        DAILY_LOSS_LIMIT_R
    )

    consecutive_ok = (
        consecutive <
        MAX_CONSECUTIVE_LOSSES
    )

    return {

        "allowed":
            bool(
                daily_ok
                and
                consecutive_ok
            ),

        "daily_r":
            daily_r,

        "consecutive_losses":
            consecutive,

        "daily_limit_r":
            DAILY_LOSS_LIMIT_R,

        "consecutive_limit":
            MAX_CONSECUTIVE_LOSSES,

        "daily_ok":
            daily_ok,

        "consecutive_ok":
            consecutive_ok
    }


# =========================================================
# NEWS FILTER
# =========================================================

def news_filter():

    # No external news request here.
    # This keeps Twelve Data quota untouched.

    return {

        "status":
            "UNKNOWN",

        "high_impact":
            None,

        "minutes_to_news":
            None,

        "filter":
            "BLOCK - NEWS SOURCE NOT CONNECTED",

        "allowed":
            False
    }


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
# MAIN ENGINE
# =========================================================

def main():

    timestamp = now_my()

    previous_dashboard = None

    # -----------------------------------------------------
    # FETCH
    # -----------------------------------------------------

    m5 = fetch_m5()

    m5 = remove_incomplete_candle(
        m5
    )

    if len(m5) < 100:

        raise RuntimeError(
            "Not enough M5 candles"
        )

    # -----------------------------------------------------
    # MTF
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # STRUCTURE
    # -----------------------------------------------------

    h1_structure = (
        analyze_structure(h1)
    )

    m15_structure = (
        analyze_structure(m15)
    )

    m5_structure = (
        analyze_structure(m5)
    )

    # -----------------------------------------------------
    # MARKET MODE
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # ANALYSIS
    # -----------------------------------------------------

    pd_data = pd_zone(
        m15
    )

    liquidity = (
        liquidity_analysis(m15)
    )

    volatility = (
        volatility_analysis(m5)
    )

    session = get_session(
        timestamp
    )

    # -----------------------------------------------------
    # M15
    # -----------------------------------------------------

    setup = detect_m15_setup(
        h1_structure,
        m15
    )

    # -----------------------------------------------------
    # M5
    # -----------------------------------------------------

    m5_confirm = (
        m5_confirmation(
            m5,
            setup["direction"]
        )
    )

    # -----------------------------------------------------
    # SCORE
    # -----------------------------------------------------

    score = calculate_score(
        h1_structure,
        setup,
        m5_confirm,
        session,
        volatility
    )

    # -----------------------------------------------------
    # TRADE PLAN
    # -----------------------------------------------------

    plan = create_trade_plan(
        setup["direction"],
        m5,
        volatility
    )

    # -----------------------------------------------------
    # STATE
    # -----------------------------------------------------

    state = load_state()

    risk = risk_protection(
        state,
        timestamp
    )

    # -----------------------------------------------------
    # NEWS
    # -----------------------------------------------------

    news = news_filter()

    # -----------------------------------------------------
    # GATES
    # -----------------------------------------------------

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
        >= MIN_RR
    )

    news_ok = (
        news["allowed"]
    )

    session_ok = (
        session_is_preferred(
            session
        )
    )

    protection_ok = (
        risk["allowed"]
    )

    # -----------------------------------------------------
    # FINAL SIGNAL
    # -----------------------------------------------------
    #
    # News is intentionally BLOCKED until
    # a real news source is connected.
    #

    valid_signal = all([

        score_ok,

        setup_ok,

        m5_ok,

        risk_ok,

        rr_ok,

        news_ok,

        protection_ok

    ])

    # -----------------------------------------------------
    # SIGNAL
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # TELEGRAM
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # DASHBOARD
    # -----------------------------------------------------

    dashboard = {

        "engine": {

            "name":
                "BOSQUE FOREX AI",

            "version":
                "PRODUCTION V3",

            "symbol":
                SYMBOL,

            "timeframe":
                "H1 → M15 → M5",

            "timestamp":
                timestamp.isoformat(),

            "status":
                "ONLINE"
        },

        "latest_price":
            round_price(
                float(
                    m5.iloc[-1]["close"]
                )
            ),

        "session":
            session,

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

        "news":
            news,

        "opportunity": {

            "type":
                setup["opportunity"],

            "direction":
                setup["direction"],

            "valid":
                setup["valid"],

            "score":
                score,

            "reason":
                setup["reason"]
        },

        "signal":
            signal,

        "plan":
            plan,

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

            "session":
                session_ok,

            "risk_protection":
                protection_ok
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
                DAILY_LOSS_LIMIT_R,

            "daily_r":
                risk[
                    "daily_r"
                ],

            "consecutive_loss_limit":
                MAX_CONSECUTIVE_LOSSES,

            "consecutive_losses":
                risk[
                    "consecutive_losses"
                ],

            "protection":
                (
                    "PASS"
                    if protection_ok
                    else
                    "BLOCK"
                )
        },

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

                "News filter must PASS",

                "Risk protection must PASS",

                "Structure failure invalidates setup"
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

            "fresh_zone":
                (
                    "YES"
                    if setup_ok
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
                ),

            "risk_protection":
                (
                    "PASS"
                    if protection_ok
                    else
                    "BLOCK"
                )
        },

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
                risk[
                    "consecutive_losses"
                ]
        },

        "backtest": {

            "status":
                "NOT RUN",

            "development_period":
                "2022-2024",

            "out_of_sample":
                "2025",

            "forward":
                "2026"
        },

        "quota": {

            "provider":
                "Twelve Data",

            "requests_per_scan":
                1,

            "source":
                "M5 → local H1/M15 aggregation",

            "status":
                "QUOTA SAFE"
        }
    }

    # -----------------------------------------------------
    # SAVE
    # -----------------------------------------------------

    save_json_atomic(
        DASHBOARD_FILE,
        dashboard
    )

    print(
        json.dumps(
            clean_for_json(
                dashboard
            ),
            indent=2
        )
    )


# =========================================================
# ENTRY
# =========================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        print(
            f"ENGINE ERROR: {error}"
        )

        raise