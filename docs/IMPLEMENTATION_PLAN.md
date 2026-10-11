# Implementation plan

Build order for the forward test described in [`ARCHITECTURE.md`](ARCHITECTURE.md). Each phase
ends with a checkable exit condition; don't start a phase whose predecessor hasn't exited, except
where marked as parallel.

---

## Phase 0 — Scaffolding

- [x] `uv init` a Python 3.12 package `gtb` (src layout); dev deps: pytest, ruff, mypy.
- [x] Runtime deps: `ctrader-open-api` (protobuf messages only), `protobuf`, `boto3`, `httpx`,
      `pyyaml`. Keep pandas out of the Lambda image.
- [x] `ruff` and `pytest` configs; empty package skeleton matching ARCHITECTURE §10.
- [x] `data/early_close.yaml` with 2026–2027 CME early-close dates and flatten times.

**Exit:** `uv run pytest` and `uv run ruff check .` pass on the empty skeleton.

*Done 2026-10-11.* ruff clean; pytest has no tests yet (exit 5) — the first come in Phase 2.
`data/early_close.yaml` holds the past dates (from Phase 1); `future` is filled from
Pepperstone's holiday notices.

---

## Phase 1 — Hermes static variant (in `../hermes`)

Follow Hermes' own `CLAUDE.md` (use `.venv/bin/hermes`, always pass `--note`, consult
`hermes runs` first, `pytest` + `ruff` gates).

- [x] `strategies/misc/gold_tokyo_breakout_v2_static.py`, derived from
      `gold_tokyo_breakout_v2_flat.py`, implementing ARCHITECTURE §2 exactly:
  - orders placed **while processing the 08:55 bar**, after it updates the range (earliest fill
    on the 09:00 bar), once per day, never re-placed;
  - ATR frozen at that bar; entry offset 0.02·ATR;
  - `d = min(stop_atr·ATR, range + 2·off − 0.05·ATR)`; size from the full `stop_atr·ATR`;
  - both orders rest, each fills at most once, last fill on the 20:00 bar;
  - within one bar, a position's SL is processed **before** the opposite entry fills;
  - flatten as `_flat` (close sent at 20:45, filled 20:50 open);
  - early-close calendar: flatten before the early close (same dates as `data/early_close.yaml`).
- [x] Per-day diagnostics export (one row per weekday): Tokyo high/low, bars, history length,
      median, ATR at plan time, gate decision, both order levels, `d`, shortened flag, risk %.
      This becomes the core's test fixture.
- [x] Run 2018-01-01 → 2026-08-06, $100k, 1%, full costs. `hermes analyze <key>`.
- [x] Check every threshold in ARCHITECTURE §9.1 against `2fe13c7c79f3ee20`, including the
      same-day/same-side overlap (script compares the two trade lists).
- [x] Compute the variant's Monte Carlo worst drawdown **in R** (1,000 shuffles of per-trade R,
      additive) for the live drawdown rule.

**Exit (pass):** all thresholds met → record run key, freeze date (= **edge clock start**) and the
R drawdown threshold in `docs/REFERENCE.md`; export the per-day diagnostics and trade list to
`tests/fixtures/`.
*Passed 2026-10-11: run `92974ddaae48e824`, all six criteria — see `docs/REFERENCE.md`.*

**Exit (fail):** stop and revisit with the user; the agreed fallback is the 5-minute exact-parity
design judged against `2fe13c7c`. Don't tune the variant.

---

## Phase 2 — Core (TDD, offline)

Red-green against Hermes fixtures. No I/O in `gtb.core`.

- [x] Time helpers: UTC day boundaries, NY 17:00 anchor with DST, weekday checks.
- [x] `tokyo_range` and `range_history` (≥ 50 bars, ≥ 15 history, median of last 20).
- [x] 4h bucketing from 1h, sealed buckets only; Wilder ATR(20). Match Hermes' seeding — read
      `hermes.indicators` ATR before writing it.
- [x] `plan_day`: gate, levels, `d`/shortened flag, lots (floor to 0.01; skip below), expiry,
      early-close adjustments, `reason` codes.
- [x] `r_multiple`.
- [x] **Replay test:** feed historical bars (exported once from Hermes' cache into a compact
      fixture) through `plan_day` for every weekday 2018–2026.08 and compare with the variant's
      per-day diagnostics: gate decision and range/median exact, ATR ≤ 0.1%, levels to the tick.
- [x] Unit tests for the rare paths: shortened SL, early close, min-size skip, Monday after a
      holiday, DST change weeks.

**Exit:** replay test passes on all days.

*Done 2026-10-11.* `tests/test_replay.py` matches all 2,218 day plans (gate, reason, range, median
exact; ATR within 1.6e-8 relative with a 90-day 1h window; levels within half a tick; lots exact);
`tests/test_plan_day.py` covers early close, min-size skip (no orders) and a missing 08:55 bar.
Fixture `tests/fixtures/bars.csv.gz` (3.3 MB) from `../hermes/research/tokyo/static_export.py`.

---

## Phase 3 — Broker client (parallel with Phase 2)

- [x] Framing + TLS + protobuf send/receive with request/response correlation, heartbeats, timeout.
- [x] App auth, account auth, token refresh (write back to SSM via an injected store).
- [x] Symbol id and spec; trendbars M5/H1 with pagination (reuse ideas from Hermes'
      `ctrader_source.py`: `trendbar_to_bar`, `prev_page_end`).
- [x] Reconcile (positions + pending orders), place stop order (absolute SL, expiry, label,
      clientOrderId), cancel, close position, deal list, tick data.
- [x] Offline tests against an in-process fake server (`tests/fake_ctrader.py`); a manual
      `scripts/broker_smoke.py` that places and cancels a far-away order — on a demo account only.

**Exit:** smoke script runs end-to-end on demo; bars fetched by the client equal Hermes' cached
bars for an overlapping week.

*Done 2026-10-11.* Bars: 1,355 M5 bars (2026-10-05 → 10-09) identical to Hermes' cache, and 113
native H1 bars identical to H1 aggregated from M5. On demo account 46585934 (trading-scope token):
auth, symbol spec, balance, ticks, reconcile, and a buy stop (order 74143854, 0.01 lot at 5,033.22,
SL 4,982.89, expiry +30 min, label `gtb-smoke`) placed while the market was closed — accepted,
visible in reconcile and in cTrader — then cancelled and gone from reconcile.
Found live: `GetTrendbarsReq` ignores `fromTimestamp` (returns `count` bars back from
`toTimestamp`); the client filters to the window (regression test added). An access token needs
the **Trading** scope to place orders (an "Account info" token gets `TRADE permission required`).

---

## Phase 4 — Runs, records, alerts

- [x] `config.py`: env (`demo`/`live`), risk %, endpoints, S3 prefix; secrets from SSM.
- [x] `records`: DayRecord dataclass ↔ JSON (ARCHITECTURE §4.4), S3 read-modify-write.
- [x] `alerts`: Telegram send; message formats for the morning summary, EOD summary, alerts.
- [x] Handlers: `morning` (sleep to 09:00:10, wait for 08:55 bar with 15 s retries to 09:05:00,
      idempotent placement), `heartbeat`, `eod` (sleep to 20:50:05, cancel, close with retries,
      deals + tick-data spread, record, summary), `watchdog` (from F+1/F+2 min, check every
      30 s until F+4 min).
- [x] Handler tests with fake broker / S3 / Telegram (time injected, no sleeping in tests).
- [x] Local runner `scripts/run_local.py <handler> --env demo` for manual runs against demo.

**Exit:** each handler run locally against demo produces the expected orders/records/messages;
duplicate morning run is a no-op.

*Built 2026-10-11; exit pending a weekday run.* 56 offline tests (handlers against an in-memory
broker/clock with the real directory record store; S3 and SSM via botocore `Stubber`; Telegram via
httpx `MockTransport`). Run locally on demo 46585934 on a Sunday: heartbeat alerted the missing
record, morning recorded `weekend` (ATR 29.18, median 52.78 over 20, equity 50,448.88), a second
morning was a no-op, eod recorded no trades, the watchdog stayed quiet to 20:54. Found live: paging
45 days of M5 is rate-limited (`BLOCKED_PAYLOAD_TYPE`) -- the client now re-sends after a pause;
and request latency made the watchdog skip its 20:54:00 check -- the last wait is now clamped to
the deadline. Still to see on a weekday: morning placing both stops, eod cancelling/closing.
Decisions: a run refuses to start unless the account's live/demo flag matches `GTB_ENV`; the
heartbeat refreshes the token once it is 7 days old (`ctrader_token_refreshed_at` in SSM);
local runs never refresh it. R = net P&L / (risk x equity at entry), equity at entry = plan-time
equity + that day's earlier closed P&L.

---

## Phase 5 — Infrastructure

- [x] `Dockerfile` (AWS Lambda Python 3.12 base, arm64), one image, handler chosen per function.
- [x] Terraform: ECR, four Lambda functions, EventBridge schedules (UTC), S3 bucket (versioned),
      SSM parameters (values set by hand, not in state), IAM, Lambda error alarms → alert path.
- [x] `make`/script: build, push, `terraform apply` with the image digest.
- [x] Early-close days: one-off EventBridge schedules for `eod` and both watchdogs at that day's
      flatten time, generated from `data/early_close.yaml`.

**Exit:** demo stack deployed; a forced morning run in AWS places/cancels on demo; Telegram
messages arrive.

*Built 2026-10-11; not deployed yet.* `infra/` (one root module, `-var-file=env/<env>.tfvars`,
state in `s3://gtb-terraform-state-<account>-<region>/gtb/<env>.tfstate`, stack and state in
eu-west-1) passes
`terraform validate`. Per env: ECR repo, records bucket (versioned, private, SSE), five functions
from one arm64 image (`gtb.runs.app.<run>_handler`; `alarm` forwards CloudWatch alarms from SNS to
Telegram), weekday schedules (DISABLED until Phase 7 via `schedules_enabled`), early-close
one-offs from `python -m gtb.schedules`, Lambda error alarms. Secrets: `scripts/put_secrets.py`
(SSM SecureStrings, never in Terraform state). `scripts/deploy.sh <env> [--plan]` does state
bucket -> init -> ECR -> build/push -> plan/apply by digest. The image builds (641 MB), imports all
five handlers, finds the calendar, and the Lambda emulator resolves a handler from the image
command. Fixed on the way: a morning run started after 09:05 (e.g. a Lambda retry) now records
`cutoff_missed` instead of placing. `data/early_close.yaml` `future` is still empty.

---

## Phase 6 — Reconciliation (local)

- [x] `reconcile/` script: `aws s3 sync`, run the Hermes variant over the window (warm-up before
      it, starting equity = account's), three-layer report (ARCHITECTURE §4.5), R-based running
      totals for the clocks.
- [x] Classifies layer-2 differences (ask-side marginal touches) instead of failing them.

**Exit:** report runs over the first demo days.

*Built 2026-10-11; exit pending demo records.* `uv run python -m reconcile.report --env demo
[--local] [--end D]`: `aws s3 sync` (or `./local`), `reconcile/hermes_replay.py` run in Hermes'
venv from `$HERMES_DIR` (variant from 45 days before the first record, account equity, the bot's
early-close calendar merged in; exports day plans, trades and 09:00–21:00 5m bid bars), then
`reconcile.compare` (layer 1 exact / ATR 0.1%; layer 2 levels to half a tick, shortened flag,
risk as a share of each side's budget, entry and exit bars with ask-side marginal touches —
buy entries and short stops within the recorded spread, else $0.12 — marked `explained`; layer 3
live − replay R per trade) and `reconcile.clocks` (edge clock over replay trades since 2026-10-11,
execution clock rolling 20). 18 offline tests. Exit code 1 on a mismatch, a missing day or a
breach. Run end to end over the local Sunday record: replay 2026-08-27 → 10-11 (32 plans,
4 trades), nothing to compare yet.

---

## Phase 7 — Demo

- [ ] Demo account balance = replay starting equity; risk 1%; schedules on.
- [ ] Reconcile daily; fix → restart the layer-1 streak.
- [ ] Deliberately break once (bad token) to test alerts.
- [ ] Track the graduation checklist (ARCHITECTURE §9.2).

**Exit:** all graduation criteria met.

---

## Phase 8 — Live

- [ ] Fund the live account (≥ ~$20k), set `live` config at 0.25% risk; live secrets in SSM.
- [ ] Record starting balance (money stop at 85%) and execution-clock start in `docs/REFERENCE.md`.
- [ ] Weekly reconciliation; kill rules per ARCHITECTURE §9.3.

**Exit:** none planned — the test runs until a kill rule triggers or 2–3 years of results.
