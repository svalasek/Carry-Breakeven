"""Gross (frictionless) carry return series for both legs of the strategy.

Quarterly futures leg: the basis observed the moment a contract becomes the front
("current") quarter is, by construction of futures convergence, the return realized by
holding it to expiry -- the trade is entered at the prior contract's delivery date (when
this contract rolls to front) and exited at this contract's own delivery date, giving
non-overlapping ~91-day windows. Only fully-expired contracts are used (no partial/live
basis treated as a realized return).

Perpetual funding leg: each funding payment (3x/day) is the short's realized P&L for that
period, assuming a delta-neutral long-spot/short-perp book. No entry/exit dates needed --
it's a continuously-held position.

Neither series includes transaction costs or financing drag -- that's applied in
sensitivity.py.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import binance

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_TABLES = PROJECT_ROOT / "output" / "tables"

PAIRS = ["BTCUSDT", "ETHUSDT"]


def build_quarterly_basis_returns(pair: str, today: dt.date) -> pd.DataFrame:
    delivery_dates = binance.quarterly_delivery_dates(today)
    spot = binance.fetch_spot_klines(pair, delivery_dates[0] - dt.timedelta(days=5), today)
    spot = spot.set_index("date")["spot_close"]

    rows = []
    for prev_delivery, this_delivery in zip(delivery_dates[:-1], delivery_dates[1:]):
        if this_delivery > today:
            break  # not yet realized -- exclude, don't use a partial/live basis
        kl = binance.fetch_quarterly_klines(pair, this_delivery)
        if kl is None or kl.empty:
            continue
        kl = kl.set_index("date")["close"]
        entry_candidates = [d for d in kl.index if d >= prev_delivery]
        if not entry_candidates:
            continue
        entry_date = min(entry_candidates)
        exit_date = kl.index.max()
        futures_entry = kl.loc[entry_date]
        if entry_date not in spot.index or futures_entry is None:
            continue
        spot_entry = spot.get(entry_date)
        if spot_entry is None or pd.isna(spot_entry):
            nearby = spot.reindex(pd.date_range(entry_date, entry_date + dt.timedelta(days=3)).date).dropna()
            if nearby.empty:
                continue
            spot_entry = nearby.iloc[0]

        basis = futures_entry / spot_entry - 1
        holding_days = (exit_date - entry_date).days
        if holding_days <= 0:
            continue
        annualized_return = basis * (365 / holding_days)
        rows.append({
            "pair": pair, "symbol": f"{pair}_{this_delivery.strftime('%y%m%d')}",
            "entry_date": entry_date, "exit_date": exit_date, "holding_days": holding_days,
            "futures_entry": futures_entry, "spot_entry": spot_entry,
            "basis_pct": basis, "annualized_return": annualized_return,
        })
    return pd.DataFrame(rows)


def build_funding_returns(pair: str) -> pd.DataFrame:
    start = dt.date(2019, 1, 1)
    df = binance.fetch_funding_history(pair, start)
    df = df.rename(columns={"fundingRate": "period_return"})
    df["pair"] = pair
    return df


def newey_west_long_run_variance(x: np.ndarray, lag: int) -> float:
    """Bartlett-kernel long-run variance estimator. Naive (lag=0) variance badly
    understates true variance for autocorrelated series -- see the funding-rate finding
    in README.md "A statistical trap in the funding leg": funding-rate autocorrelation is
    ~0.8 at a 1-day lag and doesn't decay to noise until ~120 days, so treating payments as
    iid inflates the naive Sharpe by roughly 5-10x."""
    x = np.asarray(x, dtype=float)
    T = len(x)
    xd = x - x.mean()
    gamma0 = (xd @ xd) / T
    lrv = gamma0
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        gamma_k = (xd[k:] @ xd[:-k]) / T
        lrv += 2 * w * gamma_k
    return max(lrv, 0.0)  # guard against a pathological negative estimate at large lag/small T


def summarize_gross(returns: np.ndarray, periods_per_year: float, nw_lag: int) -> dict:
    """Primary summary uses the Newey-West-adjusted annualized vol (nw_lag chosen per
    series based on where its autocorrelation function actually decays -- see
    README.md). Also reports the naive iid estimate alongside it so the gap is visible,
    not hidden."""
    mean_ann = returns.mean() * periods_per_year
    naive_vol_ann = returns.std(ddof=1) * np.sqrt(periods_per_year)
    lrv = newey_west_long_run_variance(returns, nw_lag)
    nw_vol_ann = np.sqrt(periods_per_year * lrv)
    return {
        "n_obs": len(returns), "mean_annualized": mean_ann,
        "naive_vol_annualized": naive_vol_ann, "naive_sharpe": mean_ann / naive_vol_ann if naive_vol_ann > 0 else np.nan,
        "nw_lag": nw_lag, "nw_vol_annualized": nw_vol_ann,
        "gross_sharpe": mean_ann / nw_vol_ann if nw_vol_ann > 0 else np.nan,
    }


def lag_sensitivity_table(returns: np.ndarray, periods_per_year: float, lags: list[int]) -> pd.DataFrame:
    mean_ann = returns.mean() * periods_per_year
    rows = []
    for lag in lags:
        lrv = newey_west_long_run_variance(returns, lag)
        vol_ann = np.sqrt(periods_per_year * lrv)
        rows.append({"lag": lag, "vol_annualized": vol_ann, "sharpe": mean_ann / vol_ann if vol_ann > 0 else np.nan})
    return pd.DataFrame(rows)


QUARTERLY_NW_LAG = 4   # ~1 year of persistence at quarterly frequency; n=21 is small, HAC noisy -- flagged in README
FUNDING_NW_LAG = 120   # daily funding autocorrelation empirically decays to noise by ~120 days (see README)
LAG_SWEEP = [0, 8, 30, 60, 90, 120, 180]

if __name__ == "__main__":
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()

    quarterly_all = []
    funding_all = []
    summary_rows = []
    lag_sensitivity_rows = []

    for pair in PAIRS:
        q = build_quarterly_basis_returns(pair, today)
        quarterly_all.append(q)
        qsum = summarize_gross(q["annualized_return"].values, periods_per_year=1, nw_lag=QUARTERLY_NW_LAG)
        summary_rows.append({"leg": "quarterly_basis", "pair": pair, **qsum})
        print(f"[carry] {pair} quarterly basis: n={qsum['n_obs']}, mean_ann={qsum['mean_annualized']:.2%}, "
              f"naive Sharpe={qsum['naive_sharpe']:.2f}, NW(lag={QUARTERLY_NW_LAG}) Sharpe={qsum['gross_sharpe']:.2f}")

        f = build_funding_returns(pair)
        funding_all.append(f)
        daily = f.set_index("date")["period_return"].resample("1D").sum().dropna().values
        fsum = summarize_gross(daily, periods_per_year=365, nw_lag=FUNDING_NW_LAG)
        summary_rows.append({"leg": "perp_funding", "pair": pair, **fsum})
        print(f"[carry] {pair} perp funding (daily-aggregated): n={fsum['n_obs']}, mean_ann={fsum['mean_annualized']:.2%}, "
              f"naive Sharpe={fsum['naive_sharpe']:.2f}, NW(lag={FUNDING_NW_LAG}) Sharpe={fsum['gross_sharpe']:.2f}")

        lag_tab = lag_sensitivity_table(daily, periods_per_year=365, lags=LAG_SWEEP)
        lag_tab["pair"] = pair
        lag_sensitivity_rows.append(lag_tab)

    pd.concat(quarterly_all, ignore_index=True).to_parquet(PROCESSED_DIR / "quarterly_basis_returns.parquet", index=False)
    pd.concat(funding_all, ignore_index=True).to_parquet(PROCESSED_DIR / "funding_returns.parquet", index=False)
    pd.DataFrame(summary_rows).to_csv(OUTPUT_TABLES / "gross_return_summary.csv", index=False)
    pd.concat(lag_sensitivity_rows, ignore_index=True).to_csv(OUTPUT_TABLES / "funding_sharpe_lag_sensitivity.csv", index=False)
    print("[carry] wrote quarterly_basis_returns.parquet, funding_returns.parquet, "
          "gross_return_summary.csv, funding_sharpe_lag_sensitivity.csv")
