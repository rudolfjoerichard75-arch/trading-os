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
# H1 -> M15 -> M5
#
# STAGE 2
# 1. Risk Protection
# 2. Trade Journal / Expectancy
# 3. Validation Framework
# 4. Telegram V2 Gate
# 5. Signal Quality Foundation
# =========================================================


# =========================================================
# PATHS
# =========================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"
STATE_FILE = ENGINE_DIR / "state.json"
JOURNAL_FILE = ENGINE_DIR / "trade_journal.json"


# =========================================================
# API
# =========================================================

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = "XAU/USD"
INTERVAL = "5min"
OUTPUT_SIZE = 500


# =========================================================
# RISK / SCORING
# =========================================================

MIN_SCORE = 70

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

# These are configurable defaults.
# Change later if needed.

DAILY_LOSS_LIMIT_R = 3.0
CONSECUTIVE_LOSS_LIMIT = 3

# Risk protection is measured in R.
# Example:
# -1R = one full planned risk lost
# +2R = TP2 reached
# +3R = TP3 reached

BLOCK_NEW_SETUP_WHEN_RISK_LOCKED = True


# =========================================================
# NEWS FILTER
# =========================================================

# IMPORTANT:
# News engine is not connected yet.
#
# True = unavailable news blocks signal.
# This prevents Telegram alerts when the news filter
# cannot verify the market environment.

NEWS_FILTER_REQUIRED = True


# =========================================================
# SESSION
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

        if math.isnan(value):
            return None

        if math.isinf(value):
            return None

    return value


def save_json_atomic(path, data):

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


def load_json(path, default):

    if not path.exists():
        return default

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return default


def load_state():
    return load_json(
        STATE_FILE,
        {}
    )


def save_state(state):
    save_json_atomic(
        STATE_FILE,
        state
    )


def load_journal():

    data = load_json(
        JOURNAL_FILE,
        []
    )

    if not isinstance(data, list):
        return []

    return data


def save_journal(journal):

    save_json_atomic(
        JOURNAL_FILE,
        journal
    )


def price_to_pips(distance):

    return abs(
        float(distance)
    ) / PIP_SIZE


def pips_to_price(pips):

    return float(pips) * PIP_SIZE


def round_price(price):

    return round(
        float(price),
        2
    )


def parse_datetime(value):

    if not value:
        return None

    try:

        dt = pd.to_datetime(
            value
        ).to_pydatetime()

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:

        return None


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

        h1 = highs[-2]["price"]
        h2 = highs[-1]["price"]

        l1 = lows[-2]["price"]
        l2 = lows[-1]["price"]

        if h2 > h1 and l2 > l1:

            direction = "BULLISH"

        elif h2 < h1 and l2 < l1:

            direction = "BEARISH"

    if highs:

        latest_high = highs[-1]["price"]

        if float(
            df.iloc[-1]["close"]
        ) > latest_high:

            bos = "BULLISH BOS"

    if lows:

        latest_low = lows[-1]["price"]

        if float(
            df.iloc[-1]["close"]
        ) < latest_low:

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

        "is_range": bool(is_range),

        "high": high,

        "low": low,

        "width": width,

        "location": location
    }


# =========================================================
# PD ZONE
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

        high = float(
            df.iloc[-1]["high"]
        )

        close = float(
            df.iloc[-1]["close"]
        )

        if (
            high > level
            and
            close < level
        ):

            result[
                "buy_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = level

            result[
                "description"
            ] = "BUY-SIDE LIQUIDITY SWEPT"

    if lows:

        level = lows[-1]["price"]

        low = float(
            df.iloc[-1]["low"]
        )

        close = float(
            df.iloc[-1]["close"]
        )

        if (
            low < level
            and
            close > level
        ):

            result[
                "sell_side_sweep"
            ] = True

            result[
                "swept_level"
            ] = level

            result[
                "description"
            ] = "SELL-SIDE LIQUIDITY SWEPT"

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
        "strength": max(
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
        high - low
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

        "bullish": bool(
            close_price > open_price
            and ratio >= 0.55
        ),

        "bearish": bool(
            close_price < open_price
            and ratio >= 0.55
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
    ).max(axis=1)

    atr = (
        tr.rolling(period)
        .mean()
        .iloc[-1]
    )

    if pd.isna(atr):
        return None

    return float(atr)


def volatility_analysis(df):

    atr = calculate_atr(df)

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

    structure = analyze_structure(
        m15
    )

    pd_data = pd_zone(
        m15
    )

    liquidity = liquidity_analysis(
        m15
    )

    range_info = analyze_range(
        m15
    )

    direction = None

    opportunity = "NO VALID SETUP"

    reason = []

    # -----------------------------------------------------
    # RANGE REVERSAL
    # -----------------------------------------------------

    if range_info["is_range"]:

        if (
            liquidity["sell_side_sweep"]
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
            liquidity["buy_side_sweep"]
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

        if liquidity["sell_side_sweep"]:

            direction = "BUY"

            opportunity = (
                "BUY LIQUIDITY SWEEP"
            )

            reason.append(
                "M15 sell-side liquidity sweep"
            )

        elif liquidity["buy_side_sweep"]:

            direction = "SELL"

            opportunity = (
                "SELL LIQUIDITY SWEEP"
            )

            reason.append(
                "M15 buy-side liquidity sweep"
            )

    # -----------------------------------------------------
    # PULLBACK
    # -----------------------------------------------------

    if direction is None:

        if (
            h1_direction == "BULLISH"
            and
            pd_data["zone"] == "DISCOUNT"
        ):

            direction = "BUY"

            opportunity = "BUY PULLBACK"

            reason.append(
                "H1 bullish + M15 discount"
            )

        elif (
            h1_direction == "BEARISH"
            and
            pd_data["zone"] == "PREMIUM"
        ):

            direction = "SELL"

            opportunity = "SELL PULLBACK"

            reason.append(
                "H1 bearish + M15 premium"
            )

    # -----------------------------------------------------
    # BREAKOUT
    # -----------------------------------------------------

    if direction is None:

        if structure["bos"] == "BULLISH BOS":

            direction = "BUY"

            opportunity = (
                "BUY BREAKOUT RETEST"
            )

            reason.append(
                "M15 bullish BOS"
            )

        elif structure["bos"] == "BEARISH BOS":

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
            pd_data,

        "liquidity":
            liquidity,

        "structure":
            structure,

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
            ] == "BULLISH"
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
                momentum_data["direction"],

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
            ] == "BEARISH"
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
                momentum_data["direction"],

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

        "confirmed": False,

        "bos": bos,

        "candle": False,

        "momentum":
            momentum_data["direction"],

        "reason":
            "NO DIRECTION"
    }


# =========================================================
# SCORE
# =========================================================

def calculate_score(
    h1,
    setup,
    m5_confirm,
    session,
    volatility
):

    score = 0

    # H1
    if h1["direction"] in [
        "BULLISH",
        "BEARISH"
    ]:

        score += 15

    if h1["bos"]:

        score += 5

    # M15
    if setup["valid"]:

        score += 10

    liquidity = setup[
        "liquidity"
    ]

    if (
        liquidity["buy_side_sweep"]
        or
        liquidity["sell_side_sweep"]
    ):

        score += 10

    if setup[
        "structure"
    ]["bos"]:

        score += 10

    # M5
    if m5_confirm[
        "confirmed"
    ]:

        score += 15

    if m5_confirm["bos"]:

        score += 10

    if m5_confirm["candle"]:

        score += 10

    # PD
    zone = setup[
        "pd"
    ]["zone"]

    direction = setup[
        "direction"
    ]

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
    volatility
):

    if not direction:

        return {
            "valid": False
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
            atr * 0.20
        )

    if direction == "BUY":

        if swing_low is None:

            return {
                "valid": False
            }

        sl = (
            float(swing_low)
            - buffer
        )

    else:

        if swing_high is None:

            return {
                "valid": False
            }

        sl = (
            float(swing_high)
            + buffer
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

            "valid": False,

            "reason":
                f"Risk {risk_pips:.1f} pips "
                f"outside "
                f"{MIN_RISK_PIPS}-"
                f"{MAX_RISK_PIPS}"
        }

    tp1_pips = TP1_PIPS

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
            pips_to_price(tp1_pips)
        )

        tp2 = (
            entry +
            pips_to_price(tp2_pips)
        )

        tp3 = (
            entry +
            pips_to_price(tp3_pips)
        )

    else:

        tp1 = (
            entry -
            pips_to_price(tp1_pips)
        )

        tp2 = (
            entry -
            pips_to_price(tp2_pips)
        )

        tp3 = (
            entry -
            pips_to_price(tp3_pips)
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
            round(risk_pips, 1),

        "tp1":
            round_price(tp1),

        "tp2":
            round_price(tp2),

        "tp3":
            round_price(tp3),

        "tp1_pips":
            round(tp1_pips, 1),

        "tp2_pips":
            round(tp2_pips, 1),

        "tp3_pips":
            round(tp3_pips, 1),

        "rr_tp1":
            round(rr_tp1, 2),

        "rr_tp2":
            round(rr_tp2, 2),

        "rr_tp3":
            round(rr_tp3, 2),

        "risk_level": (
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
        f"{round(entry, 2)}|"
        f"{round(sl, 2)}|"
        f"{round(tp2, 2)}"
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:16]


# =========================================================
# JOURNAL HELPERS
# =========================================================

def get_today_key():

    return now_my().strftime(
        "%Y-%m-%d"
    )


def ensure_journal_structure():

    journal = load_journal()

    return journal


def calculate_journal_stats(
    journal
):

    closed = [
        x for x in journal
        if x.get("status")
        in [
            "WIN",
            "LOSS",
            "BREAKEVEN"
        ]
    ]

    wins = [
        x for x in closed
        if x.get("status") == "WIN"
    ]

    losses = [
        x for x in closed
        if x.get("status") == "LOSS"
    ]

    total = len(closed)

    win_count = len(wins)
    loss_count = len(losses)

    if total:

        win_rate = (
            win_count /
            total
        ) * 100

    else:

        win_rate = 0.0

    r_values = [
        float(
            x.get("result_r", 0)
        )
        for x in closed
    ]

    average_r = (
        sum(r_values) /
        len(r_values)
        if r_values
        else 0.0
    )

    gross_profit = sum(
        max(
            float(
                x.get(
                    "result_r",
                    0
                )
            ),
            0
        )
        for x in closed
    )

    gross_loss = abs(
        sum(
            min(
                float(
                    x.get(
                        "result_r",
                        0
                    )
                ),
                0
            )
            for x in closed
        )
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit /
            gross_loss
        )

    else:

        profit_factor = None

    # Max consecutive losses
    current_losses = 0
    max_consecutive_losses = 0

    for trade in closed:

        if trade.get(
            "status"
        ) == "LOSS":

            current_losses += 1

            max_consecutive_losses = max(
                max_consecutive_losses,
                current_losses
            )

        else:

            current_losses = 0

    # Equity curve in R
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for trade in closed:

        equity += float(
            trade.get(
                "result_r",
                0
            )
        )

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

    # Expectancy
    if total:

        expectancy_r = (
            sum(r_values) /
            total
        )

    else:

        expectancy_r = None

    # Current consecutive losses
    current_consecutive_losses = 0

    for trade in reversed(
        closed
    ):

        if trade.get(
            "status"
        ) == "LOSS":

            current_consecutive_losses += 1

        else:

            break

    return {

        "trades": total,

        "wins": win_count,

        "losses": loss_count,

        "win_rate":
            round(
                win_rate,
                2
            ),

        "average_r":
            round(
                average_r,
                3
            ),

        "profit_factor":
            (
                round(
                    profit_factor,
                    3
                )
                if profit_factor
                is not None
                else None
            ),

        "expectancy_r":
            (
                round(
                    expectancy_r,
                    3
                )
                if expectancy_r
                is not None
                else None
            ),

        "max_drawdown":
            round(
                max_drawdown,
                3
            ),

        "max_consecutive_losses":
            max_consecutive_losses,

        "current_consecutive_losses":
            current_consecutive_losses,

        "total_r":
            round(
                equity,
                3
            )
    }


# =========================================================
# RESOLVE PREVIOUS SIGNALS
# =========================================================

def resolve_open_trades(
    m5,
    journal
):

    changed = False

    for trade in journal:

        if trade.get(
            "status"
        ) != "OPEN":

            continue

        entry_time = parse_datetime(
            trade.get(
                "entry_time"
            )
        )

        if entry_time is None:
            continue

        direction = trade.get(
            "direction"
        )

        sl = float(
            trade.get(
                "sl"
            )
        )

        tp1 = float(
            trade.get(
                "tp1"
            )
        )

        tp2 = float(
            trade.get(
                "tp2"
            )
        )

        tp3 = float(
            trade.get(
                "tp3"
            )
        )

        future = m5[
            m5["datetime"] >
            entry_time
        ].copy()

        if future.empty:
            continue

        result = None
        result_r = None
        result_price = None
        result_time = None

        for _, candle in future.iterrows():

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            candle_time = (
                candle["datetime"]
            )

            if direction == "BUY":

                hit_sl = low <= sl
                hit_tp3 = high >= tp3
                hit_tp2 = high >= tp2
                hit_tp1 = high >= tp1

            else:

                hit_sl = high >= sl
                hit_tp3 = low <= tp3
                hit_tp2 = low <= tp2
                hit_tp1 = low <= tp1

            # Conservative handling:
            # if SL and TP are both hit inside the same
            # candle, assume SL happened first.
            if hit_sl:

                result = "LOSS"
                result_r = -1.0
                result_price = sl
                result_time = candle_time
                break

            if hit_tp3:

                result = "WIN"
                result_r = 3.0
                result_price = tp3
                result_time = candle_time
                break

            if hit_tp2:

                result = "WIN"
                result_r = 2.0
                result_price = tp2
                result_time = candle_time
                break

            if hit_tp1:

                # TP1 alone is a partial target.
                # We don't close journal here.
                # Trade remains open until SL/TP2/TP3.
                continue

        if result:

            trade["status"] = result

            trade[
                "result_r"
            ] = result_r

            trade[
                "result_price"
            ] = round_price(
                result_price
            )

            trade[
                "result_time"
            ] = (
                result_time.isoformat()
                if hasattr(
                    result_time,
                    "isoformat"
                )
                else str(
                    result_time
                )
            )

            changed = True

    return changed


# =========================================================
# RISK PROTECTION
# =========================================================

def calculate_risk_protection(
    journal
):

    today = get_today_key()

    today_closed = []

    for trade in journal:

        if trade.get(
            "status"
        ) not in [
            "WIN",
            "LOSS",
            "BREAKEVEN"
        ]:

            continue

        result_time = parse_datetime(
            trade.get(
                "result_time"
            )
        )

        if result_time is None:
            continue

        local_date = (
            result_time
            .astimezone(MY_TZ)
            .strftime("%Y-%m-%d")
        )

        if local_date == today:

            today_closed.append(
                trade
            )

    daily_r = sum(
        float(
            x.get(
                "result_r",
                0
            )
        )
        for x in today_closed
    )

    current_consecutive_losses = 0

    for trade in sorted(
        today_closed,
        key=lambda x:
        x.get(
            "result_time",
            ""
        )
    ):

        if trade.get(
            "status"
        ) == "LOSS":

            current_consecutive_losses += 1

        else:

            current_consecutive_losses = 0

    daily_loss_block = (
        daily_r <=
        -abs(
            DAILY_LOSS_LIMIT_R
        )
    )

    consecutive_block = (
        current_consecutive_losses
        >=
        CONSECUTIVE_LOSS_LIMIT
    )

    blocked = (
        daily_loss_block
        or
        consecutive_block
    )

    reasons = []

    if daily_loss_block:

        reasons.append(
            "DAILY LOSS LIMIT"
        )

    if consecutive_block:

        reasons.append(
            "CONSECUTIVE LOSS LIMIT"
        )

    return {

        "blocked":
            blocked,

        "daily_r":
            round(
                daily_r,
                3
            ),

        "daily_loss_limit_r":
            DAILY_LOSS_LIMIT_R,

        "consecutive_losses":
            current_consecutive_losses,

        "consecutive_loss_limit":
            CONSECUTIVE_LOSS_LIMIT,

        "daily_loss_block":
            daily_loss_block,

        "consecutive_loss_block":
            consecutive_block,

        "reason":
            ", ".join(reasons)
            if reasons
            else "NONE"
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
# VALIDATION FRAMEWORK
# =========================================================

def validation_status():

    return {

        "status":
            "FRAMEWORK READY",

        "development":
            {
                "period":
                    "2022-2024",

                "status":
                    "NOT RUN"
            },

        "out_of_sample":
            {
                "period":
                    "2025",

                "status":
                    "NOT RUN"
            },

        "forward":
            {
                "period":
                    "2026",

                "status":
                    "LIVE TRACKING"
            },

        "warning":
            "No profitability claim is made "
            "until validation is actually executed."
    }


# =========================================================
# MAIN
# =========================================================

def main():

    timestamp = now_my()

    # -----------------------------------------------------
    # FETCH ONE REQUEST
    # -----------------------------------------------------

    m5 = fetch_m5()

    m5 = remove_incomplete_candle(
        m5
    )

    if len(m5) < 100:

        raise RuntimeError(
            "Not enough M5 candles"
        )

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

    regime_range = (
        analyze_range(h1)
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
    # SETUP
    # -----------------------------------------------------

    setup = detect_m15_setup(
        h1_structure,
        m15
    )

    m5_confirm = m5_confirmation(
        m5,
        setup["direction"]
    )

    score = calculate_score(
        h1_structure,
        setup,
        m5_confirm,
        session,
        volatility
    )

    plan = create_trade_plan(
        setup["direction"],
        m5,
        volatility
    )

    # -----------------------------------------------------
    # JOURNAL
    # -----------------------------------------------------

    journal = (
        ensure_journal_structure()
    )

    journal_changed = (
        resolve_open_trades(
            m5,
            journal
        )
    )

    if journal_changed:

        save_journal(
            journal
        )

    journal_stats = (
        calculate_journal_stats(
            journal
        )
    )

    # -----------------------------------------------------
    # RISK PROTECTION
    # -----------------------------------------------------

    risk_protection = (
        calculate_risk_protection(
            journal
        )
    )

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
        ) >= MIN_RR
    )

    session_ok = (
        session in [
            "LONDON",
            "NEW YORK"
        ]
    )

    # News is intentionally blocked
    # until an actual news source is connected.

    news_available = False

    news_ok = (
        news_available
        if NEWS_FILTER_REQUIRED
        else True
    )

    risk_protection_ok = not (
        risk_protection["blocked"]
    )

    # -----------------------------------------------------
    # FINAL GATE
    # -----------------------------------------------------

    valid_signal = all([

        score_ok,

        setup_ok,

        m5_ok,

        risk_ok,

        rr_ok,

        news_ok,

        risk_protection_ok
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
            timestamp.isoformat(),

        "blocked":
            not valid_signal,

        "block_reason":
            []
    }

    if not score_ok:

        signal[
            "block_reason"
        ].append(
            "SCORE < 70"
        )

    if not setup_ok:

        signal[
            "block_reason"
        ].append(
            "M15 SETUP"
        )

    if not m5_ok:

        signal[
            "block_reason"
        ].append(
            "M5 CONFIRMATION"
        )

    if not risk_ok:

        signal[
            "block_reason"
        ].append(
            "RISK"
        )

    if not rr_ok:

        signal[
            "block_reason"
        ].append(
            "RR"
        )

    if not news_ok:

        signal[
            "block_reason"
        ].append(
            "NEWS"
        )

    if not risk_protection_ok:

        signal[
            "block_reason"
        ].append(
            "RISK PROTECTION"
        )

    state = load_state()

    # -----------------------------------------------------
    # ONLY CREATE SIGNAL IF ALL GATES PASS
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

        # -------------------------------------------------
        # TELEGRAM
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

                # Add journal entry
                journal.append({

                    "signal_id":
                        signal_id,

                    "entry_time":
                        timestamp.isoformat(),

                    "direction":
                        setup["direction"],

                    "opportunity":
                        setup["opportunity"],

                    "score":
                        score,

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

                    "rr_tp1":
                        plan["rr_tp1"],

                    "rr_tp2":
                        plan["rr_tp2"],

                    "rr_tp3":
                        plan["rr_tp3"],

                    "status":
                        "OPEN",

                    "result_r":
                        None,

                    "result_price":
                        None,

                    "result_time":
                        None
                })

                save_journal(
                    journal
                )

                # Recalculate stats
                journal_stats = (
                    calculate_journal_stats(
                        journal
                    )
                )

    # -----------------------------------------------------
    # SAVE STATE
    # -----------------------------------------------------

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
                "SCALPING V3",

            "symbol":
                SYMBOL,

            "timeframe":
                "H1 → M15 → M5",

            "timestamp":
                timestamp.isoformat()
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

        # -------------------------------------------------
        # REGIME
        # -------------------------------------------------

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

        # -------------------------------------------------
        # VOLATILITY
        # -------------------------------------------------

        "volatility":
            volatility,

        # -------------------------------------------------
        # PD
        # -------------------------------------------------

        "pd":
            pd_data,

        # -------------------------------------------------
        # LIQUIDITY
        # -------------------------------------------------

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

        # -------------------------------------------------
        # NEWS
        # -------------------------------------------------

        "news": {

            "status":
                (
                    "AVAILABLE"
                    if news_available
                    else
                    "NEWS TIMING UNAVAILABLE"
                ),

            "high_impact":
                None,

            "minutes_to_news":
                None,

            "filter":
                (
                    "PASS"
                    if news_ok
                    else
                    "BLOCK"
                ),

            "required":
                NEWS_FILTER_REQUIRED
        },

        # -------------------------------------------------
        # OPPORTUNITY
        # -------------------------------------------------

        "opportunity": {

            "type":
                setup["opportunity"],

            "direction":
                setup["direction"],

            "valid":
                setup["valid"],

            "score":
                score
        },

        # -------------------------------------------------
        # SIGNAL
        # -------------------------------------------------

        "signal":
            signal,

        # -------------------------------------------------
        # PLAN
        # -------------------------------------------------

        "plan":
            plan,

        # -------------------------------------------------
        # POTENTIAL
        # -------------------------------------------------

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

        # -------------------------------------------------
        # MTF
        # -------------------------------------------------

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

        # -------------------------------------------------
        # FILTERS
        # -------------------------------------------------

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
                risk_protection_ok
        },

        # -------------------------------------------------
        # RISK PROTECTION
        # -------------------------------------------------

        "risk_protection": {

            "status":
                (
                    "BLOCK"
                    if risk_protection[
                        "blocked"
                    ]
                    else
                    "PASS"
                ),

            "daily_r":
                risk_protection[
                    "daily_r"
                ],

            "daily_loss_limit_r":
                risk_protection[
                    "daily_loss_limit_r"
                ],

            "consecutive_losses":
                risk_protection[
                    "consecutive_losses"
                ],

            "consecutive_loss_limit":
                risk_protection[
                    "consecutive_loss_limit"
                ],

            "daily_loss_block":
                risk_protection[
                    "daily_loss_block"
                ],

            "consecutive_loss_block":
                risk_protection[
                    "consecutive_loss_block"
                ],

            "reason":
                risk_protection[
                    "reason"
                ]
        },

        # -------------------------------------------------
        # CONFIRMATIONS
        # -------------------------------------------------

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

        # -------------------------------------------------
        # EXPECTANCY / JOURNAL
        # -------------------------------------------------

        "expectancy": {

            "status":
                (
                    "TRACKING"
                    if journal_stats[
                        "trades"
                    ] > 0
                    else
                    "WAITING FOR FIRST CLOSED TRADE"
                ),

            "trades":
                journal_stats[
                    "trades"
                ],

            "wins":
                journal_stats[
                    "wins"
                ],

            "losses":
                journal_stats[
                    "losses"
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
                    "expectancy_r"
                ],

            "max_drawdown":
                journal_stats[
                    "max_drawdown"
                ],

            "max_consecutive_losses":
                journal_stats[
                    "max_consecutive_losses"
                ],

            "current_consecutive_losses":
                journal_stats[
                    "current_consecutive_losses"
                ],

            "total_r":
                journal_stats[
                    "total_r"
                ]
        },

        # -------------------------------------------------
        # TRADE JOURNAL
        # -------------------------------------------------

        "trade_journal": {

            "file":
                str(
                    JOURNAL_FILE.name
                ),

            "total_records":
                len(journal),

            "open_trades":
                len([
                    x for x in journal
                    if x.get(
                        "status"
                    ) == "OPEN"
                ])
        },

        # -------------------------------------------------
        # VALIDATION
        # -------------------------------------------------

        "validation":
            validation_status(),

        # -------------------------------------------------
        # RISK ENGINE
        # -------------------------------------------------

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

            "daily_loss_limit_r":
                DAILY_LOSS_LIMIT_R,

            "consecutive_loss_limit":
                CONSECUTIVE_LOSS_LIMIT
        },

        # -------------------------------------------------
        # INVALIDATION
        # -------------------------------------------------

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

                "News filter must pass",

                "Risk protection must remain unlocked",

                "Avoid invalidation after structure failure"
            ]
        },

        # -------------------------------------------------
        # SOP
        # -------------------------------------------------

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
                ),

            "risk_protection":
                (
                    "PASS"
                    if risk_protection_ok
                    else
                    "BLOCK"
                )
        },

        # -------------------------------------------------
        # TELEGRAM V2
        # -------------------------------------------------

        "telegram": {

            "version":
                "V2",

            "gate":

                "PASS"
                if valid_signal
                else
                "BLOCK",

            "requirements": {

                "score":
                    score_ok,

                "m15_setup":
                    setup_ok,

                "m5_confirmation":
                    m5_ok,

                "risk":
                    risk_ok,

                "rr":
                    rr_ok,

                "news":
                    news_ok,

                "risk_protection":
                    risk_protection_ok
            }
        }
    }

    # -----------------------------------------------------
    # SAVE DASHBOARD
    # -----------------------------------------------------

    save_json_atomic(
        DASHBOARD_FILE,
        dashboard
    )

    # -----------------------------------------------------
    # CONSOLE
    # -----------------------------------------------------

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
# ENTRY
# =========================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        print(
            json.dumps(
                {
                    "engine":
                        "BOSQUE FOREX AI",

                    "status":
                        "ERROR",

                    "error":
                        str(error)
                },
                indent=2
            )
        )

        raise