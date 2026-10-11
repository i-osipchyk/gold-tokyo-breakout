# --- EventBridge Scheduler (UTC, minute granularity; second offsets are slept in the handler) ---

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  count = local.deployed ? 1 : 0
  name  = "${local.name}-invoke"
  role  = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [for k, f in aws_lambda_function.run : f.arn if k != "alarm"]
    }]
  })
}

resource "aws_scheduler_schedule_group" "gtb" {
  name = local.name
}

resource "aws_scheduler_schedule" "daily" {
  for_each                     = local.deployed ? local.schedules : {}
  name                         = each.key
  group_name                   = aws_scheduler_schedule_group.gtb.name
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "UTC"
  state                        = var.schedules_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.run[each.value.run].arn
    role_arn = aws_iam_role.scheduler.arn
    # Delivery retries only (the function's own failures are retried by Lambda); a run delivered
    # late is pointless.
    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 2
    }
  }
}

resource "aws_scheduler_schedule" "early_close" {
  for_each                     = local.deployed ? var.early_close_schedules : {}
  name                         = each.key
  group_name                   = aws_scheduler_schedule_group.gtb.name
  schedule_expression          = "at(${each.value.at})"
  schedule_expression_timezone = "UTC"
  state                        = var.schedules_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.run[each.value.run].arn
    role_arn = aws_iam_role.scheduler.arn
    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 2
    }
  }
}
