import os
import json
import requests
from datetime import datetime, timezone, timedelta


# ============================================================
# BOSQUE FOREX AI v3
# LIVE + BACKTEST ENGINE
# XAU/USD
#
# PIP CONVENTION LOCKED:
#
# 100 POINTS = 10 PIPS
# 10 POINTS  = 1 PIP
# 1 PIP      = 0.01 XAUUSD PRICE
#
# CORE:
# - ONE Twelve Data request per scan
# - M5 real OHLC
# - Local H1/H4 aggregation
# - Closed candles only
# - HH / HL / LH / LL
# - BOS
# - SNR / SBR
# - Support / Resistance
# - Supply / Demand approximation
# - Premium / Discount
# - Liquidity sweep
# - Momentum
# - Candle confirmation
# - Opportunity classification
# - 0-100 score
# - Low-risk filter: 35-60 pips
# - High-reward filter: TP1 >= 120 pips
# - Minimum R:R 1:2
# - TP1 / TP2 / TP3
# - Backtest candle-by-candle
# - No look-ahead
# - Win rate
# - TP1 / TP2 / TP3 statistics
# - Max losing streak
# - Telegram alert
# - Anti-spam
# ============================================================


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAU/USD"

TWELVEDATA_URL = (
    "https://api.twelvedata.com/time_series"
)

M5_INTERVAL = "5min"

# ------------------------------------------------------------
# DATA
# ------------------------------------------------------------

# 500 M5 candles ≈ 41 hours.
#
# Increase only if your Twelve Data plan allows it.
#
# 500 is intentionally kept as quota-safe default.
OUTPUT_SIZE = 500

# ------------------------------------------------------------
# SIGNAL FILTER
# ------------------------------------------------------------

MIN_SCORE = 70

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MIN_RISK_PIPS = 35
MAX_RISK_PIPS = 60

# ------------------------------------------------------------
# REWARD
# ------------------------------------------------------------

MIN_TP1_PIPS = 120

MIN_RR = 2.0

# ------------------------------------------------------------
# GOLD PIP CONVERSION
# ------------------------------------------------------------
#
# XAUUSD:
#
# 0.01 price = 1 pip
# 0.10 price = 10 pips
# 1.00 price = 100 pips
#
# Therefore:
#
# 100 MT5 points = 10 pips
#
# assuming broker point = 0.001.
# ------------------------------------------------------------

PIP_SIZE = 0.01

# MT5-style point reference.
POINT_SIZE = 0.001

POINTS_PER_PIP = (
    PIP_SIZE / POINT_SIZE
)

# Expected result = 10
# 10 points = 1 pip
# 100 points = 10 pips

REQUEST_TIMEOUT = 20

MALAYSIA_TZ = timezone(
    timedelta(hours=8)
)

STATE_FILE = os.path.join(
    os.path.dirname(__file__),
    "state.json"
)


# ============================================================
# BACKTEST CONFIG
# ============================================================

BACKTEST_ENABLED = True

# Minimum candles required before starting backtest.
BACKTEST_MIN_CANDLES = 120

# Number of historical M5 candles used per setup.
BACKTEST_LOOKBACK = 80

# Maximum future candles to wait for TP / SL.
#
# 120 pips on XAU can require multiple M5 candles.
BACKTEST_MAX_FORWARD_CANDLES = 72

# Only run a backtest when there is enough historical data.
#
# With OUTPUT_SIZE=500 this is possible but relatively short.
BACKTEST_MIN_SCORE = MIN_SCORE


# ============================================================
# ENVIRONMENT
# ============================================================

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
# TIME
# ============================================================

def malaysia_now():

    return datetime.now(
        MALAYSIA_TZ
    )


# ============================================================
# TIME PARSER
# ============================================================

def parse_time(value):

    if isinstance(value, datetime):

        return value

    value = str(value)

    try:

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:

        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        )


def normalize_utc(dt):

    if dt.tzinfo is None:

        return dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


# ============================================================
# TWELVE DATA
# ============================================================

def get_m5_data():

    params = {

        "symbol": SYMBOL,

        "interval": M5_INTERVAL,

        "outputsize": OUTPUT_SIZE,

        "apikey": API_KEY,

        "format": "JSON"

    }

    print(
        "Fetching real XAU/USD M5 data..."
    )

    response = requests.get(
        TWELVEDATA_URL,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Twelve Data HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    try:

        data = response.json()

    except Exception:

        raise RuntimeError(
            "Twelve Data returned invalid JSON."
        )

    if data.get("status") == "error":

        raise RuntimeError(
            "Twelve Data error: "
            + str(
                data.get(
                    "message",
                    "Unknown API error"
                )
            )
        )

    values = data.get(
        "values"
    )

    if not values:

        raise RuntimeError(
            "No OHLC data received."
        )

    candles = []

    for item in values:

        try:

            candles.append({

                "time":
                    item["datetime"],

                "open":
                    float(item["open"]),

                "high":
                    float(item["high"]),

                "low":
                    float(item["low"]),

                "close":
                    float(item["close"])

            })

        except Exception:

            continue

    if len(candles) < 50:

        raise RuntimeError(
            f"Insufficient OHLC data: "
            f"{len(candles)} candles."
        )

    candles.sort(
        key=lambda x: x["time"]
    )

    print(
        f"Received {len(candles)} M5 candles."
    )

    return candles


# ============================================================
# CLOSED M5 CANDLE
# ============================================================

def remove_incomplete_candle(
    candles
):

    if len(candles) < 2:

        return candles

    latest = candles[-1]

    try:

        candle_time = normalize_utc(
            parse_time(
                latest["time"]
            )
        )

        now_utc = datetime.now(
            timezone.utc
        )

        elapsed = (
            now_utc - candle_time
        ).total_seconds()

        # M5 candle is considered closed
        # after 5 minutes.
        if elapsed < 300:

            print(
                "Removing incomplete M5 candle."
            )

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

    if not candles:

        return []

    groups = {}

    for candle in candles:

        dt = parse_time(
            candle["time"]
        )

        if minutes == 60:

            bucket = dt.replace(
                minute=0,
                second=0,
                microsecond=0
            )

        elif minutes == 240:

            hour_block = (
                dt.hour // 4
            ) * 4

            bucket = dt.replace(
                hour=hour_block,
                minute=0,
                second=0,
                microsecond=0
            )

        else:

            continue

        key = bucket.isoformat()

        if key not in groups:

            groups[key] = []

        groups[key].append(
            candle
        )

    result = []

    for key in sorted(
        groups.keys()
    ):

        group = groups[key]

        if not group:

            continue

        result.append({

            "time": key,

            "open":
                group[0]["open"],

            "high":
                max(
                    c["high"]
                    for c in group
                ),

            "low":
                min(
                    c["low"]
                    for c in group
                ),

            "close":
                group[-1]["close"]

        })

    return result


# ============================================================
# REMOVE INCOMPLETE HTF CANDLES
# ============================================================

def remove_incomplete_htf(
    candles,
    minutes
):

    if not candles:

        return candles

    now_utc = datetime.now(
        timezone.utc
    )

    result = []

    for candle in candles:

        try:

            start = normalize_utc(
                parse_time(
                    candle["time"]
                )
            )

            end = (
                start
                + timedelta(
                    minutes=minutes
                )
            )

            if end <= now_utc:

                result.append(
                    candle
                )

        except Exception:

            continue

    return result


# ============================================================
# SWING DETECTION
# ============================================================

def detect_swings(
    candles,
    left=2,
    right=2
):

    swing_highs = []
    swing_lows = []

    if len(candles) < (
        left + right + 1
    ):

        return (
            swing_highs,
            swing_lows
        )

    for i in range(
        left,
        len(candles) - right
    ):

        current = candles[i]

        high_ok = True
        low_ok = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:

                continue

            if (
                current["high"]
                <= candles[j]["high"]
            ):

                high_ok = False

            if (
                current["low"]
                >= candles[j]["low"]
            ):

                low_ok = False

        if high_ok:

            swing_highs.append({

                "price":
                    current["high"],

                "time":
                    current["time"],

                "index":
                    i

            })

        if low_ok:

            swing_lows.append({

                "price":
                    current["low"],

                "time":
                    current["time"],

                "index":
                    i

            })

    return (
        swing_highs,
        swing_lows
    )


# ============================================================
# MARKET STRUCTURE LABELS
# ============================================================

def classify_structure(
    swing_highs,
    swing_lows
):

    labels = []

    previous_high = None
    previous_low = None

    events = []

    for item in swing_highs:

        events.append({

            "type": "HIGH",

            "price":
                item["price"],

            "time":
                item["time"],

            "index":
                item.get(
                    "index",
                    0
                )

        })

    for item in swing_lows:

        events.append({

            "type": "LOW",

            "price":
                item["price"],

            "time":
                item["time"],

            "index":
                item.get(
                    "index",
                    0
                )

        })

    events.sort(
        key=lambda x: x["index"]
    )

    for event in events:

        if event["type"] == "HIGH":

            if previous_high is None:

                label = "HH"

            elif (
                event["price"]
                > previous_high
            ):

                label = "HH"

            else:

                label = "LH"

            previous_high = (
                event["price"]
            )

        else:

            if previous_low is None:

                label = "HL"

            elif (
                event["price"]
                > previous_low
            ):

                label = "HL"

            else:

                label = "LL"

            previous_low = (
                event["price"]
            )

        labels.append({

            "label": label,

            "type":
                event["type"],

            "price":
                event["price"],

            "time":
                event["time"]

        })

    recent = labels[-8:]

    bullish_count = sum(

        1

        for x in recent

        if x["label"] in (
            "HH",
            "HL"
        )

    )

    bearish_count = sum(

        1

        for x in recent

        if x["label"] in (
            "LH",
            "LL"
        )

    )

    if (
        bullish_count >= 4
        and bullish_count > bearish_count
    ):

        trend = "BULLISH"

    elif (
        bearish_count >= 4
        and bearish_count > bullish_count
    ):

        trend = "BEARISH"

    else:

        trend = "RANGE"

    return {

        "labels": labels,

        "trend":
            trend

    }


# ============================================================
# MARKET STRUCTURE
# ============================================================

def analyze_structure(
    candles
):

    if len(candles) < 20:

        return {

            "structure":
                "RANGE",

            "trend":
                "NEUTRAL",

            "last_high":
                None,

            "last_low":
                None,

            "swing_highs":
                [],

            "swing_lows":
                [],

            "structure_labels":
                []

        }

    swing_highs, swing_lows = (
        detect_swings(candles)
    )

    structure_data = (
        classify_structure(
            swing_highs,
            swing_lows
        )
    )

    latest = candles[-1]

    last_high = (
        swing_highs[-1]
        if swing_highs
        else None
    )

    last_low = (
        swing_lows[-1]
        if swing_lows
        else None
    )

    structure = "RANGE"

    trend = (
        structure_data["trend"]
    )

    if (
        last_high
        and latest["close"]
        > last_high["price"]
    ):

        structure = "BULLISH BOS"

        trend = "BULLISH"

    elif (
        last_low
        and latest["close"]
        < last_low["price"]
    ):

        structure = "BEARISH BOS"

        trend = "BEARISH"

    elif trend == "RANGE":

        recent = candles[-10:]

        movement = (
            recent[-1]["close"]
            - recent[0]["close"]
        )

        if movement > 0:

            trend = "BULLISH"

        elif movement < 0:

            trend = "BEARISH"

        else:

            trend = "NEUTRAL"

    return {

        "structure":
            structure,

        "trend":
            trend,

        "last_high":
            last_high,

        "last_low":
            last_low,

        "swing_highs":
            swing_highs,

        "swing_lows":
            swing_lows,

        "structure_labels":
            structure_data["labels"]

    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < (
        period + 1
    ):

        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]

        previous = candles[i - 1]

        tr = max(

            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            )

        )

        true_ranges.append(tr)

    if len(true_ranges) < period:

        return None

    return sum(
        true_ranges[-period:]
    ) / period


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def detect_support_resistance(
    candles,
    lookback=80,
    tolerance_pips=20
):

    if len(candles) < 20:

        return {

            "support": None,

            "resistance": None,

            "snr": "NONE",

            "sbr": "NONE",

            "support_zone": None,

            "resistance_zone": None

        }

    recent = candles[
        -lookback:
    ]

    swing_highs, swing_lows = (
        detect_swings(
            recent,
            left=2,
            right=2
        )
    )

    current_price = (
        recent[-1]["close"]
    )

    tolerance = (
        tolerance_pips
        * PIP_SIZE
    )

    supports = [

        x["price"]

        for x in swing_lows

        if x["price"]
        < current_price

    ]

    resistances = [

        x["price"]

        for x in swing_highs

        if x["price"]
        > current_price

    ]

    support = (
        max(supports)
        if supports
        else None
    )

    resistance = (
        min(resistances)
        if resistances
        else None
    )

    snr = "NONE"
    sbr = "NONE"

    if support is not None:

        support_touches = sum(

            1

            for c in recent

            if abs(
                c["low"]
                - support
            ) <= tolerance

        )

        if support_touches >= 2:

            snr = "SUPPORT"

    if resistance is not None:

        resistance_touches = sum(

            1

            for c in recent

            if abs(
                c["high"]
                - resistance
            ) <= tolerance

        )

        if resistance_touches >= 2:

            snr = (
                "RESISTANCE"
                if snr == "NONE"
                else snr
            )

    # --------------------------------------------------------
    # SBR
    #
    # Previous support that price has broken below
    # can become resistance.
    # --------------------------------------------------------

    broken_support = None

    for low in reversed(
        swing_lows
    ):

        if (
            current_price
            < low["price"]
        ):

            broken_support = (
                low["price"]
            )

            break

    if broken_support:

        recent_retest = any(

            abs(
                c["high"]
                - broken_support
            ) <= tolerance

            for c in recent[-20:]

        )

        if recent_retest:

            sbr = "SBR"

    support_zone = None

    if support is not None:

        support_zone = {

            "low":
                support - tolerance,

            "high":
                support + tolerance

        }

    resistance_zone = None

    if resistance is not None:

        resistance_zone = {

            "low":
                resistance - tolerance,

            "high":
                resistance + tolerance

        }

    return {

        "support":
            support,

        "resistance":
            resistance,

        "snr":
            snr,

        "sbr":
            sbr,

        "support_zone":
            support_zone,

        "resistance_zone":
            resistance_zone

    }


# ============================================================
# SUPPLY / DEMAND
# ============================================================

def detect_supply_demand(
    candles,
    lookback=60
):

    recent = candles[
        -lookback:
    ]

    if len(recent) < 10:

        return {

            "supply": None,

            "demand": None

        }

    supply = None
    demand = None

    # --------------------------------------------------------
    # Supply:
    # Strong bearish displacement after local high.
    # --------------------------------------------------------

    for i in range(
        len(recent) - 6,
        2,
        -1
    ):

        candle = recent[i]

        next_candles = recent[
            i + 1:i + 4
        ]

        if not next_candles:

            continue

        move = (
            next_candles[-1]["close"]
            - candle["close"]
        )

        if move < 0:

            move_pips = (
                abs(move)
                / PIP_SIZE
            )

            if move_pips >= 40:

                supply = {

                    "high":
                        candle["high"],

                    "low":
                        candle["low"]

                }

                break

    # --------------------------------------------------------
    # Demand:
    # Strong bullish displacement after local low.
    # --------------------------------------------------------

    for i in range(
        len(recent) - 6,
        2,
        -1
    ):

        candle = recent[i]

        next_candles = recent[
            i + 1:i + 4
        ]

        if not next_candles:

            continue

        move = (
            next_candles[-1]["close"]
            - candle["close"]
        )

        if move > 0:

            move_pips = (
                abs(move)
                / PIP_SIZE
            )

            if move_pips >= 40:

                demand = {

                    "high":
                        candle["high"],

                    "low":
                        candle["low"]

                }

                break

    return {

        "supply":
            supply,

        "demand":
            demand

    }


# ============================================================
# PREMIUM / DISCOUNT
# ============================================================

def calculate_pd_zone(
    candles
):

    recent = candles[-50:]

    high = max(
        c["high"]
        for c in recent
    )

    low = min(
        c["low"]
        for c in recent
    )

    equilibrium = (
        high + low
    ) / 2

    latest = candles[-1]["close"]

    if latest < equilibrium:

        zone = "DISCOUNT"

    elif latest > equilibrium:

        zone = "PREMIUM"

    else:

        zone = "EQUILIBRIUM"

    return {

        "high":
            high,

        "low":
            low,

        "equilibrium":
            equilibrium,

        "zone":
            zone

    }


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

def detect_liquidity_sweep(
    candles
):

    if len(candles) < 10:

        return "NONE"

    previous = candles[-9:-3]

    previous_high = max(
        c["high"]
        for c in previous
    )

    previous_low = min(
        c["low"]
        for c in previous
    )

    latest = candles[-1]

    # Sell-side liquidity taken.
    # Potential bullish reversal.

    if (
        latest["low"]
        < previous_low

        and latest["close"]
        > previous_low
    ):

        return "SELL_SIDE_SWEEP"

    # Buy-side liquidity taken.
    # Potential bearish reversal.

    if (
        latest["high"]
        > previous_high

        and latest["close"]
        < previous_high
    ):

        return "BUY_SIDE_SWEEP"

    return "NONE"


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(
    candles
):

    if len(candles) < 6:

        return "NEUTRAL"

    recent = candles[-5:]

    bullish = 0
    bearish = 0

    for candle in recent:

        if (
            candle["close"]
            > candle["open"]
        ):

            bullish += 1

        elif (
            candle["close"]
            < candle["open"]
        ):

            bearish += 1

    if bullish >= 4:

        return "BULLISH"

    if bearish >= 4:

        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# CANDLE CONFIRMATION
# ============================================================

def candle_confirmation(
    candles
):

    if len(candles) < 3:

        return "NONE"

    previous = candles[-2]

    latest = candles[-1]

    latest_body = abs(
        latest["close"]
        - latest["open"]
    )

    latest_range = (
        latest["high"]
        - latest["low"]
    )

    if latest_range <= 0:

        return "NONE"

    body_ratio = (
        latest_body
        / latest_range
    )

    # Bullish confirmation.

    if (
        latest["close"]
        > latest["open"]

        and body_ratio >= 0.55

        and latest["close"]
        > previous["high"]
    ):

        return "BULLISH"

    # Bearish confirmation.

    if (
        latest["close"]
        < latest["open"]

        and body_ratio >= 0.55

        and latest["close"]
        < previous["low"]
    ):

        return "BEARISH"

    return "NONE"


# ============================================================
# SESSION
# ============================================================

def get_session():

    hour = malaysia_now().hour

    if 7 <= hour < 15:

        return "ASIAN"

    if 15 <= hour < 21:

        return "LONDON"

    if 20 <= hour <= 23:

        return "NEW YORK"

    if 0 <= hour < 1:

        return "NEW YORK"

    return "ASIAN"


# ============================================================
# OPPORTUNITY
# ============================================================

def determine_opportunity(
    h4,
    h1,
    m5,
    pd,
    sweep,
    confirmation,
    sr=None,
    zones=None
):

    # --------------------------------------------------------
    # BUY PULLBACK
    # --------------------------------------------------------

    if (
        h4["trend"] == "BULLISH"

        and h1["trend"] == "BULLISH"

        and pd["zone"] == "DISCOUNT"

        and confirmation == "BULLISH"
    ):

        return "BUY PULLBACK"

    # --------------------------------------------------------
    # SELL PULLBACK
    # --------------------------------------------------------

    if (
        h4["trend"] == "BEARISH"

        and h1["trend"] == "BEARISH"

        and pd["zone"] == "PREMIUM"

        and confirmation == "BEARISH"
    ):

        return "SELL PULLBACK"

    # --------------------------------------------------------
    # LIQUIDITY SWEEP
    # --------------------------------------------------------

    if (
        sweep == "SELL_SIDE_SWEEP"

        and confirmation == "BULLISH"
    ):

        return "BUY LIQUIDITY SWEEP"

    if (
        sweep == "BUY_SIDE_SWEEP"

        and confirmation == "BEARISH"
    ):

        return "SELL LIQUIDITY SWEEP"

    # --------------------------------------------------------
    # SNR / SBR
    # --------------------------------------------------------

    if sr:

        if (
            sr.get("snr")
            == "SUPPORT"

            and confirmation
            == "BULLISH"
        ):

            return "BUY SUPPORT"

        if (
            sr.get("sbr")
            == "SBR"

            and confirmation
            == "BEARISH"
        ):

            return "SELL SBR"

        if (
            sr.get("snr")
            == "RESISTANCE"

            and confirmation
            == "BEARISH"
        ):

            return "SELL RESISTANCE"

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    if (
        m5["structure"]
        == "BULLISH BOS"

        and confirmation == "BULLISH"
    ):

        return "BUY BREAKOUT"

    if (
        m5["structure"]
        == "BEARISH BOS"

        and confirmation == "BEARISH"
    ):

        return "SELL BREAKOUT"

    return "NO VALID SETUP"


# ============================================================
# SCORE ENGINE
# ============================================================

def calculate_score(
    h4,
    h1,
    m5,
    pd,
    sweep,
    momentum,
    confirmation,
    sr=None
):

    buy_score = 0
    sell_score = 0

    reasons_buy = []
    reasons_sell = []

    # --------------------------------------------------------
    # H4
    # --------------------------------------------------------

    if h4["trend"] == "BULLISH":

        buy_score += 20

        reasons_buy.append(
            "H4 bullish context"
        )

    elif h4["trend"] == "BEARISH":

        sell_score += 20

        reasons_sell.append(
            "H4 bearish context"
        )

    # --------------------------------------------------------
    # H1
    # --------------------------------------------------------

    if h1["trend"] == "BULLISH":

        buy_score += 20

        reasons_buy.append(
            "H1 bullish context"
        )

    elif h1["trend"] == "BEARISH":

        sell_score += 20

        reasons_sell.append(
            "H1 bearish context"
        )

    # --------------------------------------------------------
    # M5 STRUCTURE
    # --------------------------------------------------------

    if (
        m5["structure"]
        == "BULLISH BOS"
    ):

        buy_score += 15

        reasons_buy.append(
            "M5 bullish BOS"
        )

    elif (
        m5["structure"]
        == "BEARISH BOS"
    ):

        sell_score += 15

        reasons_sell.append(
            "M5 bearish BOS"
        )

    # --------------------------------------------------------
    # PD
    # --------------------------------------------------------

    if pd["zone"] == "DISCOUNT":

        buy_score += 10

        reasons_buy.append(
            "Price in discount"
        )

    elif pd["zone"] == "PREMIUM":

        sell_score += 10

        reasons_sell.append(
            "Price in premium"
        )

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if sweep == "SELL_SIDE_SWEEP":

        buy_score += 15

        reasons_buy.append(
            "Sell-side liquidity sweep"
        )

    elif sweep == "BUY_SIDE_SWEEP":

        sell_score += 15

        reasons_sell.append(
            "Buy-side liquidity sweep"
        )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    if momentum == "BULLISH":

        buy_score += 10

        reasons_buy.append(
            "Bullish momentum"
        )

    elif momentum == "BEARISH":

        sell_score += 10

        reasons_sell.append(
            "Bearish momentum"
        )

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    if confirmation == "BULLISH":

        buy_score += 10

        reasons_buy.append(
            "Bullish candle confirmation"
        )

    elif confirmation == "BEARISH":

        sell_score += 10

        reasons_sell.append(
            "Bearish candle confirmation"
        )

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    if sr:

        if sr.get("snr") == "SUPPORT":

            buy_score += 5

            reasons_buy.append(
                "Support area detected"
            )

        if sr.get("snr") == "RESISTANCE":

            sell_score += 5

            reasons_sell.append(
                "Resistance area detected"
            )

        if sr.get("sbr") == "SBR":

            sell_score += 5

            reasons_sell.append(
                "Support turned resistance"
            )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    if buy_score > sell_score:

        return {

            "direction":
                "BUY",

            "score":
                min(
                    buy_score,
                    100
                ),

            "reasons":
                reasons_buy

        }

    if sell_score > buy_score:

        return {

            "direction":
                "SELL",

            "score":
                min(
                    sell_score,
                    100
                ),

            "reasons":
                reasons_sell

        }

    return {

        "direction":
            "WAIT",

        "score":
            0,

        "reasons":
            []

    }


# ============================================================
# TRADE PLAN
# ============================================================

def create_trade_plan(
    direction,
    candles,
    m5_analysis
):

    if not candles:

        return None

    entry = candles[-1]["close"]

    atr = calculate_atr(
        candles,
        14
    )

    if atr is None:

        recent_ranges = [

            c["high"] - c["low"]

            for c in candles[-10:]

        ]

        if not recent_ranges:

            return None

        atr = (
            sum(recent_ranges)
            / len(recent_ranges)
        )

    swing_highs = (
        m5_analysis.get(
            "swing_highs",
            []
        )
    )

    swing_lows = (
        m5_analysis.get(
            "swing_lows",
            []
        )
    )

    last_swing_high = (

        swing_highs[-1]["price"]

        if swing_highs

        else None

    )

    last_swing_low = (

        swing_lows[-1]["price"]

        if swing_lows

        else None

    )

    # ========================================================
    # BUY
    # ========================================================

    if direction == "BUY":

        structural_sl = None

        if last_swing_low:

            structural_sl = (
                last_swing_low
            )

        atr_sl = (
            entry
            - atr
        )

        candidates = []

        if structural_sl:

            candidates.append(
                structural_sl
            )

        candidates.append(
            atr_sl
        )

        valid_sl = []

        for sl_candidate in candidates:

            risk = (
                entry
                - sl_candidate
            )

            risk_pips = (
                risk
                / PIP_SIZE
            )

            if (
                MIN_RISK_PIPS
                <= risk_pips
                <= MAX_RISK_PIPS
            ):

                valid_sl.append(
                    (
                        risk_pips,
                        sl_candidate
                    )
                )

        if not valid_sl:

            return None

        # Tightest valid SL.

        valid_sl.sort(
            key=lambda x: x[0]
        )

        risk_pips, sl = (
            valid_sl[0]
        )

        risk = (
            entry - sl
        )

        tp1 = entry + max(

            risk * MIN_RR,

            MIN_TP1_PIPS
            * PIP_SIZE

        )

        tp2 = entry + (
            risk * 3.0
        )

        tp3 = entry + (
            risk * 4.0
        )

        tp1_pips = (
            tp1 - entry
        ) / PIP_SIZE

        tp2_pips = (
            tp2 - entry
        ) / PIP_SIZE

        tp3_pips = (
            tp3 - entry
        ) / PIP_SIZE

        rr = (
            tp1 - entry
        ) / risk

        if not (
            MIN_RISK_PIPS
            <= risk_pips
            <= MAX_RISK_PIPS
        ):

            return None

        if tp1_pips < MIN_TP1_PIPS:

            return None

        if rr < MIN_RR:

            return None

        return {

            "direction":
                "BUY",

            "entry":
                entry,

            "sl":
                sl,

            "tp1":
                tp1,

            "tp2":
                tp2,

            "tp3":
                tp3,

            "risk":
                risk,

            "risk_pips":
                risk_pips,

            "rr":
                rr,

            "pips_to_sl":
                risk_pips,

            "pips_to_tp1":
                tp1_pips,

            "pips_to_tp2":
                tp2_pips,

            "pips_to_tp3":
                tp3_pips,

            "estimated_range": (

                risk_pips,

                tp3_pips

            )

        }

    # ========================================================
    # SELL
    # ========================================================

    if direction == "SELL":

        structural_sl = None

        if last_swing_high:

            structural_sl = (
                last_swing_high
            )

        atr_sl = (
            entry
            + atr
        )

        candidates = []

        if structural_sl:

            candidates.append(
                structural_sl
            )

        candidates.append(
            atr_sl
        )

        valid_sl = []

        for sl_candidate in candidates:

            risk = (
                sl_candidate
                - entry
            )

            risk_pips = (
                risk
                / PIP_SIZE
            )

            if (
                MIN_RISK_PIPS
                <= risk_pips
                <= MAX_RISK_PIPS
            ):

                valid_sl.append(
                    (
                        risk_pips,
                        sl_candidate
                    )
                )

        if not valid_sl:

            return None

        valid_sl.sort(
            key=lambda x: x[0]
        )

        risk_pips, sl = (
            valid_sl[0]
        )

        risk = (
            sl - entry
        )

        tp1 = entry - max(

            risk * MIN_RR,

            MIN_TP1_PIPS
            * PIP_SIZE

        )

        tp2 = entry - (
            risk * 3.0
        )

        tp3 = entry - (
            risk * 4.0
        )

        tp1_pips = (
            entry - tp1
        ) / PIP_SIZE

        tp2_pips = (
            entry - tp2
        ) / PIP_SIZE

        tp3_pips = (
            entry - tp3
        ) / PIP_SIZE

        rr = (
            entry - tp1
        ) / risk

        if not (
            MIN_RISK_PIPS
            <= risk_pips
            <= MAX_RISK_PIPS
        ):

            return None

        if tp1_pips < MIN_TP1_PIPS:

            return None

        if rr < MIN_RR:

            return None

        return {

            "direction":
                "SELL",

            "entry":
                entry,

            "sl":
                sl,

            "tp1":
                tp1,

            "tp2":
                tp2,

            "tp3":
                tp3,

            "risk":
                risk,

            "risk_pips":
                risk_pips,

            "rr":
                rr,

            "pips_to_sl":
                risk_pips,

            "pips_to_tp1":
                tp1_pips,

            "pips_to_tp2":
                tp2_pips,

            "pips_to_tp3":
                tp3_pips,

            "estimated_range": (

                risk_pips,

                tp3_pips

            )

        }

    return None


# ============================================================
# BACKTEST RESULT
# ============================================================

def empty_backtest():

    return {

        "enabled":
            BACKTEST_ENABLED,

        "candles":
            0,

        "scanned":
            0,

        "qualified":
            0,

        "rejected":
            0,

        "wins":
            0,

        "losses":
            0,

        "tp1_hits":
            0,

        "tp2_hits":
            0,

        "tp3_hits":
            0,

        "timeouts":
            0,

        "ambiguous":
            0,

        "win_rate":
            0.0,

        "loss_rate":
            0.0,

        "average_r":
            0.0,

        "total_r":
            0.0,

        "max_losing_streak":
            0,

        "signals_by_direction": {

            "BUY":
                0,

            "SELL":
                0

        },

        "structure_counts": {

            "HH":
                0,

            "HL":
                0,

            "LH":
                0,

            "LL":
                0

        },

        "zone_counts": {

            "SUPPORT":
                0,

            "RESISTANCE":
                0,

            "SBR":
                0

        },

        "details":
            []

    }


# ============================================================
# BACKTEST TRADE OUTCOME
# ============================================================

def evaluate_future_trade(
    direction,
    plan,
    candles,
    start_index
):

    end_index = min(

        len(candles),

        start_index
        + BACKTEST_MAX_FORWARD_CANDLES

    )

    tp1_hit = False
    tp2_hit = False
    tp3_hit = False
    sl_hit = False

    first_outcome = None

    r_result = None

    for i in range(
        start_index,
        end_index
    ):

        candle = candles[i]

        high = candle["high"]

        low = candle["low"]

        if direction == "BUY":

            hit_sl = (
                low
                <= plan["sl"]
            )

            hit_tp1 = (
                high
                >= plan["tp1"]
            )

            hit_tp2 = (
                high
                >= plan["tp2"]
            )

            hit_tp3 = (
                high
                >= plan["tp3"]
            )

        else:

            hit_sl = (
                high
                >= plan["sl"]
            )

            hit_tp1 = (
                low
                <= plan["tp1"]
            )

            hit_tp2 = (
                low
                <= plan["tp2"]
            )

            hit_tp3 = (
                low
                <= plan["tp3"]
            )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # If SL and TP happen inside the same candle,
        # OHLC alone cannot tell which came first.
        #
        # Therefore classify as AMBIGUOUS rather than
        # pretending we know the intrabar sequence.
        # ----------------------------------------------------

        if hit_sl and (
            hit_tp1
            or hit_tp2
            or hit_tp3
        ):

            return {

                "outcome":
                    "AMBIGUOUS",

                "r":
                    0.0,

                "tp1":
                    False,

                "tp2":
                    False,

                "tp3":
                    False,

                "bars":
                    i - start_index + 1

            }

        if hit_sl:

            sl_hit = True

            first_outcome = "LOSS"

            r_result = -1.0

            break

        if hit_tp3:

            tp1_hit = True
            tp2_hit = True
            tp3_hit = True

            first_outcome = "TP3"

            r_result = 4.0

            break

        if hit_tp2:

            tp1_hit = True
            tp2_hit = True

            first_outcome = "TP2"

            r_result = 3.0

            break

        if hit_tp1:

            tp1_hit = True

            first_outcome = "TP1"

            r_result = 2.0

            break

    if first_outcome is None:

        return {

            "outcome":
                "TIMEOUT",

            "r":
                0.0,

            "tp1":
                tp1_hit,

            "tp2":
                tp2_hit,

            "tp3":
                tp3_hit,

            "bars":
                end_index - start_index

        }

    return {

        "outcome":
            first_outcome,

        "r":
            r_result,

        "tp1":
            tp1_hit,

        "tp2":
            tp2_hit,

        "tp3":
            tp3_hit,

        "bars":
            (
                i
                - start_index
                + 1
            )

    }


# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_backtest(
    candles
):

    stats = empty_backtest()

    stats["candles"] = len(
        candles
    )

    if not BACKTEST_ENABLED:

        print(
            "Backtest disabled."
        )

        return stats

    if len(candles) < (
        BACKTEST_MIN_CANDLES
    ):

        print(
            "Backtest skipped:"
        )

        print(
            f"Need at least "
            f"{BACKTEST_MIN_CANDLES} candles."
        )

        return stats

    print()
    print("=" * 60)
    print("📈 BOSQUE BACKTEST ENGINE")
    print("=" * 60)

    losing_streak = 0

    max_losing_streak = 0

    # --------------------------------------------------------
    # Start only after sufficient lookback.
    # --------------------------------------------------------

    start_index = max(

        BACKTEST_LOOKBACK,

        60

    )

    last_test_index = (
        len(candles)
        - 10
    )

    i = start_index

    while i < last_test_index:

        stats["scanned"] += 1

        historical = candles[
            :i + 1
        ]

        # ----------------------------------------------------
        # MTF snapshots.
        #
        # Only candles available up to current point.
        # No future data is used.
        # ----------------------------------------------------

        h1_all = aggregate_candles(
            historical,
            60
        )

        h4_all = aggregate_candles(
            historical,
            240
        )

        h1 = remove_incomplete_historical(
            h1_all,
            historical[-1]["time"],
            60
        )

        h4 = remove_incomplete_historical(
            h4_all,
            historical[-1]["time"],
            240
        )

        if (
            len(h1) < 10
            or len(h4) < 5
        ):

            i += 1

            continue

        m5 = analyze_structure(
            historical
        )

        h1_analysis = analyze_structure(
            h1
        )

        h4_analysis = analyze_structure(
            h4
        )

        pd = calculate_pd_zone(
            historical
        )

        sweep = detect_liquidity_sweep(
            historical
        )

        momentum = calculate_momentum(
            historical
        )

        confirmation = (
            candle_confirmation(
                historical
            )
        )

        sr = detect_support_resistance(
            historical
        )

        zones = detect_supply_demand(
            historical
        )

        scoring = calculate_score(

            h4_analysis,

            h1_analysis,

            m5,

            pd,

            sweep,

            momentum,

            confirmation,

            sr

        )

        direction = scoring[
            "direction"
        ]

        score = scoring[
            "score"
        ]

        opportunity = (
            determine_opportunity(

                h4_analysis,

                h1_analysis,

                m5,

                pd,

                sweep,

                confirmation,

                sr,

                zones

            )
        )

        # ----------------------------------------------------
        # Structure statistics
        # ----------------------------------------------------

        labels = m5.get(
            "structure_labels",
            []
        )

        if labels:

            recent_labels = labels[-4:]

            for label in recent_labels:

                name = label["label"]

                if name in stats[
                    "structure_counts"
                ]:

                    stats[
                        "structure_counts"
                    ][name] += 1

        if sr.get("snr") in (
            "SUPPORT",
            "RESISTANCE"
        ):

            stats[
                "zone_counts"
            ][sr["snr"]] += 1

        if sr.get("sbr") == "SBR":

            stats[
                "zone_counts"
            ]["SBR"] += 1

        # ----------------------------------------------------
        # Filter
        # ----------------------------------------------------

        if (
            score
            < BACKTEST_MIN_SCORE
        ):

            stats["rejected"] += 1

            i += 1

            continue

        if direction not in (
            "BUY",
            "SELL"
        ):

            stats["rejected"] += 1

            i += 1

            continue

        if (
            opportunity
            == "NO VALID SETUP"
        ):

            stats["rejected"] += 1

            i += 1

            continue

        # ----------------------------------------------------
        # Build plan using ONLY current candle history.
        # ----------------------------------------------------

        plan = create_trade_plan(

            direction,

            historical,

            m5

        )

        if not plan:

            stats["rejected"] += 1

            i += 1

            continue

        # ----------------------------------------------------
        # Qualified setup.
        # ----------------------------------------------------

        stats["qualified"] += 1

        stats[
            "signals_by_direction"
        ][direction] += 1

        # ----------------------------------------------------
        # Evaluate future candles.
        # ----------------------------------------------------

        result = evaluate_future_trade(

            direction,

            plan,

            candles,

            i + 1

        )

        outcome = result[
            "outcome"
        ]

        r_value = result[
            "r"
        ]

        stats["total_r"] += (
            r_value
        )

        if outcome == "LOSS":

            stats["losses"] += 1

            losing_streak += 1

            max_losing_streak = max(

                max_losing_streak,

                losing_streak

            )

        elif outcome in (
            "TP1",
            "TP2",
            "TP3"
        ):

            stats["wins"] += 1

            losing_streak = 0

        elif outcome == "TIMEOUT":

            stats["timeouts"] += 1

            losing_streak = 0

        elif outcome == "AMBIGUOUS":

            stats["ambiguous"] += 1

            losing_streak = 0

        if result["tp1"]:

            stats["tp1_hits"] += 1

        if result["tp2"]:

            stats["tp2_hits"] += 1

        if result["tp3"]:

            stats["tp3_hits"] += 1

        # ----------------------------------------------------
        # Save selected details.
        #
        # Keep only latest 100 records to avoid huge output.
        # ----------------------------------------------------

        stats["details"].append({

            "time":
                candles[i]["time"],

            "direction":
                direction,

            "score":
                score,

            "opportunity":
                opportunity,

            "entry":
                round(
                    plan["entry"],
                    2
                ),

            "sl":
                round(
                    plan["sl"],
                    2
                ),

            "tp1":
                round(
                    plan["tp1"],
                    2
                ),

            "tp2":
                round(
                    plan["tp2"],
                    2
                ),

            "tp3":
                round(
                    plan["tp3"],
                    2
                ),

            "risk_pips":
                round(
                    plan["risk_pips"],
                    1
                ),

            "rr":
                round(
                    plan["rr"],
                    2
                ),

            "outcome":
                outcome,

            "r":
                r_value

        })

        if len(
            stats["details"]
        ) > 100:

            stats["details"] = (
                stats["details"][-100:]
            )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # After a qualified setup, skip forward until the
        # trade has had time to resolve.
        #
        # This prevents every M5 candle during the same move
        # becoming a separate signal.
        # ----------------------------------------------------

        bars_used = max(
            result.get(
                "bars",
                1
            ),
            1
        )

        i += bars_used

    # ========================================================
    # FINAL STATISTICS
    # ========================================================

    stats[
        "max_losing_streak"
    ] = max_losing_streak

    completed = (
        stats["wins"]
        + stats["losses"]
    )

    if completed > 0:

        stats["win_rate"] = (

            stats["wins"]
            / completed
            * 100

        )

        stats["loss_rate"] = (

            stats["losses"]
            / completed
            * 100

        )

    if stats["qualified"] > 0:

        stats["average_r"] = (

            stats["total_r"]
            / stats["qualified"]

        )

    # --------------------------------------------------------
    # CONSOLE REPORT
    # --------------------------------------------------------

    print()
    print("BACKTEST RESULTS")
    print("-" * 60)

    print(
        f"Candles          : "
        f"{stats['candles']}"
    )

    print(
        f"Scanned          : "
        f"{stats['scanned']}"
    )

    print(
        f"Qualified        : "
        f"{stats['qualified']}"
    )

    print(
        f"Rejected         : "
        f"{stats['rejected']}"
    )

    print(
        f"Wins             : "
        f"{stats['wins']}"
    )

    print(
        f"Losses           : "
        f"{stats['losses']}"
    )

    print(
        f"Win Rate         : "
        f"{stats['win_rate']:.2f}%"
    )

    print(
        f"TP1 Hits         : "
        f"{stats['tp1_hits']}"
    )

    print(
        f"TP2 Hits         : "
        f"{stats['tp2_hits']}"
    )

    print(
        f"TP3 Hits         : "
        f"{stats['tp3_hits']}"
    )

    print(
        f"Timeouts         : "
        f"{stats['timeouts']}"
    )

    print(
        f"Ambiguous        : "
        f"{stats['ambiguous']}"
    )

    print(
        f"Total R          : "
        f"{stats['total_r']:.2f}R"
    )

    print(
        f"Average R        : "
        f"{stats['average_r']:.2f}R"
    )

    print(
        f"Max Losing Streak: "
        f"{stats['max_losing_streak']}"
    )

    print()
    print(
        "STRUCTURE DETECTION"
    )

    print(
        f"HH: "
        f"{stats['structure_counts']['HH']}"
    )

    print(
        f"HL: "
        f"{stats['structure_counts']['HL']}"
    )

    print(
        f"LH: "
        f"{stats['structure_counts']['LH']}"
    )

    print(
        f"LL: "
        f"{stats['structure_counts']['LL']}"
    )

    print()
    print(
        "ZONE DETECTION"
    )

    print(
        f"Support    : "
        f"{stats['zone_counts']['SUPPORT']}"
    )

    print(
        f"Resistance : "
        f"{stats['zone_counts']['RESISTANCE']}"
    )

    print(
        f"SBR        : "
        f"{stats['zone_counts']['SBR']}"
    )

    print("=" * 60)

    return stats


# ============================================================
# HISTORICAL HTF FILTER
# ============================================================

def remove_incomplete_historical(
    candles,
    latest_m5_time,
    minutes
):

    if not candles:

        return []

    latest_dt = parse_time(
        latest_m5_time
    )

    result = []

    for candle in candles:

        try:

            start = parse_time(
                candle["time"]
            )

            end = (
                start
                + timedelta(
                    minutes=minutes
                )
            )

            # A HTF candle is usable only when
            # its closing time is <= current M5 candle.
            if end <= latest_dt:

                result.append(
                    candle
                )

        except Exception:

            continue

    return result


# ============================================================
# STATE
# ============================================================

def load_state():

    default_state = {

        "last_alert_key":
            "",

        "last_direction":
            "",

        "last_score":
            0,

        "last_entry":
            0,

        "last_alert_time":
            "",

        "last_opportunity":
            ""

    }

    if not os.path.exists(
        STATE_FILE
    ):

        return default_state

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        if not isinstance(
            data,
            dict
        ):

            return default_state

        for key, value in (
            default_state.items()
        ):

            if key not in data:

                data[key] = value

        return data

    except Exception as error:

        print(
            "State load warning:",
            error
        )

        return default_state


def save_state(
    state
):

    try:

        directory = os.path.dirname(
            STATE_FILE
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True
            )

        temp_file = (
            STATE_FILE
            + ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                state,
                file,
                indent=2
            )

        os.replace(
            temp_file,
            STATE_FILE
        )

        print(
            "Engine state saved."
        )

    except Exception as error:

        print(
            "State save warning:",
            error
        )


# ============================================================
# ANTI-SPAM
# ============================================================

def build_alert_key(
    direction,
    opportunity,
    entry,
    score
):

    entry_bucket = round(
        entry / 0.10
    )

    score_bucket = (
        score // 5
    )

    return (
        f"{direction}|"
        f"{opportunity}|"
        f"{entry_bucket}|"
        f"{score_bucket}"
    )


def should_send_alert(
    state,
    alert_key
):

    previous_key = (
        state.get(
            "last_alert_key",
            ""
        )
    )

    if previous_key == alert_key:

        print(
            "Duplicate opportunity."
        )

        print(
            "Telegram alert skipped."
        )

        return False

    return True


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram token missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID missing."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True

    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

    except Exception as error:

        print(
            "Telegram request error:",
            error
        )

        return False

    if response.status_code != 200:

        print(
            "Telegram error:",
            response.text[:500]
        )

        return False

    print(
        "Telegram notification sent."
    )

    return True


# ============================================================
# TELEGRAM FORMAT
# ============================================================

def format_alert(
    score,
    opportunity,
    session,
    latest_price,
    plan,
    reasons,
    pd,
    h4,
    h1,
    m5,
    sweep,
    momentum,
    confirmation,
    sr,
    zones
):

    direction = plan[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "BUY"
        else "🔴"
    )

    low_range, high_range = (
        plan["estimated_range"]
    )

    reason_text = "\n".join(

        f"• {reason}"

        for reason in reasons

    )

    structure_labels = (
        m5.get(
            "structure_labels",
            []
        )[-6:]
    )

    structure_text = " → ".join(

        x["label"]

        for x in structure_labels

    )

    message = f"""
<b>👑 BOSQUE FOREX AI v3</b>

{emoji} <b>XAUUSD {direction}</b>

<b>🔥 VALID LOW-RISK OPPORTUNITY</b>

<b>Opportunity:</b>
{opportunity}

<b>Score:</b>
🔥 {score}/100

━━━━━━━━━━━━━━━━━━

<b>📊 MARKET INTELLIGENCE</b>

Session:
<b>{session}</b>

Price:
<b>{latest_price:.2f}</b>

H4:
<b>{h4["trend"]}</b>

H1:
<b>{h1["trend"]}</b>

M5:
<b>{m5["structure"]}</b>

Structure:
<b>{structure_text or "N/A"}</b>

PD Zone:
<b>{pd["zone"]}</b>

S/R:
<b>{sr.get("snr", "NONE")}</b>

SBR:
<b>{sr.get("sbr", "NONE")}</b>

Liquidity:
<b>{sweep}</b>

Momentum:
<b>{momentum}</b>

Confirmation:
<b>{confirmation}</b>

━━━━━━━━━━━━━━━━━━

<b>🎯 LOW-RISK TRADE PLAN</b>

Entry:
<b>{plan["entry"]:.2f}</b>

SL:
<b>{plan["sl"]:.2f}</b>

TP1:
<b>{plan["tp1"]:.2f}</b>

TP2:
<b>{plan["tp2"]:.2f}</b>

TP3:
<b>{plan["tp3"]:.2f}</b>

━━━━━━━━━━━━━━━━━━

<b>📏 PIP ANALYSIS</b>

<b>100 points = 10 pips</b>

Risk:
<b>{plan["pips_to_sl"]:.0f} pips</b>

Risk:
<b>{plan["pips_to_sl"] * POINTS_PER_PIP:.0f} points</b>

TP1:
<b>+{plan["pips_to_tp1"]:.0f} pips</b>

TP1:
<b>+{plan["pips_to_tp1"] * POINTS_PER_PIP:.0f} points</b>

TP2:
<b>+{plan["pips_to_tp2"]:.0f} pips</b>

TP3:
<b>+{plan["pips_to_tp3"]:.0f} pips</b>

Potential:
<b>{low_range:.0f} - {high_range:.0f} pips</b>

R:R:
<b>1:{plan["rr"]:.2f}</b>

━━━━━━━━━━━━━━━━━━

<b>🧠 WHY?</b>

{reason_text or "Multi-factor confirmation"}

━━━━━━━━━━━━━━━━━━

<b>FILTERS PASSED</b>

✅ Score 70+
✅ Risk 35-60 pips
✅ TP1 120+ pips
✅ R:R 1:2+
✅ Closed M5 candle
✅ MTF confirmation
✅ Structure analysis

━━━━━━━━━━━━━━━━━━

⚠️ Educational / decision-support engine.
Confirm the setup manually before entry.

<b>👑 Bosque Forex AI</b>
"""

    return message.strip()


# ============================================================
# LIVE ANALYSIS
# ============================================================

def run_live_analysis(
    m5_candles
):

    h1_candles = aggregate_candles(
        m5_candles,
        60
    )

    h4_candles = aggregate_candles(
        m5_candles,
        240
    )

    # --------------------------------------------------------
    # Only use fully closed H1/H4 candles.
    # --------------------------------------------------------

    latest_m5_time = (
        m5_candles[-1]["time"]
    )

    h1_candles = (
        remove_incomplete_historical(
            h1_candles,
            latest_m5_time,
            60
        )
    )

    h4_candles = (
        remove_incomplete_historical(
            h4_candles,
            latest_m5_time,
            240
        )
    )

    print(
        f"H1 candles built: "
        f"{len(h1_candles)}"
    )

    print(
        f"H4 candles built: "
        f"{len(h4_candles)}"
    )

    if len(h1_candles) < 10:

        raise RuntimeError(
            "Not enough closed H1 data."
        )

    if len(h4_candles) < 5:

        raise RuntimeError(
            "Not enough closed H4 data."
        )

    h4 = analyze_structure(
        h4_candles
    )

    h1 = analyze_structure(
        h1_candles
    )

    m5 = analyze_structure(
        m5_candles
    )

    pd = calculate_pd_zone(
        m5_candles
    )

    sweep = detect_liquidity_sweep(
        m5_candles
    )

    momentum = calculate_momentum(
        m5_candles
    )

    confirmation = (
        candle_confirmation(
            m5_candles
        )
    )

    sr = detect_support_resistance(
        m5_candles
    )

    zones = detect_supply_demand(
        m5_candles
    )

    scoring = calculate_score(

        h4,

        h1,

        m5,

        pd,

        sweep,

        momentum,

        confirmation,

        sr

    )

    direction = scoring[
        "direction"
    ]

    score = scoring[
        "score"
    ]

    reasons = scoring[
        "reasons"
    ]

    opportunity = (
        determine_opportunity(

            h4,

            h1,

            m5,

            pd,

            sweep,

            confirmation,

            sr,

            zones

        )
    )

    latest_price = (
        m5_candles[-1]["close"]
    )

    session = get_session()

    return {

        "h4":
            h4,

        "h1":
            h1,

        "m5":
            m5,

        "pd":
            pd,

        "sweep":
            sweep,

        "momentum":
            momentum,

        "confirmation":
            confirmation,

        "sr":
            sr,

        "zones":
            zones,

        "scoring":
            scoring,

        "direction":
            direction,

        "score":
            score,

        "reasons":
            reasons,

        "opportunity":
            opportunity,

        "latest_price":
            latest_price,

        "session":
            session

    }


# ============================================================
# MAIN ENGINE
# ============================================================

def run_engine():

    print("=" * 60)

    print(
        "👑 BOSQUE FOREX AI v3"
    )

    print(
        "LIVE + BACKTEST"
    )

    print(
        "LOW-RISK / HIGH-REWARD"
    )

    print("=" * 60)

    validate_environment()

    print()
    print(
        "PIP CONVERSION:"
    )

    print(
        f"1 pip = {PIP_SIZE:.2f} price"
    )

    print(
        f"1 pip = {POINTS_PER_PIP:.0f} points"
    )

    print(
        "100 points = 10 pips"
    )

    # ========================================================
    # ONE API REQUEST
    # ========================================================

    m5_candles = get_m5_data()

    # ========================================================
    # CLOSED CANDLE
    # ========================================================

    m5_candles = (
        remove_incomplete_candle(
            m5_candles
        )
    )

    if len(m5_candles) < 50:

        raise RuntimeError(
            "Not enough closed M5 candles."
        )

    # ========================================================
    # BACKTEST
    # ========================================================
    #
    # Run BEFORE live analysis.
    #
    # The backtest uses historical prefixes of the same
    # dataset and never uses candles that were not available
    # at the simulated entry point.
    # ========================================================

    backtest = run_backtest(
        m5_candles
    )

    # ========================================================
    # LIVE
    # ========================================================

    live = run_live_analysis(
        m5_candles
    )

    h4 = live["h4"]
    h1 = live["h1"]
    m5 = live["m5"]
    pd = live["pd"]
    sweep = live["sweep"]
    momentum = live["momentum"]
    confirmation = live[
        "confirmation"
    ]
    sr = live["sr"]
    zones = live["zones"]

    direction = live[
        "direction"
    ]

    score = live[
        "score"
    ]

    reasons = live[
        "reasons"
    ]

    opportunity = live[
        "opportunity"
    ]

    latest_price = live[
        "latest_price"
    ]

    session = live[
        "session"
    ]

    # ========================================================
    # CONSOLE
    # ========================================================

    print()
    print("=" * 60)
    print(
        "LIVE MARKET ANALYSIS"
    )
    print("=" * 60)

    print(
        f"Session       : {session}"
    )

    print(
        f"Latest Price  : "
        f"{latest_price:.2f}"
    )

    print(
        f"H4 Trend      : "
        f"{h4['trend']}"
    )

    print(
        f"H4 Structure  : "
        f"{h4['structure']}"
    )

    print(
        f"H1 Trend      : "
        f"{h1['trend']}"
    )

    print(
        f"H1 Structure  : "
        f"{h1['structure']}"
    )

    print(
        f"M5 Trend      : "
        f"{m5['trend']}"
    )

    print(
        f"M5 Structure  : "
        f"{m5['structure']}"
    )

    labels = m5.get(
        "structure_labels",
        []
    )

    if labels:

        print(
            "Structure     : "
            + " -> ".join(
                x["label"]
                for x in labels[-6:]
            )
        )

    print(
        f"PD Zone       : "
        f"{pd['zone']}"
    )

    print(
        f"S/R           : "
        f"{sr['snr']}"
    )

    print(
        f"SBR           : "
        f"{sr['sbr']}"
    )

    print(
        f"Liquidity     : "
        f"{sweep}"
    )

    print(
        f"Momentum      : "
        f"{momentum}"
    )

    print(
        f"Confirmation  : "
        f"{confirmation}"
    )

    print(
        f"Opportunity   : "
        f"{opportunity}"
    )

    print(
        f"Score         : "
        f"{score}/100"
    )

    # ========================================================
    # SCORE FILTER
    # ========================================================

    if score < MIN_SCORE:

        print()
        print(
            "⏳ Score below 70."
        )

        print(
            "No Telegram alert."
        )

        return

    if direction not in (
        "BUY",
        "SELL"
    ):

        print()
        print(
            "⏳ Direction invalid."
        )

        return

    if (
        opportunity
        == "NO VALID SETUP"
    ):

        print()
        print(
            "⏳ No valid opportunity type."
        )

        return

    # ========================================================
    # TRADE PLAN
    # ========================================================

    plan = create_trade_plan(

        direction,

        m5_candles,

        m5

    )

    if not plan:

        print()
        print(
            "⛔ Setup rejected."
        )

        print(
            f"Risk must be "
            f"{MIN_RISK_PIPS}-"
            f"{MAX_RISK_PIPS} pips."
        )

        print(
            f"TP1 must be "
            f"{MIN_TP1_PIPS}+ pips."
        )

        print(
            f"R:R must be "
            f"1:{MIN_RR:.0f}+."
        )

        print(
            "Telegram notification skipped."
        )

        return

    # ========================================================
    # FINAL VALIDATION
    # ========================================================

    risk_pips = (
        plan["risk_pips"]
    )

    tp1_pips = (
        plan["pips_to_tp1"]
    )

    rr = (
        plan["rr"]
    )

    if not (
        MIN_RISK_PIPS
        <= risk_pips
        <= MAX_RISK_PIPS
    ):

        print(
            "⛔ Risk filter failed."
        )

        return

    if tp1_pips < MIN_TP1_PIPS:

        print(
            "⛔ TP1 filter failed."
        )

        return

    if rr < MIN_RR:

        print(
            "⛔ R:R filter failed."
        )

        return

    # ========================================================
    # VALID OPPORTUNITY
    # ========================================================

    print()
    print("=" * 60)
    print(
        "🔥🔥🔥 VALID OPPORTUNITY"
    )
    print("=" * 60)

    print(
        f"Direction : {direction}"
    )

    print(
        f"Score     : {score}/100"
    )

    print(
        f"Opportunity: {opportunity}"
    )

    print(
        f"Entry     : "
        f"{plan['entry']:.2f}"
    )

    print(
        f"SL        : "
        f"{plan['sl']:.2f}"
    )

    print(
        f"Risk      : "
        f"{risk_pips:.0f} pips"
    )

    print(
        f"Risk      : "
        f"{risk_pips * POINTS_PER_PIP:.0f} points"
    )

    print(
        f"TP1       : "
        f"{plan['tp1']:.2f}"
    )

    print(
        f"TP1       : "
        f"{tp1_pips:.0f} pips"
    )

    print(
        f"TP2       : "
        f"{plan['tp2']:.2f}"
    )

    print(
        f"TP3       : "
        f"{plan['tp3']:.2f}"
    )

    print(
        f"R:R       : "
        f"1:{rr:.2f}"
    )

    print(
        f"Potential : "
        f"{plan['estimated_range'][0]:.0f}"
        f"-"
        f"{plan['estimated_range'][1]:.0f}"
        f" pips"
    )

    # ========================================================
    # STATE
    # ========================================================

    state = load_state()

    alert_key = build_alert_key(

        direction,

        opportunity,

        plan["entry"],

        score

    )

    if not should_send_alert(
        state,
        alert_key
    ):

        return

    # ========================================================
    # TELEGRAM
    # ========================================================

    alert = format_alert(

        score,

        opportunity,

        session,

        latest_price,

        plan,

        reasons,

        pd,

        h4,

        h1,

        m5,

        sweep,

        momentum,

        confirmation,

        sr,

        zones

    )

    sent = send_telegram(
        alert
    )

    # ========================================================
    # SAVE STATE ONLY AFTER SUCCESS
    # ========================================================

    if sent:

        state[
            "last_alert_key"
        ] = alert_key

        state[
            "last_direction"
        ] = direction

        state[
            "last_score"
        ] = score

        state[
            "last_entry"
        ] = plan["entry"]

        state[
            "last_alert_time"
        ] = malaysia_now().isoformat()

        state[
            "last_opportunity"
        ] = opportunity

        save_state(
            state
        )

    else:

        print(
            "Telegram failed."
        )

        print(
            "State was NOT updated."
        )


# ============================================================
# ENTRY POINT
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