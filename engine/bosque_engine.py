import os
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# BOSQUE FOREX AI
# XAU/USD SCALPING ENGINE V3.1
#
# H1  = Bias / Market Condition
# M15 = Setup / Location / Liquidity
# M5  = Confirmation / Entry
#
# XAU/USD PIP RULE
# 1 point = 0.01 price
# 10 points = 1 pip
# 1 pip = 0.10 price
# ============================================================


# ============================================================
# PATHS
# ============================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"
STATE_FILE = ENGINE_DIR / "state.json"


# ============================================================
# ENVIRONMENT
# ============================================================

TWELVEDATA_API_KEY = os.getenv(
    "TWELVEDATA_API_KEY",
    ""
).strip()

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# TWELVE DATA
# ============================================================

TWELVEDATA_URL = (
    "https://api.twelvedata.com/time_series"
)

SYMBOL = "XAU/USD"
M5_INTERVAL = "5min"

# 500 M5 candles ≈ 41 hours
OUTPUT_SIZE = 500


# ============================================================
# V3.1 SCALPING SETTINGS
# ============================================================

# Previous:
# MIN_SCORE = 70
#
# New:
# 65 = easier to catch valid scalping opportunities
MIN_SCORE = 65


# Previous:
# 35 - 60 pips
#
# New:
# 30 - 70 pips
MIN_RISK_PIPS = 30
MAX_RISK_PIPS = 70


# First partial target
TP1_PIPS = 60


# Main target
MIN_TP2_PIPS = 120


# Extended target
TP3_PIPS = 180


# V3.1:
# TP2 must provide at least 1.8R
MIN_RR = 1.8


# 1 pip = 0.10 XAU/USD
PIP_SIZE = 0.10


# ============================================================
# MALAYSIA TIME
# ============================================================

MY_TZ_OFFSET_HOURS = 8


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = 30


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except Exception:
        return default


def round_price(value):
    value = safe_float(value)

    if value is None:
        return None

    return round(value, 2)


def price_to_pips(distance):
    distance = safe_float(distance)

    if distance is None:
        return None

    return round(
        abs(distance) / PIP_SIZE,
        1
    )


def pips_to_price(pips):
    pips = safe_float(pips)

    if pips is None:
        return None

    return pips * PIP_SIZE


def clamp(value, minimum, maximum):
    return max(
        minimum,
        min(maximum, value)
    )


def clean_for_json(value):

    if isinstance(value, float):

        if not math.isfinite(value):
            return None

    if isinstance(value, dict):

        return {
            key: clean_for_json(val)
            for key, val in value.items()
        }

    if isinstance(value, list):

        return [
            clean_for_json(item)
            for item in value
        ]

    return value


# ============================================================
# SESSION
# ============================================================

def malaysia_hour():

    utc_hour = now_utc().hour

    return (
        utc_hour +
        MY_TZ_OFFSET_HOURS
    ) % 24


def session_name():

    hour = malaysia_hour()

    if 7 <= hour < 15:
        return "ASIAN"

    if 15 <= hour < 20:
        return "LONDON"

    if 20 <= hour <= 23:
        return "NEW YORK"

    if 0 <= hour < 1:
        return "NEW YORK"

    return "OFF SESSION"


# ============================================================
# TWELVE DATA
# ============================================================

def fetch_m5_data():

    if not TWELVEDATA_API_KEY:

        raise RuntimeError(
            "TWELVEDATA_API_KEY is missing."
        )

    params = {
        "symbol": SYMBOL,
        "interval": M5_INTERVAL,
        "outputsize": OUTPUT_SIZE,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
    }

    response = requests.get(
        TWELVEDATA_URL,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Twelve Data HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    if data.get("status") == "error":

        raise RuntimeError(
            "Twelve Data error: "
            f"{data.get('message', 'Unknown error')}"
        )

    values = data.get("values")

    if not values:

        raise RuntimeError(
            "Twelve Data returned no OHLC data."
        )

    candles = []

    for item in values:

        try:

            candles.append({
                "datetime": item["datetime"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            })

        except Exception:
            continue

    if len(candles) < 50:

        raise RuntimeError(
            f"Not enough M5 candles: "
            f"{len(candles)}"
        )

    candles.sort(
        key=lambda x: x["datetime"]
    )

    return candles


# ============================================================
# REMOVE INCOMPLETE CANDLE
# ============================================================

def remove_incomplete_candle(candles):

    if len(candles) < 2:
        return candles

    try:

        last_dt = datetime.fromisoformat(
            candles[-1]["datetime"]
            .replace("Z", "+00:00")
        )

        elapsed = (
            now_utc() - last_dt
        ).total_seconds()

        if elapsed < 300:

            return candles[:-1]

    except Exception:
        pass

    return candles


# ============================================================
# AGGREGATION
# ============================================================

def aggregate_candles(
    candles,
    minutes
):

    if minutes not in (15, 60):

        raise ValueError(
            "Only 15 and 60 minutes supported."
        )

    result = []

    current_bucket = None
    bucket = []

    for candle in candles:

        try:

            dt = datetime.fromisoformat(
                candle["datetime"]
                .replace("Z", "+00:00")
            )

        except Exception:
            continue

        total_minutes = (
            dt.hour * 60 +
            dt.minute
        )

        bucket_minute = (
            total_minutes // minutes
        ) * minutes

        bucket_key = (
            dt.date(),
            bucket_minute
        )

        if current_bucket is None:

            current_bucket = bucket_key

        if bucket_key != current_bucket:

            if bucket:

                result.append(
                    build_aggregated_candle(
                        bucket
                    )
                )

            bucket = []
            current_bucket = bucket_key

        bucket.append(candle)

    if bucket:

        result.append(
            build_aggregated_candle(
                bucket
            )
        )

    return result


def build_aggregated_candle(bucket):

    return {
        "datetime": bucket[0]["datetime"],
        "open": bucket[0]["open"],
        "high": max(
            x["high"] for x in bucket
        ),
        "low": min(
            x["low"] for x in bucket
        ),
        "close": bucket[-1]["close"],
    }


# ============================================================
# SWINGS
# ============================================================

def find_swing_highs(
    candles,
    left=2,
    right=2
):

    swings = []

    if len(candles) < (
        left + right + 1
    ):
        return swings

    for i in range(
        left,
        len(candles) - right
    ):

        high = candles[i]["high"]

        valid = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] >= high:

                valid = False
                break

        if valid:

            swings.append({
                "index": i,
                "price": high,
                "datetime":
                    candles[i]["datetime"],
            })

    return swings


def find_swing_lows(
    candles,
    left=2,
    right=2
):

    swings = []

    if len(candles) < (
        left + right + 1
    ):
        return swings

    for i in range(
        left,
        len(candles) - right
    ):

        low = candles[i]["low"]

        valid = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:
                continue

            if candles[j]["low"] <= low:

                valid = False
                break

        if valid:

            swings.append({
                "index": i,
                "price": low,
                "datetime":
                    candles[i]["datetime"],
            })

    return swings


# ============================================================
# STRUCTURE
# ============================================================

def analyze_structure(candles):

    if len(candles) < 10:

        return {
            "bias": "NEUTRAL",
            "structure": "INSUFFICIENT DATA",
            "bullish_bos": False,
            "bearish_bos": False,
        }

    highs = find_swing_highs(candles)
    lows = find_swing_lows(candles)

    if len(highs) < 2 or len(lows) < 2:

        return {
            "bias": "NEUTRAL",
            "structure": "NO CLEAR STRUCTURE",
            "bullish_bos": False,
            "bearish_bos": False,
        }

    last_high = highs[-1]["price"]
    prev_high = highs[-2]["price"]

    last_low = lows[-1]["price"]
    prev_low = lows[-2]["price"]

    close = candles[-1]["close"]

    bullish_bos = close > last_high
    bearish_bos = close < last_low

    higher_high = last_high > prev_high
    higher_low = last_low > prev_low

    lower_high = last_high < prev_high
    lower_low = last_low < prev_low

    if bullish_bos:

        bias = "BULLISH"
        structure = "BULLISH BOS"

    elif bearish_bos:

        bias = "BEARISH"
        structure = "BEARISH BOS"

    elif higher_high and higher_low:

        bias = "BULLISH"
        structure = "HH / HL"

    elif lower_high and lower_low:

        bias = "BEARISH"
        structure = "LH / LL"

    else:

        bias = "RANGE"
        structure = "RANGE"

    return {
        "bias": bias,
        "structure": structure,
        "bullish_bos": bullish_bos,
        "bearish_bos": bearish_bos,
        "last_swing_high":
            round_price(last_high),
        "last_swing_low":
            round_price(last_low),
    }


# ============================================================
# RANGE
# ============================================================

def detect_range(
    candles,
    lookback=20
):

    if len(candles) < lookback:

        return {
            "is_range": False,
            "high": None,
            "low": None,
            "position": "UNKNOWN",
        }

    recent = candles[-lookback:]

    high = max(
        x["high"] for x in recent
    )

    low = min(
        x["low"] for x in recent
    )

    current = candles[-1]["close"]

    width = high - low

    if width <= 0:

        return {
            "is_range": False,
            "high": round_price(high),
            "low": round_price(low),
            "position": "UNKNOWN",
        }

    location = (
        current - low
    ) / width

    if location >= 0.75:

        position = "HIGH EXTREME"

    elif location <= 0.25:

        position = "LOW EXTREME"

    else:

        position = "MID RANGE"

    # V3.1:
    # More useful range detection.
    is_range = (
        0.15 <= location <= 0.85
        or width < current * 0.012
    )

    return {
        "is_range": is_range,
        "high": round_price(high),
        "low": round_price(low),
        "position": position,
        "width": round_price(width),
    }


# ============================================================
# PREMIUM / DISCOUNT
# ============================================================

def calculate_pd_zone(candles):

    if len(candles) < 20:

        return {
            "zone": "UNKNOWN",
            "equilibrium": None,
            "high": None,
            "low": None,
        }

    recent = candles[-20:]

    high = max(
        x["high"] for x in recent
    )

    low = min(
        x["low"] for x in recent
    )

    equilibrium = (
        high + low
    ) / 2

    current = candles[-1]["close"]

    if current > equilibrium:

        zone = "PREMIUM"

    elif current < equilibrium:

        zone = "DISCOUNT"

    else:

        zone = "EQUILIBRIUM"

    return {
        "zone": zone,
        "equilibrium":
            round_price(equilibrium),
        "high": round_price(high),
        "low": round_price(low),
    }


# ============================================================
# LIQUIDITY
# ============================================================

def detect_liquidity_sweep(candles):

    if len(candles) < 10:

        return {
            "type": "NONE",
            "detected": False,
            "level": None,
        }

    highs = find_swing_highs(candles)
    lows = find_swing_lows(candles)

    current = candles[-1]

    previous_high = (
        highs[-1]["price"]
        if highs
        else None
    )

    previous_low = (
        lows[-1]["price"]
        if lows
        else None
    )

    if (
        previous_high is not None
        and current["high"] > previous_high
        and current["close"] < previous_high
    ):

        return {
            "type":
                "BUY-SIDE LIQUIDITY SWEEP",
            "detected": True,
            "level":
                round_price(previous_high),
        }

    if (
        previous_low is not None
        and current["low"] < previous_low
        and current["close"] > previous_low
    ):

        return {
            "type":
                "SELL-SIDE LIQUIDITY SWEEP",
            "detected": True,
            "level":
                round_price(previous_low),
        }

    return {
        "type": "NONE",
        "detected": False,
        "level": None,
    }


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(candles):

    if len(candles) < 6:
        return "NEUTRAL"

    closes = [
        x["close"]
        for x in candles[-6:]
    ]

    moves = [
        closes[i] - closes[i - 1]
        for i in range(1, 6)
    ]

    positive = sum(
        1 for x in moves if x > 0
    )

    negative = sum(
        1 for x in moves if x < 0
    )

    if positive >= 4:
        return "BULLISH"

    if negative >= 4:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# CANDLE
# ============================================================

def candle_confirmation(candles):

    if len(candles) < 2:
        return "NEUTRAL"

    current = candles[-1]

    candle_range = (
        current["high"] -
        current["low"]
    )

    if candle_range <= 0:
        return "NEUTRAL"

    body = abs(
        current["close"] -
        current["open"]
    )

    ratio = body / candle_range

    if (
        current["close"] >
        current["open"]
        and ratio >= 0.50
    ):

        return "BULLISH"

    if (
        current["close"] <
        current["open"]
        and ratio >= 0.50
    ):

        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# M15 SETUP
# ============================================================

def setup_direction(setup_type):

    if setup_type.startswith("BUY"):
        return "BUY"

    if setup_type.startswith("SELL"):
        return "SELL"

    return None


def determine_m15_setup(
    candles,
    h1_analysis
):

    if len(candles) < 20:

        return {
            "type": "NO VALID SETUP",
            "valid": False,
            "reason":
                "Insufficient M15 data",
        }

    structure = analyze_structure(candles)
    range_info = detect_range(candles)
    sweep = detect_liquidity_sweep(candles)
    pd = calculate_pd_zone(candles)

    # ========================================================
    # RANGE REVERSAL
    # ========================================================

    if range_info["position"] == "LOW EXTREME":

        if sweep["type"] == (
            "SELL-SIDE LIQUIDITY SWEEP"
        ):

            return {
                "type":
                    "BUY RANGE REVERSAL",
                "valid": True,
                "reason":
                    "M15 low extreme + "
                    "sell-side sweep",
            }

    if range_info["position"] == "HIGH EXTREME":

        if sweep["type"] == (
            "BUY-SIDE LIQUIDITY SWEEP"
        ):

            return {
                "type":
                    "SELL RANGE REVERSAL",
                "valid": True,
                "reason":
                    "M15 high extreme + "
                    "buy-side sweep",
            }

    # ========================================================
    # LIQUIDITY SWEEP
    # ========================================================

    if sweep["detected"]:

        if sweep["type"] == (
            "SELL-SIDE LIQUIDITY SWEEP"
        ):

            return {
                "type":
                    "BUY LIQUIDITY SWEEP",
                "valid": True,
                "reason":
                    "Sell-side liquidity "
                    "taken and reclaimed",
            }

        if sweep["type"] == (
            "BUY-SIDE LIQUIDITY SWEEP"
        ):

            return {
                "type":
                    "SELL LIQUIDITY SWEEP",
                "valid": True,
                "reason":
                    "Buy-side liquidity "
                    "taken and rejected",
            }

    # ========================================================
    # TREND PULLBACK
    # ========================================================

    if h1_analysis["bias"] == "BULLISH":

        if pd["zone"] in (
            "DISCOUNT",
            "EQUILIBRIUM",
        ):

            return {
                "type": "BUY PULLBACK",
                "valid": True,
                "reason":
                    "H1 bullish + "
                    "M15 discount/equilibrium",
            }

    if h1_analysis["bias"] == "BEARISH":

        if pd["zone"] in (
            "PREMIUM",
            "EQUILIBRIUM",
        ):

            return {
                "type": "SELL PULLBACK",
                "valid": True,
                "reason":
                    "H1 bearish + "
                    "M15 premium/equilibrium",
            }

    # ========================================================
    # BREAKOUT
    # ========================================================

    if structure["bullish_bos"]:

        return {
            "type":
                "BUY BREAKOUT RETEST",
            "valid": True,
            "reason":
                "M15 bullish BOS",
        }

    if structure["bearish_bos"]:

        return {
            "type":
                "SELL BREAKOUT RETEST",
            "valid": True,
            "reason":
                "M15 bearish BOS",
        }

    return {
        "type": "NO VALID SETUP",
        "valid": False,
        "reason":
            "No valid M15 setup",
    }


# ============================================================
# M5 CONFIRMATION
# ============================================================

def m5_confirmation(
    candles,
    expected_direction=None
):

    structure = analyze_structure(candles)

    candle = candle_confirmation(candles)

    momentum = calculate_momentum(candles)

    bullish = (
        structure["bullish_bos"]
        or (
            candle == "BULLISH"
            and momentum == "BULLISH"
        )
    )

    bearish = (
        structure["bearish_bos"]
        or (
            candle == "BEARISH"
            and momentum == "BEARISH"
        )
    )

    if expected_direction == "BUY":

        confirmed = bullish

    elif expected_direction == "SELL":

        confirmed = bearish

    else:

        confirmed = (
            bullish or bearish
        )

    if bullish and not bearish:

        direction = "BULLISH"

    elif bearish and not bullish:

        direction = "BEARISH"

    else:

        direction = "NEUTRAL"

    return {
        "confirmed": confirmed,
        "direction": direction,
        "bos_choch":
            structure["structure"],
        "candle": candle,
        "momentum": momentum,
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    h1,
    m15,
    m5,
    pd,
    setup,
    liquidity
):

    score = 0

    # --------------------------------------------------------
    # H1 CONTEXT
    # --------------------------------------------------------

    if h1["bias"] in (
        "BULLISH",
        "BEARISH",
    ):

        score += 15

    if (
        h1["bullish_bos"]
        or h1["bearish_bos"]
    ):

        score += 5

    # --------------------------------------------------------
    # M15 SETUP
    # --------------------------------------------------------

    if setup["valid"]:
        score += 10

    if liquidity["detected"]:
        score += 10

    if m15["structure"] in (
        "BULLISH BOS",
        "BEARISH BOS",
        "HH / HL",
        "LH / LL",
    ):

        score += 10

    # --------------------------------------------------------
    # M5
    # --------------------------------------------------------

    if m5["confirmed"]:
        score += 15

    if m5["candle"] in (
        "BULLISH",
        "BEARISH",
    ):

        score += 10

    if m5["momentum"] in (
        "BULLISH",
        "BEARISH",
    ):

        score += 10

    # --------------------------------------------------------
    # LOCATION
    # --------------------------------------------------------

    direction = setup_direction(
        setup["type"]
    )

    if (
        direction == "BUY"
        and pd["zone"] == "DISCOUNT"
    ):

        score += 5

    elif (
        direction == "SELL"
        and pd["zone"] == "PREMIUM"
    ):

        score += 5

    # Directional alignment
    if (
        direction == "BUY"
        and h1["bias"] == "BULLISH"
    ):

        score += 5

    elif (
        direction == "SELL"
        and h1["bias"] == "BEARISH"
    ):

        score += 5

    # Session
    if session_name() != "OFF SESSION":

        score += 5

    return int(
        clamp(score, 0, 100)
    )


# ============================================================
# TRADE PLAN
# ============================================================

def invalid_trade_plan(
    direction,
    reason="Unable to build trade plan"
):

    return {
        "valid": False,
        "direction": direction,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "risk_pips": None,
        "rr_tp1": None,
        "rr_tp2": None,
        "rr_tp3": None,
        "tp1_pips": None,
        "tp2_pips": None,
        "tp3_pips": None,
        "reason": reason,
    }


def create_trade_plan(
    candles,
    direction,
    current_price
):

    if direction not in (
        "BUY",
        "SELL",
    ):

        return invalid_trade_plan(
            direction
        )

    highs = find_swing_highs(candles)
    lows = find_swing_lows(candles)

    entry = current_price

    if direction == "BUY":

        if not lows:

            return invalid_trade_plan(
                direction,
                "No swing low"
            )

        swing_low = lows[-1]["price"]

        sl = swing_low - 0.20

        risk_price = (
            entry - sl
        )

    else:

        if not highs:

            return invalid_trade_plan(
                direction,
                "No swing high"
            )

        swing_high = highs[-1]["price"]

        sl = swing_high + 0.20

        risk_price = (
            sl - entry
        )

    risk_pips = price_to_pips(
        risk_price
    )

    if risk_pips is None:

        return invalid_trade_plan(
            direction,
            "Invalid risk"
        )

    if (
        risk_pips < MIN_RISK_PIPS
        or risk_pips > MAX_RISK_PIPS
    ):

        return invalid_trade_plan(
            direction,
            "Risk outside "
            f"{MIN_RISK_PIPS}-"
            f"{MAX_RISK_PIPS} pips"
        )

    # --------------------------------------------------------
    # TP1
    # --------------------------------------------------------

    tp1_distance = pips_to_price(
        TP1_PIPS
    )

    if direction == "BUY":

        tp1 = entry + tp1_distance

    else:

        tp1 = entry - tp1_distance

    # --------------------------------------------------------
    # TP2
    # --------------------------------------------------------

    tp2_pips = max(
        MIN_TP2_PIPS,
        risk_pips * MIN_RR
    )

    tp2_distance = pips_to_price(
        tp2_pips
    )

    if direction == "BUY":

        tp2 = entry + tp2_distance

    else:

        tp2 = entry - tp2_distance

    # --------------------------------------------------------
    # TP3
    # --------------------------------------------------------

    tp3_pips = max(
        TP3_PIPS,
        risk_pips * 3
    )

    tp3_distance = pips_to_price(
        tp3_pips
    )

    if direction == "BUY":

        tp3 = entry + tp3_distance

    else:

        tp3 = entry - tp3_distance

    return {
        "valid": True,
        "direction": direction,
        "entry": round_price(entry),
        "sl": round_price(sl),
        "tp1": round_price(tp1),
        "tp2": round_price(tp2),
        "tp3": round_price(tp3),
        "risk_pips":
            round(risk_pips, 1),
        "rr_tp1":
            round(
                TP1_PIPS / risk_pips,
                2
            ),
        "rr_tp2":
            round(
                tp2_pips / risk_pips,
                2
            ),
        "rr_tp3":
            round(
                tp3_pips / risk_pips,
                2
            ),
        "tp1_pips": TP1_PIPS,
        "tp2_pips":
            round(tp2_pips, 1),
        "tp3_pips":
            round(tp3_pips, 1),
        "reason":
            "V3.1 scalping plan",
    }


# ============================================================
# POTENTIAL
# ============================================================

def calculate_potential(plan):

    if not plan.get("valid"):

        return {
            "status": "LOW",
            "min_pips": None,
            "max_pips": None,
            "description":
                "No valid trade plan",
        }

    tp1 = plan.get("tp1_pips")
    tp3 = plan.get("tp3_pips")

    return {
        "status": "HIGH",
        "min_pips": tp1,
        "max_pips": tp3,
        "description":
            f"Potential move "
            f"{round(tp1)}-"
            f"{round(tp3)} pips",
    }


# ============================================================
# STATE
# ============================================================

def load_state():

    if not STATE_FILE.exists():
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception:

        return {}


def save_state(state):

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = STATE_FILE.with_suffix(
        ".tmp"
    )

    with open(
        temp,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            clean_for_json(state),
            file,
            indent=2,
            ensure_ascii=False
        )

    os.replace(
        temp,
        STATE_FILE
    )


# ============================================================
# SIGNAL ID
# ============================================================

def build_signal_id(
    opportunity,
    plan,
    score
):

    if not plan.get("valid"):
        return None

    return "|".join([
        str(opportunity),
        str(plan.get("direction")),
        str(plan.get("entry")),
        str(plan.get("sl")),
        str(plan.get("tp2")),
        str(score),
    ])


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "Telegram disabled: "
            "BOT TOKEN missing."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "Telegram disabled: "
            "CHAT ID missing."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            json={
                "chat_id":
                    TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            print(
                "Telegram error:",
                response.text[:500]
            )

            return False

        return True

    except Exception as error:

        print(
            "Telegram exception:",
            error
        )

        return False


def build_telegram_message(
    price,
    session,
    h1,
    m15,
    m5,
    opportunity,
    score,
    plan,
    potential
):

    return (
        "🔥 BOSQUE FOREX AI\n"
        "\n"
        "XAU/USD SCALPING\n"
        f"Direction: {plan.get('direction')}\n"
        f"Opportunity: {opportunity}\n"
        f"Score: {score}/100\n"
        "\n"
        f"Entry: {plan.get('entry')}\n"
        f"SL: {plan.get('sl')}\n"
        f"TP1: {plan.get('tp1')} "
        f"({plan.get('tp1_pips')} pips)\n"
        f"TP2: {plan.get('tp2')} "
        f"({plan.get('tp2_pips')} pips)\n"
        f"TP3: {plan.get('tp3')} "
        f"({plan.get('tp3_pips')} pips)\n"
        f"Risk: {plan.get('risk_pips')} pips\n"
        f"RR TP2: 1:{plan.get('rr_tp2')}\n"
        "\n"
        f"Potential: "
        f"{potential.get('min_pips')}-"
        f"{potential.get('max_pips')} pips\n"
        "\n"
        f"Session: {session}\n"
        f"H1: {h1.get('bias')} | "
        f"{h1.get('structure')}\n"
        f"M15: {m15.get('bias')} | "
        f"{m15.get('structure')}\n"
        f"M5: {m5.get('direction')} | "
        f"{m5.get('bos_choch')}\n"
        "\n"
        f"Price: {price}\n"
        "\n"
        "⚠️ Manual confirmation required."
    )


# ============================================================
# DASHBOARD
# ============================================================

def build_dashboard_data(
    price,
    session,
    h1,
    m15,
    m15_range,
    m15_liquidity,
    m15_setup,
    m5,
    pd,
    opportunity,
    score,
    plan,
    potential,
    signal_id
):

    valid = (
        opportunity !=
        "NO VALID SETUP"
        and score >= MIN_SCORE
        and plan.get("valid", False)
    )

    return {

        "engine": {
            "name":
                "BOSQUE FOREX AI",
            "version":
                "SCALPING V3.1",
            "symbol":
                SYMBOL,
            "timeframes": {
                "bias": "H1",
                "setup": "M15",
                "confirmation": "M5",
            },
            "pip_size":
                PIP_SIZE,
            "pip_rule":
                "1 pip = 0.10 XAU/USD",
            "min_score":
                MIN_SCORE,
            "min_risk_pips":
                MIN_RISK_PIPS,
            "max_risk_pips":
                MAX_RISK_PIPS,
            "min_rr":
                MIN_RR,
        },

        "timestamp":
            now_iso(),

        "latest_price":
            round_price(price),

        "session":
            session,

        "market_mode":
            (
                "RANGE"
                if h1["bias"] == "RANGE"
                else "TREND"
            ),

        "pd": pd,

        "opportunity": {
            "type":
                opportunity,
            "score":
                score,
            "valid":
                valid,
        },

        "plan":
            plan,

        "potential":
            potential,

        # ====================================================
        # BROWSER ALERT PAYLOAD
        # ====================================================

        "alert": {

            "valid":
                valid,

            "signal_id":
                signal_id,

            "type":
                opportunity,

            "direction":
                plan.get("direction"),

            "score":
                score,

            "entry":
                plan.get("entry"),

            "sl":
                plan.get("sl"),

            "tp1":
                plan.get("tp1"),

            "tp2":
                plan.get("tp2"),

            "tp3":
                plan.get("tp3"),

            "risk_pips":
                plan.get("risk_pips"),

            "rr_tp2":
                plan.get("rr_tp2"),

            "potential":
                potential.get("description"),

            "message":
                (
                    f"{opportunity} | "
                    f"Score {score}/100 | "
                    f"Entry {plan.get('entry')} | "
                    f"SL {plan.get('sl')} | "
                    f"TP1 {plan.get('tp1')}"
                ),
        },

        "h1": {
            **h1
        },

        "m15": {

            **m15,

            "range":
                m15_range,

            "liquidity":
                m15_liquidity,

            "setup":
                m15_setup,
        },

        "m5": {
            **m5
        },

        "filters": {

            "score":
                (
                    "PASS"
                    if score >= MIN_SCORE
                    else "FAIL"
                ),

            "m15_setup":
                (
                    "PASS"
                    if m15_setup["valid"]
                    else "FAIL"
                ),

            "m5_confirmation":
                (
                    "PASS"
                    if m5["confirmed"]
                    else "FAIL"
                ),

            "risk":
                (
                    "PASS"
                    if plan.get("valid")
                    else "FAIL"
                ),

            "rr":
                (
                    "PASS"
                    if (
                        plan.get("valid")
                        and safe_float(
                            plan.get("rr_tp2"),
                            0
                        ) >= MIN_RR
                    )
                    else "FAIL"
                ),
        },

        "confirmations": {

            "h1_bias":
                h1["bias"],

            "h1_structure":
                h1["structure"],

            "m15_setup":
                m15_setup["type"],

            "m15_liquidity":
                m15_liquidity["type"],

            "m5_bos_choch":
                m5["bos_choch"],

            "m5_candle":
                m5["candle"],

            "m5_momentum":
                m5["momentum"],
        },
    }


# ============================================================
# SAVE DASHBOARD
# ============================================================

def save_dashboard(data):

    DASHBOARD_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = DASHBOARD_FILE.with_suffix(
        ".tmp"
    )

    with open(
        temp,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            clean_for_json(data),
            file,
            indent=2,
            ensure_ascii=False
        )

    os.replace(
        temp,
        DASHBOARD_FILE
    )

    print(
        "✅ Dashboard data saved:",
        DASHBOARD_FILE
    )


# ============================================================
# ERROR DASHBOARD
# ============================================================

def save_error_dashboard(error):

    data = {

        "engine": {
            "name":
                "BOSQUE FOREX AI",
            "version":
                "SCALPING V3.1",
            "symbol":
                SYMBOL,
        },

        "timestamp":
            now_iso(),

        "latest_price":
            None,

        "session":
            session_name(),

        "market_mode":
            "UNKNOWN",

        "pd": {
            "zone":
                "UNKNOWN",
            "equilibrium":
                None,
            "high":
                None,
            "low":
                None,
        },

        "opportunity": {
            "type":
                "NO VALID SETUP",
            "score":
                0,
            "valid":
                False,
        },

        "plan":
            invalid_trade_plan(
                None,
                "Engine error"
            ),

        "potential": {
            "status":
                "LOW",
            "min_pips":
                None,
            "max_pips":
                None,
            "description":
                "Engine error",
        },

        "alert": {
            "valid":
                False,
            "signal_id":
                None,
        },

        "h1": {
            "bias":
                "UNKNOWN",
            "structure":
                "ENGINE ERROR",
        },

        "m15": {
            "bias":
                "UNKNOWN",
            "structure":
                "ENGINE ERROR",
            "range": {},
            "liquidity": {},
            "setup": {},
        },

        "m5": {
            "confirmed":
                False,
            "direction":
                "UNKNOWN",
            "bos_choch":
                "ENGINE ERROR",
            "candle":
                "UNKNOWN",
            "momentum":
                "UNKNOWN",
        },

        "filters": {
            "score":
                "FAIL",
            "m15_setup":
                "FAIL",
            "m5_confirmation":
                "FAIL",
            "risk":
                "FAIL",
            "rr":
                "FAIL",
        },

        "confirmations": {},

        "error":
            str(error),
    }

    try:
        save_dashboard(data)

    except Exception as save_error:

        print(
            "❌ Failed to save "
            "error dashboard:",
            save_error
        )


# ============================================================
# MAIN ENGINE
# ============================================================

def run_engine():

    print("=" * 60)
    print(
        "BOSQUE FOREX AI "
        "- SCALPING V3.1"
    )
    print("=" * 60)

    print(
        "Repository:",
        REPO_DIR
    )

    print(
        "Dashboard:",
        DASHBOARD_FILE
    )

    print(
        "State:",
        STATE_FILE
    )

    print(
        "Symbol:",
        SYMBOL
    )

    print(
        "Minimum Score:",
        MIN_SCORE
    )

    print(
        "Risk Range:",
        f"{MIN_RISK_PIPS}-"
        f"{MAX_RISK_PIPS} pips"
    )

    print(
        "Minimum RR:",
        f"1:{MIN_RR}"
    )

    print(
        "Session:",
        session_name()
    )

    # ========================================================
    # FETCH
    # ========================================================

    print(
        "\n📡 Fetching Twelve Data..."
    )

    m5_raw = fetch_m5_data()

    print(
        f"✅ Received "
        f"{len(m5_raw)} M5 candles"
    )

    m5_raw = remove_incomplete_candle(
        m5_raw
    )

    if len(m5_raw) < 50:

        raise RuntimeError(
            "Not enough completed M5 candles."
        )

    # ========================================================
    # AGGREGATE
    # ========================================================

    h1_candles = aggregate_candles(
        m5_raw,
        60
    )

    m15_candles = aggregate_candles(
        m5_raw,
        15
    )

    print(
        f"H1 candles: "
        f"{len(h1_candles)}"
    )

    print(
        f"M15 candles: "
        f"{len(m15_candles)}"
    )

    print(
        f"M5 candles: "
        f"{len(m5_raw)}"
    )

    # ========================================================
    # PRICE
    # ========================================================

    current_price = m5_raw[-1]["close"]

    # ========================================================
    # H1
    # ========================================================

    h1 = analyze_structure(
        h1_candles
    )

    h1_range = detect_range(
        h1_candles
    )

    if h1_range["is_range"]:

        h1["bias"] = "RANGE"
        h1["structure"] = "RANGE"

    # ========================================================
    # M15
    # ========================================================

    m15 = analyze_structure(
        m15_candles
    )

    m15_range = detect_range(
        m15_candles
    )

    m15_liquidity = (
        detect_liquidity_sweep(
            m15_candles
        )
    )

    pd = calculate_pd_zone(
        m15_candles
    )

    # ========================================================
    # SETUP
    # ========================================================

    m15_setup = determine_m15_setup(
        m15_candles,
        h1
    )

    direction = setup_direction(
        m15_setup["type"]
    )

    # ========================================================
    # M5
    # ========================================================

    m5 = m5_confirmation(
        m5_raw,
        direction
    )

    # ========================================================
    # SCORE
    # ========================================================

    score = calculate_score(
        h1=h1,
        m15=m15,
        m5=m5,
        pd=pd,
        setup=m15_setup,
        liquidity=m15_liquidity,
    )

    # ========================================================
    # HARD GATES
    # ========================================================

    hard_gate_pass = (
        m15_setup["valid"]
        and m5["confirmed"]
        and score >= MIN_SCORE
        and direction in (
            "BUY",
            "SELL",
        )
    )

    # ========================================================
    # TRADE PLAN
    # ========================================================

    if hard_gate_pass:

        plan = create_trade_plan(
            m5_raw,
            direction,
            current_price,
        )

    else:

        plan = invalid_trade_plan(
            direction,
            "Hard gates not passed"
        )

    # ========================================================
    # RR GATE
    # ========================================================

    if plan.get("valid"):

        rr_tp2 = safe_float(
            plan.get("rr_tp2")
        )

        if (
            rr_tp2 is None
            or rr_tp2 < MIN_RR
        ):

            plan["valid"] = False

            plan["reason"] = (
                f"RR below "
                f"minimum 1:{MIN_RR}"
            )

    # ========================================================
    # FINAL OPPORTUNITY
    # ========================================================

    if (
        hard_gate_pass
        and plan.get("valid")
    ):

        opportunity = (
            m15_setup["type"]
        )

    else:

        opportunity = (
            "NO VALID SETUP"
        )

    # ========================================================
    # POTENTIAL
    # ========================================================

    potential = calculate_potential(
        plan
    )

    # ========================================================
    # SIGNAL ID
    # ========================================================

    signal_id = build_signal_id(
        opportunity,
        plan,
        score
    )

    # ========================================================
    # DASHBOARD
    # ========================================================

    dashboard = build_dashboard_data(

        price=current_price,

        session=session_name(),

        h1=h1,

        m15=m15,

        m15_range=m15_range,

        m15_liquidity=m15_liquidity,

        m15_setup=m15_setup,

        m5=m5,

        pd=pd,

        opportunity=opportunity,

        score=score,

        plan=plan,

        potential=potential,

        signal_id=signal_id,
    )

    save_dashboard(
        dashboard
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print("\n" + "=" * 60)
    print("BOSQUE ENGINE RESULT")
    print("=" * 60)

    print(
        f"Price        : "
        f"{current_price:.2f}"
    )

    print(
        f"Session      : "
        f"{session_name()}"
    )

    print(
        f"H1 Bias      : "
        f"{h1['bias']}"
    )

    print(
        f"H1 Structure : "
        f"{h1['structure']}"
    )

    print(
        f"M15 Setup    : "
        f"{m15_setup['type']}"
    )

    print(
        f"M15 Liquidity: "
        f"{m15_liquidity['type']}"
    )

    print(
        f"M5 Confirm   : "
        f"{m5['confirmed']}"
    )

    print(
        f"M5 Direction : "
        f"{m5['direction']}"
    )

    print(
        f"M5 Candle    : "
        f"{m5['candle']}"
    )

    print(
        f"M5 Momentum  : "
        f"{m5['momentum']}"
    )

    print(
        f"Score        : "
        f"{score}/100"
    )

    print(
        f"Opportunity  : "
        f"{opportunity}"
    )

    print(
        f"Plan Valid   : "
        f"{plan.get('valid')}"
    )

    print(
        f"Entry        : "
        f"{plan.get('entry')}"
    )

    print(
        f"SL           : "
        f"{plan.get('sl')}"
    )

    print(
        f"TP1          : "
        f"{plan.get('tp1')}"
    )

    print(
        f"TP2          : "
        f"{plan.get('tp2')}"
    )

    print(
        f"TP3          : "
        f"{plan.get('tp3')}"
    )

    print(
        f"Risk         : "
        f"{plan.get('risk_pips')} pips"
    )

    print(
        f"RR TP2       : "
        f"1:{plan.get('rr_tp2')}"
    )

    print(
        f"Signal ID    : "
        f"{signal_id}"
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    if (
        signal_id
        and plan.get("valid")
    ):

        state = load_state()

        previous_signal = (
            state.get(
                "last_signal_id"
            )
        )

        if (
            signal_id !=
            previous_signal
        ):

            message = (
                build_telegram_message(
                    price=current_price,
                    session=session_name(),
                    h1=h1,
                    m15=m15,
                    m5=m5,
                    opportunity=opportunity,
                    score=score,
                    plan=plan,
                    potential=potential,
                )
            )

            if send_telegram(
                message
            ):

                state[
                    "last_signal_id"
                ] = signal_id

                state[
                    "last_signal_time"
                ] = now_iso()

                state[
                    "last_signal_score"
                ] = score

                state[
                    "last_signal_opportunity"
                ] = opportunity

                save_state(state)

                print(
                    "📲 Telegram alert sent."
                )

        else:

            print(
                "ℹ️ Same signal already sent."
            )

    else:

        print(
            "ℹ️ No Telegram signal."
        )

    print("=" * 60)
    print("✅ ENGINE COMPLETE")
    print("=" * 60)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        run_engine()

    except Exception as error:

        print(
            "\n❌ BOSQUE ENGINE ERROR"
        )

        print(
            str(error)
        )

        save_error_dashboard(
            error
        )

        raise