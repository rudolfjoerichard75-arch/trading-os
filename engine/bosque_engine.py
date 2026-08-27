import os
import json
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# =========================================================
# BOSQUE FOREX AI v2.0
# PRODUCTION / QUOTA-SAFE ENGINE
# =========================================================

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAU/USD"

# XAUUSD convention used by Bosque AI
# 1 pip = 0.10 price movement
PIP_SIZE = 0.10

MIN_SCORE = 70

M5_CACHE_SECONDS = 0
H1_CACHE_SECONDS = 60 * 60
H4_CACHE_SECONDS = 4 * 60 * 60

STATE_FILE = "engine/state.json"

# Alert protection
ALERT_COOLDOWN_SECONDS = 30 * 60


# =========================================================
# TIME
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def parse_time(value):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def malaysia_session():
    now = utc_now().astimezone(
        ZoneInfo("Asia/Kuala_Lumpur")
    )

    hour = now.hour

    if 0 <= hour < 8:
        return "ASIAN"

    if 8 <= hour < 15:
        return "LONDON"

    if 15 <= hour < 20:
        return "NEW YORK"

    return "ASIAN"


# =========================================================
# HTTP
# =========================================================

def get_json(url, params=None):

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):

        if data.get("status") == "error":

            raise Exception(
                data.get(
                    "message",
                    "API request failed"
                )
            )

    return data


# =========================================================
# TWELVE DATA
# =========================================================

def get_ohlc(interval, outputsize=60):

    print(f"API REQUEST → {interval}")

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON"
    }

    data = get_json(
        url,
        params
    )

    if "values" not in data:

        raise Exception(
            "No OHLC values returned"
        )

    candles = []

    for candle in data["values"]:

        candles.append({
            "time": candle["datetime"],
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"])
        })

    # Oldest → newest
    candles.reverse()

    return candles


# =========================================================
# STATE
# =========================================================

def default_state():

    return {
        "m5": {
            "updated_at": None,
            "candles": []
        },

        "h1": {
            "updated_at": None,
            "candles": []
        },

        "h4": {
            "updated_at": None,
            "candles": []
        },

        "alert": {
            "last_sent_at": None,
            "last_signature": None,
            "last_side": None,
            "last_score": 0
        }
    }


def load_state():

    if not os.path.exists(STATE_FILE):

        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            state = json.load(file)

        base = default_state()

        for key in base:

            if key not in state:

                state[key] = base[key]

        return state

    except Exception as error:

        print(
            "State load failed:",
            error
        )

        return default_state()


def save_state(state):

    os.makedirs(
        "engine",
        exist_ok=True
    )

    temp_file = STATE_FILE + ".tmp"

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


# =========================================================
# CACHE
# =========================================================

def cache_valid(
    state,
    timeframe,
    max_age
):

    cache = state.get(
        timeframe,
        {}
    )

    updated_at = cache.get(
        "updated_at"
    )

    candles = cache.get(
        "candles",
        []
    )

    if not updated_at:
        return False

    if not candles:
        return False

    updated = parse_time(
        updated_at
    )

    if not updated:
        return False

    age = (
        utc_now() - updated
    ).total_seconds()

    return age < max_age


def get_timeframe_data(
    state,
    timeframe,
    max_age,
    outputsize=60
):

    if cache_valid(
        state,
        timeframe,
        max_age
    ):

        print(
            f"CACHE HIT → {timeframe}"
        )

        return state[timeframe]["candles"]

    print(
        f"CACHE MISS → {timeframe}"
    )

    candles = get_ohlc(
        timeframe,
        outputsize
    )

    state[timeframe] = {
        "updated_at": utc_iso(),
        "candles": candles
    }

    return candles


# =========================================================
# SWINGS
# =========================================================

def detect_swings(data):

    highs = []
    lows = []

    if len(data) < 5:
        return highs, lows

    for i in range(
        2,
        len(data) - 2
    ):

        current = data[i]

        is_high = (
            current["high"] >
            data[i - 1]["high"]
            and
            current["high"] >
            data[i - 2]["high"]
            and
            current["high"] >
            data[i + 1]["high"]
            and
            current["high"] >
            data[i + 2]["high"]
        )

        is_low = (
            current["low"] <
            data[i - 1]["low"]
            and
            current["low"] <
            data[i - 2]["low"]
            and
            current["low"] <
            data[i + 1]["low"]
            and
            current["low"] <
            data[i + 2]["low"]
        )

        if is_high:
            highs.append(current)

        if is_low:
            lows.append(current)

    return highs, lows


# =========================================================
# STRUCTURE
# =========================================================

def analyze_structure(data):

    latest = data[-1]

    highs, lows = detect_swings(data)

    last_high = (
        highs[-1]
        if highs
        else None
    )

    previous_high = (
        highs[-2]
        if len(highs) >= 2
        else None
    )

    last_low = (
        lows[-1]
        if lows
        else None
    )

    previous_low = (
        lows[-2]
        if len(lows) >= 2
        else None
    )

    structure = "RANGE"

    higher_high = False
    higher_low = False
    lower_high = False
    lower_low = False

    if last_high and previous_high:

        higher_high = (
            last_high["high"] >
            previous_high["high"]
        )

        lower_high = (
            last_high["high"] <
            previous_high["high"]
        )

    if last_low and previous_low:

        higher_low = (
            last_low["low"] >
            previous_low["low"]
        )

        lower_low = (
            last_low["low"] <
            previous_low["low"]
        )

    # Strong bullish structure
    if (
        higher_high
        and higher_low
    ):

        if (
            last_high
            and latest["close"] >
            last_high["high"]
        ):

            structure = "BULLISH BOS"

        else:

            structure = "BULLISH STRUCTURE"

    # Strong bearish structure
    elif (
        lower_high
        and lower_low
    ):

        if (
            last_low
            and latest["close"] <
            last_low["low"]
        ):

            structure = "BEARISH BOS"

        else:

            structure = "BEARISH STRUCTURE"

    # Partial structure
    elif (
        higher_high
        or higher_low
    ):

        structure = "BULLISH STRUCTURE"

    elif (
        lower_high
        or lower_low
    ):

        structure = "BEARISH STRUCTURE"

    return {
        "structure": structure,
        "last_high": last_high,
        "last_low": last_low,
        "highs": highs,
        "lows": lows
    }


# =========================================================
# PREMIUM / DISCOUNT
# =========================================================

def premium_discount(data):

    high = max(
        candle["high"]
        for candle in data
    )

    low = min(
        candle["low"]
        for candle in data
    )

    equilibrium = (
        high + low
    ) / 2

    price = data[-1]["close"]

    if price < equilibrium:

        zone = "DISCOUNT"

    else:

        zone = "PREMIUM"

    return {
        "high": high,
        "low": low,
        "equilibrium": equilibrium,
        "zone": zone
    }


# =========================================================
# LIQUIDITY
# =========================================================

def liquidity_analysis(data):

    if len(data) < 8:

        return "NONE"

    latest = data[-1]

    previous = data[-7:-1]

    recent_high = max(
        candle["high"]
        for candle in previous
    )

    recent_low = min(
        candle["low"]
        for candle in previous
    )

    # Sell-side liquidity sweep
    if (
        latest["low"] < recent_low
        and
        latest["close"] > recent_low
    ):

        return "SELL-SIDE SWEEP"

    # Buy-side liquidity sweep
    if (
        latest["high"] > recent_high
        and
        latest["close"] < recent_high
    ):

        return "BUY-SIDE SWEEP"

    return "NONE"


# =========================================================
# MOMENTUM
# =========================================================

def momentum(data):

    if len(data) < 6:

        return "NEUTRAL"

    candles = data[-5:]

    bullish = 0
    bearish = 0

    for candle in candles:

        if candle["close"] > candle["open"]:

            bullish += 1

        elif candle["close"] < candle["open"]:

            bearish += 1

    if bullish >= 4:

        return "BULLISH"

    if bearish >= 4:

        return "BEARISH"

    return "NEUTRAL"


# =========================================================
# CANDLE CONFIRMATION
# =========================================================

def candle_confirmation(data):

    if len(data) < 3:

        return "NONE"

    current = data[-1]
    previous = data[-2]

    body = abs(
        current["close"] -
        current["open"]
    )

    candle_range = (
        current["high"] -
        current["low"]
    )

    if candle_range <= 0:

        return "NONE"

    body_ratio = (
        body /
        candle_range
    )

    # Strong bullish candle
    if (
        current["close"] >
        current["open"]
        and
        current["close"] >
        previous["high"]
        and
        body_ratio >= 0.50
    ):

        return "BULLISH CONFIRMATION"

    # Strong bearish candle
    if (
        current["close"] <
        current["open"]
        and
        current["close"] <
        previous["low"]
        and
        body_ratio >= 0.50
    ):

        return "BEARISH CONFIRMATION"

    return "NONE"


# =========================================================
# OPPORTUNITY SCORING
# =========================================================

def calculate_score(
    h4,
    h1,
    m5
):

    buy = 0
    sell = 0

    buy_reasons = []
    sell_reasons = []

    # -----------------------------------------------------
    # H4 = 25 POINTS
    # -----------------------------------------------------

    if "BULLISH" in h4["structure"]:

        buy += 25

        buy_reasons.append(
            "H4 bullish structure"
        )

    elif "BEARISH" in h4["structure"]:

        sell += 25

        sell_reasons.append(
            "H4 bearish structure"
        )

    # -----------------------------------------------------
    # H1 = 25 POINTS
    # -----------------------------------------------------

    if "BULLISH" in h1["structure"]:

        buy += 25

        buy_reasons.append(
            "H1 bullish structure"
        )

    elif "BEARISH" in h1["structure"]:

        sell += 25

        sell_reasons.append(
            "H1 bearish structure"
        )

    # -----------------------------------------------------
    # M5 STRUCTURE = 10 POINTS
    # -----------------------------------------------------

    if "BULLISH" in m5["structure"]:

        buy += 10

        buy_reasons.append(
            "M5 bullish structure"
        )

    elif "BEARISH" in m5["structure"]:

        sell += 10

        sell_reasons.append(
            "M5 bearish structure"
        )

    # -----------------------------------------------------
    # PD ZONE = 10 POINTS
    # -----------------------------------------------------

    if m5["pd"]["zone"] == "DISCOUNT":

        buy += 10

        buy_reasons.append(
            "Price in discount"
        )

    elif m5["pd"]["zone"] == "PREMIUM":

        sell += 10

        sell_reasons.append(
            "Price in premium"
        )

    # -----------------------------------------------------
    # LIQUIDITY = 10 POINTS
    # -----------------------------------------------------

    if m5["liquidity"] == "SELL-SIDE SWEEP":

        buy += 10

        buy_reasons.append(
            "Sell-side liquidity swept"
        )

    elif m5["liquidity"] == "BUY-SIDE SWEEP":

        sell += 10

        sell_reasons.append(
            "Buy-side liquidity swept"
        )

    # -----------------------------------------------------
    # MOMENTUM = 10 POINTS
    # -----------------------------------------------------

    if m5["momentum"] == "BULLISH":

        buy += 10

        buy_reasons.append(
            "Bullish momentum"
        )

    elif m5["momentum"] == "BEARISH":

        sell += 10

        sell_reasons.append(
            "Bearish momentum"
        )

    # -----------------------------------------------------
    # CANDLE CONFIRMATION = 10 POINTS
    # -----------------------------------------------------

    if (
        m5["confirmation"] ==
        "BULLISH CONFIRMATION"
    ):

        buy += 10

        buy_reasons.append(
            "M5 bullish confirmation"
        )

    elif (
        m5["confirmation"] ==
        "BEARISH CONFIRMATION"
    ):

        sell += 10

        sell_reasons.append(
            "M5 bearish confirmation"
        )

    # -----------------------------------------------------
    # FINAL
    # -----------------------------------------------------

    if buy > sell:

        return (
            "BUY",
            min(buy, 100),
            buy_reasons
        )

    if sell > buy:

        return (
            "SELL",
            min(sell, 100),
            sell_reasons
        )

    return (
        "WAIT",
        max(buy, sell),
        []
    )


# =========================================================
# TRADE PLAN
# =========================================================

def build_trade_plan(
    side,
    m5
):

    price = m5["price"]

    atr = m5["atr"]

    if atr is None:

        atr = (
            m5["pd"]["high"] -
            m5["pd"]["low"]
        ) / 20

    # Prevent absurdly small stops
    stop_distance = max(
        atr * 1.20,
        0.50
    )

    entry_buffer = max(
        atr * 0.20,
        0.10
    )

    if side == "BUY":

        entry_low = price
        entry_high = (
            price +
            entry_buffer
        )

        sl = (
            price -
            stop_distance
        )

        tp1 = (
            price +
            stop_distance * 1.5
        )

        tp2 = (
            price +
            stop_distance * 2.5
        )

    else:

        entry_low = (
            price -
            entry_buffer
        )

        entry_high = price

        sl = (
            price +
            stop_distance
        )

        tp1 = (
            price -
            stop_distance * 1.5
        )

        tp2 = (
            price -
            stop_distance * 2.5
        )

    risk = abs(
        price - sl
    )

    reward1 = abs(
        tp1 - price
    )

    reward2 = abs(
        tp2 - price
    )

    rr1 = (
        reward1 / risk
        if risk
        else 0
    )

    rr2 = (
        reward2 / risk
        if risk
        else 0
    )

    potential_min = (
        reward1 /
        PIP_SIZE
    )

    potential_max = (
        reward2 /
        PIP_SIZE
    )

    return {

        "entry_low":
            round(entry_low, 2),

        "entry_high":
            round(entry_high, 2),

        "sl":
            round(sl, 2),

        "tp1":
            round(tp1, 2),

        "tp2":
            round(tp2, 2),

        "potential_min":
            round(potential_min),

        "potential_max":
            round(potential_max),

        "rr1":
            round(rr1, 1),

        "rr2":
            round(rr2, 1)
    }


# =========================================================
# ALERT FILTER
# =========================================================

def should_send_alert(
    state,
    side,
    score,
    m5
):

    alert = state["alert"]

    last_sent = parse_time(
        alert.get("last_sent_at")
    )

    last_signature = (
        alert.get(
            "last_signature"
        )
    )

    signature = (
        f"{side}|"
        f"{m5['structure']}|"
        f"{m5['liquidity']}|"
        f"{m5['confirmation']}|"
        f"{round(m5['price'], 1)}"
    )

    # First alert
    if not last_sent:

        return True, signature

    elapsed = (
        utc_now() -
        last_sent
    ).total_seconds()

    # Direction changed
    if (
        alert.get("last_side")
        and
        alert["last_side"] != side
    ):

        return True, signature

    # New setup after cooldown
    if (
        elapsed >=
        ALERT_COOLDOWN_SECONDS
        and
        signature != last_signature
    ):

        return True, signature

    # Significant score improvement
    if (
        score >=
        int(
            alert.get(
                "last_score",
                0
            )
        ) + 10
    ):

        return True, signature

    return False, signature


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    response = requests.post(
        url,
        json=payload,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):

        raise Exception(
            "Telegram rejected message"
        )

    return data


# =========================================================
# MESSAGE
# =========================================================

def build_message(
    side,
    score,
    trade,
    session,
    m5,
    h4,
    h1
):

    if side == "BUY":

        emoji = "🟢"

    else:

        emoji = "🔴"

    reason_text = "\n".join(
        f"✓ {reason}"
        for reason in m5["reasons"]
    )

    message = f"""
🚨 BOSQUE FOREX AI

{emoji} VALID {side} OPPORTUNITY

💎 XAUUSD

⭐ OPPORTUNITY SCORE
{score}/100

🌍 SESSION
{session}

━━━━━━━━━━━━━━━━

📍 ENTRY RANGE
{trade["entry_low"]:.2f}
→
{trade["entry_high"]:.2f}

🛑 STOP LOSS
{trade["sl"]:.2f}

🎯 TP1
{trade["tp1"]:.2f}

🎯 TP2
{trade["tp2"]:.2f}

📏 POTENTIAL MOVE
{trade["potential_min"]} – {trade["potential_max"]} pips

⚖️ R:R
1:{trade["rr1"]}
→
1:{trade["rr2"]}

━━━━━━━━━━━━━━━━

🧠 MARKET INTELLIGENCE

H4
{h4["structure"]}

H1
{h1["structure"]}

M5
{m5["structure"]}

💧 LIQUIDITY
{m5["liquidity"]}

💎 PD ZONE
{m5["pd"]["zone"]}

🔥 MOMENTUM
{m5["momentum"]}

🕯 CONFIRMATION
{m5["confirmation"]}

━━━━━━━━━━━━━━━━

WHY THIS SETUP?

{reason_text}

━━━━━━━━━━━━━━━━

💰 CURRENT PRICE
{m5["price"]:.2f}

⏱ {utc_now().strftime("%Y-%m-%d %H:%M UTC")}

👑 Bosque Forex AI v2.0
""".strip()

    return message


# =========================================================
# MAIN
# =========================================================

def main():

    print()
    print("=" * 55)
    print("👑 BOSQUE FOREX AI v2.0")
    print("XAUUSD MARKET INTELLIGENCE ENGINE")
    print("=" * 55)

    # -----------------------------------------------------
    # ENV CHECK
    # -----------------------------------------------------

    if not TWELVEDATA_API_KEY:

        raise Exception(
            "TWELVEDATA_API_KEY missing"
        )

    if not TELEGRAM_BOT_TOKEN:

        raise Exception(
            "TELEGRAM_BOT_TOKEN missing"
        )

    if not TELEGRAM_CHAT_ID:

        raise Exception(
            "TELEGRAM_CHAT_ID missing"
        )

    state = load_state()

    # -----------------------------------------------------
    # M5
    # -----------------------------------------------------

    m5_data = get_timeframe_data(
        state,
        "m5",
        M5_CACHE_SECONDS,
        60
    )

    # -----------------------------------------------------
    # H1
    # -----------------------------------------------------

    h1_data = get_timeframe_data(
        state,
        "h1",
        H1_CACHE_SECONDS,
        60
    )

    # -----------------------------------------------------
    # H4
    # -----------------------------------------------------

    h4_data = get_timeframe_data(
        state,
        "h4",
        H4_CACHE_SECONDS,
        60
    )

    # -----------------------------------------------------
    # ANALYSIS
    # -----------------------------------------------------

    h4_structure = analyze_structure(
        h4_data
    )

    h1_structure = analyze_structure(
        h1_data
    )

    m5_structure = analyze_structure(
        m5_data
    )

    m5_pd = premium_discount(
        m5_data
    )

    m5_liquidity = liquidity_analysis(
        m5_data
    )

    m5_momentum = momentum(
        m5_data
    )

    m5_confirmation = candle_confirmation(
        m5_data
    )

    # -----------------------------------------------------
    # ATR
    # -----------------------------------------------------

    true_ranges = []

    for i in range(
        1,
        len(m5_data)
    ):

        high = m5_data[i]["high"]
        low = m5_data[i]["low"]
        previous_close = (
            m5_data[i - 1]["close"]
        )

        tr = max(
            high - low,
            abs(
                high -
                previous_close
            ),
            abs(
                low -
                previous_close
            )
        )

        true_ranges.append(tr)

    if len(true_ranges) >= 14:

        atr = (
            sum(
                true_ranges[-14:]
            ) / 14
        )

    else:

        atr = None

    m5 = {

        "price":
            m5_data[-1]["close"],

        "structure":
            m5_structure["structure"],

        "liquidity":
            m5_liquidity,

        "momentum":
            m5_momentum,

        "confirmation":
            m5_confirmation,

        "pd":
            m5_pd,

        "atr":
            atr
    }

    # -----------------------------------------------------
    # SCORE
    # -----------------------------------------------------

    side, score, reasons = calculate_score(
        h4_structure,
        h1_structure,
        m5
    )

    m5["reasons"] = reasons

    print()
    print("PRICE :", m5["price"])
    print("H4    :", h4_structure["structure"])
    print("H1    :", h1_structure["structure"])
    print("M5    :", m5_structure["structure"])
    print("ZONE  :", m5_pd["zone"])
    print("LIQ   :", m5_liquidity)
    print("MOM   :", m5_momentum)
    print("CONF  :", m5_confirmation)
    print("SIDE  :", side)
    print("SCORE :", score)

    # -----------------------------------------------------
    # BELOW 70
    # -----------------------------------------------------

    if score < MIN_SCORE:

        print()
        print(
            f"⏸ No valid opportunity "
            f"({score}/100)"
        )

        save_state(state)

        return

    # -----------------------------------------------------
    # TRADE PLAN
    # -----------------------------------------------------

    trade = build_trade_plan(
        side,
        m5
    )

    session = malaysia_session()

    # -----------------------------------------------------
    # TELEGRAM FILTER
    # -----------------------------------------------------

    send_alert, signature = should_send_alert(
        state,
        side,
        score,
        m5
    )

    if not send_alert:

        print()
        print(
            "🔕 Valid setup detected "
            "but Telegram cooldown active."
        )

        save_state(state)

        return

    # -----------------------------------------------------
    # MESSAGE
    # -----------------------------------------------------

    message = build_message(
        side,
        score,
        trade,
        session,
        m5,
        h4_structure,
        h1_structure
    )

    # -----------------------------------------------------
    # SEND
    # -----------------------------------------------------

    print()
    print("📲 Sending Telegram alert...")

    send_telegram(
        message
    )

    # -----------------------------------------------------
    # SAVE ALERT STATE
    # -----------------------------------------------------

    state["alert"] = {

        "last_sent_at":
            utc_iso(),

        "last_signature":
            signature,

        "last_side":
            side,

        "last_score":
            score
    }

    save_state(state)

    print()
    print("✅ TELEGRAM ALERT SENT")
    print("=" * 55)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        print()
        print("❌ ENGINE ERROR")
        print(error)

        raise