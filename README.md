# M9 Fusion EV 1.3.6.6 — Negative EV OFF, $10 / $15

Paper-trading bot for PancakeSwap Prediction BNB rounds.

## Trading rule

```text
selected EV < 0.00       -> NO_TRADE
0.00 <= selected EV < .05 -> MID_NONNEGATIVE_EV -> $10
selected EV >= 0.05      -> HIGH_EV -> $15
```

The signal still must pass all existing version 1.3.6.4 protections:

- payout bucket ready;
- shadow recent PnL;
- quality win rate;
- quality profit factor;
- cooldown after each completed block of three real losses.

Negative-EV decisions remain in PostgreSQL history with `stake=0`,
`trade_executed=false`, and a NO_TRADE reason.

## Persistence

Existing PostgreSQL tables, bank, PnL and complete history are preserved.
Do not delete PostgreSQL and do not change `DATABASE_URL` during deployment.

## Endpoints

- `/healthz`
- `/health`
- `/signal`
- `/status?history=recent&limit=30`
- `/status?history=all&limit=100000`
- `/history/export.csv`
- `/shadow/performance`

See `INSTALL_FUSION_EV_1.3.6.6.txt` and
`RAILWAY_VARIABLES_FUSION_EV_1.3.6.6.txt`.
