# --- Lambda error alarms -> SNS -> alarm function -> Telegram -------------------------------
# Catches what a run can't report itself: timeouts, crashes at start-up, an unreachable SSM.

resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each            = local.deployed ? local.runs : {}
  alarm_name          = "${local.name}-${each.key}-errors"
  alarm_description   = "${local.name}-${each.key} failed (error or timeout)"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.run[each.key].function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_sns_topic_subscription" "alarm_forwarder" {
  count     = local.deployed ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.run["alarm"].arn
}

resource "aws_lambda_permission" "sns" {
  count         = local.deployed ? 1 : 0
  statement_id  = "sns-alarms"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.run["alarm"].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alarms.arn
}
