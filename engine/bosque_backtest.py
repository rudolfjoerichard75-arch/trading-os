import pandas as pd
import numpy as np
from pathlib import Path


# =========================================================
# BOSQUE FOREX AI
# BACKTEST FRAMEWORK
# =========================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent

DATA_DIR = REPO_DIR / "backtest_data"
RESULT_FILE = REPO_DIR / "backtest_results.csv"


# =========================================================
# SETTINGS
# =========================================================

START_YEAR = 2022
END_YEAR = 2025

INITIAL_R = 0.0

MIN_SCORE = 70

MIN_RISK_PIPS = 25
MAX_RISK_PIPS = 80

TP1_R = None
TP2_R = 2.0
TP3_R = 3.0


# =========================================================
# LOAD DATA
# =========================================================

def load_data():

    files = list(
        DATA_DIR.glob("*.csv")
    )

    if not files:
        raise RuntimeError(
            "No CSV files found in backtest_data/"
        )

    frames = []

    for file in files:

        df = pd.read_csv(file)

        frames.append(df)

    data = pd.concat(
        frames,
        ignore_index=True
    )

    if "datetime" not in data.columns:

        raise RuntimeError(
            "CSV must contain datetime column"
        )

    data["datetime"] = pd.to_datetime(
        data["datetime"]
    )

    data = data.sort_values(
        "datetime"
    )

    data = data.drop_duplicates(
        "datetime"
    )

    data = data.reset_index(
        drop=True
    )

    return data


# =========================================================
# ATR
# =========================================================

def calculate_atr(
    df,
    period=14
):

    prev_close = \
        df["close"].shift(1)

    tr1 = \
        df["high"] - df["low"]

    tr2 = \
        abs(
            df["high"] -
            prev_close
        )

    tr3 = \
        abs(
            df["low"] -
            prev_close
        )

    tr = pd.concat(
        [tr1,tr2,tr3],
        axis=1
    ).max(axis=1)

    return tr.rolling(
        period
    ).mean()


# =========================================================
# BASIC STRUCTURE
# =========================================================

def get_direction(
    closes
):

    if len(closes) < 10:
        return "RANGE"

    first = closes.iloc[0]
    last = closes.iloc[-1]

    change = last - first

    if change > 0:
        return "BULLISH"

    if change < 0:
        return "BEARISH"

    return "RANGE"


# =========================================================
# SESSION
# =========================================================

def get_session(
    dt
):

    hour = dt.hour

    if 7 <= hour < 15:
        return "ASIAN"

    if 15 <= hour < 20:
        return "LONDON"

    if 20 <= hour < 23:
        return "NEW YORK"

    if 0 <= hour < 1:
        return "NEW YORK"

    return "OFF"


# =========================================================
# SIGNAL SIMULATION
# =========================================================

def generate_signal(
    data,
    index
):

    if index < 100:
        return None

    current = data.iloc[index]

    history = data.iloc[
        index-100:index
    ]

    direction = get_direction(
        history["close"]
    )

    session = get_session(
        current["datetime"]
    )

    # Mandatory SOP:
    # Avoid Asia / OFF session

    if session not in [
        "LONDON",
        "NEW YORK"
    ]:
        return None

    atr = current["atr"]

    if pd.isna(atr):
        return None

    # Volatility filter

    atr_pips = atr / 0.10

    if atr_pips < 25:
        return None

    if atr_pips > 100:
        return None

    entry = float(
        current["close"]
    )

    # -----------------------------------------------------
    # Simplified historical test logic
    # -----------------------------------------------------

    if direction == "BULLISH":

        recent_low = \
            history["low"].tail(10).min()

        sl = recent_low - 0.20

        risk_price = \
            entry - sl

        risk_pips = \
            risk_price / 0.10

        if not (
            MIN_RISK_PIPS
            <= risk_pips
            <= MAX_RISK_PIPS
        ):
            return None

        tp2 = \
            entry + (
                risk_price * 2
            )

        return {
            "direction":"BUY",
            "entry":entry,
            "sl":sl,
            "tp2":tp2,
            "risk_pips":risk_pips,
            "session":session,
            "score":70
        }

    if direction == "BEARISH":

        recent_high = \
            history["high"].tail(10).max()

        sl = recent_high + 0.20

        risk_price = \
            sl - entry

        risk_pips = \
            risk_price / 0.10

        if not (
            MIN_RISK_PIPS
            <= risk_pips
            <= MAX_RISK_PIPS
        ):
            return None

        tp2 = \
            entry - (
                risk_price * 2
            )

        return {
            "direction":"SELL",
            "entry":entry,
            "sl":sl,
            "tp2":tp2,
            "risk_pips":risk_pips,
            "session":session,
            "score":70
        }

    return None


# =========================================================
# TRADE OUTCOME
# =========================================================

def simulate_trade(
    data,
    entry_index,
    signal
):

    entry = signal["entry"]
    sl = signal["sl"]
    tp2 = signal["tp2"]

    direction = signal["direction"]

    for i in range(
        entry_index + 1,
        len(data)
    ):

        candle = data.iloc[i]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        # BUY
        if direction == "BUY":

            if low <= sl:

                return {
                    **signal,
                    "result":"LOSS",
                    "r":-1,
                    "exit_index":i
                }

            if high >= tp2:

                return {
                    **signal,
                    "result":"WIN",
                    "r":2,
                    "exit_index":i
                }

        # SELL
        if direction == "SELL":

            if high >= sl:

                return {
                    **signal,
                    "result":"LOSS",
                    "r":-1,
                    "exit_index":i
                }

            if low <= tp2:

                return {
                    **signal,
                    "result":"WIN",
                    "r":2,
                    "exit_index":i
                }

    return {
        **signal,
        "result":"OPEN",
        "r":0,
        "exit_index":len(data)-1
    }


# =========================================================
# METRICS
# =========================================================

def calculate_metrics(
    trades
):

    if not trades:

        return {
            "trades":0
        }

    df = pd.DataFrame(
        trades
    )

    closed = df[
        df["result"].isin(
            ["WIN","LOSS"]
        )
    ]

    if closed.empty:

        return {
            "trades":0
        }

    wins = closed[
        closed["result"] == "WIN"
    ]

    losses = closed[
        closed["result"] == "LOSS"
    ]

    win_rate = (
        len(wins) /
        len(closed) *
        100
    )

    avg_r = \
        closed["r"].mean()

    gross_profit = \
        wins["r"].sum()

    gross_loss = abs(
        losses["r"].sum()
    )

    if gross_loss > 0:

        profit_factor = \
            gross_profit / gross_loss

    else:

        profit_factor = np.inf

    equity = 0
    peak = 0
    max_dd = 0

    for r in closed["r"]:

        equity += r

        peak = max(
            peak,
            equity
        )

        dd = peak - equity

        max_dd = max(
            max_dd,
            dd
        )

    consecutive_losses = 0
    max_consecutive_losses = 0

    for r in closed["r"]:

        if r < 0:

            consecutive_losses += 1

            max_consecutive_losses = max(
                max_consecutive_losses,
                consecutive_losses
            )

        else:

            consecutive_losses = 0

    return {
        "trades":len(closed),
        "win_rate":round(
            win_rate,
            2
        ),
        "average_r":round(
            avg_r,
            3
        ),
        "profit_factor":round(
            profit_factor,
            3
        )
        if np.isfinite(profit_factor)
        else None,
        "max_drawdown_r":round(
            max_dd,
            2
        ),
        "max_consecutive_losses":
            max_consecutive_losses
    }


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "👑 BOSQUE FOREX AI BACKTEST"
    )

    data = load_data()

    data["atr"] = calculate_atr(
        data
    )

    data = data[
        data["datetime"].dt.year
        .between(
            START_YEAR,
            END_YEAR
        )
    ].reset_index(
        drop=True
    )

    trades = []

    i = 100

    while i < len(data):

        signal = generate_signal(
            data,
            i
        )

        if signal:

            result = simulate_trade(
                data,
                i,
                signal
            )

            trades.append(
                result
            )

            exit_index = result[
                "exit_index"
            ]

            # Move to after trade closes
            i = max(
                i + 1,
                exit_index + 1
            )

        else:

            i += 1

    metrics = calculate_metrics(
        trades
    )

    print("\nBACKTEST RESULTS")
    print("================")

    for key, value in metrics.items():

        print(
            f"{key}: {value}"
        )

    if trades:

        results = pd.DataFrame(
            trades
        )

        results.to_csv(
            RESULT_FILE,
            index=False
        )

        print(
            f"\nSaved: {RESULT_FILE}"
        )


if __name__ == "__main__":
    main()