import os
import json
import math
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd


# =========================================================
# BOSQUE FOREX AI - SCALPING ENGINE V6 AUDITED
# H1 -> M15 -> M5
#
# ACTIVE / REAL:
# - Twelve Data M5 (1 market-data request per scan)
# - Local M15/H1 aggregation
# - H1 structure / regime
# - M15 setup engine
# - M5 confirmation
# - Liquidity sweep
# - Premium / Discount
# - ATR volatility
# - Directional SL/TP geometry validation
# - RR validation
# - Forex Factory weekly JSON news filter
# - News cache + fail-closed behaviour
# - Fresh-zone detection + mitigation count
# - Daily loss lock
# - Consecutive loss lock
# - Telegram alerts
# - Live journal persistence
# - Automatic WIN/LOSS outcome tracking
# - TP1 tracking
# - MFE/MAE tracking from M5 OHLC
# - Forward-test statistics
# - Performance by session/setup/regime
#
# HONESTLY NOT ACTIVE:
# - Historical 2022-2025 backtest until historical dataset exists
# - Position sizing until account balance/risk % are configured
# =========================================================


# =========================================================
# PATHS
# =========================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DASHBOARD_FILE = REPO_DIR / "dashboard_data.json"
STATE_FILE = ENGINE_DIR / "state.json"
JOURNAL_FILE = ENGINE_DIR / "trade_journal.json"
FORWARD_FILE = ENGINE_DIR / "forward_test.json"
BACKTEST_FILE = ENGINE_DIR / "backtest_results.json"
NEWS_CACHE_FILE = ENGINE_DIR / "news_cache.json"


# =========================================================
# DATA / CONNECTIONS
# =========================================================

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"
FOREX_FACTORY_JSON_URL = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
)

API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = "XAU/USD"
INTERVAL = "5min"
OUTPUT_SIZE = 500


# =========================================================
# CORE STRATEGY CONFIG
# =========================================================

MIN_SCORE = 70

# XAUUSD convention used by this project:
# 1 pip = 0.10 price movement
PIP_SIZE = 0.10

MIN_RISK_PIPS = 25
MAX_RISK_PIPS = 80

TP1_PIPS = 60
MIN_TP2_PIPS = 120
TP3_PIPS = 180
MIN_RR = 2.0


# =========================================================
# RISK LOCK CONFIG
# =========================================================

MAX_DAILY_LOSS_R = 2.0
MAX_CONSECUTIVE_LOSSES = 3


# =========================================================
# FRESH ZONE CONFIG
# =========================================================

# Zone width uses M15 ATR, bounded between 5 and 25 pips.
FRESH_ZONE_ATR_MULTIPLIER = 0.35
FRESH_ZONE_MIN_PIPS = 5
FRESH_ZONE_MAX_PIPS = 25

# Prior touch episodes:
# 0 = FRESH
# 1 = MITIGATED
# >=2 = USED and blocks new signal
MAX_FRESH_ZONE_TOUCHES = 1


# =========================================================
# NEWS CONFIG
# =========================================================

NEWS_CACHE_MINUTES = 30
NEWS_MAX_STALE_MINUTES = 240

NEWS_BLOCK_BEFORE_MINUTES = 30
NEWS_BLOCK_AFTER_MINUTES = 20
NEWS_CAUTION_BEFORE_MINUTES = 60
NEWS_CAUTION_AFTER_MINUTES = 30


# =========================================================
# SIGNAL / ALERT CONFIG
# =========================================================

SIGNAL_COOLDOWN_MINUTES = 30


# =========================================================
# TIME / SESSIONS
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


def now_utc():
    return datetime.now(timezone.utc)


def now_my():
    return now_utc().astimezone(MY_TZ)


def clean_for_json(value):
    if isinstance(value, dict):
        return {str(k): clean_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_for_json(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    return value


def save_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            clean_for_json(data),
            f,
            ensure_ascii=False,
            indent=2,
        )
    tmp.replace(path)


def load_json(path):
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def parse_dt(value):
    if value is None or value == "":
        return None
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def price_to_pips(distance):
    return abs(float(distance)) / PIP_SIZE


def pips_to_price(pips):
    return float(pips) * PIP_SIZE


def round_price(price):
    return round(float(price), 2)


def age_minutes(dt, reference=None):
    if dt is None:
        return None
    reference = reference or now_utc()
    return (
        reference.astimezone(timezone.utc)
        - dt.astimezone(timezone.utc)
    ).total_seconds() / 60.0


# =========================================================
# PERSISTENCE
# =========================================================


def load_previous_dashboard():
    return load_json(DASHBOARD_FILE)


def load_state(previous_dashboard=None):
    previous_dashboard = previous_dashboard or load_previous_dashboard()
    local = load_json(STATE_FILE)
    embedded = (
        previous_dashboard.get("_persistent_state", {})
        if isinstance(previous_dashboard, dict)
        else {}
    )

    merged = {}
    if isinstance(embedded, dict):
        merged.update(embedded)
    if isinstance(local, dict):
        merged.update(local)
    return merged


def save_state(state):
    save_json_atomic(STATE_FILE, state)


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


def is_preferred_session(session):
    return session in ("LONDON", "NEW YORK")


# =========================================================
# TWELVE DATA
# =========================================================


def fetch_m5():
    if not API_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY missing")

    response = requests.get(
        TWELVEDATA_URL,
        params={
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "outputsize": OUTPUT_SIZE,
            "apikey": API_KEY,
            "format": "JSON",
            "timezone": "UTC",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if "values" not in data:
        raise RuntimeError(f"Twelve Data error: {data}")

    df = pd.DataFrame(data["values"])
    if df.empty:
        raise RuntimeError("No market data returned")

    df["datetime"] = pd.to_datetime(
        df["datetime"], utc=True, errors="coerce"
    )

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.dropna(subset=["datetime", "open", "high", "low", "close"])
        .sort_values("datetime")
        .drop_duplicates("datetime")
        .reset_index(drop=True)
    )

    return df


def remove_incomplete_candle(df):
    if df.empty:
        return df

    last_time = pd.to_datetime(
        df.iloc[-1]["datetime"], utc=True
    ).to_pydatetime()

    if (now_utc() - last_time).total_seconds() < 300:
        return df.iloc[:-1].copy()

    return df.copy()


# =========================================================
# AGGREGATION
# =========================================================


def aggregate(df, minutes):
    x = df.copy()
    x["datetime"] = pd.to_datetime(x["datetime"], utc=True)
    x = x.set_index("datetime")

    out = x.resample(f"{minutes}min").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }
    )

    return out.dropna().reset_index()


# =========================================================
# SWINGS / STRUCTURE
# =========================================================


def find_swing_highs(df, left=2, right=2):
    highs = []
    if len(df) < left + right + 1:
        return highs

    for i in range(left, len(df) - right):
        value = float(df.iloc[i]["high"])
        left_values = df.iloc[i - left : i]["high"]
        right_values = df.iloc[i + 1 : i + right + 1]["high"]

        if (
            value > float(left_values.max())
            and value >= float(right_values.max())
        ):
            highs.append({"index": i, "price": value})

    return highs


def find_swing_lows(df, left=2, right=2):
    lows = []
    if len(df) < left + right + 1:
        return lows

    for i in range(left, len(df) - right):
        value = float(df.iloc[i]["low"])
        left_values = df.iloc[i - left : i]["low"]
        right_values = df.iloc[i + 1 : i + right + 1]["low"]

        if (
            value < float(left_values.min())
            and value <= float(right_values.min())
        ):
            lows.append({"index": i, "price": value})

    return lows


def analyze_structure(df):
    highs = find_swing_highs(df)
    lows = find_swing_lows(df)

    direction = "RANGE"
    bos = None

    if len(highs) >= 2 and len(lows) >= 2:
        old_high = highs[-2]["price"]
        new_high = highs[-1]["price"]
        old_low = lows[-2]["price"]
        new_low = lows[-1]["price"]

        if new_high > old_high and new_low > old_low:
            direction = "BULLISH"
        elif new_high < old_high and new_low < old_low:
            direction = "BEARISH"

    current_close = float(df.iloc[-1]["close"])

    if highs and current_close > highs[-1]["price"]:
        bos = "BULLISH BOS"

    if lows and current_close < lows[-1]["price"]:
        bos = "BEARISH BOS"

    return {
        "direction": direction,
        "bos": bos,
        "swing_high": highs[-1]["price"] if highs else None,
        "swing_low": lows[-1]["price"] if lows else None,
        "swing_highs_count": len(highs),
        "swing_lows_count": len(lows),
    }
    # =========================================================
# RANGE / PD / LIQUIDITY
# =========================================================


def analyze_range(df, lookback=20):
    if len(df) < lookback:
        return {
            "is_range": False,
            "high": None,
            "low": None,
            "width": None,
            "location": None,
        }

    x = df.tail(lookback)
    high = float(x["high"].max())
    low = float(x["low"].min())
    close = float(x.iloc[-1]["close"])
    width = high - low
    location = (close - low) / width if width > 0 else 0.5

    is_range = 0.20 <= location <= 0.80 or width < close * 0.01

    return {
        "is_range": bool(is_range),
        "high": round_price(high),
        "low": round_price(low),
        "width": round_price(width),
        "location": round(location, 4),
    }


def pd_zone(df, lookback=20):
    if len(df) < 5:
        return {
            "zone": "UNKNOWN",
            "equilibrium": None,
            "high": None,
            "low": None,
        }

    x = df.tail(lookback)
    high = float(x["high"].max())
    low = float(x["low"].min())
    equilibrium = (high + low) / 2.0
    close = float(x.iloc[-1]["close"])

    if close > equilibrium:
        zone = "PREMIUM"
    elif close < equilibrium:
        zone = "DISCOUNT"
    else:
        zone = "EQUILIBRIUM"

    return {
        "zone": zone,
        "equilibrium": round_price(equilibrium),
        "high": round_price(high),
        "low": round_price(low),
    }


def liquidity_analysis(df):
    highs = find_swing_highs(df)
    lows = find_swing_lows(df)

    result = {
        "buy_side_sweep": False,
        "sell_side_sweep": False,
        "swept_level": None,
        "description": "NONE",
    }

    current_high = float(df.iloc[-1]["high"])
    current_low = float(df.iloc[-1]["low"])
    current_close = float(df.iloc[-1]["close"])

    if highs:
        swing_high = highs[-1]["price"]
        if current_high > swing_high and current_close < swing_high:
            result.update(
                {
                    "buy_side_sweep": True,
                    "swept_level": round_price(swing_high),
                    "description": "BUY-SIDE LIQUIDITY SWEPT",
                }
            )

    if lows:
        swing_low = lows[-1]["price"]
        if current_low < swing_low and current_close > swing_low:
            result.update(
                {
                    "sell_side_sweep": True,
                    "swept_level": round_price(swing_low),
                    "description": "SELL-SIDE LIQUIDITY SWEPT",
                }
            )

    return result


# =========================================================
# REAL LIQUIDITY LEVELS
# =========================================================


def calculate_liquidity_levels(m5, reference_my):
    x = m5.copy()
    x["datetime"] = pd.to_datetime(x["datetime"], utc=True)
    x["datetime_my"] = x["datetime"].dt.tz_convert(MY_TZ)
    x["local_date"] = x["datetime_my"].dt.date

    today = reference_my.date()
    previous_dates = sorted(
        d for d in x["local_date"].dropna().unique() if d < today
    )

    pdh = None
    pdl = None

    if previous_dates:
        prev = x[x["local_date"] == previous_dates[-1]]
        if not prev.empty:
            pdh = round_price(prev["high"].max())
            pdl = round_price(prev["low"].min())

    current_day = x[x["local_date"] == today].copy()

    asia = current_day[
        (current_day["datetime_my"].dt.hour >= 7)
        & (current_day["datetime_my"].dt.hour < 15)
    ]

    asia_high = round_price(asia["high"].max()) if not asia.empty else None
    asia_low = round_price(asia["low"].min()) if not asia.empty else None

    current_session = get_session(reference_my)
    session_rows = pd.DataFrame()

    if current_session == "ASIAN":
        session_rows = asia

    elif current_session == "LONDON":
        session_rows = current_day[
            (current_day["datetime_my"].dt.hour >= 15)
            & (current_day["datetime_my"].dt.hour < 20)
        ]

    elif current_session == "NEW YORK":
        if reference_my.hour >= 20:
            session_rows = current_day[
                (current_day["datetime_my"].dt.hour >= 20)
                & (current_day["datetime_my"].dt.hour < 23)
            ]
        else:
            yesterday = today - timedelta(days=1)
            previous_evening = x[
                (x["local_date"] == yesterday)
                & (x["datetime_my"].dt.hour >= 20)
            ]
            early_today = current_day[current_day["datetime_my"].dt.hour < 1]
            session_rows = pd.concat(
                [previous_evening, early_today], ignore_index=True
            )

    session_high = (
        round_price(session_rows["high"].max())
        if not session_rows.empty
        else None
    )
    session_low = (
        round_price(session_rows["low"].min())
        if not session_rows.empty
        else None
    )

    return {
        "pdh": pdh,
        "pdl": pdl,
        "asia_high": asia_high,
        "asia_low": asia_low,
        "session_high": session_high,
        "session_low": session_low,
    }


# =========================================================
# MOMENTUM / CANDLE / ATR
# =========================================================


def momentum(df, lookback=6):
    if len(df) < lookback + 1:
        return {"direction": "NEUTRAL", "strength": 0}

    closes = df["close"].tail(lookback + 1).tolist()
    up = 0
    down = 0

    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            up += 1
        elif closes[i] < closes[i - 1]:
            down += 1

    if up >= 4:
        return {"direction": "BULLISH", "strength": up}
    if down >= 4:
        return {"direction": "BEARISH", "strength": down}

    return {"direction": "NEUTRAL", "strength": max(up, down)}


def candle_confirmation(df):
    c = df.iloc[-1]
    body = abs(float(c["close"]) - float(c["open"]))
    full_range = float(c["high"]) - float(c["low"])

    if full_range <= 0:
        return {"bullish": False, "bearish": False}

    ratio = body / full_range

    return {
        "bullish": bool(float(c["close"]) > float(c["open"]) and ratio >= 0.55),
        "bearish": bool(float(c["close"]) < float(c["open"]) and ratio >= 0.55),
    }


def calculate_atr(df, period=14):
    if len(df) < period + 1:
        return None

    x = df.copy()
    prev_close = x["close"].shift(1)
    tr = pd.concat(
        [
            x["high"] - x["low"],
            abs(x["high"] - prev_close),
            abs(x["low"] - prev_close),
        ],
        axis=1,
    ).max(axis=1)

    atr_value = tr.rolling(period).mean().iloc[-1]

    if pd.isna(atr_value):
        return None

    return float(atr_value)


def volatility_analysis(df):
    atr_value = calculate_atr(df)

    if atr_value is None:
        return {"atr": None, "atr_pips": None, "condition": "UNKNOWN"}

    atr_pips = price_to_pips(atr_value)

    if atr_pips < 25:
        condition = "LOW"
    elif atr_pips <= 70:
        condition = "NORMAL"
    else:
        condition = "HIGH"

    return {
        "atr": round_price(atr_value),
        "atr_pips": round(atr_pips, 1),
        "condition": condition,
    }


# =========================================================
# M15 SETUP
# =========================================================


def detect_m15_setup(h1, m15):
    h1_direction = h1["direction"]
    m15_structure = analyze_structure(m15)
    m15_pd = pd_zone(m15)
    m15_liquidity = liquidity_analysis(m15)
    range_info = analyze_range(m15)

    direction = None
    opportunity = "NO VALID SETUP"
    reason = []

    if range_info["is_range"]:
        if (
            m15_liquidity["sell_side_sweep"]
            and range_info["location"] is not None
            and range_info["location"] <= 0.25
        ):
            direction = "BUY"
            opportunity = "BUY RANGE REVERSAL"
            reason.append("M15 range low + sell-side sweep")

        elif (
            m15_liquidity["buy_side_sweep"]
            and range_info["location"] is not None
            and range_info["location"] >= 0.75
        ):
            direction = "SELL"
            opportunity = "SELL RANGE REVERSAL"
            reason.append("M15 range high + buy-side sweep")

    if direction is None:
        if m15_liquidity["sell_side_sweep"]:
            direction = "BUY"
            opportunity = "BUY LIQUIDITY SWEEP"
            reason.append("M15 sell-side liquidity sweep")

        elif m15_liquidity["buy_side_sweep"]:
            direction = "SELL"
            opportunity = "SELL LIQUIDITY SWEEP"
            reason.append("M15 buy-side liquidity sweep")

    if direction is None:
        if h1_direction == "BULLISH" and m15_pd["zone"] == "DISCOUNT":
            direction = "BUY"
            opportunity = "BUY PULLBACK"
            reason.append("H1 bullish + M15 discount")

        elif h1_direction == "BEARISH" and m15_pd["zone"] == "PREMIUM":
            direction = "SELL"
            opportunity = "SELL PULLBACK"
            reason.append("H1 bearish + M15 premium")

    if direction is None:
        if m15_structure["bos"] == "BULLISH BOS":
            direction = "BUY"
            opportunity = "BUY BREAKOUT RETEST"
            reason.append("M15 bullish BOS")

        elif m15_structure["bos"] == "BEARISH BOS":
            direction = "SELL"
            opportunity = "SELL BREAKOUT RETEST"
            reason.append("M15 bearish BOS")

    return {
        "direction": direction,
        "opportunity": opportunity,
        "valid": direction is not None,
        "reason": reason,
        "pd": m15_pd,
        "liquidity": m15_liquidity,
        "structure": m15_structure,
        "range": range_info,
    }


# =========================================================
# M5 CONFIRMATION
# =========================================================


def m5_confirmation(df, expected_direction):
    structure = analyze_structure(df)
    momentum_data = momentum(df)
    candle = candle_confirmation(df)
    bos = structure["bos"]

    if expected_direction == "BUY":
        bos_ok = bos == "BULLISH BOS"
        candle_ok = (
            candle["bullish"]
            and momentum_data["direction"] == "BULLISH"
        )

        return {
            "confirmed": bool(bos_ok or candle_ok),
            "bos": bos,
            "candle": bool(candle_ok),
            "momentum": momentum_data["direction"],
            "reason": (
                "M5 bullish BOS"
                if bos_ok
                else "M5 bullish candle + momentum"
                if candle_ok
                else "NO CONFIRMATION"
            ),
        }

    if expected_direction == "SELL":
        bos_ok = bos == "BEARISH BOS"
        candle_ok = (
            candle["bearish"]
            and momentum_data["direction"] == "BEARISH"
        )

        return {
            "confirmed": bool(bos_ok or candle_ok),
            "bos": bos,
            "candle": bool(candle_ok),
            "momentum": momentum_data["direction"],
            "reason": (
                "M5 bearish BOS"
                if bos_ok
                else "M5 bearish candle + momentum"
                if candle_ok
                else "NO CONFIRMATION"
            ),
        }

    return {
        "confirmed": False,
        "bos": bos,
        "candle": False,
        "momentum": momentum_data["direction"],
        "reason": "NO DIRECTION",
    }


# =========================================================
# FRESH ZONE ENGINE
# =========================================================


def count_zone_touch_episodes(df, start_index, zone_low, zone_high):
    if start_index is None:
        return 0

    end_index = len(df) - 1

    if start_index + 1 >= end_index:
        return 0

    touches = 0
    was_inside = False

    for i in range(start_index + 1, end_index):
        row = df.iloc[i]

        candle_high = float(row["high"])
        candle_low = float(row["low"])

        inside = (
            candle_low <= zone_high
            and candle_high >= zone_low
        )

        if inside and not was_inside:
            touches += 1

        was_inside = inside

    return touches
    def analyze_fresh_zone(m15, direction):

    if direction not in ("BUY", "SELL"):

        return {
            "valid": False,
            "status": "UNAVAILABLE",
            "direction": direction,
            "zone_low": None,
            "zone_high": None,
            "touches": None,
            "current_interaction": False,
            "reason": "No valid trade direction",
            "method": "LAST CONFIRMED M15 SWING + ATR BUFFER",
        }

    highs = find_swing_highs(m15)
    lows = find_swing_lows(m15)

    atr_value = calculate_atr(m15)

    if atr_value is None:

        return {
            "valid": False,
            "status": "UNAVAILABLE",
            "direction": direction,
            "zone_low": None,
            "zone_high": None,
            "touches": None,
            "current_interaction": False,
            "reason": "M15 ATR unavailable",
            "method": "LAST CONFIRMED M15 SWING + ATR BUFFER",
        }

    width = max(
        pips_to_price(FRESH_ZONE_MIN_PIPS),
        atr_value * FRESH_ZONE_ATR_MULTIPLIER,
    )

    width = min(
        width,
        pips_to_price(FRESH_ZONE_MAX_PIPS),
    )

    if direction == "BUY":

        if not lows:

            return {
                "valid": False,
                "status": "UNAVAILABLE",
                "direction": direction,
                "zone_low": None,
                "zone_high": None,
                "touches": None,
                "current_interaction": False,
                "reason": "No confirmed M15 swing low",
                "method": "LAST CONFIRMED M15 SWING + ATR BUFFER",
            }

        anchor = lows[-1]

        zone_low = float(anchor["price"])
        zone_high = zone_low + width

        anchor_type = "SWING LOW"

    else:

        if not highs:

            return {
                "valid": False,
                "status": "UNAVAILABLE",
                "direction": direction,
                "zone_low": None,
                "zone_high": None,
                "touches": None,
                "current_interaction": False,
                "reason": "No confirmed M15 swing high",
                "method": "LAST CONFIRMED M15 SWING + ATR BUFFER",
            }

        anchor = highs[-1]

        zone_high = float(anchor["price"])
        zone_low = zone_high - width

        anchor_type = "SWING HIGH"

    touches = count_zone_touch_episodes(
        m15,
        anchor["index"],
        zone_low,
        zone_high,
    )

    current = m15.iloc[-1]

    current_interaction = (
        float(current["low"]) <= zone_high
        and float(current["high"]) >= zone_low
    )

    if touches == 0:

        status = "FRESH"
        valid = True
        reason = "No prior mitigation after zone creation"

    elif touches <= MAX_FRESH_ZONE_TOUCHES:

        status = "MITIGATED"
        valid = True
        reason = (
            f"Zone previously mitigated "
            f"{touches} time(s)"
        )

    else:

        status = "USED"
        valid = False
        reason = (
            f"Zone already mitigated "
            f"{touches} times"
        )

    created_time = pd.to_datetime(
        m15.iloc[
            anchor["index"]
        ]["datetime"],
        utc=True,
    ).isoformat()

    return {
        "valid": valid,
        "status": status,
        "direction": direction,
        "anchor_type": anchor_type,
        "anchor_price": round_price(
            anchor["price"]
        ),
        "zone_low": round_price(
            zone_low
        ),
        "zone_high": round_price(
            zone_high
        ),
        "zone_width_pips": round(
            price_to_pips(width),
            1,
        ),
        "touches": touches,
        "current_interaction": bool(
            current_interaction
        ),
        "created_at": created_time,
        "reason": reason,
        "method": "LAST CONFIRMED M15 SWING + ATR BUFFER",
    }


# =========================================================
# SCORE
# =========================================================


def calculate_score(
    h1,
    m15_setup,
    m5_confirm,
    session,
    volatility,
):

    score = 0

    if h1["direction"] in (
        "BULLISH",
        "BEARISH",
    ):
        score += 15

    if h1["bos"]:
        score += 5

    if m15_setup["valid"]:
        score += 10

    if (
        m15_setup["liquidity"].get(
            "buy_side_sweep"
        )
        or m15_setup["liquidity"].get(
            "sell_side_sweep"
        )
    ):
        score += 10

    if m15_setup[
        "structure"
    ].get("bos"):
        score += 10

    if m5_confirm["confirmed"]:
        score += 15

    if m5_confirm["bos"]:
        score += 10

    if m5_confirm["candle"]:
        score += 10

    direction = m15_setup[
        "direction"
    ]

    zone = m15_setup[
        "pd"
    ]["zone"]

    if (
        direction == "BUY"
        and zone == "DISCOUNT"
    ):
        score += 5

    elif (
        direction == "SELL"
        and zone == "PREMIUM"
    ):
        score += 5

    # Session = bonus only.
    if is_preferred_session(
        session
    ):
        score += 5

    if (
        volatility["condition"]
        == "NORMAL"
    ):
        score += 5

    return min(
        score,
        100,
    )


# =========================================================
# TRADE PLAN
# =========================================================


def create_trade_plan(
    direction,
    m5,
    volatility,
):

    if direction not in (
        "BUY",
        "SELL",
    ):

        return {
            "valid": False,
            "reason": "NO VALID DIRECTION",
        }

    entry = float(
        m5.iloc[-1]["close"]
    )

    structure = analyze_structure(
        m5
    )

    swing_low = structure[
        "swing_low"
    ]

    swing_high = structure[
        "swing_high"
    ]

    atr_value = volatility.get(
        "atr"
    )

    buffer = 0.20

    if atr_value:

        buffer = max(
            0.20,
            float(atr_value) * 0.20,
        )

    if direction == "BUY":

        if swing_low is None:

            return {
                "valid": False,
                "reason": "BUY requires swing low",
                "entry": round_price(entry),
            }

        if float(swing_low) >= entry:

            return {
                "valid": False,
                "reason": (
                    "BUY invalid: "
                    "swing low is not below entry"
                ),
                "entry": round_price(entry),
                "swing_low": round_price(
                    swing_low
                ),
            }

        sl = (
            float(swing_low)
            - buffer
        )

    else:

        if swing_high is None:

            return {
                "valid": False,
                "reason": "SELL requires swing high",
                "entry": round_price(entry),
            }

        if float(swing_high) <= entry:

            return {
                "valid": False,
                "reason": (
                    "SELL invalid: "
                    "swing high is not above entry"
                ),
                "entry": round_price(entry),
                "swing_high": round_price(
                    swing_high
                ),
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
        or risk_pips > MAX_RISK_PIPS
    ):

        return {
            "valid": False,
            "reason": (
                f"Risk {risk_pips:.1f} "
                f"pips outside "
                f"{MIN_RISK_PIPS}-"
                f"{MAX_RISK_PIPS}"
            ),
            "entry": round_price(entry),
            "sl": round_price(sl),
            "risk_pips": round(
                risk_pips,
                1,
            ),
        }

    tp2_pips = max(
        MIN_TP2_PIPS,
        risk_pips * 2.0,
    )

    tp3_pips = max(
        TP3_PIPS,
        risk_pips * 3.0,
    )

    if direction == "BUY":

        tp1 = entry + pips_to_price(
            TP1_PIPS
        )

        tp2 = entry + pips_to_price(
            tp2_pips
        )

        tp3 = entry + pips_to_price(
            tp3_pips
        )

        geometry_ok = (
            sl
            < entry
            < tp1
            < tp2
            < tp3
        )

    else:

        tp1 = entry - pips_to_price(
            TP1_PIPS
        )

        tp2 = entry - pips_to_price(
            tp2_pips
        )

        tp3 = entry - pips_to_price(
            tp3_pips
        )

        geometry_ok = (
            sl
            > entry
            > tp1
            > tp2
            > tp3
        )

    if not geometry_ok:

        return {
            "valid": False,
            "reason": (
                "TRADE PLAN GEOMETRY INVALID"
            ),
        }

    rr_tp1 = (
        TP1_PIPS
        / risk_pips
    )

    rr_tp2 = (
        tp2_pips
        / risk_pips
    )

    rr_tp3 = (
        tp3_pips
        / risk_pips
    )

    if rr_tp2 < MIN_RR:

        return {
            "valid": False,
            "reason": "TP2 RR below minimum",
        }

    return {
        "valid": True,
        "geometry": "VALID",
        "direction": direction,
        "entry": round_price(entry),
        "sl": round_price(sl),
        "risk_pips": round(
            risk_pips,
            1,
        ),
        "tp1": round_price(tp1),
        "tp2": round_price(tp2),
        "tp3": round_price(tp3),
        "tp1_pips": round(
            TP1_PIPS,
            1,
        ),
        "tp2_pips": round(
            tp2_pips,
            1,
        ),
        "tp3_pips": round(
            tp3_pips,
            1,
        ),
        "rr_tp1": round(
            rr_tp1,
            2,
        ),
        "rr_tp2": round(
            rr_tp2,
            2,
        ),
        "rr_tp3": round(
            rr_tp3,
            2,
        ),
        "risk_level": (
            "LOW"
            if risk_pips <= 40
            else "MEDIUM"
        ),
    }


# =========================================================
# FOREX FACTORY NEWS
# =========================================================


def normalize_ff_event(event):

    if not isinstance(
        event,
        dict,
    ):
        return None

    country = str(
        event.get(
            "country",
            "",
        )
    ).strip().upper()

    impact = str(
        event.get(
            "impact",
            "",
        )
    ).strip().title()

    title = str(
        event.get(
            "title",
            "",
        )
    ).strip()

    event_dt = parse_dt(
        event.get("date")
    )

    if (
        not title
        or not country
        or event_dt is None
    ):
        return None

    return {
        "title": title,
        "country": country,
        "impact": impact,
        "date": event_dt.isoformat(),
        "forecast": event.get(
            "forecast"
        ),
        "previous": event.get(
            "previous"
        ),
    }


def get_cached_news(
    previous_dashboard,
):

    candidates = []

    local_cache = load_json(
        NEWS_CACHE_FILE
    )

    if local_cache:
        candidates.append(
            local_cache
        )

    embedded = (
        previous_dashboard.get(
            "_news_cache",
            {},
        )
        if isinstance(
            previous_dashboard,
            dict,
        )
        else {}
    )

    if embedded:
        candidates.append(
            embedded
        )

    best = None
    best_time = None

    for cache in candidates:

        fetched_at = parse_dt(
            cache.get("fetched_at")
        )

        if fetched_at is None:
            continue

        if (
            best_time is None
            or fetched_at > best_time
        ):
            best = cache
            best_time = fetched_at

    return best or {}


def fetch_forexfactory_calendar(
    previous_dashboard,
):

    reference = now_utc()

    cached = get_cached_news(
        previous_dashboard
    )

    cached_at = parse_dt(
        cached.get(
            "fetched_at"
        )
    )

    cached_age = age_minutes(
        cached_at,
        reference,
    )

    if (
        cached
        and cached_age is not None
        and cached_age <= NEWS_CACHE_MINUTES
        and isinstance(
            cached.get("events"),
            list,
        )
        and cached.get("events")
    ):

        return {
            "source": "FOREX FACTORY",
            "feed_status": "CACHE",
            "fetched_at": (
                cached_at.isoformat()
            ),
            "age_minutes": round(
                cached_age,
                1,
            ),
            "events": cached["events"],
            "error": None,
        }

    try:

        response = requests.get(
            FOREX_FACTORY_JSON_URL,
            headers={
                "User-Agent": (
                    "BosqueForexAI/6.0"
                ),
                "Accept": (
                    "application/json"
                ),
            },
            timeout=20,
        )

        response.raise_for_status()

        raw = response.json()

        if not isinstance(
            raw,
            list,
        ):

            raise RuntimeError(
                "Forex Factory feed "
                "did not return a list"
            )

        events = []

        for item in raw:

            event = normalize_ff_event(
                item
            )

            if event:

                events.append(
                    event
                )

        if not events:

            raise RuntimeError(
                "Forex Factory returned "
                "zero parseable events"
            )

        fetched_at = (
            reference.isoformat()
        )

        cache = {
            "source": "FOREX FACTORY",
            "fetched_at": fetched_at,
            "events": events,
        }

        save_json_atomic(
            NEWS_CACHE_FILE,
            cache,
        )

        return {
            "source": "FOREX FACTORY",
            "feed_status": "LIVE",
            "fetched_at": fetched_at,
            "age_minutes": 0.0,
            "events": events,
            "error": None,
        }

    except Exception as exc:

        if (
            cached
            and cached_age is not None
            and cached_age
            <= NEWS_MAX_STALE_MINUTES
            and isinstance(
                cached.get("events"),
                list,
            )
            and cached.get("events")
        ):

            return {
                "source": "FOREX FACTORY",
                "feed_status": (
                    "STALE CACHE"
                ),
                "fetched_at": (
                    cached_at.isoformat()
                    if cached_at
                    else None
                ),
                "age_minutes": round(
                    cached_age,
                    1,
                ),
                "events": cached.get(
                    "events",
                    [],
                ),
                "error": str(exc),
            }

        return {
            "source": "FOREX FACTORY",
            "feed_status": "ERROR",
            "fetched_at": (
                cached_at.isoformat()
                if cached_at
                else None
            ),
            "age_minutes": (
                round(
                    cached_age,
                    1,
                )
                if cached_age
                is not None
                else None
            ),
            "events": [],
            "error": str(exc),
        }


def evaluate_news_filter(
    feed,
    reference=None,
):

    reference = (
        reference
        or now_utc()
    ).astimezone(
        timezone.utc
    )

    feed_status = feed.get(
        "feed_status",
        "ERROR",
    )

    if feed_status == "ERROR":

        return {
            "ok": False,
            "status": "BLOCK",
            "filter": "BLOCK",
            "high_impact": "UNKNOWN",
            "event": None,
            "event_time": None,
            "minutes_to_news": None,
            "minutes_since_news": None,
            "reason": "NEWS FEED UNAVAILABLE",
            "feed_status": feed_status,
            "source": feed.get("source"),
            "feed_age_minutes": feed.get(
                "age_minutes"
            ),
            "error": feed.get("error"),
        }

    relevant = []

    for event in feed.get(
        "events",
        [],
    ):

        if (
            str(
                event.get(
                    "country",
                    "",
                )
            ).upper()
            != "USD"
        ):
            continue

        if (
            str(
                event.get(
                    "impact",
                    "",
                )
            ).title()
            != "High"
        ):
            continue

        event_dt = parse_dt(
            event.get("date")
        )

        if event_dt is None:
            continue

        diff_minutes = (
            event_dt
            - reference
        ).total_seconds() / 60.0

        relevant.append(
            {
                **event,
                "_dt": event_dt,
                "_diff": diff_minutes,
            }
        )

    relevant.sort(
        key=lambda item: abs(
            item["_diff"]
        )
    )

    block_event = None
    caution_event = None

    for event in relevant:

        diff = event["_diff"]

        if (
            -NEWS_BLOCK_AFTER_MINUTES
            <= diff
            <= NEWS_BLOCK_BEFORE_MINUTES
        ):

            block_event = event
            break

        pre_caution = (
            NEWS_BLOCK_BEFORE_MINUTES
            < diff
            <= NEWS_CAUTION_BEFORE_MINUTES
        )

        post_caution = (
            -NEWS_CAUTION_AFTER_MINUTES
            <= diff
            < -NEWS_BLOCK_AFTER_MINUTES
        )

        if (
            caution_event is None
            and (
                pre_caution
                or post_caution
            )
        ):
            caution_event = event

    if block_event:

        diff = block_event["_diff"]

        before = diff >= 0

        return {
            "ok": False,
            "status": "BLOCK",
            "filter": "BLOCK",
            "high_impact": "YES",
            "event": block_event[
                "title"
            ],
            "event_time": block_event[
                "_dt"
            ].isoformat(),
            "minutes_to_news": (
                round(diff, 1)
                if before
                else None
            ),
            "minutes_since_news": (
                round(abs(diff), 1)
                if not before
                else None
            ),
            "reason": (
                "HIGH IMPACT USD NEWS APPROACHING"
                if before
                else "POST-NEWS COOLDOWN"
            ),
            "feed_status": feed_status,
            "source": feed.get("source"),
            "feed_age_minutes": feed.get(
                "age_minutes"
            ),
            "error": feed.get("error"),
        }

    if caution_event:

        diff = caution_event[
            "_diff"
        ]

        return {
            "ok": True,
            "status": "CAUTION",
            "filter": "PASS",
            "high_impact": "YES",
            "event": caution_event[
                "title"
            ],
            "event_time": caution_event[
                "_dt"
            ].isoformat(),
            "minutes_to_news": (
                round(diff, 1)
                if diff >= 0
                else None
            ),
            "minutes_since_news": (
                round(abs(diff), 1)
                if diff < 0
                else None
            ),
            "reason": (
                "HIGH IMPACT USD "
                "NEWS NEARBY"
            ),
            "feed_status": feed_status,
            "source": feed.get("source"),
            "feed_age_minutes": feed.get(
                "age_minutes"
            ),
            "error": feed.get("error"),
        }

    future = [
        e
        for e in relevant
        if e["_diff"] > 0
    ]

    next_event = (
        min(
            future,
            key=lambda e: e["_diff"],
        )
        if future
        else None
    )

    return {
        "ok": True,
        "status": "CLEAR",
        "filter": "PASS",
        "high_impact": (
            "YES"
            if next_event
            else "CLEAR"
        ),
        "event": (
            next_event["title"]
            if next_event
            else None
        ),
        "event_time": (
            next_event["_dt"].isoformat()
            if next_event
            else None
        ),
        "minutes_to_news": (
            round(
                next_event["_diff"],
                1,
            )
            if next_event
            else None
        ),
        "minutes_since_news": None,
        "reason": (
            "NO HIGH IMPACT USD "
            "NEWS IN BLOCK WINDOW"
        ),
        "feed_status": feed_status,
        "source": feed.get("source"),
        "feed_age_minutes": feed.get(
            "age_minutes"
        ),
        "error": feed.get("error"),
    }
    # =========================================================
# SIGNAL ID / COOLDOWN
# =========================================================


def make_signal_id(
    direction,
    opportunity,
    candle_time,
    entry,
    sl,
    tp2,
):

    raw = (
        f"{direction}|"
        f"{opportunity}|"
        f"{candle_time}|"
        f"{round(entry, 2)}|"
        f"{round(sl, 2)}|"
        f"{round(tp2, 2)}"
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:16]


def is_duplicate_or_cooldown(
    state,
    direction,
    opportunity,
    signal_id,
    timestamp,
):

    if (
        signal_id
        and signal_id
        == state.get(
            "last_signal_id"
        )
    ):
        return True

    last_time = parse_dt(
        state.get(
            "last_signal_time"
        )
        or state.get(
            "last_signal_sent"
        )
    )

    same_setup = (
        state.get(
            "last_signal_direction"
        )
        == direction
        and state.get(
            "last_signal_opportunity"
        )
        == opportunity
    )

    if last_time and same_setup:

        minutes = (
            timestamp.astimezone(
                timezone.utc
            )
            - last_time
        ).total_seconds() / 60.0

        if (
            0
            <= minutes
            < SIGNAL_COOLDOWN_MINUTES
        ):
            return True

    return False


# =========================================================
# TELEGRAM
# =========================================================


def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        return False

    try:

        response = requests.post(
            (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/"
                "sendMessage"
            ),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=20,
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
    volatility,
    news_result,
    fresh_zone,
    risk_lock,
):

    preferred = (
        "YES"
        if is_preferred_session(
            session
        )
        else "NO"
    )

    return (
        "👑 BOSQUE FOREX AI V6\n\n"

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

        f"⭐ Preferred Session: "
        f"{preferred}\n"

        f"📐 PD: "
        f"{pd_data['zone']}\n"

        f"🌱 Fresh Zone: "
        f"{fresh_zone['status']}\n"

        f"🌡 ATR: "
        f"{volatility.get('atr_pips')} "
        f"pips\n"

        f"📰 News: "
        f"{news_result.get('status')}\n"

        f"🛡 Risk Lock: "
        f"{'LOCKED' if risk_lock['locked'] else 'PASS'}\n\n"

        "⚠️ Manual confirmation required."
    )


# =========================================================
# JOURNAL PERSISTENCE
# =========================================================


def empty_journal():

    return {
        "version": "4.0",
        "symbol": SYMBOL,
        "created_at": (
            now_my().isoformat()
        ),
        "trades": [],
    }


def merge_journals(
    *journals,
):

    merged = empty_journal()

    by_id = {}

    for journal in journals:

        if not isinstance(
            journal,
            dict,
        ):
            continue

        trades = journal.get(
            "trades",
            [],
        )

        if not isinstance(
            trades,
            list,
        ):
            continue

        for trade in trades:

            if not isinstance(
                trade,
                dict,
            ):
                continue

            signal_id = trade.get(
                "signal_id"
            )

            if not signal_id:
                continue

            existing = by_id.get(
                signal_id
            )

            if existing is None:

                by_id[
                    signal_id
                ] = trade.copy()

                continue

            existing_closed = (
                existing.get(
                    "result"
                )
                != "OPEN"
            )

            trade_closed = (
                trade.get(
                    "result"
                )
                != "OPEN"
            )

            if (
                trade_closed
                and not existing_closed
            ):

                by_id[
                    signal_id
                ] = trade.copy()

                continue

            existing_updated = parse_dt(
                existing.get(
                    "closed_at"
                )
                or existing.get(
                    "timestamp"
                )
            )

            trade_updated = parse_dt(
                trade.get(
                    "closed_at"
                )
                or trade.get(
                    "timestamp"
                )
            )

            if (
                trade_updated
                and (
                    existing_updated
                    is None
                    or trade_updated
                    > existing_updated
                )
            ):

                by_id[
                    signal_id
                ] = trade.copy()

    merged["trades"] = sorted(
        by_id.values(),
        key=lambda t:
        parse_dt(
            t.get(
                "signal_candle_time"
            )
            or t.get(
                "timestamp"
            )
        )
        or datetime.min.replace(
            tzinfo=timezone.utc
        ),
    )

    return merged


def load_journal(
    previous_dashboard=None,
):

    previous_dashboard = (
        previous_dashboard
        or load_previous_dashboard()
    )

    local = load_json(
        JOURNAL_FILE
    )

    embedded = (
        previous_dashboard.get(
            "_journal_store",
            {},
        )
        if isinstance(
            previous_dashboard,
            dict,
        )
        else {}
    )

    journal = merge_journals(
        local,
        embedded,
    )

    return journal


def save_journal(journal):

    save_json_atomic(
        JOURNAL_FILE,
        journal,
    )


# =========================================================
# JOURNAL SIGNAL ENTRY
# =========================================================


def journal_signal(
    journal,
    signal,
    setup,
    m5_confirm,
    plan,
    score,
    session,
    news_result,
    fresh_zone,
    market_mode,
    h1_structure,
    volatility,
    scan_time,
    signal_candle_time,
):

    if not signal.get(
        "active",
        False,
    ):
        return journal

    if not signal.get(
        "new_signal",
        False,
    ):
        return journal

    if not plan.get(
        "valid",
        False,
    ):
        return journal

    signal_id = signal.get(
        "id"
    )

    if not signal_id:
        return journal

    if any(
        t.get("signal_id")
        == signal_id
        for t in journal["trades"]
    ):
        return journal

    trade = {
        "signal_id": signal_id,

        "timestamp": (
            scan_time.isoformat()
        ),

        "signal_candle_time": (
            signal_candle_time.isoformat()
        ),

        "symbol": SYMBOL,

        "direction": signal.get(
            "direction"
        ),

        "opportunity": signal.get(
            "opportunity"
        ),

        "score": score,

        "session": session,

        "preferred_session": (
            is_preferred_session(
                session
            )
        ),

        "market_mode": market_mode,

        "h1_direction": (
            h1_structure.get(
                "direction"
            )
        ),

        "h1_bos": h1_structure.get(
            "bos"
        ),

        "m15_pd_zone": (
            setup.get(
                "pd",
                {},
            ).get(
                "zone"
            )
        ),

        "liquidity": (
            setup.get(
                "liquidity",
                {},
            ).get(
                "description"
            )
        ),

        "fresh_zone_status": (
            fresh_zone.get(
                "status"
            )
        ),

        "fresh_zone_touches": (
            fresh_zone.get(
                "touches"
            )
        ),

        "fresh_zone_low": (
            fresh_zone.get(
                "zone_low"
            )
        ),

        "fresh_zone_high": (
            fresh_zone.get(
                "zone_high"
            )
        ),

        "atr_pips": (
            volatility.get(
                "atr_pips"
            )
        ),

        "news_status": (
            news_result.get(
                "status"
            )
        ),

        "news_event": (
            news_result.get(
                "event"
            )
        ),

        "news_minutes_to_event": (
            news_result.get(
                "minutes_to_news"
            )
        ),

        "entry": plan.get(
            "entry"
        ),

        "sl": plan.get(
            "sl"
        ),

        "tp1": plan.get(
            "tp1"
        ),

        "tp2": plan.get(
            "tp2"
        ),

        "tp3": plan.get(
            "tp3"
        ),

        "risk_pips": plan.get(
            "risk_pips"
        ),

        "tp1_pips": plan.get(
            "tp1_pips"
        ),

        "tp2_pips": plan.get(
            "tp2_pips"
        ),

        "tp3_pips": plan.get(
            "tp3_pips"
        ),

        "rr_tp2": plan.get(
            "rr_tp2"
        ),

        "result": "OPEN",

        "result_pips": None,

        "result_r": None,

        "closed_at": None,

        "close_reason": None,

        "tp1_hit": False,

        "tp1_hit_at": None,

        "tp2_hit": False,

        "tp3_hit": False,

        "mfe_pips": 0.0,

        "mae_pips": 0.0,

        "bars_held": 0,

        "tracking_precision": (
            "M5 OHLC CONSERVATIVE"
        ),

        "setup_reason": (
            setup.get(
                "reason",
                [],
            )
        ),

        "m5_confirmation": (
            m5_confirm.get(
                "reason"
            )
        ),
    }

    journal[
        "trades"
    ].append(
        trade
    )

    save_journal(
        journal
    )

    return journal


# =========================================================
# JOURNAL OUTCOME + MFE / MAE
# =========================================================


def update_open_trade_with_candle(
    trade,
    candle,
):

    if trade.get(
        "result"
    ) != "OPEN":

        return False

    direction = trade.get(
        "direction"
    )

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp1 = float(
        trade["tp1"]
    )

    tp2 = float(
        trade["tp2"]
    )

    tp3 = float(
        trade["tp3"]
    )

    risk_pips = float(
        trade["risk_pips"]
    )

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    candle_time = pd.to_datetime(
        candle["datetime"],
        utc=True,
    ).to_pydatetime()

    signal_time = parse_dt(
        trade.get(
            "signal_candle_time"
        )
        or trade.get(
            "timestamp"
        )
    )

    if (
        signal_time
        and candle_time
        <= signal_time
    ):
        return False

    before = json.dumps(
        clean_for_json(
            trade
        ),
        sort_keys=True,
    )

    trade["bars_held"] = (
        int(
            trade.get(
                "bars_held",
                0,
            )
        )
        + 1
    )

    if direction == "BUY":

        favorable = max(
            0.0,
            price_to_pips(
                high - entry
            ),
        )

        adverse = max(
            0.0,
            price_to_pips(
                entry - low
            ),
        )

        sl_hit = low <= sl
        tp1_hit = high >= tp1
        tp2_hit = high >= tp2
        tp3_hit = high >= tp3

    elif direction == "SELL":

        favorable = max(
            0.0,
            price_to_pips(
                entry - low
            ),
        )

        adverse = max(
            0.0,
            price_to_pips(
                high - entry
            ),
        )

        sl_hit = high >= sl
        tp1_hit = low <= tp1
        tp2_hit = low <= tp2
        tp3_hit = low <= tp3

    else:

        return False
            # Conservative:
    # if SL and TP are inside the same M5 candle,
    # assume SL happened first.

    if sl_hit:

        trade["mae_pips"] = max(
            float(
                trade.get(
                    "mae_pips",
                    0.0,
                )
            ),
            risk_pips,
        )

        trade["mfe_pips"] = max(
            float(
                trade.get(
                    "mfe_pips",
                    0.0,
                )
            ),
            0.0,
        )

        trade["result"] = "LOSS"

        trade["result_pips"] = round(
            -risk_pips,
            1,
        )

        trade["result_r"] = -1.0

        trade["closed_at"] = (
            candle_time.isoformat()
        )

        trade["close_reason"] = (
            "SL_AND_TP_SAME_CANDLE_SL_FIRST"
            if (
                tp1_hit
                or tp2_hit
                or tp3_hit
            )
            else "SL HIT"
        )

    else:

        trade["mfe_pips"] = round(
            max(
                float(
                    trade.get(
                        "mfe_pips",
                        0.0,
                    )
                ),
                favorable,
            ),
            1,
        )

        trade["mae_pips"] = round(
            max(
                float(
                    trade.get(
                        "mae_pips",
                        0.0,
                    )
                ),
                adverse,
            ),
            1,
        )

        if (
            tp1_hit
            and not trade.get(
                "tp1_hit"
            )
        ):

            trade[
                "tp1_hit"
            ] = True

            trade[
                "tp1_hit_at"
            ] = candle_time.isoformat()

        if tp3_hit:

            trade[
                "tp3_hit"
            ] = True

        if tp2_hit:

            trade[
                "tp2_hit"
            ] = True

            result_pips = (
                price_to_pips(
                    abs(
                        tp2
                        - entry
                    )
                )
            )

            trade["mfe_pips"] = max(
                float(
                    trade.get(
                        "mfe_pips",
                        0.0,
                    )
                ),
                round(
                    result_pips,
                    1,
                ),
            )

            trade[
                "result"
            ] = "WIN"

            trade[
                "result_pips"
            ] = round(
                result_pips,
                1,
            )

            trade[
                "result_r"
            ] = round(
                result_pips
                /
                max(
                    risk_pips,
                    0.0001,
                ),
                3,
            )

            trade[
                "closed_at"
            ] = (
                candle_time.isoformat()
            )

            trade[
                "close_reason"
            ] = "TP2 HIT"

    after = json.dumps(
        clean_for_json(
            trade
        ),
        sort_keys=True,
    )

    return (
        before != after
    )


def update_journal_outcomes(
    journal,
    m5,
):

    changed = False

    for trade in journal.get(
        "trades",
        [],
    ):

        if trade.get(
            "result"
        ) != "OPEN":

            continue

        signal_time = parse_dt(
            trade.get(
                "signal_candle_time"
            )
            or trade.get(
                "timestamp"
            )
        )

        if signal_time is None:
            continue

        future = m5[
            m5["datetime"]
            >
            pd.Timestamp(
                signal_time
            )
        ]

        # Recalculate to avoid double-count.
        trade[
            "bars_held"
        ] = 0

        for _, candle in (
            future.iterrows()
        ):

            this_changed = (
                update_open_trade_with_candle(
                    trade,
                    candle,
                )
            )

            if this_changed:
                changed = True

            if trade.get(
                "result"
            ) != "OPEN":

                break

    if changed:

        save_journal(
            journal
        )

    return journal


# =========================================================
# RISK LOCK ENGINE
# =========================================================


def calculate_risk_lock(
    journal,
    reference_my,
):

    closed = [
        t
        for t in journal.get(
            "trades",
            [],
        )
        if t.get(
            "result"
        ) in (
            "WIN",
            "LOSS",
            "BE",
        )
        and t.get(
            "closed_at"
        )
    ]

    closed.sort(
        key=lambda t:
        parse_dt(
            t.get(
                "closed_at"
            )
        )
        or datetime.min.replace(
            tzinfo=timezone.utc
        )
    )

    today = (
        reference_my.date()
    )

    today_trades = []

    for trade in closed:

        closed_at = parse_dt(
            trade.get(
                "closed_at"
            )
        )

        if closed_at is None:
            continue

        if (
            closed_at
            .astimezone(
                MY_TZ
            )
            .date()
            == today
        ):

            today_trades.append(
                trade
            )

    daily_r = sum(
        float(
            t.get(
                "result_r"
            )
            or 0.0
        )
        for t in today_trades
    )

    consecutive_losses = 0

    for trade in reversed(
        closed
    ):

        if (
            trade.get(
                "result"
            )
            == "LOSS"
        ):

            consecutive_losses += 1

        else:

            break

    daily_lock = (
        daily_r
        <=
        -abs(
            MAX_DAILY_LOSS_R
        )
    )

    consecutive_lock = (
        consecutive_losses
        >=
        MAX_CONSECUTIVE_LOSSES
    )

    locked = (
        daily_lock
        or consecutive_lock
    )

    reasons = []

    if daily_lock:

        reasons.append(
            (
                f"Daily P/L "
                f"{daily_r:.2f}R "
                f"reached "
                f"-{MAX_DAILY_LOSS_R:.2f}R "
                f"limit"
            )
        )

    if consecutive_lock:

        reasons.append(
            (
                f"{consecutive_losses} "
                f"consecutive losses "
                f"reached limit "
                f"{MAX_CONSECUTIVE_LOSSES}"
            )
        )

    return {
        "locked": locked,

        "status": (
            "LOCKED"
            if locked
            else "PASS"
        ),

        "daily_lock": (
            daily_lock
        ),

        "consecutive_lock": (
            consecutive_lock
        ),

        "today_closed_trades": (
            len(today_trades)
        ),

        "today_r": round(
            daily_r,
            3,
        ),

        "daily_loss_limit_r": (
            MAX_DAILY_LOSS_R
        ),

        "consecutive_losses": (
            consecutive_losses
        ),

        "consecutive_loss_limit": (
            MAX_CONSECUTIVE_LOSSES
        ),

        "reasons": reasons,

        "reset_rule": (
            "Daily R resets by Malaysia "
            "calendar date; consecutive "
            "loss count resets after a "
            "non-loss closed trade"
        ),
    }


# =========================================================
# JOURNAL / FORWARD STATS
# =========================================================


def safe_average(values):

    return (
        sum(values) / len(values)
        if values
        else None
    )


def max_drawdown_from_r(
    result_r,
):

    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    for value in result_r:

        equity += value

        peak = max(
            peak,
            equity,
        )

        max_dd = max(
            max_dd,
            peak - equity,
        )

    return max_dd


def group_performance(
    closed,
    key,
):

    groups = {}

    for trade in closed:

        name = (
            trade.get(key)
            or "UNKNOWN"
        )

        groups.setdefault(
            name,
            [],
        ).append(
            trade
        )

    output = {}

    for (
        name,
        trades,
    ) in groups.items():

        rs = [
            float(
                t["result_r"]
            )
            for t in trades
            if t.get(
                "result_r"
            )
            is not None
        ]

        wins = sum(
            t.get("result")
            == "WIN"
            for t in trades
        )

        losses = sum(
            t.get("result")
            == "LOSS"
            for t in trades
        )

        total = len(
            trades
        )

        output[
            str(name)
        ] = {
            "trades": total,

            "wins": wins,

            "losses": losses,

            "win_rate": (
                round(
                    wins
                    /
                    total
                    *
                    100.0,
                    2,
                )
                if total
                else None
            ),

            "average_r": (
                round(
                    safe_average(
                        rs
                    ),
                    3,
                )
                if rs
                else None
            ),

            "total_r": round(
                sum(rs),
                3,
            ),
        }

    return output


def calculate_journal_stats(
    journal,
):

    trades = journal.get(
        "trades",
        [],
    )

    if not isinstance(
        trades,
        list,
    ):
        trades = []

    open_trades = [
        t
        for t in trades
        if t.get("result")
        == "OPEN"
    ]

    closed = [
        t
        for t in trades
        if t.get(
            "result"
        ) in (
            "WIN",
            "LOSS",
            "BE",
        )
    ]

    wins = [
        t
        for t in closed
        if t.get("result")
        == "WIN"
    ]

    losses = [
        t
        for t in closed
        if t.get("result")
        == "LOSS"
    ]

    breakeven = [
        t
        for t in closed
        if t.get("result")
        == "BE"
    ]

    result_r = [
        float(
            t["result_r"]
        )
        for t in closed
        if t.get(
            "result_r"
        )
        is not None
    ]

    result_pips = [
        float(
            t["result_pips"]
        )
        for t in closed
        if t.get(
            "result_pips"
        )
        is not None
    ]

    mfe_values = [
        float(
            t["mfe_pips"]
        )
        for t in closed
        if t.get(
            "mfe_pips"
        )
        is not None
    ]

    mae_values = [
        float(
            t["mae_pips"]
        )
        for t in closed
        if t.get(
            "mae_pips"
        )
        is not None
    ]

    closed_count = len(
        closed
    )

    win_rate = (
        len(wins)
        /
        closed_count
        *
        100.0
        if closed_count
        else None
    )

    total_r = sum(
        result_r
    )

    average_r = safe_average(
        result_r
    )

    total_pips = sum(
        result_pips
    )

    average_pips = safe_average(
        result_pips
    )

    gross_profit_r = sum(
        x
        for x in result_r
        if x > 0
    )

    gross_loss_r = abs(
        sum(
            x
            for x in result_r
            if x < 0
        )
    )

    if gross_loss_r > 0:

        profit_factor = (
            gross_profit_r
            /
            gross_loss_r
        )

        profit_factor_note = None

    elif gross_profit_r > 0:

        profit_factor = None

        profit_factor_note = (
            "UNDEFINED — NO LOSSES YET"
        )

    else:

        profit_factor = None

        profit_factor_note = (
            "NO CLOSED TRADE DATA"
        )

    current_losses = 0

    max_consecutive_losses = 0

    for trade in closed:

        if (
            trade.get(
                "result"
            )
            == "LOSS"
        ):

            current_losses += 1

            max_consecutive_losses = max(
                max_consecutive_losses,
                current_losses,
            )

        else:

            current_losses = 0

    tp1_hits = sum(
        bool(
            t.get(
                "tp1_hit"
            )
        )
        for t in trades
    )

    tp1_hit_rate = (
        tp1_hits
        /
        len(trades)
        *
        100.0
        if trades
        else None
    )

    return {
        "status": "ACTIVE",

        "sample_status": (
            "NO CLOSED TRADES"

            if closed_count == 0

            else "EARLY SAMPLE"

            if closed_count < 30

            else "BUILDING SAMPLE"

            if closed_count < 100

            else "ESTABLISHED SAMPLE"
        ),

        "tracking_model": (
            "TP1 TRACKED; "
            "TP2 = WIN/EXIT; "
            "SL = LOSS; "
            "M5 OHLC CONSERVATIVE"
        ),

        "total_signals": len(
            trades
        ),

        "open_trades": len(
            open_trades
        ),

        "closed_trades": (
            closed_count
        ),

        "wins": len(
            wins
        ),

        "losses": len(
            losses
        ),

        "breakeven": len(
            breakeven
        ),

        "win_rate": (
            round(
                win_rate,
                2,
            )
            if win_rate
            is not None
            else None
        ),

        "total_pips": round(
            total_pips,
            1,
        ),

        "average_pips": (
            round(
                average_pips,
                1,
            )
            if average_pips
            is not None
            else None
        ),

        "total_r": round(
            total_r,
            3,
        ),

        "average_r": (
            round(
                average_r,
                3,
            )
            if average_r
            is not None
            else None
        ),

        "expectancy_r": (
            round(
                average_r,
                3,
            )
            if average_r
            is not None
            else None
        ),

        "profit_factor": (
            round(
                profit_factor,
                3,
            )
            if profit_factor
            is not None
            else None
        ),

        "profit_factor_note": (
            profit_factor_note
        ),

        "max_drawdown_r": round(
            max_drawdown_from_r(
                result_r
            ),
            3,
        ),

        "max_consecutive_losses": (
            max_consecutive_losses
        ),

        "tp1_hits": tp1_hits,

        "tp1_hit_rate": (
            round(
                tp1_hit_rate,
                2,
            )
            if tp1_hit_rate
            is not None
            else None
        ),

        "average_mfe_pips": (
            round(
                safe_average(
                    mfe_values
                ),
                1,
            )
            if mfe_values
            else None
        ),

        "average_mae_pips": (
            round(
                safe_average(
                    mae_values
                ),
                1,
            )
            if mae_values
            else None
        ),

        "performance_by_session": (
            group_performance(
                closed,
                "session",
            )
        ),

        "performance_by_setup": (
            group_performance(
                closed,
                "opportunity",
            )
        ),

        "performance_by_regime": (
            group_performance(
                closed,
                "market_mode",
            )
        ),

        "latest_trade": (
            trades[-1]
            if trades
            else None
        ),

        "latest_closed_trade": (
            closed[-1]
            if closed
            else None
        ),

        "last_updated": (
            now_my().isoformat()
        ),
    }


# =========================================================
# BACKTEST STATUS
# =========================================================


def load_backtest_status():

    data = load_json(
        BACKTEST_FILE
    )

    if (
        isinstance(
            data,
            dict,
        )
        and data.get(
            "status"
        ) in (
            "COMPLETED",
            "RUNNING",
            "FAILED",
        )
    ):

        return data

    return {
        "status": "NOT RUN",

        "development_period": (
            "2022-2024 — "
            "HISTORICAL M5 DATASET REQUIRED"
        ),

        "out_of_sample": (
            "2025 — NOT RUN"
        ),

        "forward": (
            "2026 — LIVE JOURNAL ACTIVE"
        ),

        "note": (
            "The live engine does not fake "
            "historical results. A dedicated "
            "historical runner and 2022-2025 "
            "M5 dataset are required."
        ),
    }


# =========================================================
# SIGNAL GATE
# =========================================================


def validate_signal_gate(
    score,
    setup,
    m5_confirm,
    plan,
    news_result,
    fresh_zone,
    risk_lock,
):

    reasons = []

    if score < MIN_SCORE:

        reasons.append(
            f"Score {score} < {MIN_SCORE}"
        )

    if not setup.get(
        "valid",
        False,
    ):

        reasons.append(
            "M15 setup invalid"
        )

    if not m5_confirm.get(
        "confirmed",
        False,
    ):

        reasons.append(
            "M5 confirmation missing"
        )

    if not fresh_zone.get(
        "valid",
        False,
    ):

        reasons.append(
            (
                f"Fresh zone "
                f"{fresh_zone.get('status', 'UNAVAILABLE')}: "
                f"{fresh_zone.get('reason', 'invalid')}"
            )
        )

    if not plan.get(
        "valid",
        False,
    ):

        reasons.append(
            plan.get(
                "reason",
                "Trade plan invalid",
            )
        )

    if (
        plan.get(
            "valid",
            False,
        )
        and plan.get(
            "rr_tp2",
            0,
        )
        < MIN_RR
    ):

        reasons.append(
            "RR TP2 below 1:2"
        )

    if not news_result.get(
        "ok",
        False,
    ):

        reasons.append(
            "NEWS BLOCK: "
            + str(
                news_result.get(
                    "reason",
                    "Unknown news state",
                )
            )
        )

    if risk_lock.get(
        "locked",
        False,
    ):

        reasons.append(
            "RISK LOCK: "
            + "; ".join(
                risk_lock.get(
                    "reasons",
                    ["Risk lock active"],
                )
            )
        )

    return {
        "valid": (
            len(reasons) == 0
        ),
        "reasons": reasons,
    }
    # =========================================================
# MAIN ENGINE
# =========================================================


def main():

    timestamp_my = now_my()

    timestamp_utc = (
        timestamp_my
        .astimezone(
            timezone.utc
        )
    )

    previous_dashboard = (
        load_previous_dashboard()
    )

    # =====================================================
    # ONE TWELVE DATA REQUEST
    # =====================================================

    m5 = remove_incomplete_candle(
        fetch_m5()
    )

    if len(m5) < 100:

        raise RuntimeError(
            "Not enough M5 candles"
        )

    latest_candle_time = (
        pd.to_datetime(
            m5.iloc[-1]["datetime"],
            utc=True,
        ).to_pydatetime()
    )

    # =====================================================
    # JOURNAL / OLD OUTCOMES
    # =====================================================

    journal = load_journal(
        previous_dashboard
    )

    journal = (
        update_journal_outcomes(
            journal,
            m5,
        )
    )

    risk_lock = (
        calculate_risk_lock(
            journal,
            timestamp_my,
        )
    )

    # =====================================================
    # MTF
    # =====================================================

    m15 = aggregate(
        m5,
        15,
    )

    h1 = aggregate(
        m5,
        60,
    )

    if (
        len(m15) < 50
        or len(h1) < 30
    ):

        raise RuntimeError(
            "Not enough MTF data"
        )

    h1_structure = (
        analyze_structure(
            h1
        )
    )

    m15_structure = (
        analyze_structure(
            m15
        )
    )

    m5_structure = (
        analyze_structure(
            m5
        )
    )

    h1_range = (
        analyze_range(
            h1
        )
    )

    if h1_structure[
        "direction"
    ] in (
        "BULLISH",
        "BEARISH",
    ):

        market_mode = "TRENDING"

    elif h1_range[
        "is_range"
    ]:

        market_mode = "RANGING"

    else:

        market_mode = "NEUTRAL"

    # =====================================================
    # CONTEXT
    # =====================================================

    pd_data = pd_zone(
        m15
    )

    liquidity = (
        liquidity_analysis(
            m15
        )
    )

    liquidity_levels = (
        calculate_liquidity_levels(
            m5,
            timestamp_my,
        )
    )

    volatility = (
        volatility_analysis(
            m5
        )
    )

    session = get_session(
        timestamp_my
    )

    session_ok = (
        is_preferred_session(
            session
        )
    )

    # =====================================================
    # REAL NEWS
    # =====================================================

    news_feed = (
        fetch_forexfactory_calendar(
            previous_dashboard
        )
    )

    news_result = (
        evaluate_news_filter(
            news_feed,
            timestamp_utc,
        )
    )

    # =====================================================
    # SETUP / CONFIRMATION / FRESH ZONE
    # =====================================================

    setup = detect_m15_setup(
        h1_structure,
        m15,
    )

    m5_confirm = (
        m5_confirmation(
            m5,
            setup["direction"],
        )
    )

    fresh_zone = (
        analyze_fresh_zone(
            m15,
            setup["direction"],
        )
    )

    # =====================================================
    # SCORE
    # =====================================================

    score = calculate_score(
        h1_structure,
        setup,
        m5_confirm,
        session,
        volatility,
    )

    # =====================================================
    # PLAN
    # =====================================================

    plan = create_trade_plan(
        setup["direction"],
        m5,
        volatility,
    )

    # =====================================================
    # FINAL GATE
    # =====================================================

    gate = validate_signal_gate(
        score,
        setup,
        m5_confirm,
        plan,
        news_result,
        fresh_zone,
        risk_lock,
    )

    valid_signal = (
        gate["valid"]
    )

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

    fresh_zone_ok = (
        fresh_zone[
            "valid"
        ]
    )

    risk_ok = (
        plan.get(
            "valid",
            False,
        )
    )

    rr_ok = (
        plan.get(
            "valid",
            False,
        )
        and plan.get(
            "rr_tp2",
            0,
        )
        >= MIN_RR
    )

    news_ok = (
        news_result[
            "ok"
        ]
    )

    risk_lock_ok = (
        not risk_lock[
            "locked"
        ]
    )

    # =====================================================
    # SIGNAL
    # =====================================================

    signal = {
        "active": False,
        "new_signal": False,
        "telegram_sent": False,
        "id": None,
        "direction": (
            setup["direction"]
        ),
        "opportunity": (
            setup["opportunity"]
        ),
        "score": score,
        "timestamp": (
            timestamp_my.isoformat()
        ),
        "candle_time": (
            latest_candle_time.isoformat()
        ),
        "status": "WAIT",
        "gate_reasons": (
            gate["reasons"]
        ),
    }

    state = load_state(
        previous_dashboard
    )

    # =====================================================
    # VALID SIGNAL
    # =====================================================

    if valid_signal:

        signal_id = make_signal_id(
            setup["direction"],
            setup["opportunity"],
            latest_candle_time.isoformat(),
            plan["entry"],
            plan["sl"],
            plan["tp2"],
        )

        signal.update(
            {
                "active": True,
                "id": signal_id,
                "status": "VALID",
                "entry": plan[
                    "entry"
                ],
                "sl": plan[
                    "sl"
                ],
                "tp1": plan[
                    "tp1"
                ],
                "tp2": plan[
                    "tp2"
                ],
                "tp3": plan[
                    "tp3"
                ],
                "risk_pips": plan[
                    "risk_pips"
                ],
                "rr_tp2": plan[
                    "rr_tp2"
                ],
            }
        )

        duplicate = (
            is_duplicate_or_cooldown(
                state,
                setup["direction"],
                setup["opportunity"],
                signal_id,
                timestamp_my,
            )
        )

        if not duplicate:

            signal[
                "new_signal"
            ] = True

            message = format_telegram(
                setup["direction"],
                setup["opportunity"],
                score,
                plan,
                session,
                pd_data,
                volatility,
                news_result,
                fresh_zone,
                risk_lock,
            )

            sent = send_telegram(
                message
            )

            signal[
                "telegram_sent"
            ] = bool(sent)

            # Save detection regardless of
            # Telegram delivery.
            state.update(
                {
                    "last_signal_id": (
                        signal_id
                    ),

                    "last_signal_time": (
                        timestamp_my.isoformat()
                    ),

                    "last_signal_direction": (
                        setup[
                            "direction"
                        ]
                    ),

                    "last_signal_opportunity": (
                        setup[
                            "opportunity"
                        ]
                    ),

                    "last_telegram_success": (
                        bool(sent)
                    ),
                }
            )

            if sent:

                state[
                    "last_signal_sent"
                ] = (
                    timestamp_my.isoformat()
                )

            save_state(
                state
            )

    # =====================================================
    # JOURNAL NEW SIGNAL
    # =====================================================

    journal = journal_signal(
        journal,
        signal,
        setup,
        m5_confirm,
        plan,
        score,
        session,
        news_result,
        fresh_zone,
        market_mode,
        h1_structure,
        volatility,
        timestamp_my,
        latest_candle_time,
    )

    journal = (
        update_journal_outcomes(
            journal,
            m5,
        )
    )

    journal_stats = (
        calculate_journal_stats(
            journal
        )
    )

    forward_stats = {
        **journal_stats,

        "status": (
            "FORWARD TESTING"
        ),

        "symbol": SYMBOL,
    }

    save_json_atomic(
        FORWARD_FILE,
        forward_stats,
    )

    # =====================================================
    # RECALCULATE RISK LOCK
    # =====================================================

    risk_lock = (
        calculate_risk_lock(
            journal,
            timestamp_my,
        )
    )

    # =====================================================
    # BACKTEST
    # =====================================================

    backtest = (
        load_backtest_status()
    )

    # =====================================================
    # DASHBOARD STATUS
    # =====================================================

    if valid_signal:

        dashboard_status = (
            "VALID SIGNAL"
        )

    elif risk_lock[
        "locked"
    ]:

        dashboard_status = (
            "WAIT — RISK LOCK"
        )

    elif not news_ok:

        dashboard_status = (
            "WAIT — NEWS BLOCK"
        )

    elif not fresh_zone_ok:

        dashboard_status = (
            "WAIT — FRESH ZONE INVALID"
        )

    elif score < MIN_SCORE:

        dashboard_status = (
            "WAIT — SCORE BELOW 70"
        )

    else:

        dashboard_status = (
            "WAIT — CONDITIONS NOT MET"
        )

    # =====================================================
    # EXECUTABLE PLAN
    # =====================================================

    if valid_signal:

        dashboard_plan = plan

    else:

        dashboard_plan = {
            "valid": False,

            "status": (
                "NO EXECUTABLE PLAN"
            ),

            "reason": (
                "; ".join(
                    gate["reasons"]
                )
                if gate["reasons"]
                else (
                    "Signal gate "
                    "not passed"
                )
            ),
        }

    # =====================================================
    # NEWS
    # =====================================================

    dashboard_news = {
        "status": (
            news_result["status"]
        ),

        "high_impact": (
            news_result[
                "high_impact"
            ]
        ),

        "minutes_to_news": (
            news_result[
                "minutes_to_news"
            ]
        ),

        "minutes_since_news": (
            news_result[
                "minutes_since_news"
            ]
        ),

        "filter": (
            news_result["filter"]
        ),

        "event": (
            news_result["event"]
        ),

        "event_time": (
            news_result[
                "event_time"
            ]
        ),

        "reason": (
            news_result["reason"]
        ),

        "feed_status": (
            news_result[
                "feed_status"
            ]
        ),

        "source": (
            news_result["source"]
        ),

        "feed_age_minutes": (
            news_result[
                "feed_age_minutes"
            ]
        ),

        "error": (
            news_result["error"]
        ),

        "block_before_minutes": (
            NEWS_BLOCK_BEFORE_MINUTES
        ),

        "block_after_minutes": (
            NEWS_BLOCK_AFTER_MINUTES
        ),
    }

    # =====================================================
    # JOURNAL DASHBOARD
    # =====================================================

    dashboard_journal = {
        "status": "ACTIVE",
        "enabled": True,
        "file": JOURNAL_FILE.name,
        "symbol": SYMBOL,
        **journal_stats,
    }

    expectancy = {
        "status": (
            journal_stats[
                "sample_status"
            ]
        ),

        "trades": (
            journal_stats[
                "closed_trades"
            ]
        ),

        "win_rate": (
            journal_stats[
                "win_rate"
            ]
        ),

        "average_r": (
            journal_stats[
                "average_r"
            ]
        ),

        "profit_factor": (
            journal_stats[
                "profit_factor"
            ]
        ),

        "expectancy_r": (
            journal_stats[
                "expectancy_r"
            ]
        ),

        "max_drawdown": (
            journal_stats[
                "max_drawdown_r"
            ]
        ),

        "max_consecutive_losses": (
            journal_stats[
                "max_consecutive_losses"
            ]
        ),
    }

    # =====================================================
    # DASHBOARD
    # =====================================================

    dashboard = {

        "engine": {
            "name": (
                "BOSQUE FOREX AI"
            ),

            "version": (
                "SCALPING V6 AUDITED"
            ),

            "symbol": SYMBOL,

            "timeframe": (
                "H1 → M15 → M5"
            ),

            "timestamp": (
                timestamp_my.isoformat()
            ),

            "status": (
                dashboard_status
            ),

            "twelve_data_requests_this_scan": 1,

            "news_source_requests_this_scan": (
                0
                if news_feed[
                    "feed_status"
                ] == "CACHE"
                else 1
            ),
        },

        "latest_price": (
            round_price(
                float(
                    m5.iloc[-1][
                        "close"
                    ]
                )
            )
        ),

        "session": session,

        "session_preferred": (
            session_ok
        ),

        "market_mode": (
            market_mode
        ),

        "regime": {
            "type": market_mode,

            "h1_direction": (
                h1_structure[
                    "direction"
                ]
            ),

            "range": h1_range,
        },

        "volatility": volatility,

        "pd": pd_data,

        "liquidity": {
            **liquidity,
            **liquidity_levels,
        },

        "fresh_zone": (
            fresh_zone
        ),

        "news": (
            dashboard_news
        ),

        "opportunity": {
            "type": (
                setup[
                    "opportunity"
                ]
            ),

            "direction": (
                setup[
                    "direction"
                ]
            ),

            "valid": (
                valid_signal
            ),

            "analysis_valid": (
                setup[
                    "valid"
                ]
            ),

            "score": score,

            "minimum_score": (
                MIN_SCORE
            ),

            "status": (
                dashboard_status
            ),
        },

        "signal": signal,

        "plan": dashboard_plan,

        "potential": {
            "tp1_pips": (
                plan.get(
                    "tp1_pips"
                )
                if valid_signal
                else None
            ),

            "tp2_pips": (
                plan.get(
                    "tp2_pips"
                )
                if valid_signal
                else None
            ),

            "tp3_pips": (
                plan.get(
                    "tp3_pips"
                )
                if valid_signal
                else None
            ),

            "status": (
                "VALID"
                if valid_signal
                else "WAIT"
            ),
        },

        "h1": {
            **h1_structure,

            "condition": (
                market_mode
            ),
        },

        "m15": {
            **m15_structure,

            "setup": setup,
        },

        "m5": {
            **m5_structure,

            "confirmation": (
                m5_confirm
            ),
        },

        "filters": {
            "score": score_ok,

            "setup": setup_ok,

            "m5_confirmation": (
                m5_ok
            ),

            "fresh_zone": (
                fresh_zone_ok
            ),

            "risk": risk_ok,

            "rr": rr_ok,

            "news": news_ok,

            "risk_lock": (
                risk_lock_ok
            ),

            "session": (
                session_ok
            ),

            "session_blocking": (
                False
            ),
        },

        "confirmations": {
            "h1_bias": (
                h1_structure[
                    "direction"
                ]
            ),

            "m15_setup": (
                setup[
                    "opportunity"
                ]
            ),

            "m5_confirmation": (
                m5_confirm[
                    "reason"
                ]
            ),

            "liquidity": (
                liquidity[
                    "description"
                ]
            ),

            "pd_zone": (
                pd_data["zone"]
            ),

            "fresh_zone": (
                fresh_zone[
                    "status"
                ]
            ),

            "news": (
                news_result[
                    "reason"
                ]
            ),
        },

        "signal_gate": {
            "valid": (
                valid_signal
            ),

            "score": score,

            "minimum_score": (
                MIN_SCORE
            ),

            "reasons": (
                gate["reasons"]
            ),
        },

        "risk_engine": {
            "status": (
                "VALID"
                if risk_ok
                else "INVALID"
            ),

            "risk_pips": (
                plan.get(
                    "risk_pips"
                )
            ),

            "risk_level": (
                plan.get(
                    "risk_level"
                )
            ),

            "min_risk_pips": (
                MIN_RISK_PIPS
            ),

            "max_risk_pips": (
                MAX_RISK_PIPS
            ),

            "daily_loss_limit_r": (
                MAX_DAILY_LOSS_R
            ),

            "consecutive_loss_limit": (
                MAX_CONSECUTIVE_LOSSES
            ),

            "risk_lock": (
                risk_lock
            ),
        },

        "trade_plan_audit": {
            "status": (
                "PASS"
                if plan.get(
                    "valid",
                    False
                )
                else "FAIL"
            ),

            "direction": (
                setup[
                    "direction"
                ]
            ),

            "entry": (
                plan.get("entry")
            ),

            "sl": (
                plan.get("sl")
            ),

            "tp1": (
                plan.get("tp1")
            ),

            "tp2": (
                plan.get("tp2")
            ),

            "tp3": (
                plan.get("tp3")
            ),

            "geometry": (
                plan.get(
                    "geometry"
                )
            ),

            "reason": (
                plan.get(
                    "reason"
                )
            ),
        },

        "news_audit": (
            dashboard_news
        ),

        "fresh_zone_audit": (
            fresh_zone
        ),

        "risk_lock": (
            risk_lock
        ),

        "invalidation": {
            "status": (
                "VALID"
                if valid_signal
                else "WAIT"
            ),

            "conditions": [
                "M5 confirmation required",

                (
                    "Fresh zone must not be "
                    "USED/UNAVAILABLE"
                ),

                "Risk must remain valid",

                (
                    "RR TP2 must remain "
                    ">= 1:2"
                ),

                (
                    "BUY SL must remain "
                    "below Entry"
                ),

                (
                    "SELL SL must remain "
                    "above Entry"
                ),

                (
                    "High-impact USD news "
                    "block overrides "
                    "technical signal"
                ),

                (
                    "Daily/consecutive-loss "
                    "risk lock overrides "
                    "technical signal"
                ),
            ],
        },

        "sop": {
            "news_filter": (
                "PASS"
                if news_ok
                else "BLOCK"
            ),

            "fresh_zone": (
                fresh_zone[
                    "status"
                ]
            ),

            "fresh_zone_filter": (
                "PASS"
                if fresh_zone_ok
                else "BLOCK"
            ),

            "session_filter": (
                "PREFERRED"
                if session_ok
                else "NON-PREFERRED"
            ),

            "session_blocking": (
                "NO"
            ),

            "risk": (
                plan.get(
                    "risk_level",
                    "UNKNOWN"
                )
            ),

            "risk_lock": (
                risk_lock[
                    "status"
                ]
            ),

            "m15_setup": (
                "YES"
                if setup_ok
                else "NO"
            ),

            "m5_confirmation": (
                "YES"
                if m5_ok
                else "NO"
            ),
        },

        "journal": (
            dashboard_journal
        ),

        "trading_journal": (
            dashboard_journal
        ),

        "expectancy": (
            expectancy
        ),

        "forward_test": (
            forward_stats
        ),

        "backtest": backtest,

        "implementation_status": {

            "live_engine": (
                "ACTIVE"
            ),

            "telegram": (
                "CONFIGURED"
                if (
                    TELEGRAM_BOT_TOKEN
                    and TELEGRAM_CHAT_ID
                )
                else "NOT CONFIGURED"
            ),

            "news_filter": (
                "ACTIVE"
                if news_feed[
                    "feed_status"
                ] != "ERROR"
                else "ERROR"
            ),

            "journal": (
                "ACTIVE"
            ),

            "forward_test": (
                "ACTIVE"
            ),

            "fresh_zone": (
                "ACTIVE"
            ),

            "daily_loss_lock": (
                "ACTIVE"
            ),

            "consecutive_loss_lock": (
                "ACTIVE"
            ),

            "mfe_mae_tracking": (
                "ACTIVE"
            ),

            "performance_breakdown": (
                "ACTIVE"
            ),

            "position_sizing": (
                "NOT CONFIGURED"
            ),

            "historical_backtest": (
                backtest.get(
                    "status",
                    "NOT RUN",
                )
            ),
        },

        # =================================================
        # PERSISTENCE BACKUP
        # =================================================

        "_persistent_state": (
            state
        ),

        "_journal_store": (
            journal
        ),

        "_news_cache": {
            "source": (
                news_feed[
                    "source"
                ]
            ),

            "fetched_at": (
                news_feed[
                    "fetched_at"
                ]
            ),

            "events": (
                news_feed[
                    "events"
                ]
            ),
        },
    }

    # =====================================================
    # SAVE DASHBOARD
    # =====================================================

    save_json_atomic(
        DASHBOARD_FILE,
        dashboard,
    )

    # =====================================================
    # CONSOLE OUTPUT
    # =====================================================

    print(
        json.dumps(
            clean_for_json(
                dashboard
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()