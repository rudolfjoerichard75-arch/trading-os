import os
import json
import math
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# BOSQUE FOREX AI
# Production Market Intelligence Engine
# XAU/USD
#
# FEATURES
# - Quota-safe: ONE Twelve Data request per scan
# - M5 real OHLC
# - Local H1/H4 aggregation from M5
# - Market structure
# - Swing detection
# - Premium / Discount
# - Liquidity sweep detection
# - Momentum / confirmation
# - Opportunity type
# - 0-100 scoring engine
# - Entry / SL / TP
# - Risk:Reward
# - Estimated movement in pips
# - Telegram alert for valid opportunities
#
# ENVIRONMENT VARIABLES
# TWELVEDATA_API_KEY
# TELEGRAM_BOT_TOKEN
# TELEGRAM_CHAT_ID
# ============================================================


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAU/USD"

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

M5_INTERVAL = "5min"

# 500 M5 candles ≈ 41 hours
# Enough to construct H1 and H4 context locally.
OUTPUT_SIZE = 500

MIN_SCORE = 70

# Gold pip assumption:
# 1 pip = 0.01 price movement
PIP_SIZE = 0.01

REQUEST_TIMEOUT = 20

MALAYSIA_TZ = timezone(timedelta(hours=8))


# ============================================================
# ENVIRONMENT
# ============================================================

API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# VALIDATION
# ============================================================

def validate_environment():

    missing = []

    if not API_KEY:
        missing.append("TWELVEDATA_API_KEY")

    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:

        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


# ============================================================
# TIME
# ============================================================

def malaysia_now():

    return datetime.now(MALAYSIA_TZ)


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

    print("Fetching real XAU/USD M5 data...")

    response = requests.get(
        TWELVEDATA_URL,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Twelve Data HTTP {response.status_code}: "
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
            + str(data.get("message", "Unknown API error"))
        )

    values = data.get("values")

    if not values:

        raise RuntimeError(
            "No OHLC data received from Twelve Data."
        )

    candles = []

    for item in values:

        try:

            candles.append({
                "time": item["datetime"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

        except Exception:

            continue

    if len(candles) < 50:

        raise RuntimeError(
            f"Insufficient OHLC data: {len(candles)} candles."
        )

    # Twelve Data normally returns newest first.
    candles.sort(key=lambda x: x["time"])

    print(
        f"Received {len(candles)} M5 candles."
    )

    return candles


# ============================================================
# CANDLE AGGREGATION
# ============================================================

def parse_time(value):

    try:

        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

    except Exception:

        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        )


def aggregate_candles(candles, minutes):

    if not candles:

        return []

    groups = {}

    for candle in candles:

        dt = parse_time(candle["time"])

        if minutes == 60:

            bucket = dt.replace(
                minute=0,
                second=0,
                microsecond=0
            )

        elif minutes == 240:

            hour_block = (dt.hour // 4) * 4

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

        groups[key].append(candle)

    result = []

    for key in sorted(groups.keys()):

        group = groups[key]

        if not group:

            continue

        result.append({

            "time": key,

            "open": group[0]["open"],

            "high": max(
                c["high"] for c in group
            ),

            "low": min(
                c["low"] for c in group
            ),

            "close": group[-1]["close"]

        })

    return result


# ============================================================
# SWING DETECTION
# ============================================================

def detect_swings(candles, left=2, right=2):

    swing_highs = []
    swing_lows = []

    if len(candles) < left + right + 1:

        return swing_highs, swing_lows

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

            if current["high"] <= candles[j]["high"]:
                high_ok = False

            if current["low"] >= candles[j]["low"]:
                low_ok = False

        if high_ok:

            swing_highs.append({
                "price": current["high"],
                "time": current["time"]
            })

        if low_ok:

            swing_lows.append({
                "price": current["low"],
                "time": current["time"]
            })

    return swing_highs, swing_lows


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:

        return None

    true_ranges = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(

            current["high"] - current["low"],

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

def analyze_structure(candles):

    if len(candles) < 20:

        return {
            "structure": "RANGE",
            "trend": "NEUTRAL",
            "last_high": None,
            "last_low": None
        }

    swing_highs, swing_lows = detect_swings(
        candles
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

    if last_high and latest["close"] > last_high["price"]:

        structure = "BULLISH BOS"
        trend = "BULLISH"

    elif last_low and latest["close"] < last_low["price"]:

        structure = "BEARISH BOS"
        trend = "BEARISH"

    else:

        # Determine trend from recent closes
        recent = candles[-10:]

        first_close = recent[0]["close"]
        last_close = recent[-1]["close"]

        movement = last_close - first_close

        if movement > 0:
            trend = "BULLISH"

        elif movement < 0:
            trend = "BEARISH"

    return {

        "structure": structure,

        "trend": trend,

        "last_high": last_high,

        "last_low": last_low,

        "swing_highs": swing_highs,

        "swing_lows": swing_lows

    }


# ============================================================
# PREMIUM / DISCOUNT
# ============================================================

def calculate_pd_zone(candles):

    recent = candles[-50:]

    high = max(
        c["high"] for c in recent
    )

    low = min(
        c["low"] for c in recent
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
        "equilibrium": equilibrium,
        "zone": zone
    }


# ============================================================
# LIQUIDITY DETECTION
# ============================================================

def detect_liquidity_sweep(candles):

    if len(candles) < 10:

        return "NONE"

    recent = candles[-8:]

    previous = candles[-9:-3]

    previous_high = max(
        c["high"] for c in previous
    )

    previous_low = min(
        c["low"] for c in previous
    )

    latest = candles[-1]

    # Bullish liquidity sweep
    # Price takes previous low then closes back above.
    if (
        latest["low"] < previous_low
        and latest["close"] > previous_low
    ):

        return "SELL_SIDE_SWEEP"

    # Bearish liquidity sweep
    # Price takes previous high then closes back below.
    if (
        latest["high"] > previous_high
        and latest["close"] < previous_high
    ):

        return "BUY_SIDE_SWEEP"

    return "NONE"


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(candles):

    if len(candles) < 6:

        return "NEUTRAL"

    recent = candles[-5:]

    bullish = 0
    bearish = 0

    for candle in recent:

        if candle["close"] > candle["open"]:

            bullish += 1

        elif candle["close"] < candle["open"]:

            bearish += 1

    if bullish >= 4:

        return "BULLISH"

    if bearish >= 4:

        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# CANDLE CONFIRMATION
# ============================================================

def candle_confirmation(candles):

    if len(candles) < 3:

        return "NONE"

    previous = candles[-2]
    latest = candles[-1]

    latest_body = abs(
        latest["close"] - latest["open"]
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

    # Bullish confirmation
    if (
        latest["close"] > latest["open"]
        and body_ratio >= 0.55
        and latest["close"] > previous["high"]
    ):

        return "BULLISH"

    # Bearish confirmation
    if (
        latest["close"] < latest["open"]
        and body_ratio >= 0.55
        and latest["close"] < previous["low"]
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

    if (
        m5["structure"] == "BULLISH BOS"
        and confirmation == "BULLISH"
    ):

        return "BUY BREAKOUT"

    if (
        m5["structure"] == "BEARISH BOS"
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

    # --------------------------------------------------------
    # H4 TREND
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
    # H1 TREND
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

    if m5["structure"] == "BULLISH BOS":

        buy_score += 15
        reasons_buy.append(
            "M5 bullish BOS"
        )

    elif m5["structure"] == "BEARISH BOS":

        sell_score += 15
        reasons_sell.append(
            "M5 bearish BOS"
        )

    # --------------------------------------------------------
    # PD ZONE
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
    # FINAL DIRECTION
    # --------------------------------------------------------

    if buy_score > sell_score:

        return {

            "direction": "BUY",

            "score": min(
                buy_score,
                100
            ),

            "reasons": reasons_buy

        }

    if sell_score > buy_score:

        return {

            "direction": "SELL",

            "score": min(
                sell_score,
                100
            ),

            "reasons": reasons_sell

        }

    return {

        "direction": "WAIT",

        "score": 0,

        "reasons": []

    }


# ============================================================
# TRADE PLAN
# ============================================================

def create_trade_plan(
    direction,
    score,
    candles,
    m5_analysis
):

    latest = candles[-1]["close"]

    atr = calculate_atr(
        candles,
        14
    )

    if atr is None:

        atr = (
            max(
                c["high"] - c["low"]
                for c in candles[-10:]
            )
        )

    swing_highs = m5_analysis.get(
        "swing_highs",
        []
    )

    swing_lows = m5_analysis.get(
        "swing_lows",
        []
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

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if direction == "BUY":

        entry = latest

        if last_swing_low:

            structural_sl = last_swing_low

            atr_sl = (
                entry
                - atr * 1.20
            )

            sl = min(
                structural_sl,
                atr_sl
            )

        else:

            sl = (
                entry
                - atr * 1.20
            )

        risk = entry - sl

        if risk <= 0:

            return None

        tp1 = entry + (
            risk * 1.5
        )

        tp2 = entry + (
            risk * 2.0
        )

        tp3 = entry + (
            risk * 3.0
        )

        return {

            "direction": "BUY",

            "entry": entry,

            "sl": sl,

            "tp1": tp1,

            "tp2": tp2,

            "tp3": tp3,

            "risk": risk,

            "rr": "1:2",

            "pips_to_sl": risk / PIP_SIZE,

            "pips_to_tp1": (
                tp1 - entry
            ) / PIP_SIZE,

            "pips_to_tp2": (
                tp2 - entry
            ) / PIP_SIZE,

            "pips_to_tp3": (
                tp3 - entry
            ) / PIP_SIZE,

            "estimated_range": (
                risk / PIP_SIZE,
                (tp3 - entry) / PIP_SIZE
            )

        }

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if direction == "SELL":

        entry = latest

        if last_swing_high:

            structural_sl = last_swing_high

            atr_sl = (
                entry
                + atr * 1.20
            )

            sl = max(
                structural_sl,
                atr_sl
            )

        else:

            sl = (
                entry
                + atr * 1.20
            )

        risk = sl - entry

        if risk <= 0:

            return None

        tp1 = entry - (
            risk * 1.5
        )

        tp2 = entry - (
            risk * 2.0
        )

        tp3 = entry - (
            risk * 3.0
        )

        return {

            "direction": "SELL",

            "entry": entry,

            "sl": sl,

            "tp1": tp1,

            "tp2": tp2,

            "tp3": tp3,

            "risk": risk,

            "rr": "1:2",

            "pips_to_sl": risk / PIP_SIZE,

            "pips_to_tp1": (
                entry - tp1
            ) / PIP_SIZE,

            "pips_to_tp2": (
                entry - tp2
            ) / PIP_SIZE,

            "pips_to_tp3": (
                entry - tp3
            ) / PIP_SIZE,

            "estimated_range": (
                risk / PIP_SIZE,
                (entry - tp3) / PIP_SIZE
            )

        }

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram token missing. "
            "Skipping Telegram."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID missing. "
            "Skipping Telegram."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {

        "chat_id": TELEGRAM_CHAT_ID,

        "text": message,

        "parse_mode": "HTML",

        "disable_web_page_preview": True

    }

    response = requests.post(
        url,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

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
# FORMAT TELEGRAM ALERT
# ============================================================

def format_alert(
    score,
    opportunity,
    session,
    latest_price,
    plan,
    reasons,
    pd
):

    direction = plan["direction"]

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
<b>🚨 BOSQUE FOREX AI</b>

{emoji} <b>XAUUSD {direction}</b>

<b>Opportunity:</b>
{opportunity}

<b>Score:</b>
🔥 {score}/100

<b>Status:</b>
VALID OPPORTUNITY

━━━━━━━━━━━━━━━━━━

<b>📊 MARKET</b>

Session: {session}
Price: {latest_price:.2f}
PD Zone: {pd["zone"]}

━━━━━━━━━━━━━━━━━━

<b>🎯 TRADE PLAN</b>

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

R:R:
<b>{plan["rr"]}</b>

━━━━━━━━━━━━━━━━━━

<b>📏 ESTIMATED PRICE RANGE</b>

Risk:
<b>{plan["pips_to_sl"]:.0f} pips</b>

TP1:
<b>{plan["pips_to_tp1"]:.0f} pips</b>

TP2:
<b>{plan["pips_to_tp2"]:.0f} pips</b>

TP3:
<b>{plan["pips_to_tp3"]:.0f} pips</b>

Potential range:
<b>{low_range:.0f} - {high_range:.0f} pips</b>

━━━━━━━━━━━━━━━━━━

<b>🧠 WHY?</b>

{reason_text}

━━━━━━━━━━━━━━━━━━

⚠️ Educational / decision-support engine.
Always confirm the setup manually before entry.

<b>👑 Bosque Forex AI</b>
"""

    return message.strip()


# ============================================================
# MAIN ANALYSIS
# ============================================================

def run_engine():

    print("=" * 60)

    print(
        "👑 BOSQUE FOREX AI ENGINE"
    )

    print(
        "XAU/USD Market Intelligence"
    )

    print("=" * 60)

    validate_environment()

    # --------------------------------------------------------
    # ONE API CALL ONLY
    # --------------------------------------------------------

    m5_candles = get_m5_data()

    # --------------------------------------------------------
    # LOCAL MTF AGGREGATION
    # --------------------------------------------------------

    h1_candles = aggregate_candles(
        m5_candles,
        60
    )

    h4_candles = aggregate_candles(
        m5_candles,
        240
    )

    print(
        f"H1 candles built: {len(h1_candles)}"
    )

    print(
        f"H4 candles built: {len(h4_candles)}"
    )

    if len(h1_candles) < 10:

        raise RuntimeError(
            "Not enough H1 aggregated data."
        )

    if len(h4_candles) < 5:

        raise RuntimeError(
            "Not enough H4 aggregated data."
        )

    # --------------------------------------------------------
    # ANALYSIS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    scoring = calculate_score(
        h4,
        h1,
        m5,
        pd,
        sweep,
        momentum,
        confirmation
    )

    direction = scoring["direction"]

    score = scoring["score"]

    reasons = scoring["reasons"]

    opportunity = determine_opportunity(
        h4,
        h1,
        m5,
        pd,
        sweep,
        confirmation
    )

    latest_price = (
        m5_candles[-1]["close"]
    )

    session = get_session()

    # --------------------------------------------------------
    # CONSOLE OUTPUT
    # --------------------------------------------------------

    print()

    print(
        f"Session       : {session}"
    )

    print(
        f"Latest Price  : {latest_price:.2f}"
    )

    print(
        f"H4 Trend      : {h4['trend']}"
    )

    print(
        f"H4 Structure  : {h4['structure']}"
    )

    print(
        f"H1 Trend      : {h1['trend']}"
    )

    print(
        f"H1 Structure  : {h1['structure']}"
    )

    print(
        f"M5 Trend      : {m5['trend']}"
    )

    print(
        f"M5 Structure  : {m5['structure']}"
    )

    print(
        f"PD Zone       : {pd['zone']}"
    )

    print(
        f"Liquidity     : {sweep}"
    )

    print(
        f"Momentum      : {momentum}"
    )

    print(
        f"Confirmation  : {confirmation}"
    )

    print(
        f"Opportunity   : {opportunity}"
    )

    print(
        f"Score         : {score}/100"
    )

    # --------------------------------------------------------
    # VALID OPPORTUNITY
    # --------------------------------------------------------

    if (
        score >= MIN_SCORE
        and direction in ["BUY", "SELL"]
        and opportunity != "NO VALID SETUP"
    ):

        plan = create_trade_plan(
            direction,
            score,
            m5_candles,
            m5
        )

        if not plan:

            print(
                "Trade plan could not be generated."
            )

            return

        print()

        print(
            "🔥 VALID OPPORTUNITY FOUND"
        )

        print(
            f"Direction : {direction}"
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
            f"TP1       : {plan['tp1']:.2f}"
        )

        print(
            f"TP2       : {plan['tp2']:.2f}"
        )

        print(
            f"TP3       : {plan['tp3']:.2f}"
        )

        print(
            f"Risk      : {plan['pips_to_sl']:.0f} pips"
        )

        print(
            f"Potential : "
            f"{plan['estimated_range'][0]:.0f}"
            f"-"
            f"{plan['estimated_range'][1]:.0f}"
            f" pips"
        )

        alert = format_alert(
            score,
            opportunity,
            session,
            latest_price,
            plan,
            reasons,
            pd
        )

        send_telegram(alert)

    else:

        print()

        print(
            "⏳ No valid 70+ opportunity."
        )

        print(
            "Telegram notification skipped."
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