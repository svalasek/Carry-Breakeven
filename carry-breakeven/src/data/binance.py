"""Binance public API fetchers -- quarterly delivery futures, spot, and perpetual
funding-rate history. No auth needed for any of this; see notebooks/00_recon.md for how
the data availability was verified before writing this module.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import requests

FAPI = "https://fapi.binance.com"
SPOT_API = "https://api.binance.com"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

QUARTERLY_START_YEAR = 2021  # confirmed in recon: no BTC/ETH quarterly contract before 2021-03-26


def _last_friday(year: int, month: int) -> dt.date:
    last_day = calendar.monthrange(year, month)[1]
    d = dt.date(year, month, last_day)
    while d.weekday() != 4:
        d -= dt.timedelta(days=1)
    return d


def quarterly_delivery_dates(as_of: dt.date) -> list[dt.date]:
    """Every quarterly delivery date from QUARTERLY_START_YEAR through the first one
    at or after `as_of` (i.e. includes the still-live contracts too)."""
    dates = []
    for year in range(QUARTERLY_START_YEAR, as_of.year + 2):
        for month in (3, 6, 9, 12):
            d = _last_friday(year, month)
            dates.append(d)
    return sorted(d for d in dates if d <= as_of + dt.timedelta(days=200))


def _cache_path(name: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_DIR / name


def fetch_quarterly_klines(pair: str, delivery_date: dt.date) -> pd.DataFrame | None:
    symbol = f"{pair}_{delivery_date.strftime('%y%m%d')}"
    cache = _cache_path(f"futures_{symbol}.json")
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        r = requests.get(f"{FAPI}/fapi/v1/klines", params={"symbol": symbol, "interval": "1d", "limit": 1000}, timeout=30)
        if r.status_code != 200:
            return None
        raw = r.json()
        if not isinstance(raw, list) or not raw:
            return None
        cache.write_text(json.dumps(raw))
        time.sleep(0.1)
    df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume",
                                      "close_time", "quote_vol", "n_trades", "taker_base", "taker_quote", "ignore"])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms").dt.date
    df["close"] = df["close"].astype(float)
    df["symbol"] = symbol
    return df[["date", "symbol", "close"]]


def fetch_spot_klines(pair: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    cache = _cache_path(f"spot_{pair}_{start}_{end}.json")
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        all_rows = []
        cur_start = int(dt.datetime.combine(start, dt.time()).timestamp() * 1000)
        end_ms = int(dt.datetime.combine(end, dt.time()).timestamp() * 1000)
        while cur_start < end_ms:
            r = requests.get(f"{SPOT_API}/api/v3/klines",
                              params={"symbol": pair, "interval": "1d", "limit": 1000, "startTime": cur_start},
                              timeout=30)
            r.raise_for_status()
            chunk = r.json()
            if not chunk:
                break
            all_rows.extend(chunk)
            cur_start = chunk[-1][0] + 24 * 3600 * 1000
            time.sleep(0.1)
        raw = all_rows
        cache.write_text(json.dumps(raw))
    df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume",
                                      "close_time", "quote_vol", "n_trades", "taker_base", "taker_quote", "ignore"])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms").dt.date
    df["close"] = df["close"].astype(float)
    return df[["date", "close"]].rename(columns={"close": "spot_close"})


def fetch_funding_history(pair: str, start: dt.date) -> pd.DataFrame:
    """Paginated fetch, 1000 rows/call max. Omitting startTime silently returns only a
    recent window (verified in recon), so it's always passed explicitly here."""
    cache = _cache_path(f"funding_{pair}_{start}.json")
    if cache.exists():
        all_rows = json.loads(cache.read_text())
    else:
        all_rows = []
        cur_start = int(dt.datetime.combine(start, dt.time()).timestamp() * 1000)
        while True:
            r = requests.get(f"{FAPI}/fapi/v1/fundingRate",
                              params={"symbol": pair, "limit": 1000, "startTime": cur_start}, timeout=30)
            r.raise_for_status()
            chunk = r.json()
            if not chunk:
                break
            all_rows.extend(chunk)
            if len(chunk) < 1000:
                break
            cur_start = chunk[-1]["fundingTime"] + 1
            time.sleep(0.15)
        cache.write_text(json.dumps(all_rows))
    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float)
    df["pair"] = pair
    print(f"[binance] {pair} funding: {len(df)} observations, {df['date'].min()} to {df['date'].max()}")
    return df[["date", "pair", "fundingRate"]]
