import ccxt
import pandas as pd
import ta
import time
import math
import datetime
import threading
import os 

# ================= CONFIG =================

import os

API_KEY = os.getenv("R74MGDu1Nyzpcp8cqe")
API_SECRET = os.getenv("NnWGVkCw7az8xCd1nYAVxTHU2FxpYkcSebm9")

TIMEFRAME_ENTRY = "1m"
TIMEFRAME_TREND = "5m"

KILL_RATIO = 0.60
COOLDOWN_SECONDS = 120

WICK_ATR_MULT = 2.5
ATR_EXPANSION_MULT = 1.05
PULLBACK_ATR = 0.20

RSI_LONG_MIN, RSI_LONG_MAX = 50, 70
RSI_SHORT_MIN, RSI_SHORT_MAX = 30, 50

QUALITY_THRESHOLD = 4

PAIRS = {
    "BTCUSDT": 66,
    "ETHUSDT": 66,
    "SOLUSDT": 66,
    "XRPUSDT": 45,
    "BNBUSDT": 45,
    "DOGEUSDT": 45,
    "ADAUSDT": 45,
    "AVAXUSDT": 45,
    "LINKUSDT": 45,
    "OPUSDT": 45
}

exchange = ccxt.bybit({
    "apiKey": R74MGDu1Nyzpcp8cqe,
    "secret": NnWGVkCw7az8xCd1nYAVxTHU2FxpYkcSebm9,
    "enableRateLimit": True,
    "options": {"defaultType": "future"}
})

START_DAY_BALANCE = None
open_trade = None
last_trade_time = 0
day_marker = None

# ================= HELPERS =================

def equity():
    return exchange.fetch_balance()["USDT"]["total"]

def in_session():
    h = datetime.datetime.utcnow().hour
    return (7 <= h <= 11) or (13 <= h <= 17)

def safe_set_leverage(symbol, lev):
    try:
        exchange.set_leverage(lev, symbol)
    except:
        pass

def spread_ok(symbol):
    ob = exchange.fetch_order_book(symbol)
    spread = (ob["asks"][0][0] - ob["bids"][0][0]) / ob["bids"][0][0]
    return spread <= 0.0006

def fetch_df(symbol, tf, limit=200):
    ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["t","o","h","l","c","v"])
    df["ema9"] = ta.trend.ema_indicator(df["c"], 9)
    df["ema21"] = ta.trend.ema_indicator(df["c"], 21)
    df["ema50"] = ta.trend.ema_indicator(df["c"], 50)
    df["ema200"] = ta.trend.ema_indicator(df["c"], 200)
    df["rsi"] = ta.momentum.rsi(df["c"], 14)
    df["atr"] = ta.volatility.average_true_range(df["h"], df["l"], df["c"])
    df["atr_ma"] = df["atr"].rolling(20).mean()
    df["vol_ma"] = df["v"].rolling(20).mean()
    return df

def trade_quality(df5, df1, side):
    score = 0
    if side == "long" and df5["ema50"].iloc[-1] > df5["ema200"].iloc[-1]:
        score += 1
    if side == "short" and df5["ema50"].iloc[-1] < df5["ema200"].iloc[-1]:
        score += 1
    if side == "long" and df1["ema9"].iloc[-1] > df1["ema21"].iloc[-1]:
        score += 1
    if side == "short" and df1["ema9"].iloc[-1] < df1["ema21"].iloc[-1]:
        score += 1
    if df5["v"].iloc[-1] > 1.3 * df5["vol_ma"].iloc[-1]:
        score += 1
    if df5["atr"].iloc[-1] > df5["atr_ma"].iloc[-1] * ATR_EXPANSION_MULT:
        score += 1
    return score

# ================= MAIN LOOP =================

while True:
    try:
        now = datetime.datetime.utcnow().date()

        global START_DAY_BALANCE, day_marker
        if day_marker != now:
            START_DAY_BALANCE = equity()
            day_marker = now

        if equity() <= START_DAY_BALANCE * KILL_RATIO:
            time.sleep(3600)
            continue

        if open_trade:
            price = exchange.fetch_ticker(open_trade["symbol"])["last"]
            move = (price - open_trade["entry"]) / open_trade["entry"]
            if open_trade["side"] == "short":
                move *= -1

            peak = open_trade.get("peak", 0)
            open_trade["peak"] = max(peak, move)

            soft_exit = open_trade["peak"] - (open_trade["atr"] / open_trade["entry"])
            crash_exit = move < -2 * (open_trade["atr"] / open_trade["entry"])

            if move <= soft_exit or crash_exit:
                exchange.create_market_order(
                    open_trade["symbol"],
                    "sell" if open_trade["side"] == "long" else "buy",
                    open_trade["size"],
                    params={"reduceOnly": True}
                )
                open_trade = None

            time.sleep(5)
            continue

        if not in_session() or time.time() - last_trade_time < COOLDOWN_SECONDS:
            time.sleep(20)
            continue

        for symbol, lev in PAIRS.items():
            if not spread_ok(symbol):
                continue

            df5 = fetch_df(symbol, TIMEFRAME_TREND)
            df1 = fetch_df(symbol, TIMEFRAME_ENTRY)

            wick = df1["h"].iloc[-1] - df1["l"].iloc[-1]
            if wick > WICK_ATR_MULT * df1["atr"].iloc[-1]:
                continue

            side = None
            if df5["ema50"].iloc[-1] > df5["ema200"].iloc[-1] and RSI_LONG_MIN < df5["rsi"].iloc[-1] < RSI_LONG_MAX:
                side = "long"
            if df5["ema50"].iloc[-1] < df5["ema200"].iloc[-1] and RSI_SHORT_MIN < df5["rsi"].iloc[-1] < RSI_SHORT_MAX:
                side = "short"

            if not side:
                continue

            if trade_quality(df5, df1, side) < QUALITY_THRESHOLD:
                continue

            safe_set_leverage(symbol, lev)

            price = df1["c"].iloc[-1]
            size = math.floor((equity() * lev / price) * 1000) / 1000

            order = exchange.create_market_order(
                symbol,
                "buy" if side == "long" else "sell",
                size
            )

            if order and order["filled"] > 0:
                open_trade = {
                    "symbol": symbol,
                    "side": side,
                    "entry": order["average"],
                    "size": order["filled"],
                    "atr": df1["atr"].iloc[-1],
                    "peak": 0
                }
                last_trade_time = time.time()
                break

        time.sleep(15)

    except Exception as e:
        print("ERROR:", e)
        time.sleep(30)