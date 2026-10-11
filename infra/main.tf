data "aws_caller_identity" "current" {}

locals {
  name    = "gtb-${var.env}"
  account = data.aws_caller_identity.current.account_id
  # The image is pushed between the ECR-only first apply and the full one (scripts/deploy.sh).
  deployed = var.image_uri != ""

  # Run handlers: all of ARCHITECTURE §4.3 sleep inside the function to second offsets, and eod /
  # watchdog keep retrying until F + 4 min, so give them 6 minutes.
  runs = {
    morning   = { timeout = 360 }
    heartbeat = { timeout = 120 }
    eod       = { timeout = 360 }
    watchdog  = { timeout = 360 }
  }

  # Regular schedules, UTC, weekdays. Early-close days add one-offs (var.early_close_schedules).
  schedules = {
    morning   = { run = "morning", cron = "cron(0 9 ? * MON-FRI *)" }
    heartbeat = { run = "heartbeat", cron = "cron(10 9 ? * MON-FRI *)" }
    eod       = { run = "eod", cron = "cron(50 20 ? * MON-FRI *)" }
    watchdog1 = { run = "watchdog", cron = "cron(51 20 ? * MON-FRI *)" }
    watchdog2 = { run = "watchdog", cron = "cron(52 20 ? * MON-FRI *)" }
  }
}

# --- image registry -------------------------------------------------------------------------

resource "aws_ecr_repository" "gtb" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = false
}

resource "aws_ecr_lifecycle_policy" "gtb" {
  repository = aws_ecr_repository.gtb.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

# --- records (ARCHITECTURE §4.4) ------------------------------------------------------------

resource "aws_s3_bucket" "records" {
  bucket = "${local.name}-records-${local.account}"
}

resource "aws_s3_bucket_versioning" "records" {
  bucket = aws_s3_bucket.records.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "records" {
  bucket                  = aws_s3_bucket.records.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "records" {
  bucket = aws_s3_bucket.records.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
