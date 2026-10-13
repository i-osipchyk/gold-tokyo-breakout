# Working in gold-tokyo-breakout

Forward test of the gold Tokyo-range breakout: an automated bot on Pepperstone cTrader (demo,
then live), running as AWS Lambdas, judged against a Hermes backtest replay.

Read these before writing code:

| File | What it is |
|---|---|
| `docs/GOLD_TOKYO_BREAKOUT_SPEC.md` | The original strategy and backtest evidence. |
| `docs/ARCHITECTURE.md` | What we actually trade and how. **Wins over the spec** where they differ (its §3 lists every difference). |
| `docs/IMPLEMENTATION_PLAN.md` | Build order, phases and exit conditions. Work the current phase. |

## Rules that must not drift

- **The trading rules (ARCHITECTURE §2) are frozen.** Don't change a constant, a time, or the
  sizing/SL formula without the user explicitly asking — any rule change restarts the forward-test
  clock. If a rule looks wrong, say so; don't fix it.
- **The core is pure.** `gtb.core` takes bars, equity, date and calendar and returns a `DayPlan`.
  No network, clock, env or AWS in it. It is the part that must match Hermes exactly.
- **All times UTC; bars timestamped at open.** The last Tokyo bar is 08:55; the range is final at
  09:00:00. 4h buckets anchor at 17:00 America/New_York (zoneinfo handles DST); never use
  cTrader's native 4h bars.
- **Runs are stateless and idempotent.** Derive everything from the broker and S3 each time;
  identify our orders by `label="gtb"` and `clientOrderId=gtb-YYYYMMDD-{L|S}`; touch nothing else
  on the account.
- **Record broker facts.** Execution data in a DayRecord comes from the deal history and tick
  data, not from what the bot sent.
- **Hermes is the judge, not a dependency.** Nothing from Hermes is imported by the bot or shipped
  in the image. Hermes lives at `../hermes`; work there under its own `CLAUDE.md` (its venv, its
  CLI, `--note` on every run).

## Safety

- Never place, modify or close orders on the **live** account, or deploy the `prod` environment
  (or set `PROD_ENABLED`), without an explicit instruction in the current conversation. Demo is
  fine for smoke tests.
- Check which account `.env` points at before anything that trades (`CTraderClient.is_live()`);
  `scripts/broker_smoke.py` refuses to place orders on a live account.
- Never print or commit secrets. They live in SSM Parameter Store; locally in an untracked `.env`.
- Tests are **offline**: fake broker/S3/Telegram, recorded fixtures, injected time. No test sleeps
  or hits the network.

## Commands

```bash
uv run pytest                 # offline test suite
uv run ruff check .           # lint
uv run python scripts/broker_smoke.py   # real Open API via .env; order round trip on demo only
uv run python scripts/run_local.py <morning|eod|watchdog|heartbeat> --env dev
docker build --platform linux/arm64 -t gtb .
scripts/plan.sh <dev|prod>   # local terraform plan against CI's state; never apply locally
uv run python scripts/put_secrets.py --env <dev|prod>   # .env -> SSM /gtb/<env>/*
uv run python -m gtb.schedules > infra/early_close.auto.tfvars.json   # after editing the calendar
```

Infra and deployment follow `~/dev/platform` (`lambda-app` / `data-bucket` modules, GitHub
Actions deploy). Deliberate deviations:
- **Environments are `dev` and `prod`, one per branch**, not the platform's `demo` / `live` with
  promotion: a push to `dev` deploys dev (cTrader **demo** account, 1% risk); a push to `main`
  deploys prod (**live** account, 0.25%), after approval and only while the repo variable
  `PROD_ENABLED` is `true` (unset until dev is validated). `gtb.config.ACCOUNT` maps env → account
  kind, and every run refuses an account of the wrong kind. Names, SSM paths, S3 prefixes and
  records use the env (`gtb-dev-*`, `/gtb/dev/*`, `dev/records/…`); "demo" / "live" mean the
  broker accounts only.
- SSM parameter names are snake_case (`/gtb/<env>/ctrader_client_id`), not kebab-case: the
  heartbeat writes the rotated token pair back under these names, so renaming needs a coordinated
  re-put of every secret. The app reads them with its own `config.load_secrets`.
- Functions keep `retries = 2` (every run is idempotent; morning refuses to place after 09:05) and
  400-day log retention (audit trail).

(Created in Phase 0–5; if a command doesn't exist yet, the plan says which phase adds it.)

## Gates

Before calling anything done: `uv run pytest`, `uv run ruff check .` and `uv run mypy` pass (CI runs all three). Changes to
`gtb.core` must keep the Hermes replay test green.
