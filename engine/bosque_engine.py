import os
import json
import requests
from datetime import datetime, timezone, timedelta


# ============================================================
# BOSQUE FOREX AI v2.1
# PRODUCTION LOW-RISK / HIGH-REWARD ENGINE
# XAU/USD
#
# PIP STANDARD — LOCKED
#
# 100 points = 10 pips
# 10 points  = 1 pip
# 0.10 price  = 1 pip
#
# Example:
#
# Entry 3350.00
# SL    3345.00
#
# Difference = 5.00
# Risk       = 50 pips
#
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

# Minimum signal score
MIN_SCORE = 70


# ============================================================
# LOCKED RISK / REWARD FILTER
# ============================================================

MIN_RISK_PIPS = 35
MAX_RISK_PIPS = 60

MIN_TP1_PIPS = 120

MIN_RR = 2.0


# ============================================================
# XAUUSD PIP STANDARD
# ============================================================
#
# 0.10 price movement = 1 pip
#
# Therefore:
#
# 1.00 price movement = 10 pips
# 10.00 price movement = 100 pips
#
# ============================================================

PIP_SIZE = 0.10


REQUEST_TIMEOUT = 20


MALAYSIA_TZ = timezone(
    timedelta(hours=8)
)


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


# ============================================================
# TIME PARSER
# ============================================================

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

    values = data.get("values")

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
        f"Received {len(candles)} "
        f"M5 candles."
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

        if elapsed < 300:

            print(
                "Removing incomplete "
                "M5 candle."
            )

            return candles[:-1]

    except Exception as error:

        print(
            "Candle close check warning:",
            error
        )

    return candles


# ============================================================
# CANDLE AGGREGATION
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
# SWING DETECTION
# ============================================================

def detect_swings(
    candles,
    left=2,
    right=2
):

    swing_highs = []
    swing_lows = []

    minimum = (
        left
        + right
        + 1
    )

    if len(candles) < minimum:

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

    return (
        sum(
            true_ranges[-period:]
        )
        / period
    )


# ============================================================
# MARKET STRUCTURE
# ============================================================

def analyze_structure(candles):

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
                []

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

        recent = candles[-10:]

        first_close = (
            recent[0]["close"]
        )

        last_close = (
            recent[-1]["close"]
        )

        movement = (
            last_close
            - first_close
        )

        if movement > 0:

            trend = "BULLISH"

        elif movement < 0:

            trend = "BEARISH"

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
            swing_lows

    }


# ============================================================
# PREMIUM / DISCOUNT
# ============================================================

def calculate_pd_zone(candles):

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

    # SELL-SIDE LIQUIDITY TAKEN
    # Potential bullish reversal
    if (
        latest["low"]
        < previous_low

        and latest["close"]
        > previous_low
    ):

        return "SELL_SIDE_SWEEP"

    # BUY-SIDE LIQUIDITY TAKEN
    # Potential bearish reversal
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

    # BULLISH
    if (
        latest["close"]
        > latest["open"]

        and body_ratio >= 0.55

        and latest["close"]
        > previous["high"]
    ):

        return "BULLISH"

    # BEARISH
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

    if (
        20 <= hour <= 23
    ):

        return "NEW YORK"

    if 0 <= hour < 1:

        return "NEW YORK"

    return "ASIAN"


# ============================================================
# OPPORTUNITY TYPE
# ============================================================

def determine_opportunity(
    h4,
    h1,
    m5,
    pd,
    sweep,
    confirmation
):

    # ========================================================
    # PULLBACK
    # ========================================================

    if (
        h4["trend"] == "BULLISH"

        and h1["trend"] == "BULLISH"

        and pd["zone"] == "DISCOUNT"

        and confirmation == "BULLISH"
    ):

        return "BUY PULLBACK"

    if (
        h4["trend"] == "BEARISH"

        and h1["trend"] == "BEARISH"

        and pd["zone"] == "PREMIUM"

        and confirmation == "BEARISH"
    ):

        return "SELL PULLBACK"


    # ========================================================
    # LIQUIDITY SWEEP
    # ========================================================

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


    # ========================================================
    # BREAKOUT
    # ========================================================

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
    confirmation
):

    buy_score = 0
    sell_score = 0

    reasons_buy = []
    reasons_sell = []


    # ========================================================
    # H4
    # ========================================================

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


    # ========================================================
    # H1
    # ========================================================

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


    # ========================================================
    # M5 STRUCTURE
    # ========================================================

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


    # ========================================================
    # PREMIUM / DISCOUNT
    # ========================================================

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


    # ========================================================
    # LIQUIDITY
    # ========================================================

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


    # ========================================================
    # MOMENTUM
    # ========================================================

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


    # ========================================================
    # CONFIRMATION
    # ========================================================

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


    # ========================================================
    # FINAL
    # ========================================================

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
# PIP CONVERSION
# ============================================================

def price_to_pips(price_difference):

    """
    Convert XAUUSD price movement to
    Bosque standard pips.

    Example:

    5.00 price movement
    / 0.10
    = 50 pips
    """

    return (
        abs(price_difference)
        / PIP_SIZE
    )


def pips_to_price(pips):

    """
    Convert Bosque pips back to
    XAUUSD price distance.

    Example:

    120 pips
    * 0.10
    = 12.00 price movement
    """

    return (
        pips
        * PIP_SIZE
    )


# ============================================================
# TRADE PLAN
# ============================================================

def create_trade_plan(
    direction,
    candles,
    m5_analysis
):

    latest = candles[-1]["close"]

    atr = calculate_atr(
        candles,
        14
    )

    if atr is None:

        recent_ranges = [

            c["high"] - c["low"]

            for c in candles[-10:]

        ]

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

        entry = latest

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
                (
                    "STRUCTURAL",
                    structural_sl
                )
            )


        candidates.append(
            (
                "ATR",
                atr_sl
            )
        )


        valid_sl = []


        for method, sl_candidate in candidates:

            risk_price = (
                entry
                - sl_candidate
            )

            risk_pips = (
                price_to_pips(
                    risk_price
                )
            )


            if (
                MIN_RISK_PIPS
                <= risk_pips
                <= MAX_RISK_PIPS
            ):

                valid_sl.append(
                    (
                        risk_pips,
                        sl_candidate,
                        method
                    )
                )


        if not valid_sl:

            return None


        # Tightest valid SL
        valid_sl.sort(
            key=lambda x: x[0]
        )


        risk_pips, sl, sl_method = (
            valid_sl[0]
        )


        risk_price = (
            entry - sl
        )


        # ====================================================
        # TP
        # ====================================================

        tp1_pips = max(
            risk_pips * MIN_RR,
            MIN_TP1_PIPS
        )


        tp2_pips = (
            risk_pips * 3.0
        )


        tp3_pips = (
            risk_pips * 4.0
        )


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


        rr = (
            tp1_pips
            / risk_pips
        )


        if (
            risk_pips < MIN_RISK_PIPS
            or risk_pips > MAX_RISK_PIPS
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

            "sl_method":
                sl_method,

            "risk":
                risk_price,

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

        entry = latest

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
                (
                    "STRUCTURAL",
                    structural_sl
                )
            )


        candidates.append(
            (
                "ATR",
                atr_sl
            )
        )


        valid_sl = []


        for method, sl_candidate in candidates:

            risk_price = (
                sl_candidate
                - entry
            )

            risk_pips = (
                price_to_pips(
                    risk_price
                )
            )


            if (
                MIN_RISK_PIPS
                <= risk_pips
                <= MAX_RISK_PIPS
            ):

                valid_sl.append(
                    (
                        risk_pips,
                        sl_candidate,
                        method
                    )
                )


        if not valid_sl:

            return None


        valid_sl.sort(
            key=lambda x: x[0]
        )


        risk_pips, sl, sl_method = (
            valid_sl[0]
        )


        risk_price = (
            sl - entry
        )


        # ====================================================
        # TP
        # ====================================================

        tp1_pips = max(
            risk_pips * MIN_RR,
            MIN_TP1_PIPS
        )


        tp2_pips = (
            risk_pips * 3.0
        )


        tp3_pips = (
            risk_pips * 4.0
        )


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


        rr = (
            tp1_pips
            / risk_pips
        )


        if (
            risk_pips < MIN_RISK_PIPS
            or risk_pips > MAX_RISK_PIPS
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

            "sl_method":
                sl_method,

            "risk":
                risk_price,

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


# ============================================================
# SAVE STATE
# ============================================================

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
# ANTI-SPAM
# ============================================================

def build_alert_key(
    direction,
    opportunity,
    entry,
    score
):

    # Entry bucket:
    #
    # 0.10 price = 1 pip
    #
    # 0.10 bucket prevents
    # tiny price fluctuations
    # from creating new alerts.

    entry_bucket = round(
        entry / 0.10
    )


    # Score grouped into
    # 5-point blocks.

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
    confirmation
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

<b>🔥 VALID OPPORTUNITY</b>

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

PD Zone:
<b>{pd["zone"]}</b>

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

SL Method:
<b>{plan["sl_method"]}</b>

━━━━━━━━━━━━━━━━━━

<b>📏 PIP ANALYSIS</b>

<b>Standard: 0.10 = 1 pip</b>

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

✅ Score {MIN_SCORE}+
✅ Risk {MIN_RISK_PIPS}-{MAX_RISK_PIPS} pips
✅ TP1 {MIN_TP1_PIPS}+ pips
✅ R:R 1:2+
✅ Closed M5 candle
✅ MTF confirmation

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

    print("=" * 60)

    print(
        "👑 BOSQUE FOREX AI v2.1"
    )

    print(
        "LOW-RISK / HIGH-REWARD ENGINE"
    )

    print(
        "PIP STANDARD: 0.10 PRICE = 1 PIP"
    )

    print("=" * 60)


    validate_environment()


    # ========================================================
    # ONE API REQUEST
    # ========================================================

    m5_candles = get_m5_data()


    # ========================================================
    # REMOVE INCOMPLETE CANDLE
    # ========================================================

    m5_candles = (
        remove_incomplete_candle(
            m5_candles
        )
    )


    if len(m5_candles) < 50:

        raise RuntimeError(
            "Not enough closed "
            "M5 candles."
        )


    # ========================================================
    # LOCAL MTF
    # ========================================================

    h1_candles = (
        aggregate_candles(
            m5_candles,
            60
        )
    )


    h4_candles = (
        aggregate_candles(
            m5_candles,
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
            "Not enough H1 data."
        )


    if len(h4_candles) < 5:

        raise RuntimeError(
            "Not enough H4 data."
        )


    # ========================================================
    # ANALYSIS
    # ========================================================

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


    confirmation = candle_confirmation(
        m5_candles
    )


    # ========================================================
    # SCORE
    # ========================================================

    scoring = calculate_score(

        h4,

        h1,

        m5,

        pd,

        sweep,

        momentum,

        confirmation

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

            confirmation

        )
    )


    latest_price = (
        m5_candles[-1]["close"]
    )


    session = get_session()


    # ========================================================
    # CONSOLE
    # ========================================================

    print()

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

    print(
        f"PD Zone       : "
        f"{pd['zone']}"
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
            f"⏳ Score below "
            f"{MIN_SCORE}."
        )

        print(
            "No Telegram alert."
        )

        return


    if direction not in [
        "BUY",
        "SELL"
    ]:

        print()

        print(
            "⏳ Direction invalid."
        )

        return


    if opportunity == (
        "NO VALID SETUP"
    ):

        print()

        print(
            "⏳ No valid "
            "opportunity type."
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
            "Reason:"
        )

        print(
            f"- Risk must be "
            f"{MIN_RISK_PIPS}-"
            f"{MAX_RISK_PIPS} pips"
        )

        print(
            f"- TP1 must be "
            f"{MIN_TP1_PIPS}+ pips"
        )

        print(
            f"- R:R must be "
            f"1:{MIN_RR:.0f}+"
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

    print(
        "🔥🔥🔥 VALID OPPORTUNITY"
    )

    print(
        f"Direction : "
        f"{direction}"
    )

    print(
        f"Score     : "
        f"{score}/100"
    )

    print(
        f"Opportunity: "
        f"{opportunity}"
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
    # STATE / ANTI-SPAM
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

        confirmation

    )


    sent = send_telegram(
        alert
    )


    # ========================================================
    # SAVE STATE ONLY AFTER
    # SUCCESSFUL TELEGRAM
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