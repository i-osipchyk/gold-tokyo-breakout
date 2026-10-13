locals {
  # Run handlers: all of ARCHITECTURE §4.3 sleep inside the function to second offsets, and eod /
  # watchdog keep retrying until F + 4 min, so give them 6 minutes. A failed run is retried by
  # Lambda after ~1 and ~2 min: every run is idempotent, and morning refuses to place orders after
  # 09:05, so retries can only help.
  functions = {
    for name, timeout in { morning = 360, heartbeat = 120, eod = 360, watchdog = 360 } :
    name => {
      handler       = "gtb.runs.app.${name}_handler"
      timeout       = timeout
      retries       = 2
      max_event_age = 300
    }
  }

  # Regular schedules, UTC, weekdays (minute granularity; second offsets are slept in the handler).
  schedules = {
    morning   = { function = "morning", expression = "cron(0 9 ? * MON-FRI *)" }
    heartbeat = { function = "heartbeat", expression = "cron(10 9 ? * MON-FRI *)" }
    eod       = { function = "eod", expression = "cron(50 20 ? * MON-FRI *)" }
    watchdog1 = { function = "watchdog", expression = "cron(51 20 ? * MON-FRI *)" }
    watchdog2 = { function = "watchdog", expression = "cron(52 20 ? * MON-FRI *)" }
  }

  # Early-close days: one-offs from early_close.auto.tfvars.json (committed; a test keeps it equal
  # to `python -m gtb.schedules`). Ones already past are dropped, so they leave on the next deploy.
  early_close = {
    for name, s in var.early_close_schedules : name => {
      function   = s.run
      expression = "at(${s.at})"
    } if timecmp("${s.at}Z", plantimestamp()) > 0
  }
}

# --- records (ARCHITECTURE §4.4) ------------------------------------------------------------

module "records" {
  source  = "git::https://github.com/i-osipchyk/platform.git//terraform/modules/data-bucket?ref=v0.1.2"
  project = var.project
  env     = var.env
  purpose = "records"
}

# --- functions, schedules, alarms (-> platform-alarms -> Telegram) ---------------------------

module "app" {
  source    = "git::https://github.com/i-osipchyk/platform.git//terraform/modules/lambda-app?ref=v0.1.2"
  project   = var.project
  env       = var.env
  image_uri = var.image_uri

  functions         = local.functions
  schedules         = merge(local.schedules, local.early_close)
  schedules_enabled = var.schedules_enabled

  environment = {
    GTB_ENV    = var.env
    GTB_BUCKET = module.records.name
  }
  write_bucket_arns  = [module.records.arn]
  ssm_write          = true # heartbeat rotates the cTrader token pair
  log_retention_days = 400  # audit trail
}
