import os
import json
import requests
from datetime import datetime, timezone, timedelta


# ============================================================
# BOSQUE FOREX AI
# SCALPING ENGINE V3
#
# XAU/USD
#
# TIMEFRAME:
# H1  = MARKET CONTEXT
# M15 = SETUP / LOCATION
# M5  = ENTRY CONFIRMATION
#
# PIP CONVENTION:
#
# 1 point = 0.01 price
# 10 points = 1 pip
# 100 points = 10 pips
# 1 pip = 0.10 XAUUSD price movement
#
# SCALPING FILTER:
#
# Score       : 70+
# Risk        : 35 - 60 pips
# TP1         : ~60 pips
# TP2         : 2R minimum
# TP3         : ~3R when structure allows
#
# DATA FLOW:
#
# Twelve Data
#     ↓
# M5 OHLC
#     ↓
# H1 / M15 aggregation
#     ↓
# H1 → M15 → M5
#     ↓
# Opportunity Engine
#     ↓
# Trade Plan
#     ↓
# dashboard_data.json
#     ↓
# Telegram
# ============================================================


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAU/USD"

TWELVEDATA_URL = (
    "https://api.twelvedata.com/time_series"
)

M5_INTERVAL = "5min"

# 500 M5 candles ≈ 41 hours
OUTPUT_SIZE = 500

MIN_SCORE = 70

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MIN_RISK_PIPS = 35
MAX_RISK_PIPS = 60

# ------------------------------------------------------------
# TARGET
# ------------------------------------------------------------

TP1_PIPS = 60

MIN_TP2_PIPS = 120

TP3_PIPS = 180

MIN_RR = 2.0

# ------------------------------------------------------------
# XAUUSD PIP
# ------------------------------------------------------------

PIP_SIZE = 0.10

# ------------------------------------------------------------
# ATR
# ------------------------------------------------------------

ATR_PERIOD = 14

# ------------------------------------------------------------
# REQUEST
# ------------------------------------------------------------

REQUEST_TIMEOUT = 20

# ------------------------------------------------------------
# MALAYSIA TIME
# ------------------------------------------------------------

MALAYSIA_TZ = timezone(
    timedelta(hours=8)
)

# ------------------------------------------------------------
# FILES
# ------------------------------------------------------------

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

STATE_FILE = os.path.join(
    BASE_DIR,
    "state.json"
)

DASHBOARD_FILE = os.path.join(
    BASE_DIR,
    "dashboard_data.json"
)

# ------------------------------------------------------------
# ENVIRONMENT
# ------------------------------------------------------------

API_KEY = os.getenv(
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
# BASIC HELPERS
# ============================================================

def price_to_pips(distance):

    return distance / PIP_SIZE


def pips_to_price(pips):

    return pips * PIP_SIZE


def round_price(price):

    if price is None:
        return None

    return round(
        float(price),
        2
    )


def malaysia_now():

    return datetime.now(
        timezone.utc
    ).astimezone(
        MALAYSIA_TZ
    )


def safe_float(value):

    try:

        return float(value)

    except Exception:

        return None


def clamp(value, minimum, maximum):

    return max(
        minimum,
        min(
            maximum,
            value
        )
    )


# ============================================================
# ENVIRONMENT VALIDATION
# ============================================================

def validate_environment():

    missing = []

    if not API_KEY:
        missing.append(
            "TWELVEDATA_API_KEY"
        )

    if not TELEGRAM_BOT_TOKEN:
        missing.append(
            "TELEGRAM_BOT_TOKEN"
        )

    if not TELEGRAM_CHAT_ID:
        missing.append(
            "TELEGRAM_CHAT_ID"
        )

    if missing:

        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


# ============================================================
# TWELVE DATA
# ============================================================

def get_m5_data():

    print()
    print("📡 Fetching Twelve Data...")
    print(
        f"   Symbol    : {SYMBOL}"
    )
    print(
        f"   Interval  : {M5_INTERVAL}"
    )
    print(
        f"   Output    : {OUTPUT_SIZE}"
    )

    params = {
        "symbol": SYMBOL,
        "interval": M5_INTERVAL,
        "outputsize": OUTPUT_SIZE,
        "apikey": API_KEY
    }

    headers = {
        "Authorization": f"apikey {API_KEY}"
    }

    response = requests.get(
        TWELVEDATA_URL,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    if response.status_code != 200:

        raise RuntimeError(
            "Twelve Data HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    try:

        payload = response.json()

    except Exception as error:

        raise RuntimeError(
            "Invalid Twelve Data JSON: "
            + str(error)
        )

    if payload.get("status") == "error":

        raise RuntimeError(
            "Twelve Data API error: "
            + str(
                payload.get(
                    "message",
                    payload
                )
            )
        )

    values = payload.get(
        "values"
    )

    if not values:

        raise RuntimeError(
            "Twelve Data returned no OHLC data."
        )

    candles = []

    for item in values:

        timestamp = item.get(
            "datetime"
        )

        open_price = safe_float(
            item.get("open")
        )

        high_price = safe_float(
            item.get("high")
        )

        low_price = safe_float(
            item.get("low")
        )

        close_price = safe_float(
            item.get("close")
        )

        if not timestamp:
            continue

        if None in (
            open_price,
            high_price,
            low_price,
            close_price
        ):
            continue

        candles.append(
            {
                "time": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price
            }
        )

    if len(candles) < 100:

        raise RuntimeError(
            "Insufficient M5 candles: "
            f"{len(candles)}"
        )

    candles.sort(
        key=lambda candle: candle["time"]
    )

    candles = remove_incomplete_candle(
        candles
    )

    print(
        f"✅ M5 candles loaded: {len(candles)}"
    )

    return candles


# ============================================================
# TIME PARSER
# ============================================================

def parse_time(value):

    text = str(value).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M"
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                text,
                fmt
            ).replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            pass

    try:

        return datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:

        return None


# ============================================================
# REMOVE INCOMPLETE M5
# ============================================================

def remove_incomplete_candle(candles):

    if not candles:
        return candles

    latest = candles[-1]

    candle_time = parse_time(
        latest["time"]
    )

    if candle_time is None:
        return candles

    now = datetime.now(
        timezone.utc
    )

    age = (
        now - candle_time
    ).total_seconds()

    # M5 candle should be at least 5 minutes old.
    if age < 300:

        print(
            "⚠️ Removing incomplete M5 candle."
        )

        return candles[:-1]

    return candles


# ============================================================
# AGGREGATE CANDLES
# ============================================================

def aggregate_candles(
    candles,
    minutes
):

    if minutes not in (
        15,
        60
    ):

        raise ValueError(
            "Supported aggregation: "
            "15 or 60 minutes."
        )

    buckets = {}

    for candle in candles:

        dt = parse_time(
            candle["time"]
        )

        if dt is None:
            continue

        epoch = int(
            dt.timestamp()
        )

        bucket_seconds = (
            minutes * 60
        )

        bucket_epoch = (
            epoch // bucket_seconds
        ) * bucket_seconds

        bucket_dt = datetime.fromtimestamp(
            bucket_epoch,
            tz=timezone.utc
        )

        key = bucket_dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if key not in buckets:

            buckets[key] = {
                "time": key,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "count": 0
            }

        bucket = buckets[key]

        bucket["high"] = max(
            bucket["high"],
            candle["high"]
        )

        bucket["low"] = min(
            bucket["low"],
            candle["low"]
        )

        bucket["close"] = candle["close"]

        bucket["count"] += 1

    result = []

    expected = minutes // 5

    for candle in buckets.values():

        # Only keep complete higher TF candles.
        if candle["count"] < expected:
            continue

        result.append(
            {
                "time": candle["time"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"]
            }
        )

    result.sort(
        key=lambda candle: candle["time"]
    )

    return result


# ============================================================
# SWINGS
# ============================================================

def detect_swings(
    candles,
    left=2,
    right=2
):

    highs = []
    lows = []

    if len(candles) < (
        left + right + 1
    ):
        return highs, lows

    for i in range(
        left,
        len(candles) - right
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]

        is_high = True
        is_low = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] >= high:
                is_high = False

            if candles[j]["low"] <= low:
                is_low = False

        if is_high:

            highs.append(
                {
                    "index": i,
                    "time": candles[i]["time"],
                    "price": high
                }
            )

        if is_low:

            lows.append(
                {
                    "index": i,
                    "time": candles[i]["time"],
                    "price": low
                }
            )

    return highs, lows


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=ATR_PERIOD
):

    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        tr = max(
            high - low,
            abs(
                high - previous_close
            ),
            abs(
                low - previous_close
            )
        )

        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    recent = true_ranges[-period:]

    return sum(recent) / len(recent)


# ============================================================
# STRUCTURE ANALYSIS
# ============================================================

def analyze_structure(candles):

    if len(candles) < 20:

        return {
            "trend": "RANGE",
            "bias": "NEUTRAL",
            "structure": "RANGE",
            "bos": "NONE",
            "choch": "NONE",
            "latest_price": None,
            "swing_high": None,
            "swing_low": None,
            "range_high": None,
            "range_low": None,
            "atr": None
        }

    highs, lows = detect_swings(
        candles
    )

    latest = candles[-1]

    latest_price = latest["close"]

    last_high = (
        highs[-1]["price"]
        if highs
        else None
    )

    last_low = (
        lows[-1]["price"]
        if lows
        else None
    )

    range_candles = candles[-20:]

    range_high = max(
        candle["high"]
        for candle in range_candles
    )

    range_low = min(
        candle["low"]
        for candle in range_candles
    )

    structure = "RANGE"
    trend = "RANGE"
    bias = "NEUTRAL"
    bos = "NONE"
    choch = "NONE"

    if (
        last_high is not None
        and latest_price > last_high
    ):

        structure = "BULLISH BOS"
        trend = "BULLISH"
        bias = "BULLISH"
        bos = "BULLISH BOS"

    elif (
        last_low is not None
        and latest_price < last_low
    ):

        structure = "BEARISH BOS"
        trend = "BEARISH"
        bias = "BEARISH"
        bos = "BEARISH BOS"

    else:

        # Use swing relationships when there is
        # no immediate BOS.

        if len(highs) >= 2:

            previous_high = highs[-2]["price"]

            if last_high > previous_high:

                trend = "BULLISH"
                bias = "BULLISH"

        if len(lows) >= 2:

            previous_low = lows[-2]["price"]

            if last_low < previous_low:

                trend = "BEARISH"
                bias = "BEARISH"

        if trend == "RANGE":

            bias = "NEUTRAL"

    return {
        "trend": trend,
        "bias": bias,
        "structure": structure,
        "bos": bos,
        "choch": choch,
        "latest_price": latest_price,
        "swing_high": last_high,
        "swing_low": last_low,
        "range_high": range_high,
        "range_low": range_low,
        "atr": calculate_atr(candles)
    }


# ============================================================
# PREMIUM / DISCOUNT
# ============================================================

def calculate_pd_zone(
    candles
):

    if not candles:

        return {
            "zone": "UNKNOWN",
            "equilibrium": None,
            "range_high": None,
            "range_low": None
        }

    recent = candles[-50:]

    high = max(
        candle["high"]
        for candle in recent
    )

    low = min(
        candle["low"]
        for candle in recent
    )

    equilibrium = (
        high + low
    ) / 2

    price = recent[-1]["close"]

    if price < equilibrium:

        zone = "DISCOUNT"

    elif price > equilibrium:

        zone = "PREMIUM"

    else:

        zone = "EQUILIBRIUM"

    return {
        "zone": zone,
        "equilibrium": equilibrium,
        "range_high": high,
        "range_low": low
    }


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

def detect_liquidity_sweep(
    candles
):

    if len(candles) < 10:

        return {
            "direction": "NONE",
            "type": "NONE",
            "level": None
        }

    current = candles[-1]

    previous = candles[-6:-1]

    previous_high = max(
        candle["high"]
        for candle in previous
    )

    previous_low = min(
        candle["low"]
        for candle in previous
    )

    # Bearish sweep:
    # price takes previous high but closes back below.

    if (
        current["high"] > previous_high
        and current["close"] < previous_high
    ):

        return {
            "direction": "SELL",
            "type": "BEARISH LIQUIDITY SWEEP",
            "level": previous_high
        }

    # Bullish sweep:
    # price takes previous low but closes back above.

    if (
        current["low"] < previous_low
        and current["close"] > previous_low
    ):

        return {
            "direction": "BUY",
            "type": "BULLISH LIQUIDITY SWEEP",
            "level": previous_low
        }

    return {
        "direction": "NONE",
        "type": "NONE",
        "level": None
    }


# ============================================================
# RANGE LOCATION
# ============================================================

def detect_range_location(
    candles
):

    if len(candles) < 20:

        return "MID"

    recent = candles[-20:]

    high = max(
        candle["high"]
        for candle in recent
    )

    low = min(
        candle["low"]
        for candle in recent
    )

    price = recent[-1]["close"]

    total_range = high - low

    if total_range <= 0:
        return "MID"

    position = (
        price - low
    ) / total_range

    if position <= 0.20:

        return "LOW EXTREME"

    if position >= 0.80:

        return "HIGH EXTREME"

    return "MID"


# ============================================================
# CANDLE CONFIRMATION
# ============================================================

def candle_confirmation(
    candles
):

    if len(candles) < 3:

        return {
            "direction": "NONE",
            "type": "NONE"
        }

    current = candles[-1]
    previous = candles[-2]

    body = abs(
        current["close"]
        - current["open"]
    )

    candle_range = (
        current["high"]
        - current["low"]
    )

    if candle_range <= 0:

        return {
            "direction": "NONE",
            "type": "NONE"
        }

    body_ratio = (
        body / candle_range
    )

    # Bullish strong body

    if (
        current["close"] > current["open"]
        and body_ratio >= 0.55
        and current["close"] > previous["high"]
    ):

        return {
            "direction": "BUY",
            "type": "BULLISH CONFIRMATION"
        }

    # Bearish strong body

    if (
        current["close"] < current["open"]
        and body_ratio >= 0.55
        and current["close"] < previous["low"]
    ):

        return {
            "direction": "SELL",
            "type": "BEARISH CONFIRMATION"
        }

    # Bullish engulfing

    if (
        previous["close"] < previous["open"]
        and current["close"] > current["open"]
        and current["open"] <= previous["close"]
        and current["close"] >= previous["open"]
    ):

        return {
            "direction": "BUY",
            "type": "BULLISH ENGULFING"
        }

    # Bearish engulfing

    if (
        previous["close"] > previous["open"]
        and current["close"] < current["open"]
        and current["open"] >= previous["close"]
        and current["close"] <= previous["open"]
    ):

        return {
            "direction": "SELL",
            "type": "BEARISH ENGULFING"
        }

    return {
        "direction": "NONE",
        "type": "NONE"
    }


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(
    candles
):

    if len(candles) < 10:

        return {
            "status": "NEUTRAL",
            "ratio": 0
        }

    recent_ranges = [
        candle["high"] - candle["low"]
        for candle in candles[-5:]
    ]

    previous_ranges = [
        candle["high"] - candle["low"]
        for candle in candles[-10:-5]
    ]

    recent_avg = (
        sum(recent_ranges)
        / len(recent_ranges)
    )

    previous_avg = (
        sum(previous_ranges)
        / len(previous_ranges)
    )

    if previous_avg <= 0:

        return {
            "status": "NEUTRAL",
            "ratio": 0
        }

    ratio = (
        recent_avg
        / previous_avg
    )

    if ratio >= 1.30:

        status = "STRONG"

    elif ratio >= 1.05:

        status = "HEALTHY"

    else:

        status = "NEUTRAL"

    return {
        "status": status,
        "ratio": round(
            ratio,
            2
        )
    }


# ============================================================
# SESSION
# ============================================================

def get_session():

    now = malaysia_now()

    hour = now.hour

    if 7 <= hour < 15:

        return "ASIAN"

    if 15 <= hour < 20:

        return "LONDON"

    if 20 <= hour < 23:

        return "NEW YORK"

    if hour == 23:

        return "NEW YORK"

    if 0 <= hour < 1:

        return "NEW YORK"

    return "OFF SESSION"


# ============================================================
# M15 SETUP ENGINE
# ============================================================

def determine_m15_setup(
    h1,
    m15,
    m15_sweep,
    m15_confirmation
):

    h1_trend = h1["trend"]
    m15_trend = m15["trend"]

    m15_structure = m15["structure"]

    range_location = detect_range_location(
        M15_CANDLES_GLOBAL
    )

    setup = "NONE"

    direction = "WAIT"

    confirmations = []

    valid = False

    # ========================================================
    # TREND PULLBACK
    # ========================================================

    if (
        h1_trend == "BULLISH"
        and (
            m15_trend == "BULLISH"
            or m15_structure == "BULLISH BOS"
        )
    ):

        if (
            m15_sweep["direction"] == "BUY"
            or m15_confirmation["direction"] == "BUY"
        ):

            setup = "BUY PULLBACK"
            direction = "BUY"
            valid = True

            confirmations.append(
                "H1 bullish context"
            )

            confirmations.append(
                "M15 bullish pullback setup"
            )

    elif (
        h1_trend == "BEARISH"
        and (
            m15_trend == "BEARISH"
            or m15_structure == "BEARISH BOS"
        )
    ):

        if (
            m15_sweep["direction"] == "SELL"
            or m15_confirmation["direction"] == "SELL"
        ):

            setup = "SELL PULLBACK"
            direction = "SELL"
            valid = True

            confirmations.append(
                "H1 bearish context"
            )

            confirmations.append(
                "M15 bearish pullback setup"
            )

    # ========================================================
    # LIQUIDITY SWEEP
    # ========================================================

    if m15_sweep["direction"] in (
        "BUY",
        "SELL"
    ):

        sweep_direction = (
            m15_sweep["direction"]
        )

        if (
            h1_trend == "RANGE"
            or h1_trend == sweep_direction_to_trend(
                sweep_direction
            )
            or range_location in (
                "LOW EXTREME",
                "HIGH EXTREME"
            )
        ):

            setup = (
                f"{sweep_direction} "
                "LIQUIDITY SWEEP"
            )

            direction = sweep_direction

            valid = True

            confirmations.append(
                f"M15 {m15_sweep['type']}"
            )

    # ========================================================
    # RANGE REVERSAL
    # ========================================================

    if h1_trend == "RANGE":

        if (
            range_location == "LOW EXTREME"
            and m15_sweep["direction"] == "BUY"
        ):

            setup = "BUY RANGE REVERSAL"
            direction = "BUY"
            valid = True

            confirmations.append(
                "H1 range"
            )

            confirmations.append(
                "M15 low range extreme"
            )

            confirmations.append(
                "M15 liquidity sweep"
            )

        elif (
            range_location == "HIGH EXTREME"
            and m15_sweep["direction"] == "SELL"
        ):

            setup = "SELL RANGE REVERSAL"
            direction = "SELL"
            valid = True

            confirmations.append(
                "H1 range"
            )

            confirmations.append(
                "M15 high range extreme"
            )

            confirmations.append(
                "M15 liquidity sweep"
            )

    # ========================================================
    # BREAKOUT RETEST
    # ========================================================

    if m15_structure == "BULLISH BOS":

        if h1_trend in (
            "BULLISH",
            "RANGE"
        ):

            setup = "BUY BREAKOUT RETEST"
            direction = "BUY"

            valid = True

            confirmations.append(
                "M15 bullish BOS"
            )

    elif m15_structure == "BEARISH BOS":

        if h1_trend in (
            "BEARISH",
            "RANGE"
        ):

            setup = "SELL BREAKOUT RETEST"
            direction = "SELL"

            valid = True

            confirmations.append(
                "M15 bearish BOS"
            )

    return {
        "setup": setup,
        "direction": direction,
        "valid": valid,
        "range_location": range_location,
        "confirmations": confirmations
    }


def sweep_direction_to_trend(
    direction
):

    if direction == "BUY":
        return "BULLISH"

    if direction == "SELL":
        return "BEARISH"

    return "RANGE"


# ============================================================
# M5 CONFIRMATION
# ============================================================

def determine_m5_confirmation(
    direction,
    m5,
    m5_sweep,
    m5_candle
):

    if direction not in (
        "BUY",
        "SELL"
    ):

        return {
            "valid": False,
            "direction": "NONE",
            "structure": m5["structure"],
            "candle": m5_candle["type"],
            "momentum": "NEUTRAL",
            "confirmations": []
        }

    confirmations = []

    structure_valid = (
        (
            direction == "BUY"
            and m5["structure"]
            == "BULLISH BOS"
        )
        or
        (
            direction == "SELL"
            and m5["structure"]
            == "BEARISH BOS"
        )
    )

    candle_valid = (
        m5_candle["direction"]
        == direction
    )

    sweep_valid = (
        m5_sweep["direction"]
        == direction
    )

    if structure_valid:

        confirmations.append(
            f"M5 {direction} BOS"
        )

    if candle_valid:

        confirmations.append(
            m5_candle["type"]
        )

    if sweep_valid:

        confirmations.append(
            f"M5 {direction} liquidity behavior"
        )

    # Strong confirmation:
    # M5 BOS OR sweep + candle.
    valid = (
        structure_valid
        and candle_valid
    ) or (
        sweep_valid
        and candle_valid
    )

    return {
        "valid": valid,
        "direction": direction
            if valid
            else "NONE",
        "structure": m5["structure"],
        "candle": m5_candle["type"],
        "momentum": "NEUTRAL",
        "confirmations": confirmations
    }


# ============================================================
# SCORING V3
#
# H1 Context       = 20
# M15 Setup        = 30
# M5 Entry         = 35
# Trade Location   = 15
#
# TOTAL            = 100
# ============================================================

def calculate_score(
    direction,
    h1,
    m15,
    m5,
    pd,
    m15_setup,
    m15_sweep,
    m5_confirmation,
    momentum,
    session
):

    score = 0

    confirmations = []

    # ========================================================
    # H1 CONTEXT - 20
    # ========================================================

    if (
        direction == "BUY"
        and h1["trend"] == "BULLISH"
    ):

        score += 15

        confirmations.append(
            "H1 bullish structure"
        )

    elif (
        direction == "SELL"
        and h1["trend"] == "BEARISH"
    ):

        score += 15

        confirmations.append(
            "H1 bearish structure"
        )

    elif h1["trend"] == "RANGE":

        score += 8

        confirmations.append(
            "H1 range condition"
        )

    if (
        direction == "BUY"
        and h1["structure"]
        == "BULLISH BOS"
    ):

        score += 5

        confirmations.append(
            "H1 bullish BOS"
        )

    elif (
        direction == "SELL"
        and h1["structure"]
        == "BEARISH BOS"
    ):

        score += 5

        confirmations.append(
            "H1 bearish BOS"
        )

    # ========================================================
    # M15 SETUP - 30
    # ========================================================

    if m15_setup["valid"]:

        score += 10

        confirmations.extend(
            m15_setup["confirmations"]
        )

    if (
        m15_sweep["direction"]
        == direction
    ):

        score += 10

        confirmations.append(
            f"M15 {direction} liquidity sweep"
        )

    if (
        direction == "BUY"
        and m15["structure"]
        == "BULLISH BOS"
    ):

        score += 10

        confirmations.append(
            "M15 bullish structure"
        )

    elif (
        direction == "SELL"
        and m15["structure"]
        == "BEARISH BOS"
    ):

        score += 10

        confirmations.append(
            "M15 bearish structure"
        )

    # ========================================================
    # M5 ENTRY - 35
    # ========================================================

    if m5_confirmation["valid"]:

        score += 15

        confirmations.extend(
            m5_confirmation["confirmations"]
        )

    if (
        m5_confirmation["candle"]
        != "NONE"
        and m5_confirmation["direction"]
        == direction
    ):

        score += 10

        confirmations.append(
            f"{direction} M5 candle confirmation"
        )

    if momentum["status"] == "STRONG":

        score += 10

        confirmations.append(
            "Strong M5 momentum"
        )

    elif momentum["status"] == "HEALTHY":

        score += 5

        confirmations.append(
            "Healthy M5 momentum"
        )

    # ========================================================
    # TRADE LOCATION - 15
    # ========================================================

    if (
        direction == "BUY"
        and pd["zone"] == "DISCOUNT"
    ):

        score += 5

        confirmations.append(
            "BUY located in discount"
        )

    elif (
        direction == "SELL"
        and pd["zone"] == "PREMIUM"
    ):

        score += 5

        confirmations.append(
            "SELL located in premium"
        )

    if session in (
        "LONDON",
        "NEW YORK"
    ):

        score += 5

        confirmations.append(
            f"Active {session} session"
        )

    elif session == "ASIAN":

        score += 3

        confirmations.append(
            "Asian session"
        )

    # movement condition

    if momentum["status"] in (
        "STRONG",
        "HEALTHY"
    ):

        score += 5

        confirmations.append(
            "Movement condition acceptable"
        )

    score = int(
        clamp(
            score,
            0,
            100
        )
    )

    # Remove duplicate confirmations.

    unique_confirmations = []

    for item in confirmations:

        if item not in unique_confirmations:

            unique_confirmations.append(
                item
            )

    return {
        "score": score,
        "confirmations": unique_confirmations
    }


# ============================================================
# TARGET / TRADE PLAN
# ============================================================

def create_trade_plan(
    direction,
    m5_candles,
    m15_candles,
    m15_analysis
):

    if direction not in (
        "BUY",
        "SELL"
    ):

        return {
            "valid": False,
            "entry": None,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "risk_pips": None,
            "rr_min": None,
            "rr_max": None,
            "room_to_target": False
        }

    entry = m5_candles[-1]["close"]

    m5_atr = calculate_atr(
        m5_candles
    )

    m15_atr = calculate_atr(
        m15_candles
    )

    if m5_atr is None:
        m5_atr = 1.0

    if m15_atr is None:
        m15_atr = m5_atr * 2

    m5_highs, m5_lows = detect_swings(
        m5_candles
    )

    m15_highs, m15_lows = detect_swings(
        m15_candles
    )

    # ========================================================
    # SL
    # ========================================================

    if direction == "BUY":

        swing_low = (
            m5_lows[-1]["price"]
            if m5_lows
            else entry - m5_atr
        )

        structural_sl = (
            swing_low
            - max(
                m5_atr * 0.20,
                pips_to_price(5)
            )
        )

        atr_sl = (
            entry
            - m5_atr * 1.20
        )

        # Choose the tighter reasonable SL
        # while remaining below structure.

        sl = max(
            structural_sl,
            atr_sl
        )

        risk_price = (
            entry - sl
        )

    else:

        swing_high = (
            m5_highs[-1]["price"]
            if m5_highs
            else entry + m5_atr
        )

        structural_sl = (
            swing_high
            + max(
                m5_atr * 0.20,
                pips_to_price(5)
            )
        )

        atr_sl = (
            entry
            + m5_atr * 1.20
        )

        sl = min(
            structural_sl,
            atr_sl
        )

        risk_price = (
            sl - entry
        )

    risk_pips = price_to_pips(
        abs(risk_price)
    )

    # ========================================================
    # Risk correction
    #
    # Reject if too small or too large.
    # ========================================================

    if (
        risk_pips < MIN_RISK_PIPS
        or risk_pips > MAX_RISK_PIPS
    ):

        return {
            "valid": False,
            "entry": round_price(entry),
            "sl": round_price(sl),
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "risk_pips": round(
                risk_pips,
                1
            ),
            "rr_min": None,
            "rr_max": None,
            "room_to_target": False
        }

    # ========================================================
    # TP1
    #
    # User requested around 60 pips.
    # ========================================================

    tp1_distance = pips_to_price(
        TP1_PIPS
    )

    if direction == "BUY":

        tp1 = entry + tp1_distance

    else:

        tp1 = entry - tp1_distance

    # ========================================================
    # TP2
    #
    # At least 2R.
    # Also at least 120 pips.
    # ========================================================

    tp2_distance = max(
        pips_to_price(
            MIN_TP2_PIPS
        ),
        abs(risk_price) * MIN_RR
    )

    if direction == "BUY":

        tp2 = entry + tp2_distance

    else:

        tp2 = entry - tp2_distance

    # ========================================================
    # TP3
    #
    # Around 180 pips / 3R.
    # ========================================================

    tp3_distance = max(
        pips_to_price(
            TP3_PIPS
        ),
        abs(risk_price) * 3.0
    )

    if direction == "BUY":

        tp3 = entry + tp3_distance

    else:

        tp3 = entry - tp3_distance

    # ========================================================
    # STRUCTURE TARGET
    #
    # Do not force TP beyond major opposing M15 structure
    # when there is clearly no room.
    # ========================================================

    room_to_target = True

    opposing_level = None

    if direction == "BUY":

        future_highs = [
            swing["price"]
            for swing in m15_highs
            if swing["price"] > entry
        ]

        if future_highs:

            opposing_level = min(
                future_highs
            )

    else:

        future_lows = [
            swing["price"]
            for swing in m15_lows
            if swing["price"] < entry
        ]

        if future_lows:

            opposing_level = max(
                future_lows
            )

    # ========================================================
    # If structure is too close to TP2, reject.
    # ========================================================

    if opposing_level is not None:

        if direction == "BUY":

            room_to_tp2 = (
                opposing_level - entry
            )

        else:

            room_to_tp2 = (
                entry - opposing_level
            )

        if (
            room_to_tp2
            < pips_to_price(
                MIN_TP2_PIPS
            )
        ):

            room_to_target = False

    # ========================================================
    # If TP3 hits opposing structure first,
    # move TP3 before the structure.
    # ========================================================

    if opposing_level is not None:

        if direction == "BUY":

            if opposing_level < tp3:

                tp3 = (
                    opposing_level
                    - pips_to_price(10)
                )

        else:

            if opposing_level > tp3:

                tp3 = (
                    opposing_level
                    + pips_to_price(10)
                )

    # ========================================================
    # RR
    # ========================================================

    rr_tp1 = (
        abs(tp1 - entry)
        / abs(risk_price)
    )

    rr_tp2 = (
        abs(tp2 - entry)
        / abs(risk_price)
    )

    rr_tp3 = (
        abs(tp3 - entry)
        / abs(risk_price)
    )

    return {
        "valid": (
            room_to_target
            and rr_tp2 >= MIN_RR
        ),
        "entry": round_price(entry),
        "sl": round_price(sl),
        "tp1": round_price(tp1),
        "tp2": round_price(tp2),
        "tp3": round_price(tp3),
        "risk_pips": round(
            risk_pips,
            1
        ),
        "rr_min": round(
            rr_tp1,
            2
        ),
        "rr_max": round(
            rr_tp2,
            2
        ),
        "rr_tp3": round(
            rr_tp3,
            2
        ),
        "room_to_target": room_to_target
    }


# ============================================================
# FINAL OPPORTUNITY
# ============================================================

def build_opportunity(
    direction,
    score_data,
    m15_setup,
    m5_confirmation,
    trade_plan
):

    score = score_data["score"]

    setup_valid = (
        m15_setup["valid"]
    )

    confirmation_valid = (
        m5_confirmation["valid"]
    )

    risk_valid = (
        trade_plan["risk_pips"]
        is not None
        and
        MIN_RISK_PIPS
        <= trade_plan["risk_pips"]
        <= MAX_RISK_PIPS
    )

    rr_valid = (
        trade_plan["rr_max"]
        is not None
        and
        trade_plan["rr_max"]
        >= MIN_RR
    )

    room_valid = (
        trade_plan["room_to_target"]
        is True
    )

    score_valid = (
        score >= MIN_SCORE
    )

    all_valid = (
        direction in (
            "BUY",
            "SELL"
        )
        and score_valid
        and setup_valid
        and confirmation_valid
        and risk_valid
        and rr_valid
        and room_valid
        and trade_plan["valid"]
    )

    if not all_valid:

        final_direction = "WAIT"

    else:

        final_direction = direction

    return {
        "direction": final_direction,
        "raw_direction": direction,
        "score": score,
        "type": (
            m15_setup["setup"]
            if all_valid
            else "NO VALID SETUP"
        ),
        "m15_setup_valid": setup_valid,
        "m5_confirmation_valid": confirmation_valid,
        "risk_valid": risk_valid,
        "rr_valid": rr_valid,
        "room_to_target": room_valid,
        "valid": all_valid,
        "confirmations": score_data[
            "confirmations"
        ]
    }


# ============================================================
# PRICE POTENTIAL
# ============================================================

def calculate_potential(
    direction,
    plan
):

    if direction not in (
        "BUY",
        "SELL"
    ):

        return {
            "min_pips": 0,
            "max_pips": 0,
            "confidence": "LOW"
        }

    entry = plan["entry"]

    tp1 = plan["tp1"]

    tp3 = plan["tp3"]

    if (
        entry is None
        or tp1 is None
    ):

        return {
            "min_pips": 0,
            "max_pips": 0,
            "confidence": "LOW"
        }

    min_pips = round(
        price_to_pips(
            abs(
                tp1 - entry
            )
        )
    )

    if tp3 is not None:

        max_pips = round(
            price_to_pips(
                abs(
                    tp3 - entry
                )
            )
        )

    else:

        max_pips = min_pips

    if max_pips >= 180:

        confidence = "HIGH"

    elif max_pips >= 120:

        confidence = "MEDIUM"

    else:

        confidence = "LOW"

    return {
        "min_pips": min_pips,
        "max_pips": max_pips,
        "confidence": confidence
    }


# ============================================================
# STATE
# ============================================================

def load_state():

    if not os.path.exists(
        STATE_FILE
    ):

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

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            state,
            file,
            indent=2
        )


# ============================================================
# ALERT KEY
# ============================================================

def create_alert_key(
    opportunity,
    plan
):

    return (
        f"{opportunity['direction']}"
        f"|{opportunity['score']}"
        f"|{plan.get('entry')}"
        f"|{plan.get('sl')}"
        f"|{plan.get('tp2')}"
    )


# ============================================================
# TELEGRAM
# ============================================================

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
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "⚠️ Telegram error:",
                response.text[:500]
            )

            return False

        print(
            "📲 Telegram alert sent."
        )

        return True

    except Exception as error:

        print(
            "⚠️ Telegram exception:",
            str(error)
        )

        return False


# ============================================================
# FORMAT TELEGRAM
# ============================================================

def format_alert(
    opportunity,
    plan,
    potential,
    session,
    h1,
    m15,
    m5,
    momentum
):

    direction = (
        opportunity["direction"]
    )

    emoji = (
        "🟢"
        if direction == "BUY"
        else "🔴"
    )

    message = f"""
{emoji} <b>BOSQUE FOREX AI</b>

<b>🚨 VALID SCALPING OPPORTUNITY</b>

Symbol:
<b>XAUUSD</b>

Direction:
<b>{direction}</b>

Setup:
<b>{opportunity['type']}</b>

Score:
<b>{opportunity['score']}/100</b>

━━━━━━━━━━━━━━━━━━

<b>🧭 MTF</b>

H1:
<b>{h1['trend']}</b>
{h1['structure']}

M15:
<b>{m15['trend']}</b>
{m15['structure']}

M5:
<b>{m5['trend']}</b>
{m5['structure']}

M5 Confirmation:
<b>{m5['candle']}</b>

Momentum:
<b>{momentum['status']}</b>

Session:
<b>{session}</b>

━━━━━━━━━━━━━━━━━━

<b>🎯 TRADE PLAN</b>

Entry:
<b>{plan['entry']:.2f}</b>

SL:
<b>{plan['sl']:.2f}</b>

TP1:
<b>{plan['tp1']:.2f}</b>
~{round(price_to_pips(abs(plan['tp1'] - plan['entry'])))} pips

TP2:
<b>{plan['tp2']:.2f}</b>
~{round(price_to_pips(abs(plan['tp2'] - plan['entry'])))} pips

TP3:
<b>{plan['tp3']:.2f}</b>
~{round(price_to_pips(abs(plan['tp3'] - plan['entry'])))} pips

Risk:
<b>{plan['risk_pips']:.1f} pips</b>

R:R TP2:
<b>1:{plan['rr_max']:.2f}</b>

Potential:
<b>{potential['min_pips']}–{potential['max_pips']} pips</b>

Confidence:
<b>{potential['confidence']}</b>

━━━━━━━━━━━━━━━━━━

<b>🔎 CONFIRMATION</b>

"""

    for item in opportunity[
        "confirmations"
    ][-8:]:

        message += (
            f"• {item}\n"
        )

    message += """

━━━━━━━━━━━━━━━━━━

⚠️ Manual execution only.
Wait for confirmation on MT5.

<b>BOSQUE FOREX AI</b>
"""

    return message


# ============================================================
# DASHBOARD JSON
# ============================================================

def build_dashboard_data(
    opportunity,
    plan,
    potential,
    session,
    h1,
    m15,
    m5,
    pd,
    m15_sweep,
    m15_setup,
    m5_confirmation,
    momentum
):

    latest_price = (
        m5["latest_price"]
    )

    market_mode = h1["trend"]

    data = {

        "engine": {
            "name": "BOSQUE FOREX AI",
            "version": "SCALPING V3",
            "symbol": "XAU/USD",
            "timeframes": "H1 → M15 → M5",
            "status": "ONLINE"
        },

        "timestamp_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "timestamp_malaysia":
            malaysia_now().isoformat(),

        "latest_price":
            round_price(
                latest_price
            ),

        "session":
            session,

        "market_mode":
            market_mode,

        "pd": pd,

        "opportunity":
            opportunity,

        "plan":
            plan,

        "potential":
            potential,

        "h1": {
            **h1
        },

        "m15": {
            **m15,

            "range":
                m15_setup[
                    "range_location"
                ],

            "liquidity":
                m15_sweep[
                    "type"
                ],

            "setup":
                m15_setup[
                    "setup"
                ],

            "setup_valid":
                m15_setup[
                    "valid"
                ]
        },

        "m5": {
            **m5,

            "confirmation":
                m5_confirmation[
                    "candle"
                ],

            "confirmation_valid":
                m5_confirmation[
                    "valid"
                ],

            "momentum":
                momentum[
                    "status"
                ]
        },

        "filters": {

            "score_70_plus":
                opportunity[
                    "score"
                ] >= MIN_SCORE,

            "m15_setup":
                m15_setup[
                    "valid"
                ],

            "m5_confirmation":
                m5_confirmation[
                    "valid"
                ],

            "risk_35_60":
                plan[
                    "risk_pips"
                ] is not None
                and
                MIN_RISK_PIPS
                <= plan[
                    "risk_pips"
                ]
                <= MAX_RISK_PIPS,

            "tp1_60_plus":
                plan[
                    "tp1"
                ] is not None,

            "rr_1_2_plus":
                plan[
                    "rr_max"
                ] is not None
                and
                plan[
                    "rr_max"
                ] >= MIN_RR,

            "room_to_target":
                plan[
                    "room_to_target"
                ]
        },

        "confirmations":
            opportunity[
                "confirmations"
            ]

    }

    return data


def save_dashboard_data(
    data
):

    temp_file = (
        DASHBOARD_FILE
        + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )

    os.replace(
        temp_file,
        DASHBOARD_FILE
    )

    print(
        f"💾 Dashboard data saved: "
        f"{DASHBOARD_FILE}"
    )


# ============================================================
# MAIN ENGINE
# ============================================================

# Global M15 candles used by setup detector.
M15_CANDLES_GLOBAL = []


def run_engine():

    global M15_CANDLES_GLOBAL

    print()
    print("=" * 60)
    print("👑 BOSQUE FOREX AI")
    print("SCALPING ENGINE V3")
    print("=" * 60)

    print()

    validate_environment()

    # ========================================================
    # 1. FETCH M5
    # ========================================================

    m5_candles = get_m5_data()

    # ========================================================
    # 2. BUILD M15
    # ========================================================

    m15_candles = aggregate_candles(
        m5_candles,
        15
    )

    # ========================================================
    # 3. BUILD H1
    # ========================================================

    h1_candles = aggregate_candles(
        m5_candles,
        60
    )

    if len(m15_candles) < 30:

        raise RuntimeError(
            "Not enough M15 candles."
        )

    if len(h1_candles) < 20:

        raise RuntimeError(
            "Not enough H1 candles."
        )

    M15_CANDLES_GLOBAL = (
        m15_candles
    )

    # ========================================================
    # 4. ANALYSIS
    # ========================================================

    h1 = analyze_structure(
        h1_candles
    )

    m15 = analyze_structure(
        m15_candles
    )

    m5 = analyze_structure(
        m5_candles
    )

    pd = calculate_pd_zone(
        m15_candles
    )

    m15_sweep = detect_liquidity_sweep(
        m15_candles
    )

    m5_sweep = detect_liquidity_sweep(
        m5_candles
    )

    m15_candle = candle_confirmation(
        m15_candles
    )

    m5_candle = candle_confirmation(
        m5_candles
    )

    momentum = calculate_momentum(
        m5_candles
    )

    session = get_session()

    # ========================================================
    # 5. M15 SETUP
    # ========================================================

    m15_setup = determine_m15_setup(
        h1,
        m15,
        m15_sweep,
        m15_candle
    )

    direction = (
        m15_setup["direction"]
    )

    # ========================================================
    # 6. M5 CONFIRMATION
    # ========================================================

    m5_confirmation = (
        determine_m5_confirmation(
            direction,
            m5,
            m5_sweep,
            m5_candle
        )
    )

    # ========================================================
    # 7. SCORE
    # ========================================================

    score_data = calculate_score(
        direction,
        h1,
        m15,
        m5,
        pd,
        m15_setup,
        m15_sweep,
        m5_confirmation,
        momentum,
        session
    )

    # ========================================================
    # 8. TRADE PLAN
    # ========================================================

    plan = create_trade_plan(
        direction,
        m5_candles,
        m15_candles,
        m15
    )

    # ========================================================
    # 9. FINAL OPPORTUNITY
    # ========================================================

    opportunity = build_opportunity(
        direction,
        score_data,
        m15_setup,
        m5_confirmation,
        plan
    )

    # ========================================================
    # 10. IMPORTANT:
    #
    # If trade plan invalid, force WAIT.
    # ========================================================

    if not plan["valid"]:

        opportunity["direction"] = "WAIT"

        opportunity["valid"] = False

        opportunity["type"] = (
            "NO VALID SETUP"
        )

    # ========================================================
    # 11. POTENTIAL
    # ========================================================

    potential = calculate_potential(
        opportunity["direction"],
        plan
    )

    # ========================================================
    # 12. DASHBOARD DATA
    # ========================================================

    dashboard_data = build_dashboard_data(
        opportunity,
        plan,
        potential,
        session,
        h1,
        m15,
        m5,
        pd,
        m15_sweep,
        m15_setup,
        m5_confirmation,
        momentum
    )

    save_dashboard_data(
        dashboard_data
    )

    # ========================================================
    # 13. PRINT RESULT
    # ========================================================

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("📊 BOSQUE MARKET INTELLIGENCE")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    print(
        "H1:",
        h1["trend"],
        "|",
        h1["structure"]
    )

    print(
        "M15:",
        m15["trend"],
        "|",
        m15["structure"]
    )

    print(
        "M5:",
        m5["trend"],
        "|",
        m5["structure"]
    )

    print(
        "M15 Setup:",
        m15_setup["setup"]
    )

    print(
        "M5 Confirmation:",
        m5_confirmation["candle"]
    )

    print(
        "Momentum:",
        momentum["status"]
    )

    print(
        "Session:",
        session
    )

    print()
    print(
        "🎯 Direction:",
        opportunity["direction"]
    )

    print(
        "📈 Score:",
        opportunity["score"]
    )

    print(
        "📌 Type:",
        opportunity["type"]
    )

    print()

    if plan["entry"] is not None:

        print(
            "Entry:",
            plan["entry"]
        )

        print(
            "SL:",
            plan["sl"]
        )

        print(
            "TP1:",
            plan["tp1"]
        )

        print(
            "TP2:",
            plan["tp2"]
        )

        print(
            "TP3:",
            plan["tp3"]
        )

        print(
            "Risk:",
            plan["risk_pips"],
            "pips"
        )

        print(
            "RR TP2:",
            plan["rr_max"]
        )

    else:

        print(
            "Trade Plan: INVALID"
        )

    print()

    print(
        "Potential:",
        potential["min_pips"],
        "-",
        potential["max_pips"],
        "pips"
    )

    print(
        "Confidence:",
        potential["confidence"]
    )

    print()
    print(
        "M15 Setup:",
        "PASS"
        if m15_setup["valid"]
        else "FAIL"
    )

    print(
        "M5 Confirmation:",
        "PASS"
        if m5_confirmation["valid"]
        else "FAIL"
    )

    print(
        "Risk:",
        "PASS"
        if plan["risk_pips"]
        and
        MIN_RISK_PIPS
        <= plan["risk_pips"]
        <= MAX_RISK_PIPS
        else "FAIL"
    )

    print(
        "RR 1:2:",
        "PASS"
        if plan["rr_max"]
        and
        plan["rr_max"] >= MIN_RR
        else "FAIL"
    )

    print(
        "Room:",
        "PASS"
        if plan["room_to_target"]
        else "FAIL"
    )

    print()
    print(
        "FINAL:",
        "VALID OPPORTUNITY"
        if opportunity["valid"]
        else "WAIT"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # 14. TELEGRAM ONLY IF VALID
    # ========================================================

    if opportunity["valid"]:

        state = load_state()

        alert_key = create_alert_key(
            opportunity,
            plan
        )

        previous_key = state.get(
            "last_alert_key"
        )

        if alert_key != previous_key:

            telegram_message = format_alert(
                opportunity,
                plan,
                potential,
                session,
                h1,
                m15,
                m5,
                momentum
            )

            sent = send_telegram(
                telegram_message
            )

            if sent:

                state[
                    "last_alert_key"
                ] = alert_key

                state[
                    "last_alert_time"
                ] = (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                )

                save_state(
                    state
                )

        else:

            print(
                "🔕 Same opportunity already alerted."
            )

    else:

        print(
            "🔕 No valid opportunity. "
            "Telegram skipped."
        )

    print()
    print(
        "✅ BOSQUE ENGINE COMPLETE"
    )

    print(
        "📁 dashboard_data.json updated"
    )

    print()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        run_engine()

    except Exception as error:

        print()

        print(
            "❌ BOSQUE ENGINE ERROR"
        )

        print(
            str(error)
        )

        raise