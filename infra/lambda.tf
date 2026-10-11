# --- functions: one image, handler per function ---------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "Records"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.records.arn}/${var.env}/records/*"]
  }
  statement {
    # Without ListBucket a missing record is AccessDenied instead of NoSuchKey.
    sid       = "RecordsList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.records.arn]
  }
  statement {
    # Secrets are SecureStrings under the AWS-managed aws/ssm key, whose key policy already
    # allows use through SSM by principals of this account. Values are set by
    # scripts/put_secrets.py, never by Terraform (so they never enter its state).
    sid     = "Secrets"
    actions = ["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:PutParameter"]
    resources = [
      "arn:aws:ssm:${var.region}:${local.account}:parameter/gtb/${var.env}",
      "arn:aws:ssm:${var.region}:${local.account}:parameter/gtb/${var.env}/*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.name}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "run" {
  for_each          = merge(local.runs, { alarm = { timeout = 30 } })
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = 400
}

resource "aws_lambda_function" "run" {
  for_each      = local.deployed ? merge(local.runs, { alarm = { timeout = 30 } }) : {}
  function_name = "${local.name}-${each.key}"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = ["arm64"]
  memory_size   = 512
  timeout       = each.value.timeout

  image_config {
    command = ["gtb.runs.app.${each.key}_handler"]
  }

  environment {
    variables = {
      GTB_ENV    = var.env
      GTB_BUCKET = aws_s3_bucket.records.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.run, aws_iam_role_policy_attachment.lambda_logs]
}

resource "aws_lambda_function_event_invoke_config" "run" {
  for_each      = local.deployed ? local.runs : {}
  function_name = aws_lambda_function.run[each.key].function_name
  # A failed run is retried by Lambda after ~1 and ~2 min. Every run is idempotent, and morning
  # refuses to place orders after 09:05, so retries can only help.
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 300
}
