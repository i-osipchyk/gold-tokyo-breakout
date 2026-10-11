# Forward-test reference

The frozen numbers the forward test is judged against (ARCHITECTURE §9). Written at the end of
Phase 1; append to it, don't rewrite it.

## Reference run

| | |
|---|---|
| Strategy | `../hermes/strategies/misc/gold_tokyo_breakout_v2_static.py` |
| Hermes ledger key | **`92974ddaae48e824`** (`hermes show 92974dda`) |
| Configuration | XAUUSD 5m bid, 2018-01-01 → 2026-08-06, $100k, 1% risk, width 1.0, stop 1.0 ATR, full costs (spec §3) |
| Rules frozen | **2026-10-11** — the **edge clock starts here** |
| Execution clock | starts with the first live trade (not yet) |

## Acceptance (ARCHITECTURE §9.1) — ACCEPTED

Compared with the spec's run `2fe13c7c79f3ee20`, thresholds fixed before the run
(`../hermes/research/tokyo/static_acceptance.py 92974ddaae48e824`):

| Criterion | Variant | Reference | Threshold | |
|---|---|---|---|---|
| Trades | 1,126 | 1,121 | 1,065–1,177 | pass |
| Sharpe | 1.068 | 1.10 | ≥ 0.95 | pass |
| Mean R per trade | 0.0953 | 0.0976 | ≥ 0.08 | pass |
| Profit factor | 1.223 | 1.23 | ≥ 1.18 | pass |
| Monte Carlo worst drawdown | −39.0% | −45.9% | ≥ −52% | pass |
| Same-day, same-side overlap | 99.9% | — | ≥ 95% | pass |

Sanity: the first trade (2018-01-24 09:20 buy, 1348.478 / SL 1343.961, 2.27 lots) is identical to
the reference's.

Other figures from `hermes analyze 92974dda`: total return +170.8%, CAGR 12.3%, max drawdown
−17.0%, win rate 47.1%; Monte Carlo median drawdown −19.4%; costs 0x/1x/2x Sharpe 1.32/1.07/0.82;
first 70% / last 30% Sharpe 1.09/1.13; profitable in all four trend × volatility regimes; stop
shortened on 28 of 1,070 traded days (2.6%). Axis not run: `gate` (needs an LLM summary; not part
of the acceptance).

## Live kill-rule thresholds (ARCHITECTURE §9.3)

| Rule | Threshold |
|---|---|
| Drawdown (edge clock) | cumulative drawdown worse than **−47.2 R** (Monte Carlo worst over 1,000 shuffles of the variant's per-trade R, additive, seed 42) |
| Expectancy (edge clock) | after ≥ 100 trades, mean R < −0.10 |
| Execution (execution clock) | rolling 20-trade mean of (live R − replay R) < −0.05 R |
| Money stop (live account) | equity < 85% of the starting live balance (set when funded) |

R = net P&L ÷ (risk % × equity at entry).

## Fixtures

`tests/fixtures/static_day_plans.csv` (2,218 weekday plans: 1,070 trade, 1,133 narrow_range,
15 short_history) and `tests/fixtures/static_trades.csv` (1,126 trades), exported by
`../hermes/research/tokyo/static_export.py` from the same configuration.
