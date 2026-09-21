import os
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# BOSQUE FOREX AI
# XAU/USD SCALPING ENGINE V3
#
# H1  -> Market Bias / Market Condition
# M15 -> Setup / Location / Liquidity
# M5  -> Confirmation / Entry
#
# Pip convention:
# 1 point = 0.01 price
# 10 points = 1 pip
# 1 pip = 0.10 XAU/USD price movement
# ============================================================


# ============================================================
# PATHS
# ============================================================

# This file is:
# Repository/Engine/bosque_engine.py
ENGINE_DIR = Path(__file__).resolve().parent

# Repository root:
# Repository/
REPO_DIR = ENGINE_DIR.parent

# Dashboard JSON MUST be in repository root
DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"

# State stays inside Engine folder
STATE_FILE = ENGINE_DIR / "state.json"


# ============================================================
# ENVIRONMENT
# ============================================================

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# TWELVE DATA
# ============================================================

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

SYMBOL = "XAU/USD"

M5_INTERVAL = "5min"

# 500 M5 candles ≈ 41 hours
OUTPUT_SIZE = 500


# ============================================================
# ENGINE SETTINGS
# ============================================================

MIN_SCORE = 70

MIN_RISK_PIPS = 35
MAX_RISK_PIPS = 60

TP1_PIPS = 60
MIN_TP2_PIPS = 120
TP3_PIPS = 180

MIN_RR = 2.0

# IMPORTANT:
# XAUUSD:
# 1 point = 0.01
# 10 points = 1 pip
# Therefore:
# 1 pip = 0.10 price
PIP_SIZE = 0.10


# ============================================================
# SESSION SETTINGS - MALAYSIA TIME UTC+8
# ============================================================

MY_TZ_OFFSET_HOURS = 8


# ============================================================
# HTTP SETTINGS
# ============================================================

REQUEST_TIMEOUT = 30


# ============================================================
# GLOBALS
# ============================================================

M15_CANDLES_GLOBAL = []


# ============================================================
# BASIC HELPERS
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

    return round(abs(distance) / PIP_SIZE, 1)


def pips_to_price(pips):
    pips = safe_float(pips)

    if pips is None:
        return None

    return pips * PIP_SIZE


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def clean_for_json(value):
    """
    Prevent NaN / Infinity from entering dashboard JSON.
    """

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
# MALAYSIA TIME
# ============================================================

def malaysia_datetime():
    return datetime.now(
        timezone.utc
    ).astimezone(
        timezone(
            timezone.utc.utcoffset(
                datetime.now(timezone.utc)
            )
            or timezone.utc.utcoffset(
                datetime.now(timezone.utc)
            )
        )
    )


def malaysia_hour():
    """
    Simple UTC+8 conversion.
    """

    utc_now = datetime.now(timezone.utc)

    return (utc_now.hour + MY_TZ_OFFSET_HOURS) % 24


def session_name():
    hour = malaysia_hour()

    # Asian / Tokyo
    if 7 <= hour < 15:
        return "ASIAN"

    # London
    if 15 <= hour < 20:
        return "LONDON"

    # New York
    if 20 <= hour <= 23:
        return "NEW YORK"

    # NY continuation after midnight
    if 0 <= hour < 1:
        return "NEW YORK"

    return "OFF SESSION"


# ============================================================
# FETCH TWELVE DATA
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
            f"Twelve Data HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    if "status" in data and data["status"] == "error":
        message = data.get(
            "message",
            "Unknown Twelve Data error"
        )

        raise RuntimeError(
            f"Twelve Data error: {message}"
        )

    values = data.get("values")

    if not values:
        raise RuntimeError(
            "Twelve Data returned no OHLC data."
        )

    candles = []

    for item in values:
        try:
            candle = {
                "datetime": item["datetime"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            }

            candles.append(candle)

        except Exception:
            continue

    if len(candles) < 50:
        raise RuntimeError(
            f"Not enough M5 candles: {len(candles)}"
        )

    # Twelve Data usually returns newest first.
    candles.sort(
        key=lambda x: x["datetime"]
    )

    return candles


# ============================================================
# REMOVE INCOMPLETE M5 CANDLE
# ============================================================

def remove_incomplete_candle(candles):
    if len(candles) < 2:
        return candles

    try:
        last_dt = datetime.fromisoformat(
            candles[-1]["datetime"].replace(
                "Z",
                "+00:00"
            )
        )

        current = now_utc()

        elapsed = (
            current - last_dt
        ).total_seconds()

        # If latest candle is still inside
        # the current 5-minute window,
        # remove it.
        if elapsed < 300:
            return candles[:-1]

    except Exception:
        pass

    return candles


# ============================================================
# AGGREGATE CANDLES
# ============================================================

def aggregate_candles(candles, minutes):
    if not candles:
        return []

    if minutes not in (15, 60):
        raise ValueError(
            "Only 15 and 60 minute aggregation supported."
        )

    result = []

    current_bucket = None
    bucket = []

    for candle in candles:

        try:
            dt = datetime.fromisoformat(
                candle["datetime"].replace(
                    "Z",
                    "+00:00"
                )
            )
        except Exception:
            continue

        minute = (
            dt.hour * 60 +
            dt.minute
        )

        bucket_minute = (
            minute // minutes
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
                    build_aggregated_candle(bucket)
                )

            bucket = []
            current_bucket = bucket_key

        bucket.append(candle)

    if bucket:
        result.append(
            build_aggregated_candle(bucket)
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
# SWING DETECTION
# ============================================================

def find_swing_highs(candles, left=2, right=2):
    swings = []

    if len(candles) < left + right + 1:
        return swings

    for i in range(
        left,
        len(candles) - right
    ):

        high = candles[i]["high"]

        is_swing = True

        for j in range(
            i - left,
            i + right + 1
        ):
            if j == i:
                continue

            if candles[j]["high"] >= high:
                is_swing = False
                break

        if is_swing:
            swings.append({
                "index": i,
                "price": high,
                "datetime": candles[i]["datetime"],
            })

    return swings


def find_swing_lows(candles, left=2, right=2):
    swings = []

    if len(candles) < left + right + 1:
        return swings

    for i in range(
        left,
        len(candles) - right
    ):

        low = candles[i]["low"]

        is_swing = True

        for j in range(
            i - left,
            i + right + 1
        ):
            if j == i:
                continue

            if candles[j]["low"] <= low:
                is_swing = False
                break

        if is_swing:
            swings.append({
                "index": i,
                "price": low,
                "datetime": candles[i]["datetime"],
            })

    return swings


# ============================================================
# MARKET STRUCTURE
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

    last_close = candles[-1]["close"]

    bullish_bos = (
        last_close > last_high
    )

    bearish_bos = (
        last_close < last_low
    )

    higher_high = (
        last_high > prev_high
    )

    higher_low = (
        last_low > prev_low
    )

    lower_high = (
        last_high < prev_high
    )

    lower_low = (
        last_low < prev_low
    )

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
        "last_swing_high": round_price(last_high),
        "last_swing_low": round_price(last_low),
    }


# ============================================================
# RANGE DETECTION
# ============================================================

def detect_range(candles, lookback=20):
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
        (current - low) / width
    )

    if location >= 0.75:
        position = "HIGH EXTREME"

    elif location <= 0.25:
        position = "LOW EXTREME"

    else:
        position = "MID RANGE"

    # A relatively balanced structure
    # without strong directional expansion.
    is_range = (
        0.20 <= location <= 0.80
        or width < current * 0.01
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
        "equilibrium": round_price(equilibrium),
        "high": round_price(high),
        "low": round_price(low),
    }


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

def detect_liquidity_sweep(candles):
    if len(candles) < 10:
        return {
            "type": "NONE",
            "detected": False,
            "level": None,
        }

    swings_high = find_swing_highs(candles)
    swings_low = find_swing_lows(candles)

    current = candles[-1]

    current_high = current["high"]
    current_low = current["low"]
    current_close = current["close"]

    previous_high = (
        swings_high[-1]["price"]
        if swings_high
        else None
    )

    previous_low = (
        swings_low[-1]["price"]
        if swings_low
        else None
    )

    # Sweep buy-side liquidity:
    # price takes previous high
    # but closes back below it.
    if (
        previous_high is not None
        and current_high > previous_high
        and current_close < previous_high
    ):
        return {
            "type": "BUY-SIDE LIQUIDITY SWEEP",
            "detected": True,
            "level": round_price(previous_high),
        }

    # Sweep sell-side liquidity:
    # price takes previous low
    # but closes back above it.
    if (
        previous_low is not None
        and current_low < previous_low
        and current_close > previous_low
    ):
        return {
            "type": "SELL-SIDE LIQUIDITY SWEEP",
            "detected": True,
            "level": round_price(previous_low),
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
        for i in range(1, len(closes))
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
# CANDLE CONFIRMATION
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

    body_ratio = body / candle_range

    # Bullish strong body
    if (
        current["close"] >
        current["open"]
        and body_ratio >= 0.55
    ):
        return "BULLISH"

    # Bearish strong body
    if (
        current["close"] <
        current["open"]
        and body_ratio >= 0.55
    ):
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# M15 SETUP
# ============================================================

def determine_m15_setup(candles, h1_analysis):
    if len(candles) < 20:
        return {
            "type": "NO VALID SETUP",
            "valid": False,
            "reason": "Insufficient M15 data",
        }

    structure = analyze_structure(candles)

    range_info = detect_range(candles)

    sweep = detect_liquidity_sweep(candles)

    pd = calculate_pd_zone(candles)

    current = candles[-1]["close"]

    # --------------------------------------------------------
    # RANGE REVERSAL
    # --------------------------------------------------------

    if range_info["position"] == "LOW EXTREME":

        if (
            sweep["type"] ==
            "SELL-SIDE LIQUIDITY SWEEP"
        ):

            return {
                "type": "BUY RANGE REVERSAL",
                "valid": True,
                "reason": (
                    "M15 low extreme + "
                    "sell-side liquidity sweep"
                ),
            }

    if range_info["position"] == "HIGH EXTREME":

        if (
            sweep["type"] ==
            "BUY-SIDE LIQUIDITY SWEEP"
        ):

            return {
                "type": "SELL RANGE REVERSAL",
                "valid": True,
                "reason": (
                    "M15 high extreme + "
                    "buy-side liquidity sweep"
                ),
            }

    # --------------------------------------------------------
    # LIQUIDITY SWEEP
    # --------------------------------------------------------

    if sweep["detected"]:

        if (
            sweep["type"] ==
            "SELL-SIDE LIQUIDITY SWEEP"
        ):
            return {
                "type": "BUY LIQUIDITY SWEEP",
                "valid": True,
                "reason": (
                    "M15 sell-side liquidity "
                    "taken and reclaimed"
                ),
            }

        if (
            sweep["type"] ==
            "BUY-SIDE LIQUIDITY SWEEP"
        ):
            return {
                "type": "SELL LIQUIDITY SWEEP",
                "valid": True,
                "reason": (
                    "M15 buy-side liquidity "
                    "taken and rejected"
                ),
            }

    # --------------------------------------------------------
    # TREND PULLBACK
    # --------------------------------------------------------

    if h1_analysis["bias"] == "BULLISH":

        if pd["zone"] == "DISCOUNT":
            return {
                "type": "BUY PULLBACK",
                "valid": True,
                "reason": (
                    "H1 bullish + M15 discount "
                    "pullback location"
                ),
            }

    if h1_analysis["bias"] == "BEARISH":

        if pd["zone"] == "PREMIUM":
            return {
                "type": "SELL PULLBACK",
                "valid": True,
                "reason": (
                    "H1 bearish + M15 premium "
                    "pullback location"
                ),
            }

    # --------------------------------------------------------
    # BREAKOUT RETEST
    # --------------------------------------------------------

    if structure["bullish_bos"]:
        return {
            "type": "BUY BREAKOUT RETEST",
            "valid": True,
            "reason": "M15 bullish BOS detected",
        }

    if structure["bearish_bos"]:
        return {
            "type": "SELL BREAKOUT RETEST",
            "valid": True,
            "reason": "M15 bearish BOS detected",
        }

    return {
        "type": "NO VALID SETUP",
        "valid": False,
        "reason": "No valid M15 setup",
    }


# ============================================================
# M5 CONFIRMATION
# ============================================================

def m5_confirmation(candles, expected_direction=None):
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
        "bos_choch": structure["structure"],
        "candle": candle,
        "momentum": momentum,
    }


# ============================================================
# DETERMINE DIRECTION
# ============================================================

def setup_direction(setup_type):
    if setup_type.startswith("BUY"):
        return "BUY"

    if setup_type.startswith("SELL"):
        return "SELL"

    return None


# ============================================================
# SCORING
# ============================================================

def calculate_score(
    h1,
    m15,
    m5,
    pd,
    setup,
    liquidity,
):
    score = 0

    # --------------------------------------------------------
    # H1 CONTEXT - 20
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
    # M15 SETUP - 30
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
    # M5 ENTRY - 35
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
    # LOCATION - 15
    # --------------------------------------------------------

    direction = setup_direction(
        setup["type"]
    )

    if direction == "BUY" and pd["zone"] == "DISCOUNT":
        score += 5

    elif direction == "SELL" and pd["zone"] == "PREMIUM":
        score += 5

    # Structure alignment
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

    # Session condition
    if session_name() != "OFF SESSION":
        score += 5

    return int(
        clamp(score, 0, 100)
    )


# ============================================================
# TRADE PLAN
# ============================================================

def create_trade_plan(
    candles,
    direction,
    current_price,
):
    if direction not in (
        "BUY",
        "SELL",
    ):
        return {
            "valid": False,
            "direction": None,
            "entry": None,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "risk_pips": None,
            "rr_tp1": None,
            "rr_tp2": None,
            "rr_tp3": None,
        }

    highs = find_swing_highs(candles)
    lows = find_swing_lows(candles)

    if direction == "BUY":

        entry = current_price

        if not lows:
            return invalid_trade_plan(
                direction
            )

        swing_low = lows[-1]["price"]

        # Small structural buffer
        sl = swing_low - 0.20

        risk_price = (
            entry - sl
        )

    else:

        entry = current_price

        if not highs:
            return invalid_trade_plan(
                direction
            )

        swing_high = highs[-1]["price"]

        # Small structural buffer
        sl = swing_high + 0.20

        risk_price = (
            sl - entry
        )

    risk_pips = price_to_pips(
        risk_price
    )

    if risk_pips is None:
        return invalid_trade_plan(
            direction
        )

    # Risk gate
    if (
        risk_pips < MIN_RISK_PIPS
        or risk_pips > MAX_RISK_PIPS
    ):
        return {
            "valid": False,
            "direction": direction,
            "entry": round_price(entry),
            "sl": round_price(sl),
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "risk_pips": risk_pips,
            "rr_tp1": None,
            "rr_tp2": None,
            "rr_tp3": None,
            "reason": "Risk outside 35-60 pips",
        }

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
    #
    # At least 2R
    # --------------------------------------------------------

    tp2_pips = max(
        MIN_TP2_PIPS,
        risk_pips * 2
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
    #
    # At least 3R but maintain
    # 180 pip base target.
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

    rr_tp1 = (
        TP1_PIPS / risk_pips
    )

    rr_tp2 = (
        tp2_pips / risk_pips
    )

    rr_tp3 = (
        tp3_pips / risk_pips
    )

    return {
        "valid": True,
        "direction": direction,
        "entry": round_price(entry),
        "sl": round_price(sl),
        "tp1": round_price(tp1),
        "tp2": round_price(tp2),
        "tp3": round_price(tp3),
        "risk_pips": round(risk_pips, 1),
        "rr_tp1": round(rr_tp1, 2),
        "rr_tp2": round(rr_tp2, 2),
        "rr_tp3": round(rr_tp3, 2),
        "tp1_pips": TP1_PIPS,
        "tp2_pips": round(tp2_pips, 1),
        "tp3_pips": round(tp3_pips, 1),
        "reason": (
            "Risk valid and multi-target "
            "scalping plan generated"
        ),
    }


def invalid_trade_plan(direction):
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
        "reason": "Unable to build trade plan",
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
            "description": "No valid trade plan",
        }

    tp1 = plan.get("tp1_pips")
    tp3 = plan.get("tp3_pips")

    if tp1 is None or tp3 is None:
        return {
            "status": "LOW",
            "min_pips": None,
            "max_pips": None,
            "description": "Target data unavailable",
        }

    return {
        "status": "HIGH",
        "min_pips": round(tp1, 1),
        "max_pips": round(tp3, 1),
        "description": (
            f"Potential move "
            f"{round(tp1)}-{round(tp3)} pips"
        ),
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print(
            "Telegram disabled: "
            "TELEGRAM_BOT_TOKEN missing."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "Telegram disabled: "
            "TELEGRAM_CHAT_ID missing."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }

    try:
        response = requests.post(
            url,
            json=payload,
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

    temp_file = STATE_FILE.with_suffix(
        ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            clean_for_json(state),
            file,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(
        temp_file,
        STATE_FILE
    )


# ============================================================
# TELEGRAM ANTI-SPAM
# ============================================================

def signal_key(
    opportunity,
    plan,
):
    if not plan.get("valid"):
        return None

    return "|".join([
        str(opportunity),
        str(plan.get("direction")),
        str(plan.get("entry")),
        str(plan.get("sl")),
        str(plan.get("tp2")),
    ])


def should_send_telegram(
    opportunity,
    score,
    plan,
):
    if score < MIN_SCORE:
        return False

    if not plan.get("valid"):
        return False

    key = signal_key(
        opportunity,
        plan,
    )

    if not key:
        return False

    state = load_state()

    if (
        state.get("last_signal_key")
        == key
    ):
        return False

    return True


def mark_signal_sent(
    opportunity,
    score,
    plan,
):
    key = signal_key(
        opportunity,
        plan,
    )

    state = load_state()

    state["last_signal_key"] = key
    state["last_signal_time"] = now_iso()
    state["last_signal_score"] = score
    state["last_signal_opportunity"] = (
        opportunity
    )

    save_state(state)


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_telegram_message(
    price,
    session,
    h1,
    m15,
    m5,
    opportunity,
    score,
    plan,
    potential,
):
    direction = plan.get(
        "direction",
        "-"
    )

    return (
        "🔥 BOSQUE FOREX AI\n"
        "\n"
        f"XAU/USD SCALPING\n"
        f"Direction: {direction}\n"
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
# DASHBOARD DATA
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
):
    return {
        "engine": {
            "name": "BOSQUE FOREX AI",
            "version": "SCALPING V3",
            "symbol": SYMBOL,
            "timeframes": {
                "bias": "H1",
                "setup": "M15",
                "confirmation": "M5",
            },
            "pip_size": PIP_SIZE,
            "pip_rule": (
                "1 pip = 0.10 XAU/USD "
                "(10 points)"
            ),
            "min_score": MIN_SCORE,
        },

        "timestamp": now_iso(),

        "latest_price": round_price(
            price
        ),

        "session": session,

        "market_mode": (
            "RANGE"
            if h1["bias"] == "RANGE"
            else "TREND"
        ),

        "pd": pd,

        "opportunity": {
            "type": opportunity,
            "score": score,
            "valid": (
                opportunity !=
                "NO VALID SETUP"
                and score >= MIN_SCORE
                and plan.get("valid", False)
            ),
        },

        "plan": plan,

        "potential": potential,

        "h1": {
            **h1,
        },

        "m15": {
            **m15,
            "range": m15_range,
            "liquidity": m15_liquidity,
            "setup": m15_setup,
        },

        "m5": {
            **m5,
        },

        "filters": {
            "score": (
                "PASS"
                if score >= MIN_SCORE
                else "FAIL"
            ),

            "m15_setup": (
                "PASS"
                if m15_setup["valid"]
                else "FAIL"
            ),

            "m5_confirmation": (
                "PASS"
                if m5["confirmed"]
                else "FAIL"
            ),

            "risk": (
                "PASS"
                if plan.get("valid")
                else "FAIL"
            ),

            "rr": (
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
            "h1_bias": h1["bias"],
            "h1_structure": h1["structure"],

            "m15_setup": m15_setup["type"],
            "m15_liquidity": (
                m15_liquidity["type"]
            ),

            "m5_bos_choch": (
                m5["bos_choch"]
            ),

            "m5_candle": (
                m5["candle"]
            ),

            "m5_momentum": (
                m5["momentum"]
            ),
        },
    }


# ============================================================
# SAVE DASHBOARD JSON
# ============================================================

def save_dashboard(data):
    DASHBOARD_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = DASHBOARD_FILE.with_suffix(
        ".tmp"
    )

    cleaned = clean_for_json(data)

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            cleaned,
            file,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(
        temp_file,
        DASHBOARD_FILE
    )

    print(
        f"✅ Dashboard data saved: "
        f"{DASHBOARD_FILE}"
    )


# ============================================================
# MAIN ENGINE
# ============================================================

def run_engine():

    print("=" * 60)
    print("BOSQUE FOREX AI - SCALPING V3")
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
        "Session:",
        session_name()
    )

    # --------------------------------------------------------
    # FETCH M5
    # --------------------------------------------------------

    print(
        "\n📡 Fetching Twelve Data..."
    )

    m5_raw = fetch_m5_data()

    print(
        f"✅ Received {len(m5_raw)} M5 candles"
    )

    # --------------------------------------------------------
    # REMOVE INCOMPLETE CANDLE
    # --------------------------------------------------------

    m5_raw = remove_incomplete_candle(
        m5_raw
    )

    if len(m5_raw) < 50:
        raise RuntimeError(
            "Not enough completed M5 candles."
        )

    # --------------------------------------------------------
    # AGGREGATION
    # --------------------------------------------------------

    h1_candles = aggregate_candles(
        m5_raw,
        60
    )

    m15_candles = aggregate_candles(
        m5_raw,
        15
    )

    global M15_CANDLES_GLOBAL

    M15_CANDLES_GLOBAL = m15_candles

    print(
        f"✅ H1 candles: {len(h1_candles)}"
    )

    print(
        f"✅ M15 candles: {len(m15_candles)}"
    )

    print(
        f"✅ M5 candles: {len(m5_raw)}"
    )

    # --------------------------------------------------------
    # CURRENT PRICE
    # --------------------------------------------------------

    current_price = m5_raw[-1]["close"]

    # --------------------------------------------------------
    # H1
    # --------------------------------------------------------

    h1 = analyze_structure(
        h1_candles
    )

    h1_range = detect_range(
        h1_candles
    )

    if h1_range["is_range"]:
        h1["bias"] = "RANGE"
        h1["structure"] = "RANGE"

    # --------------------------------------------------------
    # M15
    # --------------------------------------------------------

    m15 = analyze_structure(
        m15_candles
    )

    m15_range = detect_range(
        m15_candles
    )

    m15_liquidity = detect_liquidity_sweep(
        m15_candles
    )

    m15_pd = calculate_pd_zone(
        m15_candles
    )

    # --------------------------------------------------------
    # M15 SETUP
    # --------------------------------------------------------

    m15_setup = determine_m15_setup(
        m15_candles,
        h1
    )

    # --------------------------------------------------------
    # EXPECTED DIRECTION
    # --------------------------------------------------------

    direction = setup_direction(
        m15_setup["type"]
    )

    # --------------------------------------------------------
    # M5 CONFIRMATION
    # --------------------------------------------------------

    m5 = m5_confirmation(
        m5_raw,
        direction
    )

    # --------------------------------------------------------
    # PD
    # --------------------------------------------------------

    pd = calculate_pd_zone(
        m15_candles
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = calculate_score(
        h1=h1,
        m15=m15,
        m5=m5,
        pd=pd,
        setup=m15_setup,
        liquidity=m15_liquidity,
    )

    # --------------------------------------------------------
    # HARD GATES
    # --------------------------------------------------------

    hard_gate_pass = (
        m15_setup["valid"]
        and m5["confirmed"]
        and score >= MIN_SCORE
    )

    # --------------------------------------------------------
    # TRADE PLAN
    # --------------------------------------------------------

    if hard_gate_pass:
        plan = create_trade_plan(
            m5_raw,
            direction,
            current_price,
        )

    else:
        plan = invalid_trade_plan(
            direction
        )

        plan["reason"] = (
            "Hard gates not passed"
        )

    # --------------------------------------------------------
    # RR GATE
    # --------------------------------------------------------

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
                "RR below minimum 1:2"
            )

    # --------------------------------------------------------
    # FINAL OPPORTUNITY
    # --------------------------------------------------------

    if (
        hard_gate_pass
        and plan.get("valid")
        and direction in ("BUY", "SELL")
    ):
        opportunity = m15_setup["type"]

    else:
        opportunity = "NO VALID SETUP"

    # --------------------------------------------------------
    # POTENTIAL
    # --------------------------------------------------------

    potential = calculate_potential(
        plan
    )

    # --------------------------------------------------------
    # DASHBOARD
    # --------------------------------------------------------

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
    )

    save_dashboard(
        dashboard
    )

    # --------------------------------------------------------
    # PRINT SUMMARY
    # --------------------------------------------------------

    print("\n" + "=" * 60)
    print("BOSQUE ENGINE RESULT")
    print("=" * 60)

    print(
        f"Price       : {current_price:.2f}"
    )

    print(
        f"Session     : {session_name()}"
    )

    print(
        f"H1 Bias     : {h1['bias']}"
    )

    print(
        f"H1 Structure: {h1['structure']}"
    )

    print(
        f"M15 Setup   : {m15_setup['type']}"
    )

    print(
        f"M15 Liquidity: "
        f"{m15_liquidity['type']}"
    )

    print(
        f"M5 Confirm  : "
        f"{m5['confirmed']}"
    )

    print(
        f"M5 Direction: "
        f"{m5['direction']}"
    )

    print(
        f"M5 Candle   : "
        f"{m5['candle']}"
    )

    print(
        f"M5 Momentum : "
        f"{m5['momentum']}"
    )

    print(
        f"Score       : {score}/100"
    )

    print(
        f"Opportunity : {opportunity}"
    )

    print(
        f"Plan Valid  : {plan.get('valid')}"
    )

    print(
        f"Entry       : {plan.get('entry')}"
    )

    print(
        f"SL          : {plan.get('sl')}"
    )

    print(
        f"TP1         : {plan.get('tp1')}"
    )

    print(
        f"TP2         : {plan.get('tp2')}"
    )

    print(
        f"TP3         : {plan.get('tp3')}"
    )

    print(
        f"Risk        : "
        f"{plan.get('risk_pips')} pips"
    )

    print(
        f"RR TP2      : "
        f"1:{plan.get('rr_tp2')}"
    )

    print(
        f"Potential   : "
        f"{potential.get('min_pips')}-"
        f"{potential.get('max_pips')} pips"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    if should_send_telegram(
        opportunity,
        score,
        plan,
    ):

        message = build_telegram_message(
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

        if send_telegram(message):
            mark_signal_sent(
                opportunity,
                score,
                plan,
            )

            print(
                "📲 Telegram alert sent."
            )

    else:
        print(
            "ℹ️ Telegram alert not sent."
        )

    print("=" * 60)
    print("✅ ENGINE COMPLETE")
    print("=" * 60)


# ============================================================
# SAFE ERROR OUTPUT
# ============================================================

def save_error_dashboard(error):
    """
    If engine fails, still create dashboard_data.json
    so the frontend does not receive a 404.
    """

    error_data = {
        "engine": {
            "name": "BOSQUE FOREX AI",
            "version": "SCALPING V3",
            "symbol": SYMBOL,
        },

        "timestamp": now_iso(),

        "latest_price": None,

        "session": session_name(),

        "market_mode": "UNKNOWN",

        "pd": {
            "zone": "UNKNOWN",
            "equilibrium": None,
            "high": None,
            "low": None,
        },

        "opportunity": {
            "type": "NO VALID SETUP",
            "score": 0,
            "valid": False,
        },

        "plan": invalid_trade_plan(
            None
        ),

        "potential": {
            "status": "LOW",
            "min_pips": None,
            "max_pips": None,
            "description": "Engine error",
        },

        "h1": {
            "bias": "UNKNOWN",
            "structure": "ENGINE ERROR",
        },

        "m15": {
            "bias": "UNKNOWN",
            "structure": "ENGINE ERROR",
            "range": {},
            "liquidity": {},
            "setup": {},
        },

        "m5": {
            "confirmed": False,
            "direction": "UNKNOWN",
            "bos_choch": "ENGINE ERROR",
            "candle": "UNKNOWN",
            "momentum": "UNKNOWN",
        },

        "filters": {
            "score": "FAIL",
            "m15_setup": "FAIL",
            "m5_confirmation": "FAIL",
            "risk": "FAIL",
            "rr": "FAIL",
        },

        "confirmations": {},

        "error": str(error),
    }

    try:
        save_dashboard(
            error_data
        )

    except Exception as save_error:
        print(
            "❌ Failed to save error dashboard:",
            save_error
        )


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

        # Important:
        # GitHub Actions must fail when
        # Twelve Data / engine has an error.
        raise