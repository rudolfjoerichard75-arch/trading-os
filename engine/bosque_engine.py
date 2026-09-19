import os
import json
import requests
from datetime import datetime, timezone, timedelta


# ============================================================
# BOSQUE FOREX AI
# SCALPING ENGINE V3
# XAU/USD
#
# TIMEFRAME HIERARCHY
#
# H1  = MARKET BIAS / MARKET CONDITION
# M15 = SETUP / LOCATION
# M5  = ENTRY CONFIRMATION
#
# PIP CONVENTION
#
# 1 pip  = 0.10 XAUUSD price
# 10 pips = 1.00 price
# 60 pips = 6.00 price
#
# RISK
# 35 - 60 pips
#
# TP
# TP1 = 60 pips minimum
# TP2 = 2R minimum
# TP3 = 3R when structure allows
#
# SCORE
# 70+ required
#
# IMPORTANT
# Score alone does NOT create a trade.
# Hard gates must also pass.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAU/USD"

TWELVEDATA_URL = (
    "https://api.twelvedata.com/time_series"
)

M15_INTERVAL = "15min"

# 500 M5 candles ≈ 41 hours
OUTPUT_SIZE = 500

MIN_SCORE = 70

REQUEST_TIMEOUT = 20

MALAYSIA_TZ = timezone(
    timedelta(hours=8)
)


# ============================================================
# RISK / REWARD
# ============================================================

MIN_RISK_PIPS = 35
MAX_RISK_PIPS = 60

TP1_PIPS = 60

MIN_RR = 2.0


# ============================================================
# XAUUSD PIP CONVENTION
# ============================================================

# 1 pip = 0.10 price

PIP_SIZE = 0.10


def price_to_pips(distance):
    return distance / PIP_SIZE


def pips_to_price(pips):
    return pips * PIP_SIZE


# ============================================================
# STATE FILE
# ============================================================

STATE_FILE = os.path.join(
    os.path.dirname(__file__),
    "state.json"
)


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


def parse_time(value):

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


# ============================================================
# TWELVE DATA
# ============================================================

def get_m5_data():

    params = {

        "symbol": SYMBOL,

        "interval": "5min",

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

    values = data.get("values")

    if not values:

        raise RuntimeError(
            "No OHLC data received."
        )

    candles = []

    for item in values:

        try:

            candles.append({

                "time": item["datetime"],

                "open": float(
                    item["open"]
                ),

                "high": float(
                    item["high"]
                ),

                "low": float(
                    item["low"]
                ),

                "close": float(
                    item["close"]
                )

            })

        except Exception:

            continue

    if len(candles) < 100:

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
# CLOSED CANDLE FILTER
# ============================================================

def remove_incomplete_candle(candles):

    if len(candles) < 2:

        return candles

    latest = candles[-1]

    try:

        candle_time = parse_time(
            latest["time"]
        )

        if candle_time.tzinfo is None:

            candle_time = candle_time.replace(
                tzinfo=timezone.utc
            )

        now_utc = datetime.now(
            timezone.utc
        )

        elapsed = (
            now_utc - candle_time
        ).total_seconds()

        # Twelve Data normally returns candle
        # start time. Keep candle only when
        # at least 5 minutes have passed.

        if elapsed < 300:

            print(
                "Removing incomplete M5 candle."
            )

            return candles[:-1]

    except Exception as error:

        print(
            "Closed candle check warning:",
            error
        )

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

        if minutes == 15:

            minute_block = (
                dt.minute // 15
            ) * 15

            bucket = dt.replace(
                minute=minute_block,
                second=0,
                microsecond=0
            )

        elif minutes == 60:

            bucket = dt.replace(
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

            "open": group[0]["open"],

            "high": max(
                c["high"]
                for c in group
            ),

            "low": min(
                c["low"]
                for c in group
            ),

            "close": group[-1]["close"]

        })

    return result


# ============================================================
# REMOVE INCOMPLETE HIGHER TIMEFRAME CANDLES
# ============================================================

def remove_incomplete_higher_tf(
    candles,
    minutes
):

    if len(candles) < 2:

        return candles

    latest = candles[-1]

    try:

        candle_time = parse_time(
            latest["time"]
        )

        if candle_time.tzinfo is None:

            candle_time = candle_time.replace(
                tzinfo=timezone.utc
            )

        now_utc = datetime.now(
            timezone.utc
        )

        elapsed = (
            now_utc - candle_time
        ).total_seconds()

        required_seconds = (
            minutes * 60
        )

        if elapsed < required_seconds:

            print(
                f"Removing incomplete "
                f"{minutes}m candle."
            )

            return candles[:-1]

    except Exception:

        pass

    return candles


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
                    current["time"]

            })

        if low_ok:

            swing_lows.append({

                "price":
                    current["low"],

                "time":
                    current["time"]

            })

    return (
        swing_highs,
        swing_lows
    )


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
# MARKET STRUCTURE
# ============================================================

def analyze_structure(
    candles
):

    if len(candles) < 20:

        return {

            "structure": "RANGE",

            "trend": "NEUTRAL",

            "last_high": None,

            "last_low": None,

            "swing_highs": [],

            "swing_lows": []

        }

    swing_highs, swing_lows = (
        detect_swings(candles)
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

    trend = "NEUTRAL"

    # --------------------------------------------------------
    # BREAK OF STRUCTURE
    # --------------------------------------------------------

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

    else:

        # Compare recent swing sequence
        if (
            len(swing_highs) >= 2
            and len(swing_lows) >= 2
        ):

            previous_high = (
                swing_highs[-2]["price"]
            )

            current_high = (
                swing_highs[-1]["price"]
            )

            previous_low = (
                swing_lows[-2]["price"]
            )

            current_low = (
                swing_lows[-1]["price"]
            )

            if (
                current_high
                > previous_high
                and current_low
                > previous_low
            ):

                trend = "BULLISH"

            elif (
                current_high
                < previous_high
                and current_low
                < previous_low
            ):

                trend = "BEARISH"

            else:

                trend = "RANGE"

        else:

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

                trend = "RANGE"

    return {

        "structure": structure,

        "trend": trend,

        "last_high": last_high,

        "last_low": last_low,

        "swing_highs": swing_highs,

        "swing_lows": swing_lows

    }


# ============================================================
# RANGE INFORMATION
# ============================================================

def calculate_range(
    candles,
    lookback=30
):

    recent = candles[-lookback:]

    high = max(
        c["high"]
        for c in recent
    )

    low = min(
        c["low"]
        for c in recent
    )

    width = high - low

    if width <= 0:

        return {

            "high": high,

            "low": low,

            "mid": high,

            "position": "MIDDLE",

            "width": width

        }

    price = candles[-1]["close"]

    position_ratio = (
        price - low
    ) / width

    if position_ratio <= 0.25:

        position = "LOW_EXTREME"

    elif position_ratio >= 0.75:

        position = "HIGH_EXTREME"

    else:

        position = "MIDDLE"

    return {

        "high": high,

        "low": low,

        "mid": (
            high + low
        ) / 2,

        "position": position,

        "width": width

    }


# ============================================================
# PREMIUM / DISCOUNT
# ============================================================

def calculate_pd_zone(
    candles,
    lookback=50
):

    recent = candles[-lookback:]

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

        "high": high,

        "low": low,

        "equilibrium":
            equilibrium,

        "zone": zone

    }


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

def detect_liquidity_sweep(
    candles,
    lookback=6
):

    if len(candles) < (
        lookback + 2
    ):

        return "NONE"

    previous = candles[
        -(lookback + 2):-2
    ]

    previous_high = max(
        c["high"]
        for c in previous
    )

    previous_low = min(
        c["low"]
        for c in previous
    )

    latest = candles[-1]

    # Price takes previous low
    # then closes back above it
    if (
        latest["low"]
        < previous_low

        and latest["close"]
        > previous_low
    ):

        return "SELL_SIDE_SWEEP"

    # Price takes previous high
    # then closes back below it
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

    body = abs(
        latest["close"]
        - latest["open"]
    )

    candle_range = (
        latest["high"]
        - latest["low"]
    )

    if candle_range <= 0:

        return "NONE"

    body_ratio = (
        body / candle_range
    )

    # Strong bullish candle
    if (
        latest["close"]
        > latest["open"]

        and body_ratio >= 0.55

        and latest["close"]
        > previous["high"]
    ):

        return "BULLISH"

    # Strong bearish candle
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
# M5 BOS / CHOCH
# ============================================================

def detect_m5_confirmation(
    candles
):

    if len(candles) < 15:

        return "NONE"

    swings = detect_swings(
        candles,
        left=2,
        right=2
    )

    swing_highs, swing_lows = swings

    latest = candles[-1]

    if swing_highs:

        last_high = (
            swing_highs[-1]["price"]
        )

        if latest["close"] > last_high:

            return "BULLISH BOS"

    if swing_lows:

        last_low = (
            swing_lows[-1]["price"]
        )

        if latest["close"] < last_low:

            return "BEARISH BOS"

    return "NONE"


# ============================================================
# SESSION
# ============================================================

def get_session():

    hour = malaysia_now().hour

    # New York
    if 20 <= hour < 24:

        return "NEW YORK"

    # Asian
    if 0 <= hour < 8:

        return "ASIAN"

    # London
    if 8 <= hour < 16:

        return "LONDON"

    # London / New York overlap
    if 16 <= hour < 20:

        return "LONDON / NEW YORK"

    return "ASIAN"


# ============================================================
# M15 SETUP DETECTION
# ============================================================

def detect_m15_setup(
    h1,
    m15,
    pd,
    m15_range,
    sweep,
    m15_confirmation
):

    direction = "NONE"

    setup = "NO VALID SETUP"

    # --------------------------------------------------------
    # H1 BULLISH
    # --------------------------------------------------------

    if h1["trend"] == "BULLISH":

        # Pullback into discount
        if (
            pd["zone"] == "DISCOUNT"
            and m15["trend"]
            in ["BULLISH", "RANGE"]
        ):

            direction = "BUY"

            setup = "BUY PULLBACK"

        # Liquidity sweep
        elif (
            sweep == "SELL_SIDE_SWEEP"
        ):

            direction = "BUY"

            setup = (
                "BUY LIQUIDITY SWEEP"
            )

        # Breakout
        elif (
            m15["structure"]
            == "BULLISH BOS"
        ):

            direction = "BUY"

            setup = (
                "BUY BREAKOUT RETEST"
            )

    # --------------------------------------------------------
    # H1 BEARISH
    # --------------------------------------------------------

    elif h1["trend"] == "BEARISH":

        # Pullback into premium
        if (
            pd["zone"] == "PREMIUM"
            and m15["trend"]
            in ["BEARISH", "RANGE"]
        ):

            direction = "SELL"

            setup = "SELL PULLBACK"

        # Liquidity sweep
        elif (
            sweep == "BUY_SIDE_SWEEP"
        ):

            direction = "SELL"

            setup = (
                "SELL LIQUIDITY SWEEP"
            )

        # Breakout
        elif (
            m15["structure"]
            == "BEARISH BOS"
        ):

            direction = "SELL"

            setup = (
                "SELL BREAKOUT RETEST"
            )

    # --------------------------------------------------------
    # H1 RANGE
    # --------------------------------------------------------

    elif h1["trend"] in [
        "RANGE",
        "NEUTRAL"
    ]:

        # BUY range reversal
        if (
            m15_range["position"]
            == "LOW_EXTREME"

            and sweep
            == "SELL_SIDE_SWEEP"

            and m15_confirmation
            == "BULLISH"
        ):

            direction = "BUY"

            setup = (
                "BUY RANGE REVERSAL"
            )

        # SELL range reversal
        elif (
            m15_range["position"]
            == "HIGH_EXTREME"

            and sweep
            == "BUY_SIDE_SWEEP"

            and m15_confirmation
            == "BEARISH"
        ):

            direction = "SELL"

            setup = (
                "SELL RANGE REVERSAL"
            )

    return {
        "direction": direction,
        "setup": setup
    }


# ============================================================
# SCORE ENGINE V3
# ============================================================

def calculate_score(
    direction,
    h1,
    m15,
    m5_confirmation,
    pd,
    sweep,
    momentum,
    candle_confirm,
    setup,
    session
):

    buy_score = 0
    sell_score = 0

    buy_reasons = []
    sell_reasons = []

    # ========================================================
    # H1 CONTEXT — 20
    # ========================================================

    if h1["trend"] == "BULLISH":

        buy_score += 15

        buy_reasons.append(
            "H1 bullish market bias"
        )

    elif h1["trend"] == "BEARISH":

        sell_score += 15

        sell_reasons.append(
            "H1 bearish market bias"
        )

    elif h1["trend"] in [
        "RANGE",
        "NEUTRAL"
    ]:

        # Range gets smaller context score.
        # Direction must come from reversal.
        if direction == "BUY":

            buy_score += 10

            buy_reasons.append(
                "H1 range condition"
            )

        elif direction == "SELL":

            sell_score += 10

            sell_reasons.append(
                "H1 range condition"
            )

    if h1["structure"] == "BULLISH BOS":

        buy_score += 5

        buy_reasons.append(
            "Fresh H1 bullish BOS"
        )

    elif h1["structure"] == "BEARISH BOS":

        sell_score += 5

        sell_reasons.append(
            "Fresh H1 bearish BOS"
        )

    # ========================================================
    # M15 SETUP — 30
    # ========================================================

    if setup != "NO VALID SETUP":

        if direction == "BUY":

            buy_score += 10

            buy_reasons.append(
                "Valid M15 setup"
            )

        elif direction == "SELL":

            sell_score += 10

            sell_reasons.append(
                "Valid M15 setup"
            )

    if sweep == "SELL_SIDE_SWEEP":

        buy_score += 10

        buy_reasons.append(
            "M15 sell-side liquidity sweep"
        )

    elif sweep == "BUY_SIDE_SWEEP":

        sell_score += 10

        sell_reasons.append(
            "M15 buy-side liquidity sweep"
        )

    if m15["structure"] == "BULLISH BOS":

        buy_score += 10

        buy_reasons.append(
            "M15 bullish structure confirmation"
        )

    elif m15["structure"] == "BEARISH BOS":

        sell_score += 10

        sell_reasons.append(
            "M15 bearish structure confirmation"
        )

    # ========================================================
    # M5 ENTRY — 35
    # ========================================================

    if m5_confirmation == "BULLISH BOS":

        buy_score += 15

        buy_reasons.append(
            "M5 bullish BOS / CHOCH"
        )

    elif m5_confirmation == "BEARISH BOS":

        sell_score += 15

        sell_reasons.append(
            "M5 bearish BOS / CHOCH"
        )

    if candle_confirm == "BULLISH":

        buy_score += 10

        buy_reasons.append(
            "Bullish M5 candle confirmation"
        )

    elif candle_confirm == "BEARISH":

        sell_score += 10

        sell_reasons.append(
            "Bearish M5 candle confirmation"
        )

    if momentum == "BULLISH":

        buy_score += 10

        buy_reasons.append(
            "Bullish M5 momentum"
        )

    elif momentum == "BEARISH":

        sell_score += 10

        sell_reasons.append(
            "Bearish M5 momentum"
        )

    # ========================================================
    # LOCATION / CONDITION — 15
    # ========================================================

    if pd["zone"] == "DISCOUNT":

        buy_score += 5

        buy_reasons.append(
            "Price in discount"
        )

    elif pd["zone"] == "PREMIUM":

        sell_score += 5

        sell_reasons.append(
            "Price in premium"
        )

    if session in [
        "LONDON",
        "LONDON / NEW YORK",
        "NEW YORK"
    ]:

        if direction == "BUY":

            buy_score += 5

            buy_reasons.append(
                "Active trading session"
            )

        elif direction == "SELL":

            sell_score += 5

            sell_reasons.append(
                "Active trading session"
            )

    # ========================================================
    # FINAL
    # ========================================================

    if direction == "BUY":

        return {
            "direction": "BUY",
            "score": min(
                buy_score,
                100
            ),
            "reasons": buy_reasons
        }

    if direction == "SELL":

        return {
            "direction": "SELL",
            "score": min(
                sell_score,
                100
            ),
            "reasons": sell_reasons
        }

    # Fallback comparison
    if buy_score > sell_score:

        return {
            "direction": "BUY",
            "score": min(
                buy_score,
                100
            ),
            "reasons": buy_reasons
        }

    if sell_score > buy_score:

        return {
            "direction": "SELL",
            "score": min(
                sell_score,
                100
            ),
            "reasons": sell_reasons
        }

    return {
        "direction": "WAIT",
        "score": 0,
        "reasons": []
    }


# ============================================================
# STRUCTURAL SL
# ============================================================

def find_valid_stop(
    direction,
    entry,
    m5_analysis,
    atr
):

    candidates = []

    swing_highs = m5_analysis.get(
        "swing_highs",
        []
    )

    swing_lows = m5_analysis.get(
        "swing_lows",
        []
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if direction == "BUY":

        if swing_lows:

            last_low = (
                swing_lows[-1]["price"]
            )

            candidates.append(
                last_low
            )

        # ATR fallback
        candidates.append(
            entry - (
                atr * 1.2
            )
        )

        valid = []

        for sl in candidates:

            if sl >= entry:

                continue

            risk_pips = price_to_pips(
                entry - sl
            )

            if (
                MIN_RISK_PIPS
                <= risk_pips
                <= MAX_RISK_PIPS
            ):

                valid.append(
                    (
                        risk_pips,
                        sl
                    )
                )

        if not valid:

            return None

        # Prefer structural/tighter valid risk
        valid.sort(
            key=lambda x: x[0]
        )

        return valid[0]

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if direction == "SELL":

        if swing_highs:

            last_high = (
                swing_highs[-1]["price"]
            )

            candidates.append(
                last_high
            )

        # ATR fallback
        candidates.append(
            entry + (
                atr * 1.2
            )
        )

        valid = []

        for sl in candidates:

            if sl <= entry:

                continue

            risk_pips = price_to_pips(
                sl - entry
            )

            if (
                MIN_RISK_PIPS
                <= risk_pips
                <= MAX_RISK_PIPS
            ):

                valid.append(
                    (
                        risk_pips,
                        sl
                    )
                )

        if not valid:

            return None

        valid.sort(
            key=lambda x: x[0]
        )

        return valid[0]

    return None


# ============================================================
# FIND STRUCTURAL TARGETS
# ============================================================

def find_opposing_levels(
    direction,
    entry,
    m15,
    h1
):

    levels = []

    if direction == "BUY":

        # M15 swing highs above entry
        for swing in m15.get(
            "swing_highs",
            []
        ):

            price = swing["price"]

            if price > entry:

                levels.append(
                    price
                )

        # H1 swing highs above entry
        for swing in h1.get(
            "swing_highs",
            []
        ):

            price = swing["price"]

            if price > entry:

                levels.append(
                    price
                )

    elif direction == "SELL":

        # M15 swing lows below entry
        for swing in m15.get(
            "swing_lows",
            []
        ):

            price = swing["price"]

            if price < entry:

                levels.append(
                    price
                )

        # H1 swing lows below entry
        for swing in h1.get(
            "swing_lows",
            []
        ):

            price = swing["price"]

            if price < entry:

                levels.append(
                    price
                )

    return sorted(
        set(levels)
    )


# ============================================================
# TRADE PLAN
# ============================================================

def create_trade_plan(
    direction,
    candles,
    m15_analysis,
    h1_analysis
):

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

        atr = sum(
            recent_ranges
        ) / len(
            recent_ranges
        )

    stop_result = find_valid_stop(
        direction,
        entry,
        {
            "swing_highs":
                m15_analysis.get(
                    "swing_highs",
                    []
                ),

            "swing_lows":
                m15_analysis.get(
                    "swing_lows",
                    []
                )
        },
        atr
    )

    if not stop_result:

        return None

    risk_pips, sl = stop_result

    risk_price = pips_to_price(
        risk_pips
    )

    # ========================================================
    # BASE TARGETS
    # ========================================================

    tp1_pips = max(
        TP1_PIPS,
        risk_pips * MIN_RR
    )

    tp2_pips = max(
        tp1_pips,
        risk_pips * 2.0
    )

    tp3_pips = max(
        tp2_pips,
        risk_pips * 3.0
    )

    # ========================================================
    # STRUCTURE / LIQUIDITY ROOM
    # ========================================================

    opposing_levels = (
        find_opposing_levels(
            direction,
            entry,
            m15_analysis,
            h1_analysis
        )
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if direction == "BUY":

        tp1 = (
            entry
            + pips_to_price(
                tp1_pips
            )
        )

        tp2 = (
            entry
            + pips_to_price(
                tp2_pips
            )
        )

        tp3 = (
            entry
            + pips_to_price(
                tp3_pips
            )
        )

        # Find first major resistance
        # beyond entry.
        blockers = [
            level
            for level in opposing_levels
            if level > entry
        ]

        if blockers:

            nearest = min(
                blockers
            )

            nearest_pips = price_to_pips(
                nearest - entry
            )

            # If resistance is before TP1,
            # trade does not have enough room.
            if nearest_pips < tp1_pips:

                return None

            # Adjust TP3 if resistance is
            # between TP2 and TP3.
            if (
                nearest > tp2
                and nearest < tp3
            ):

                tp3 = (
                    nearest
                    - pips_to_price(
                        10
                    )
                )

        tp1_pips_final = price_to_pips(
            tp1 - entry
        )

        tp2_pips_final = price_to_pips(
            tp2 - entry
        )

        tp3_pips_final = price_to_pips(
            tp3 - entry
        )

        rr_tp2 = (
            tp2_pips_final
            / risk_pips
        )

        if rr_tp2 < MIN_RR:

            return None

        return {

            "direction": "BUY",

            "entry": entry,

            "sl": sl,

            "tp1": tp1,

            "tp2": tp2,

            "tp3": tp3,

            "risk": risk_price,

            "risk_pips": risk_pips,

            "rr": rr_tp2,

            "pips_to_sl":
                risk_pips,

            "pips_to_tp1":
                tp1_pips_final,

            "pips_to_tp2":
                tp2_pips_final,

            "pips_to_tp3":
                tp3_pips_final,

            "estimated_range": (
                tp1_pips_final,
                tp3_pips_final
            )

        }

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if direction == "SELL":

        tp1 = (
            entry
            - pips_to_price(
                tp1_pips
            )
        )

        tp2 = (
            entry
            - pips_to_price(
                tp2_pips
            )
        )

        tp3 = (
            entry
            - pips_to_price(
                tp3_pips
            )
        )

        blockers = [
            level
            for level in opposing_levels
            if level < entry
        ]

        if blockers:

            nearest = max(
                blockers
            )

            nearest_pips = price_to_pips(
                entry - nearest
            )

            if nearest_pips < tp1_pips:

                return None

            if (
                nearest < tp2
                and nearest > tp3
            ):

                tp3 = (
                    nearest
                    + pips_to_price(
                        10
                    )
                )

        tp1_pips_final = price_to_pips(
            entry - tp1
        )

        tp2_pips_final = price_to_pips(
            entry - tp2
        )

        tp3_pips_final = price_to_pips(
            entry - tp3
        )

        rr_tp2 = (
            tp2_pips_final
            / risk_pips
        )

        if rr_tp2 < MIN_RR:

            return None

        return {

            "direction": "SELL",

            "entry": entry,

            "sl": sl,

            "tp1": tp1,

            "tp2": tp2,

            "tp3": tp3,

            "risk": risk_price,

            "risk_pips": risk_pips,

            "rr": rr_tp2,

            "pips_to_sl":
                risk_pips,

            "pips_to_tp1":
                tp1_pips_final,

            "pips_to_tp2":
                tp2_pips_final,

            "pips_to_tp3":
                tp3_pips_final,

            "estimated_range": (
                tp1_pips_final,
                tp3_pips_final
            )

        }

    return None


# ============================================================
# HARD GATES
# ============================================================

def validate_trade_gates(
    score,
    direction,
    setup,
    m5_confirmation,
    plan
):

    if score < MIN_SCORE:

        return False, (
            "Score below 70"
        )

    if direction not in [
        "BUY",
        "SELL"
    ]:

        return False, (
            "Invalid direction"
        )

    if setup == "NO VALID SETUP":

        return False, (
            "No valid M15 setup"
        )

    if m5_confirmation not in [
        "BULLISH BOS",
        "BEARISH BOS"
    ]:

        return False, (
            "No M5 BOS/CHOCH confirmation"
        )

    if direction == "BUY":

        if m5_confirmation != (
            "BULLISH BOS"
        ):

            return False, (
                "BUY requires bullish M5 confirmation"
            )

    if direction == "SELL":

        if m5_confirmation != (
            "BEARISH BOS"
        ):

            return False, (
                "SELL requires bearish M5 confirmation"
            )

    if not plan:

        return False, (
            "Trade plan rejected"
        )

    if not (
        MIN_RISK_PIPS
        <= plan["risk_pips"]
        <= MAX_RISK_PIPS
    ):

        return False, (
            "Risk outside 35-60 pips"
        )

    if (
        plan["pips_to_tp1"]
        < TP1_PIPS
    ):

        return False, (
            "TP1 below 60 pips"
        )

    if plan["rr"] < MIN_RR:

        return False, (
            "TP2 R:R below 1:2"
        )

    return True, (
        "ALL GATES PASSED"
    )


# ============================================================
# STATE
# ============================================================

def load_state():

    default_state = {

        "last_alert_key": "",

        "last_direction": "",

        "last_score": 0,

        "last_entry": 0,

        "last_alert_time": "",

        "last_opportunity": ""

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


def save_state(state):

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
# ANTI SPAM
# ============================================================

def build_alert_key(
    direction,
    opportunity,
    entry,
    score
):

    entry_bucket = round(
        price_to_pips(
            entry
        )
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

    previous_key = state.get(
        "last_alert_key",
        ""
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

def send_telegram(message):

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
    h1,
    m15,
    m5,
    sweep,
    momentum,
    confirmation,
    setup
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

    message = f"""
<b>👑 BOSQUE FOREX AI</b>

{emoji} <b>XAUUSD {direction}</b>

<b>🔥 VALID SCALPING OPPORTUNITY</b>

<b>Setup:</b>
{setup}

<b>Score:</b>
🔥 {score}/100

━━━━━━━━━━━━━━━━━━

<b>📊 MARKET INTELLIGENCE</b>

Session:
<b>{session}</b>

Price:
<b>{latest_price:.2f}</b>

H1 Bias:
<b>{h1["trend"]}</b>

H1 Structure:
<b>{h1["structure"]}</b>

M15 Trend:
<b>{m15["trend"]}</b>

M15 Structure:
<b>{m15["structure"]}</b>

PD Zone:
<b>{pd["zone"]}</b>

Liquidity:
<b>{sweep}</b>

M5 Confirmation:
<b>{confirmation}</b>

Momentum:
<b>{momentum}</b>

━━━━━━━━━━━━━━━━━━

<b>🎯 SCALPING TRADE PLAN</b>

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

Pip convention:
<b>1 pip = 0.10 price</b>

Risk:
<b>{plan["pips_to_sl"]:.0f} pips</b>

TP1:
<b>+{plan["pips_to_tp1"]:.0f} pips</b>

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

{reason_text}

━━━━━━━━━━━━━━━━━━

<b>FILTERS PASSED</b>

✅ Score 70+
✅ H1 → M15 → M5
✅ Valid M15 setup
✅ M5 BOS/CHOCH
✅ Risk 35-60 pips
✅ TP1 60+ pips
✅ TP2 R:R 1:2+
✅ Closed candles
✅ Room-to-target checked

━━━━━━━━━━━━━━━━━━

⚠️ Educational / decision-support engine.
Confirm the setup manually before entry.

<b>👑 Bosque Forex AI</b>
"""

    return message.strip()


# ============================================================
# MAIN ENGINE
# ============================================================

def run_engine():

    print("=" * 65)

    print(
        "👑 BOSQUE FOREX AI"
    )

    print(
        "SCALPING ENGINE V3"
    )

    print(
        "H1 → M15 → M5"
    )

    print(
        "XAUUSD PIP SIZE: 0.10"
    )

    print(
        "RISK: 35-60 | TP1: 60+ | RR: 1:2+"
    )

    print("=" * 65)

    # --------------------------------------------------------
    # ENVIRONMENT
    # --------------------------------------------------------

    validate_environment()

    # --------------------------------------------------------
    # M5 DATA
    # --------------------------------------------------------

    m5_candles = get_m5_data()

    m5_candles = (
        remove_incomplete_candle(
            m5_candles
        )
    )

    if len(m5_candles) < 100:

        raise RuntimeError(
            "Not enough closed M5 candles."
        )

    # --------------------------------------------------------
    # BUILD M15
    # --------------------------------------------------------

    m15_candles = aggregate_candles(
        m5_candles,
        15
    )

    m15_candles = (
        remove_incomplete_higher_tf(
            m15_candles,
            15
        )
    )

    # --------------------------------------------------------
    # BUILD H1
    # --------------------------------------------------------

    h1_candles = aggregate_candles(
        m5_candles,
        60
    )

    h1_candles = (
        remove_incomplete_higher_tf(
            h1_candles,
            60
        )
    )

    print(
        f"M15 candles built: "
        f"{len(m15_candles)}"
    )

    print(
        f"H1 candles built: "
        f"{len(h1_candles)}"
    )

    if len(m15_candles) < 20:

        raise RuntimeError(
            "Not enough M15 data."
        )

    if len(h1_candles) < 20:

        raise RuntimeError(
            "Not enough H1 data."
        )

    # --------------------------------------------------------
    # ANALYSIS
    # --------------------------------------------------------

    h1 = analyze_structure(
        h1_candles
    )

    m15 = analyze_structure(
        m15_candles
    )

    m5 = analyze_structure(
        m5_candles
    )

    # --------------------------------------------------------
    # H1 / M15 / M5 INDICATORS
    # --------------------------------------------------------

    h1_pd = calculate_pd_zone(
        h1_candles
    )

    m15_pd = calculate_pd_zone(
        m15_candles
    )

    m5_pd = calculate_pd_zone(
        m5_candles
    )

    # M15 is the primary setup location
    pd = m15_pd

    m15_range = calculate_range(
        m15_candles,
        30
    )

    m15_sweep = (
        detect_liquidity_sweep(
            m15_candles
        )
    )

    m5_sweep = (
        detect_liquidity_sweep(
            m5_candles
        )
    )

    # Prefer M15 sweep.
    # If none exists, M5 sweep can
    # support the setup.
    sweep = m15_sweep

    if sweep == "NONE":

        sweep = m5_sweep

    m15_momentum = (
        calculate_momentum(
            m15_candles
        )
    )

    m5_momentum = (
        calculate_momentum(
            m5_candles
        )
    )

    momentum = m5_momentum

    m15_confirmation = (
        candle_confirmation(
            m15_candles
        )
    )

    m5_candle_confirm = (
        candle_confirmation(
            m5_candles
        )
    )

    m5_bos = (
        detect_m5_confirmation(
            m5_candles
        )
    )

    # --------------------------------------------------------
    # M15 SETUP
    # --------------------------------------------------------

    setup_result = (
        detect_m15_setup(
            h1,
            m15,
            pd,
            m15_range,
            sweep,
            m15_confirmation
        )
    )

    direction = setup_result[
        "direction"
    ]

    setup = setup_result[
        "setup"
    ]

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    scoring = calculate_score(
        direction,
        h1,
        m15,
        m5_bos,
        pd,
        sweep,
        momentum,
        m5_candle_confirm,
        setup,
        get_session()
    )

    score = scoring[
        "score"
    ]

    reasons = scoring[
        "reasons"
    ]

    # --------------------------------------------------------
    # PRICE / SESSION
    # --------------------------------------------------------

    latest_price = (
        m5_candles[-1]["close"]
    )

    session = get_session()

    # --------------------------------------------------------
    # CONSOLE
    # --------------------------------------------------------

    print()

    print(
        "================ MARKET ================="
    )

    print(
        f"Session       : {session}"
    )

    print(
        f"Price         : {latest_price:.2f}"
    )

    print()

    print(
        f"H1 Trend      : {h1['trend']}"
    )

    print(
        f"H1 Structure  : {h1['structure']}"
    )

    print()

    print(
        f"M15 Trend     : {m15['trend']}"
    )

    print(
        f"M15 Structure : {m15['structure']}"
    )

    print(
        f"M15 PD        : {m15_pd['zone']}"
    )

    print(
        f"M15 Range     : {m15_range['position']}"
    )

    print(
        f"M15 Sweep     : {m15_sweep}"
    )

    print()

    print(
        f"M5 Structure  : {m5['structure']}"
    )

    print(
        f"M5 BOS        : {m5_bos}"
    )

    print(
        f"M5 Candle     : {m5_candle_confirm}"
    )

    print(
        f"M5 Momentum   : {m5_momentum}"
    )

    print()

    print(
        f"Setup         : {setup}"
    )

    print(
        f"Direction     : {direction}"
    )

    print(
        f"Score         : {score}/100"
    )

    # --------------------------------------------------------
    # SCORE GATE
    # --------------------------------------------------------

    if score < MIN_SCORE:

        print()

        print(
            "⏳ SCORE BELOW 70"
        )

        print(
            "Telegram alert skipped."
        )

        return

    # --------------------------------------------------------
    # DIRECTION GATE
    # --------------------------------------------------------

    if direction not in [
        "BUY",
        "SELL"
    ]:

        print()

        print(
            "⏳ INVALID DIRECTION"
        )

        return

    # --------------------------------------------------------
    # SETUP GATE
    # --------------------------------------------------------

    if setup == "NO VALID SETUP":

        print()

        print(
            "⏳ NO VALID M15 SETUP"
        )

        return

    # --------------------------------------------------------
    # CREATE TRADE PLAN
    # --------------------------------------------------------

    plan = create_trade_plan(
        direction,
        m5_candles,
        m15,
        h1
    )

    # --------------------------------------------------------
    # HARD GATES
    # --------------------------------------------------------

    gates_passed, gate_reason = (
        validate_trade_gates(
            score,
            direction,
            setup,
            m5_bos,
            plan
        )
    )

    print()

    print(
        f"Hard Gate     : {gate_reason}"
    )

    if not gates_passed:

        print(
            "⛔ SETUP REJECTED"
        )

        print(
            "Telegram alert skipped."
        )

        return

    # --------------------------------------------------------
    # VALID OPPORTUNITY
    # --------------------------------------------------------

    print()

    print(
        "🔥🔥🔥 VALID SCALPING OPPORTUNITY"
    )

    print(
        f"Direction : {direction}"
    )

    print(
        f"Setup     : {setup}"
    )

    print(
        f"Score     : {score}/100"
    )

    print(
        f"Entry     : {plan['entry']:.2f}"
    )

    print(
        f"SL        : {plan['sl']:.2f}"
    )

    print(
        f"Risk      : "
        f"{plan['risk_pips']:.0f} pips"
    )

    print(
        f"TP1       : "
        f"{plan['tp1']:.2f} "
        f"({plan['pips_to_tp1']:.0f} pips)"
    )

    print(
        f"TP2       : "
        f"{plan['tp2']:.2f} "
        f"({plan['pips_to_tp2']:.0f} pips)"
    )

    print(
        f"TP3       : "
        f"{plan['tp3']:.2f} "
        f"({plan['pips_to_tp3']:.0f} pips)"
    )

    print(
        f"R:R       : "
        f"1:{plan['rr']:.2f}"
    )

    print(
        f"Potential  : "
        f"{plan['estimated_range'][0]:.0f}"
        f"-"
        f"{plan['estimated_range'][1]:.0f}"
        f" pips"
    )

    # --------------------------------------------------------
    # STATE / ANTI SPAM
    # --------------------------------------------------------

    state = load_state()

    alert_key = build_alert_key(
        direction,
        setup,
        plan["entry"],
        score
    )

    if not should_send_alert(
        state,
        alert_key
    ):

        return

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    alert = format_alert(
        score,
        setup,
        session,
        latest_price,
        plan,
        reasons,
        pd,
        h1,
        m15,
        m5,
        sweep,
        momentum,
        m5_candle_confirm,
        setup
    )

    sent = send_telegram(
        alert
    )

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

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
        ] = setup

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