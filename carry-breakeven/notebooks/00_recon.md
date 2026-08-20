# Data recon

Run: 2026-07-29. Source: Binance public APIs (spot + USDT-margined futures), no auth needed.

## Quarterly delivery futures (the cost-of-carry leg)

- `GET /fapi/v1/exchangeInfo` lists only the two currently-tradeable quarterly contracts
  per asset (`CURRENT_QUARTER`, `NEXT_QUARTER`) — not history.
- Historical (expired) contracts are still queryable directly by symbol via
  `GET /fapi/v1/klines`, e.g. `BTCUSDT_240329`. Confirmed by checking that contract's
  actual trading window: listed 2023-09-29, last bar 2024-03-29 — a 6-month life, exactly
  as expected (Binance lists "next quarter" as soon as "current quarter" begins).
- Naming pattern confirmed: `<PAIR>_<YYMMDD>`, delivery date = last Friday of
  Mar/Jun/Sep/Dec. Swept candidate dates from 2019 through 2026; **first tradeable
  contract for both BTC and ETH is the 2021-03-26 delivery** (everything before that
  404s) — Binance quarterlies launched March 2021, not earlier. Confirmed 22 fully expired
  quarterly rolls per asset since then (2021 Q1 → 2026 Q2), plus 2 currently live/unexpired
  contracts per asset.
- Spot reference: `GET /api/v3/klines` (spot API, separate host `api.binance.com`), daily
  bars available for the full window with no gaps checked.

## Perpetual funding rate (the transaction-cost leg)

- `GET /fapi/v1/fundingRate`: paginated, max 1000 rows/call, needs an explicit `startTime`
  to get full history (omitting it silently returns only a recent window, not the max
  available — a gotcha worth remembering when building the fetcher).
- Funding paid 3x/day. Confirmed depth: **BTCUSDT from 2019-09-10**, **ETHUSDT from
  2019-11-27**, through today. That's ~2,300+ funding observations per asset — plenty for
  a statistically meaningful gross-return/vol estimate, unlike the futures leg's ~22 points.

## Kill decision

Both legs check out on real, live, unauthenticated data with genuine multi-year depth.
No ticker-discovery saga this time — proceeding straight to the data layer.
