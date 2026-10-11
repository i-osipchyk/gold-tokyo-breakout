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

- [ ] Time helpers: UTC day boundaries, NY 17:00 anchor with DST, weekday checks.
- [ ] `tokyo_range` and `range_history` (≥ 50 bars, ≥ 15 history, median of last 20).
- [ ] 4h bucketing from 1h, sealed buckets only; Wilder ATR(20). Match Hermes' seeding — read
      `hermes.indicators` ATR before writing it.
- [ ] `plan_day`: gate, levels, `d`/shortened flag, lots (floor to 0.01; skip below), expiry,
      early-close adjustments, `reason` codes.
- [ ] `r_multiple`.
- [ ] **Replay test:** feed historical bars (exported once from Hermes' cache into a compact
      fixture) through `plan_day` for every weekday 2018–2026.08 and compare with the variant's
      per-day diagnostics: gate decision and range/median exact, ATR ≤ 0.1%, levels to the tick.
- [ ] Unit tests for the rare paths: shortened SL, early close, min-size skip, Monday after a
      holiday, DST change weeks.

**Exit:** replay test passes on all days.

---

## Phase 3 — Broker client (parallel with Phase 2)

- [ ] Framing + TLS + protobuf send/receive with request/response correlation, heartbeats, timeout.
- [ ] App auth, account auth, token refresh (write back to SSM via an injected store).
- [ ] Symbol id and spec; trendbars M5/H1 with pagination (reuse ideas from Hermes'
      `ctrader_source.py`: `trendbar_to_bar`, `prev_page_end`).
- [ ] Reconcile (positions + pending orders), place stop order (absolute SL, expiry, label,
      clientOrderId), cancel, close position, deal list, tick data.
- [ ] Offline unit tests on framing and decoding with recorded fixtures; a manual
      `scripts/broker_smoke.py` against the **demo** that places and cancels a far-away order.

**Exit:** smoke script runs end-to-end on demo; bars fetched by the client equal Hermes' cached
bars for an overlapping week.

---

## Phase 4 — Runs, records, alerts

- [ ] `config.py`: env (`demo`/`live`), risk %, endpoints, S3 prefix; secrets from SSM.
- [ ] `records`: DayRecord dataclass ↔ JSON (ARCHITECTURE §4.4), S3 read-modify-write.
- [ ] `alerts`: Telegram send; message formats for the morning summary, EOD summary, alerts.
- [ ] Handlers: `morning` (sleep to 09:00:10, wait for 08:55 bar with 15 s retries to 09:05:00,
      idempotent placement), `heartbeat`, `eod` (sleep to 20:50:05, cancel, close with retries,
      deals + tick-data spread, record, summary), `watchdog` (from F+1/F+2 min, check every
      30 s until F+4 min).
- [ ] Handler tests with fake broker / S3 / Telegram (time injected, no sleeping in tests).
- [ ] Local runner `scripts/run_local.py <handler> --env demo` for manual runs against demo.

**Exit:** each handler run locally against demo produces the expected orders/records/messages;
duplicate morning run is a no-op.

---

## Phase 5 — Infrastructure

- [ ] `Dockerfile` (AWS Lambda Python 3.12 base, arm64), one image, handler chosen per function.
- [ ] Terraform: ECR, four Lambda functions, EventBridge schedules (UTC), S3 bucket (versioned),
      SSM parameters (values set by hand, not in state), IAM, Lambda error alarms → alert path.
- [ ] `make`/script: build, push, `terraform apply` with the image digest.
- [ ] Early-close days: one-off EventBridge schedules for `eod` and both watchdogs at that day's
      flatten time, generated from `data/early_close.yaml`.

**Exit:** demo stack deployed; a forced morning run in AWS places/cancels on demo; Telegram
messages arrive.

---

## Phase 6 — Reconciliation (local)

- [ ] `reconcile/` script: `aws s3 sync`, run the Hermes variant over the window (warm-up before
      it, starting equity = account's), three-layer report (ARCHITECTURE §4.5), R-based running
      totals for the clocks.
- [ ] Classifies layer-2 differences (ask-side marginal touches) instead of failing them.

**Exit:** report runs over the first demo days.

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
