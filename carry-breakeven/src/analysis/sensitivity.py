"""The core deliverable: for each series (BTC/ETH x quarterly-basis/perp-funding), how
does the MINIMUM gross (frictionless) Sharpe required to still clear a target net Sharpe
scale with transaction costs and financing rates?

Closed-form, not a numerical search: costs are modeled as a pure drag on annualized mean
return (volatility is assumed unaffected by costs -- a simplifying assumption, flagged in
README.md). That makes the algebra exact:

    net_sharpe = (gross_mean - txn_drag - financing_drag) / gross_vol
    required_gross_sharpe = target_net_sharpe + (txn_drag + financing_drag) / gross_vol

Written directly, no iteration needed -- and it makes the "how much better must the pure
strategy be" question literally readable off the equation: every extra unit of drag
divided by the strategy's own volatility is pure required-Sharpe tax.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_TABLES = PROJECT_ROOT / "output" / "tables"
OUTPUT_FIGURES = PROJECT_ROOT / "output" / "figures"
CONFIG_DIR = PROJECT_ROOT / "config"


def txn_drag_annualized(round_trip_bps: float, holding_days: float) -> float:
    return (round_trip_bps / 10_000) * (365 / holding_days)


def required_gross_sharpe(target_net_sharpe: float, txn_drag: float, financing_drag: float, gross_vol: float) -> float:
    if gross_vol <= 0:
        return np.nan
    return target_net_sharpe + (txn_drag + financing_drag) / gross_vol


def build_breakeven_grid(gross_vol: float, holding_days: float, params: dict) -> pd.DataFrame:
    rows = []
    for bps in params["transaction_cost"]["grid_bps_round_trip"]:
        txn_drag = txn_drag_annualized(bps, holding_days)
        for rate in params["financing"]["grid_annualized_rate"]:
            fin_drag = rate * params["financing"]["capital_multiplier"]
            req = required_gross_sharpe(params["target_net_sharpe"], txn_drag, fin_drag, gross_vol)
            rows.append({"txn_cost_bps": bps, "financing_rate": rate,
                         "txn_drag_annualized": txn_drag, "financing_drag_annualized": fin_drag,
                         "required_gross_sharpe": req})
    return pd.DataFrame(rows)


def plot_breakeven_panel(grids: dict[str, tuple[pd.DataFrame, float]], target_net_sharpe: float) -> Path:
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    for ax, (label, (grid, actual_gross_sharpe)) in zip(axes.flat, grids.items()):
        pivot = grid.pivot(index="financing_rate", columns="txn_cost_bps", values="required_gross_sharpe")
        im = ax.imshow(pivot.values, aspect="auto", origin="lower", cmap="RdYlGn_r",
                        vmin=target_net_sharpe, vmax=max(3.0, pivot.values.max()))
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([f"{r:.0%}" for r in pivot.index])
        ax.set_xlabel("round-trip txn cost (bps)")
        ax.set_ylabel("financing rate (annualized)")
        ax.set_title(f"{label}\nactual gross Sharpe = {actual_gross_sharpe:.2f}")
        # mark cells where the actual strategy's gross Sharpe still clears the requirement
        for i, rate in enumerate(pivot.index):
            for j, bps in enumerate(pivot.columns):
                survives = actual_gross_sharpe >= pivot.values[i, j]
                ax.text(j, i, "OK" if survives else "X", ha="center", va="center",
                        color="black", fontsize=8, fontweight="bold")
        fig.colorbar(im, ax=ax, label="required gross Sharpe")
    plt.tight_layout()
    path = OUTPUT_FIGURES / "breakeven_surface.png"
    plt.savefig(path, dpi=150)
    return path


if __name__ == "__main__":
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)
    OUTPUT_FIGURES.mkdir(parents=True, exist_ok=True)
    params = yaml.safe_load((CONFIG_DIR / "params.yaml").read_text())
    summary = pd.read_csv(OUTPUT_TABLES / "gross_return_summary.csv")

    grids = {}
    all_grid_rows = []
    for _, row in summary.iterrows():
        leg, pair = row["leg"], row["pair"]
        holding_days = (params["transaction_cost"]["quarterly_holding_days"] if leg == "quarterly_basis"
                         else params["transaction_cost"]["funding_holding_days"])
        grid = build_breakeven_grid(row["nw_vol_annualized"], holding_days, params)
        grid["leg"] = leg
        grid["pair"] = pair
        all_grid_rows.append(grid)
        label = f"{pair} {'quarterly basis' if leg=='quarterly_basis' else 'perp funding'}"
        grids[label] = (grid, row["gross_sharpe"])
        n_survive = (row["gross_sharpe"] >= grid["required_gross_sharpe"]).sum()
        print(f"[sensitivity] {label}: gross Sharpe={row['gross_sharpe']:.2f}, "
              f"survives {n_survive}/{len(grid)} cost/rate combinations at target net Sharpe={params['target_net_sharpe']}")

    pd.concat(all_grid_rows, ignore_index=True).to_csv(OUTPUT_TABLES / "breakeven_grid_all.csv", index=False)
    fig_path = plot_breakeven_panel(grids, params["target_net_sharpe"])
    print(f"[sensitivity] wrote {fig_path}")

    # headline number: at the *observed* naive assumptions (0 cost, 0 financing) vs a
    # "realistic retail" case (20bps round trip, 5% financing), how much required-Sharpe
    # tax does that realistic case cost, per series?
    realistic_rows = []
    for _, row in summary.iterrows():
        leg = row["leg"]
        holding_days = (params["transaction_cost"]["quarterly_holding_days"] if leg == "quarterly_basis"
                         else params["transaction_cost"]["funding_holding_days"])
        txn_drag = txn_drag_annualized(20, holding_days)
        fin_drag = 0.05 * params["financing"]["capital_multiplier"]
        req = required_gross_sharpe(params["target_net_sharpe"], txn_drag, fin_drag, row["nw_vol_annualized"])
        realistic_rows.append({"leg": leg, "pair": row["pair"], "actual_gross_sharpe": row["gross_sharpe"],
                                "required_gross_sharpe_at_20bps_5pct": req,
                                "survives": row["gross_sharpe"] >= req})
    realistic_df = pd.DataFrame(realistic_rows)
    realistic_df.to_csv(OUTPUT_TABLES / "realistic_case_summary.csv", index=False)
    print("[sensitivity] realistic case (20bps round trip, 5% financing):")
    print(realistic_df.to_string(index=False))
