# Architecture — Gold Tokyo-breakout forward test

How the forward test of the strategy in [`GOLD_TOKYO_BREAKOUT_SPEC.md`](GOLD_TOKYO_BREAKOUT_SPEC.md)
is run. The spec says *what the strategy is and what the backtest showed*; this document says
*what we actually trade, how, and how we judge it*. Where the two differ, this document wins for
the forward test, and every difference is listed in [§3](#3-differences-from-the-spec).

Decisions agreed on 2026-10-11 (grilling session); the decision log is in [§11](#11-decision-log).

---

## 1. Goal and phases

Find out (a) whether the edge is real on unseen data and (b) whether it survives real execution.

| Phase | What | Risk | Exit |
|---|---|---|---|
| 1. Reference | Hermes **static variant** backtest, checked against fixed acceptance thresholds ([§9.1](#91-variant-acceptance)) | — | pass → rules frozen, **edge clock starts**; fail → fallback ([§9.1](#91-variant-acceptance)) |
| 2. Build | bot core proven day-by-day against the variant; broker, runs, infra | — | core reproduces the variant's daily plans |
| 3. Demo | Pepperstone cTrader **demo**, automated | **1%**, balance = replay starting equity | all graduation criteria ([§9.2](#92-demo-graduation)) |
| 4. Live | Pepperstone cTrader **live**, automated | **0.25%**, ≥ ~$20k (comfortable $30–50k) | kill rules ([§9.3](#93-live-kill-rules)) |

Demo validates **machinery**, not edge or execution: ~10 trades say nothing about either, and demo
fills are not live fills.

---

## 2. Trading rules (static variant)

All times **UTC**. Bars are 5-minute **bid** trendbars, timestamped at bar **open**.
Instrument XAUUSD, 100 oz/lot, volume step 0.01 lot, tick 0.01.

### Parameters and constants

| Name | Value |
|---|---|
| `width_ratio` | 1.0 |
| `stop_atr` | 1.0 |
| entry offset `off` | 0.02 × ATR |
| SL buffer | 0.05 × ATR |
| risk per trade | 1% (demo) / 0.25% (live) of account equity |
| Tokyo window | bars with open time 00:00 ≤ t < 09:00 (last bar: 08:55) |
| valid range | weekday and ≥ 50 bars in the window |
| yardstick | median of the previous 20 valid Tokyo ranges, ≥ 15 required, today excluded |
| ATR | Wilder ATR(20) on 4h bars rebuilt from 1h, buckets anchored to 17:00 America/New_York (17, 21, 01, 05, 09, 13 NY), **sealed bars only** (a bucket is sealed once its last 5m bar has closed), evaluated once at the plan time. After the 08:55 bar: summer includes the 05:00–09:00 UTC bucket, winter ends with 02:00–06:00 UTC |

### Day logic

1. **09:00:10 — plan.** Requires the 08:55 bar to be closed and present. Then:
   `trade_today = weekday ∧ bars ≥ 50 ∧ len(history) ≥ 15 ∧ range ≥ width_ratio × median(history[-20:])`.
2. If `trade_today`, with `ATR` frozen at this moment:
   - `d = min(stop_atr·ATR, range + 2·off − 0.05·ATR)` — the SL never reaches the opposite entry;
   - **buy stop** at `high + off`, SL `entry − d`;
   - **sell stop** at `low − off`, SL `entry + d`;
   - **size from the full `stop_atr·ATR`** (not `d`):
     `lots = floor(risk · equity / (stop_atr · ATR · 100) / 0.01) · 0.01`;
     if `lots < 0.01` → **skip the day** (`skipped_min_size`), alert;
   - both orders rest at the broker with the SL attached and **expire at 20:05**
     (latest fill on the 20:00 bar).
3. Each order can fill once. Because `d < range + 2·off`, a position's SL is always reached before
   the opposite entry, so at most one position is open at a time.
4. **20:50:05 — flatten.** Market-close any open position; nothing else may remain.
5. **Early-close days** (CME holidays, ~9–10 a year, see `data/early_close.yaml`, which lists the
   open time `L` of the session's last 5m bar): the flatten fills at `min(20:50, L)` — the same
   convention as a summer Friday, whose last bar is 20:50 — and orders expire two bars before `L`
   (the last bar that can fill opens at `L − 10 min`).

No take-profit, no trailing, no other exits.

---

## 3. Differences from the spec

The spec's reference run `2fe13c7c79f3ee20` is **not** the forward-test reference. The reference is
the Hermes static variant (`gold_tokyo_breakout_v2_static`), which encodes exactly the rules above.

**Spec erratum (not a difference):** spec §2 says the ATR includes the forming 4h bar. The reference
code never set an indicator mode, so Hermes used its default `latest_confirmed` — **sealed bars
only** — and the spec's §7.1 trades reproduce exactly that way (2018-01-24: entry 1348.478, SL
1343.961, 2.27 lots; with the forming bar it would be SL 1343.835, 2.20 lots). The forward test uses
sealed bars, like the evidence.

| Spec (§2–3) | Forward test | Why |
|---|---|---|
| Orders re-placed every 5m bar with current ATR | placed once at 09:00:10, ATR frozen | two stateless runs a day instead of all-day monitoring |
| Orders first placed processing the 09:00 bar (earliest fill 09:05 bar) | placed after the 08:55 bar closes (earliest fill 09:00 bar) | range is final at 09:00:00; catch 09:00–09:05 breakouts at the level |
| No orders while a position is open | opposite order keeps resting; SL shortened on narrow days (~1.7% of filtered days) | keeps one-position-at-a-time without intraday logic |
| Size against stop distance | size against full 1·ATR even when SL is shortened | no extra size on the weakest-shaped trades |
| Buy stop / short SL trigger on bid | cTrader triggers them on **ask** | broker reality; ~1 spread (~0.005 R) per affected event, accepted |
| 5 holiday trades held overnight | flatten before CME early close | live note in spec §2 |
| Size floors to 0 → order rejected | same, plus alert | parity; account funded so it is rare |

---

## 4. Components

```
                     EventBridge Scheduler (UTC cron, minute granularity)
          09:00            09:10          20:50          20:51, 20:52
            │                │              │                 │
            ▼                ▼              ▼                 ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐   ┌────────────┐
     │  morning   │  │ heartbeat  │  │    eod     │   │  watchdog  │   Lambda handlers
     └─────┬──────┘  └─────┬──────┘  └─────┬──────┘   └─────┬──────┘   (one container image)
           │               │               │                │
           ▼               ▼               ▼                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ core (pure)   calendar · tokyo range · history · ATR(4h)    │
   │               gate · levels · sizing · DayPlan · R          │
   ├─────────────────────────────────────────────────────────────┤
   │ broker        asyncio cTrader Open API client (protobuf/TLS)│ ── cTrader demo / live
   │ records       DayRecord schema, S3 read/write               │ ── S3
   │ alerts        Telegram                                      │ ── Telegram
   │ config        demo|live, secrets from SSM                   │ ── SSM Parameter Store
   └─────────────────────────────────────────────────────────────┘

   local machine:  reconcile  ── pulls S3 records, runs Hermes variant replay, 3-layer report
```

### 4.1 core — pure, no I/O

Everything that decides *what to trade* is a pure function of bars, account equity, a date and the
early-close calendar. It is the only part that must match Hermes, and it is tested offline against
the variant's per-day plans for 2018–2026.

- `tokyo_range(bars_5m, day) -> Range | None` — high, low, bar count, validity.
- `range_history(bars_5m, day) -> list[float]` — previous valid ranges.
- `atr_4h(bars_1h, at) -> float` — rebuild 4h buckets (17:00 NY anchor, DST via zoneinfo),
  sealed buckets only, Wilder(20). Must match Hermes' ATR seeding; history fetched long enough
  (≥ 200 4h bars) that the seed is irrelevant.
- `plan_day(...) -> DayPlan` — gate decision with all inputs, the two orders (entry, SL, shortened
  flag, lots, expiry) or the reason for no trade.
- `r_multiple(trade, plan) -> float` — P&L ÷ risk actually taken (`lots · stop_atr·ATR · 100`).

### 4.2 broker — cTrader Open API

A small **asyncio** client: TLS socket, length-prefixed protobuf frames using the message classes
from `ctrader-open-api`, connect → app auth → account auth → requests → disconnect, per
invocation. Not the library's Twisted reactor (it cannot be restarted, which breaks on warm
Lambda containers).

Operations: symbol lookup and spec; trendbars (M5, H1) with pagination; reconcile (open positions
and pending orders); place stop order with absolute SL, expiry, `label` and `clientOrderId`;
cancel order; close position; deal list for a time range; tick data (bid and ask) for spread at
fill; token refresh.

Orders are tagged `label = "gtb"`, `clientOrderId = gtb-YYYYMMDD-{L|S}`. The bot only ever touches
orders and positions with its label.

### 4.3 Runs (Lambda handlers)

Every run is **stateless and idempotent**: it derives everything from the broker and S3, so a
duplicate or retried invocation is harmless.

| Run | Schedule | Does | Failure |
|---|---|---|---|
| `morning` | fires 09:00, sleeps to **09:00:10** | fetch bars (5m ~45 days, 1h ~90 days) and equity; wait until the 08:55 bar is closed, **retrying every 15 s until 09:05:00**; `plan_day`; if today's orders already exist (by `clientOrderId`) do nothing; place orders; write the morning part of the DayRecord; Telegram summary with layer-1 inputs | cutoff passed → alert, no orders (missed day, no risk) |
| `heartbeat` | 09:10 | alert if today's morning record is missing in S3 (catches a run that never started) | — |
| `eod` | fires 20:50, sleeps to **20:50:05** (earlier on early-close days) | cancel any `gtb` pending orders; market-close any `gtb` position with retries; read the day's deals and tick data; complete the DayRecord; Telegram summary | alert on any error |
| `watchdog` | two independent invocations, **20:51 and 20:52** | every 30 s until **20:54:00**: if any `gtb` position is open → close it (retrying) and **alert** at once | alert |

**Close timing.** The market can close as early as **20:55 UTC** (every summer Friday's last bar
is 20:50), so everything after the flatten happens inside those 5 minutes: `eod` sends the close at
20:50:05 and retries; two watchdogs (separate invocations, so one failing to start doesn't matter)
re-check every 30 s from 20:51 until 20:54:00, a minute before the earliest close. The first failed
close attempt alerts immediately, leaving ~4 minutes to close by hand. On early-close days the same
offsets apply to that day's flatten time `F` (`eod` at F+5 s, watchdogs F+1 and F+2 min, last check
F+4 min), via one-off schedules generated from `data/early_close.yaml`.

Alerts (Telegram): any run error, cutoff missed, missing heartbeat, `skipped_min_size`, order
rejected, watchdog had to close something, token refresh failure.

### 4.4 records — S3

One JSON document per trading day: `s3://<bucket>/<env>/records/YYYY/MM/DD.json`. Written in two
parts (morning, eod) by read-modify-write. Execution facts come from the broker's deal history,
not from what the bot believes it sent.

```json
{
  "date": "2026-10-12", "env": "demo", "code_version": "<git sha>",
  "inputs": {
    "tokyo_high": 0, "tokyo_low": 0, "bars": 108, "range": 0,
    "history_len": 20, "median": 0, "atr": 0, "equity": 0,
    "plan_time": "2026-10-12T09:00:14Z", "attempts": 1
  },
  "decision": {"trade": true, "reason": null},
  "orders": [
    {"side": "buy", "entry": 0, "sl": 0, "d": 0, "shortened": false, "lots": 0.0,
     "expiry": "2026-10-12T20:05:00Z", "client_order_id": "gtb-20261012-L", "status": "filled"}
  ],
  "trades": [
    {"side": "buy", "entry_time": "", "entry_price": 0, "spread_at_entry": 0,
     "exit_time": "", "exit_price": 0, "spread_at_exit": 0, "exit_reason": "sl|eod|watchdog",
     "lots": 0.0, "commission": 0, "pnl": 0, "risk_usd": 0, "r": 0}
  ],
  "alerts": []
}
```

`reason` values: `weekend`, `too_few_bars`, `short_history`, `narrow_range`, `skipped_min_size`,
`cutoff_missed`, `holiday`.

### 4.5 reconcile — local

Script in this repo, run on the developer machine with access to the Hermes repo (`HERMES_DIR`,
default `../hermes`). Pulls records (`aws s3 sync`), runs the Hermes static variant over the same
dates (with warm-up before the window, and starting equity = the account's), and prints a
three-layer report:

| Layer | Compared | Tolerance | A mismatch means |
|---|---|---|---|
| 1. Inputs | range, bars, median, gate decision; ATR | exact; ATR ≤ 0.1% | bug in code or data |
| 2. Orders and triggers | levels, SL, shortened flag, risk %, which sides triggered and when | levels to the tick; trigger differences labelled when the touch is within ~1 spread (ask-side) | broker trigger semantics |
| 3. Execution | entry/exit prices, spread at fill, commission, R | report live R − replay R per trade | execution cost |

Daily during demo, weekly in live. Hermes stays in Hermes; nothing of it runs in AWS.

---

## 5. Infrastructure

| Piece | Choice |
|---|---|
| IaC | Terraform (`infra/`) |
| Compute | AWS Lambda, **container image** (Docker, **arm64**), Python 3.12, timeout ~6 min, one image with one handler per run |
| Registry | ECR; Terraform points the functions at the image digest |
| Schedules | EventBridge Scheduler, cron in UTC (minute granularity — second offsets are slept inside the handler) |
| Storage | S3 bucket, versioning on |
| Secrets | SSM Parameter Store SecureString: cTrader client id/secret, access + refresh token, account id; Telegram bot token + chat id. The bot refreshes the access token (~30-day lifetime) and writes it back |
| Monitoring | Lambda error alarms → alert path, plus the S3-based `heartbeat` |
| Environments | `demo` and `live`: same image, different config (endpoint, account id, risk %, S3 prefix, schedules on/off) |
| Region | `$AWS_REGION` at deploy time — eu-west-1 (Ireland; latency is irrelevant for 5m bars), Terraform state included (`gtb-terraform-state-<account>-<region>`) |

---

## 6. Data the runs fetch

Each run recomputes from the broker; no state is carried between runs.

- **5m bid trendbars**, ~45 calendar days back → today's Tokyo range and the last 20 valid ranges.
- **1h bid trendbars**, ~90 calendar days back → sealed 4h buckets and ATR. cTrader's native 4h bars are not used (different anchoring; Hermes ADR-0006).
- **Account equity** at plan time (flat, so equity = balance).
- **Deals** and **tick data** (bid and ask) at EOD for fills and spread at fill.

---

## 7. Failure modes

| Failure | Effect | Handling |
|---|---|---|
| morning run fails or misses 09:05:00 cutoff | no trade that day | alert; heartbeat catches a run that never started |
| morning run fires twice | duplicate orders | idempotent by `clientOrderId` |
| 08:55 bar not yet available at 09:00:10 | — | retry every 15 s to 09:05:00 |
| **flatten fails** | position held overnight (Friday: over the weekend) | retries in `eod`, two watchdogs re-checking every 30 s from 20:51 to 20:54, alert on the first failed attempt → manual close before 20:55 |
| token expired / refresh fails | runs can't trade | alert on refresh failure, well before expiry |
| broker rejects an order (margin, invalid level) | no trade | alert; recorded |
| size floors below 0.01 lot | no trade | `skipped_min_size` + alert; fund the account so it is rare |
| early-close day missing from calendar | position open into the early close | calendar reviewed yearly; covered by unit test |
| opposite order fills while a position is open (gap through SL and entry) | two positions | cannot happen with continuous prices because of the 0.05 ATR buffer; on a gap the watchdog/EOD still flattens; alert |

---

## 8. Account and sizing

- **Demo:** 1% risk; demo balance set equal to the Hermes replay's starting equity so sizes are
  comparable (compared as risk %, since equity curves drift).
- **Live:** 0.25% risk. At current volatility one 0.01 lot risks ~$35–42 (stop ≈ 1 ATR ≈ $37 on
  ~$4,300 gold), so ≥ ~$17k is needed to never skip; ~$20k minimum, $30–50k for sizes that track
  0.25% closely. If volatility falls back toward 2018 levels (~0.35% of price), the minimum drops.
- Hedging account mode (the cTrader default) is assumed; with the shortened SL, positions never
  overlap, so netting would also work.

---

## 9. Evaluation

### 9.1 Variant acceptance

Run once, 2018-01-01 → 2026-08-06, full costs (spec §3), 1%, $100k. **All** must hold, compared with
`2fe13c7c79f3ee20`:

| Metric | Reference | Accept if |
|---|---|---|
| Trades | 1,121 | 1,065–1,177 (±5%) |
| Sharpe | 1.10 | ≥ 0.95 |
| Mean R per trade | +0.10 | ≥ +0.08 |
| Profit factor | 1.23 | ≥ 1.18 |
| Monte Carlo worst drawdown | −45.9% | ≥ −52% |
| Same-day, same-side overlap | — | ≥ 95% of reference trade days |

Sanity: the first trades of spec §7.1 match within the frozen-ATR tolerance.

**If it fails:** no tweaking. Fall back to a 5-minute stateless reconciler Lambda that keeps exact
spec parity and is judged against `2fe13c7c` itself.

### 9.2 Demo graduation

All of:

1. ≥ 20 trading days and ≥ 10 trades on demo.
2. Layer 1 exact on 10 consecutive trading days (a fixed mismatch restarts the count).
3. Every layer-2 mismatch explained.
4. Every scenario seen at least once: long fill, short fill, SL exit, EOD exit, gate-failed day,
   both sides filled on one day, **Friday flatten**. Shortened SL and early close are covered by
   unit tests with recorded fixtures.
5. No watchdog close (if one happens: fix, and the count in 2 restarts).
6. Alerts tested deliberately (e.g. a bad token on demo).

Demo P&L and demo slippage are not criteria.

### 9.3 Live kill rules

Two clocks:

- **Edge clock** — starts on the date the variant is frozen (its reference run recorded, before the
  demo). Counts every trade of the Hermes variant replay from then on, demo period included.
  Aug 6 → freeze may be reported as a side note but does not count.
- **Execution clock** — starts with the first live trade.

| Rule | Measured on | Trigger | Action |
|---|---|---|---|
| Drawdown | edge clock | cumulative drawdown in R > the variant's Monte Carlo worst drawdown in R (**−47.2 R**, see `docs/REFERENCE.md`) | stop the test |
| Expectancy | edge clock | after ≥ 100 trades, mean R < −0.10 | stop the test |
| Execution | execution clock | rolling 20-trade mean of (live R − replay R) < −0.05 R | pause, fix, resume (clock keeps running) |
| Money stop | live account | equity < 85% of the starting live balance | stop |
| Rule change | — | any change to §2 | restart both clocks |

Expect 2–3 years of results to confirm a Sharpe near 1; one year can only falsify it (spec §6).

---

## 10. Repository layout

```
docs/                 spec, this file, implementation plan
src/gtb/
  core/               pure strategy logic (§4.1)
  broker/             cTrader asyncio client (§4.2)
  runs/               Lambda handlers: morning, heartbeat, eod, watchdog (§4.3)
  records/            DayRecord schema + S3 (§4.4)
  alerts/             Telegram
  config.py           demo|live config, SSM secrets
reconcile/            local three-layer report against Hermes (§4.5)
data/early_close.yaml CME early-close calendar
tests/                offline; fixtures exported from Hermes
infra/                Terraform
Dockerfile            arm64 Lambda image
```

The Hermes static variant lives in the Hermes repo:
`../hermes/strategies/misc/gold_tokyo_breakout_v2_static.py`.

---

## 11. Decision log

| # | Decision | Rejected |
|---|---|---|
| 1 | Demo (automated), then live at 0.25% | signal-only shadow; manual execution |
| 2 | Fund so size rarely floors; floor → skip + alert | round up to 0.01 lot |
| 3 | Standalone Python bot; Hermes as independent judge | live venue inside Hermes; C# cBot |
| 4 | Native orders at spec prices; ask-side triggers accepted | spread-shifted levels; bot-side tick triggers |
| 5 | Place once (09:00), flatten once (20:50), as a declared, backtested rule change | 5-minute exact-parity reconciler (kept as fallback) |
| 6 | SL shortened to stay inside the opposite entry, buffer 0.05 ATR; size from full ATR | size from shortened stop; skip narrow days |
| 7 | AWS Lambda + EventBridge; flatten watchdogs; Telegram alerts | VPS + cron; email alerts |
| 8 | S3 JSON per day; local reconciliation, daily (demo) → weekly (live) | DynamoDB; automated reconciliation in the cloud |
| 9 | Demo graduation on machinery criteria only | P&L-based graduation |
| 10 | Demo at 1% risk; R-based kill rules; −15% money stop | %-equity rules |
| 11 | Two clocks: edge (variant replay from freeze) and execution (live) | live trades only; clock from 2026-08-06 |
| 12 | Variant acceptance thresholds fixed before the run; fail → fallback, no tweaking | — |
| 13 | Terraform, Docker arm64 image, asyncio client, SSM, eu-west-1 (moved from eu-central-1 before the first deploy) | zip + layers; Twisted in Lambda |
| 14 | Plan at 09:00:10 after the 08:55 bar, retry every 15 s to 09:05:00; expiry 20:05 | plan at 09:05 (spec timing) |
