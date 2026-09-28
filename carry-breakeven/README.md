# How Much Edge Does a Cash-and-Carry Trade Need to Survive Its Own Costs?

The crypto cash-and-carry trade (buy spot, short the corresponding future or perpetual,
collect the spread between them) is one of the cleanest and simplest "textbook arbitrage" 
stories in modern markets. For that reason, I chose it to model a basic question I've
been curious to observe in action: the unconstrained backtest gross Sharpe needed to
survive different financing and transaction cost regimes.

This project asks a narrower question than "does the arb work": **how much does the 
strategy's raw, frictionless Sharpe ratio need to be, before costs, to survive a
given level of transaction costs or financing rates?** And how fast does that 
requirement climb as either one ticks up?

---

## Table of contents

1. [The trade, in two forms](#the-trade-in-two-forms)
2. [Data](#data)
3. [A statistical trap in the funding leg](#a-statistical-trap-in-the-funding-leg)
4. [The breakeven framework](#the-breakeven-framework)
5. [Results](#results)
6. [Limitations](#limitations)
7. [Reproducing this project](#reproducing-this-project)

---

## The trade, in two forms

- **Quarterly futures basis**: buy spot BTC (or ETH), short the quarterly delivery
  future. The future is contractually forced to converge to spot at expiry, so the basis
  observed the moment you enter is, in a frictionless world, exactly the return you
  realize by holding to expiry. This is the textbook cost-of-carry trade — `F = S·e^(r·T)`
  — where a financing rate is a formal, first-order input to the fair value, not just an
  assumption bolted on afterward.
- **Perpetual funding**: buy spot, short the perpetual future (no expiry). Every 8 hours,
  a funding payment tethers the perp to spot; when the perp trades rich, longs pay shorts.
  Held indefinitely, this is a continuous, market-neutral "yield" position rather than a
  discrete, expiry-bound trade.

The two mechanisms answer different halves of the question: the futures leg gives a clean,
formal way to reason about financing-rate sensitivity but only a couple of dozen
independent historical observations; the funding leg gives thousands of observations —
useful for a trustworthy Sharpe estimate — but a much fuzzier connection to "interest
rate" as a modeled input. Both are computed and compared here rather than picking one.

---

## Data

Both legs come from Binance's public, unauthenticated API. Quarterly delivery contracts
for BTC and ETH have traded since **2021-03-26**, giving **21 fully-expired, non-overlapping
quarterly rolls per asset**; perpetual funding history goes back to **2019-09-10 (BTC)** and
**2019-11-27 (ETH)**, giving **~2,500 daily-aggregated observations per asset**. Everything
is cached to disk under `data/raw/` on first fetch.

---

## A statistical trap in the funding leg

The first pass at the funding leg's Sharpe ratio came out at **~10**. That number is 
obviously wrong.

Funding is paid three times a day, and the naive approach — treat each payment as an
independent draw, scale the mean by 1,095 (periods/year) and the volatility by √1,095 — 
contained a terrible econometrics blunder. Funding-rate regimes persist: a market that's paying
positive funding tends to keep paying positive funding for weeks, not flip randomly every
8 hours. Measured directly, the autocorrelation is **0.80 at a one-day lag**, still **0.19**
at 90 days, and doesn't decay to noise until roughly **120 days**. For a positively
autocorrelated series, the true variance of a compounded/summed return is *larger* than
naive i.i.d. scaling assumes, meaning the naive approach understates volatility and manufactures
an inflated Sharpe. That gap is roughly **6-7x**.

The fix is a Newey-West (Bartlett-kernel) long-run variance estimator instead of the naive
sample variance, with the truncation lag chosen from where the autocorrelation function
actually decays (120 days), not from a mechanical rule-of-thumb formula, which, at the 
conventional 8 days, would have still left the Sharpe overstated by ~2.4x. The full 
lag-sensitivity table is reported:

| lag (days) | BTC Sharpe | ETH Sharpe |
|---|---|---|
| 0 (naive) | 10.58 | 9.76 |
| 8 (rule-of-thumb) | 4.11 | 3.84 |
| 30 | 2.61 | 2.36 |
| 60 | 2.06 | 1.84 |
| 90 | 1.83 | 1.60 |
| **120 (chosen)** | **1.69** | **1.46** |
| 180 | 1.57 | 1.30 |

The quarterly futures leg has the same issue at a much smaller scale. It has lag-1 
autocorrelation of 0.33–0.45 on only 21 observations — too little data for a reliable 
long-run-variance estimate on its own, so a fixed, conservative lag of 4 is used and 
flagged as noisy rather than treated as precise.

---

## The breakeven framework

Costs are modeled as a pure drag on annualized return (volatility is assumed unaffected —
see [Limitations](#limitations)), which makes the relationship closed-form rather than a
numerical search:

```
net_sharpe            = (gross_mean - txn_drag - financing_drag) / gross_vol
required_gross_sharpe = target_net_sharpe + (txn_drag + financing_drag) / gross_vol
```

Transaction cost is a round-trip fee/slippage assumption (0–100 bps), amortized over each
leg's natural holding period (~91 days for the quarterly roll; a full year for the
buy-and-hold funding position — a lower bound, since more frequent rebalancing
would only make costs worse, not better). Financing cost is an annualized rate applied
directly to capital deployed. Target net Sharpe is fixed at **1.0** before looking at any results.

---

## Results

| leg | pair | gross Sharpe (NW-adjusted) | survives *any* cost/rate combo at net Sharpe ≥ 1.0? |
|---|---|---|---|
| quarterly basis | BTC | 0.93 | **No — fails even at zero cost** |
| quarterly basis | ETH | 0.80 | **No — fails even at zero cost** |
| perp funding | BTC | 1.69 | 23 of 64 grid combinations |
| perp funding | ETH | 1.46 | 21 of 64 grid combinations |

**The quarterly basis trade doesn't need a cost-sensitivity analysis to fail — it's already
below the target Sharpe before a single basis point of cost or a single percentage point of
financing is applied.** That's the finding for this leg: once the small-sample
autocorrelation is corrected for, the historically realized futures basis on Binance simply
hasn't offered enough risk-adjusted return to clear a 1.0 Sharpe bar, cost-free.

**The funding leg is where the interesting sensitivity lives, and it isn't the one most
people would guess.** Sweeping a full grid of transaction cost (0–100 bps) against
financing rate (0–15%) and checking, for each cell, whether the strategy's actual gross
Sharpe still clears the requirement (see `output/figures/breakeven_surface.png`):
**survival is almost entirely determined by the financing-rate axis, not transaction
costs.** BTC funding survives at financing rates up to 4% *regardless* of transaction cost
level (even 100 bps round-trip doesn't flip it), and fails at every transaction-cost level
once financing reaches 6%. In this model, a fixed round-trip trading cost — amortized over
a full year of holding — is simply too small a drag to matter next to a multi-percentage-
point financing rate applied to the whole position. At a realistic retail assumption (20
bps round-trip, 5% financing), both BTC and ETH funding strategies fail (required Sharpe of
1.76 and 1.54 against actual 1.69 and 1.46) — close misses, not blowouts, which is itself
informative: a couple more percentage points of financing cost, easily plausible in a
tightening-rate environment, is the difference between a viable and non-viable strategy.

---

## Limitations
- **Costs are modeled as a pure mean drag, not a volatility source.** In reality, variable
  slippage and financing-rate volatility would add some noise on top of the level effect
  modeled here — a second-order simplification.
- **The quarterly-leg Newey-West lag (4) rests on only 21 observations.** Autocorrelation
  estimated from 21 points is itself noisy; treat that leg's exact gross Sharpe as
  directionally right but imprecise.
- **The funding-leg holding period (1 year, entered and exited once) is a simplifying
  assumption**, not a claim about how real desks manage the position. More frequent
  hedge rebalancing is realistic and would raise costs, not lower them, so this is a
  conservative choice for that leg specifically.
- **No execution/slippage modeling beyond a flat bps assumption** — real fills would vary
  with order size and market depth, especially during the funding-rate regime shifts that
  matter most for this analysis.
- **The strategy is static, never flipping to a reverse cash-and-carry during backwardation.**
  Both legs stay long spot/short the derivative even in the two backwardation quarters and
  ~13-15% of negative funding periods found in the data, rather than switching to
  short-spot/long-future to capture convergence from the other side. That's the textbook-correct
  move on paper, but modeling it well isn't free: shorting spot requires locating a borrow, and
  spot-borrow rates spike (or dry up) precisely in those stressed conditions — both backwardation
  quarters here were Q3/Q4 2022, in the FTX collapse that produced backwardation in the first
  place. The magnitudes involved (~0.1-0.2% basis) may not clear that extra friction. A 
  regime-switching version would also become a timing strategy rather than a static carry-harvesting
  one, and would need its own out-of-sample validation rather than being assumed to work.

---

## Reproducing this project

```bash
pip install -r requirements.txt

python -m src.analysis.carry         # fetches/caches data, builds both gross return series
python -m src.analysis.sensitivity   # builds the breakeven grid and figure
```

All raw API responses are cached under `data/raw/` on first run. No step fills a gap with
synthetic data; every simplifying assumption above is a modeling choice made explicit in
`config/params.yaml`, not a silent default.
