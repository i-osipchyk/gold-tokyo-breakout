variable "project" {
  type    = string
  default = "gtb"
}

variable "env" {
  description = "dev (cTrader demo account) | prod (live account)"
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be dev or prod."
  }
}

variable "region" {
  description = "Fed from AWS_REGION: CI passes -var region=..., local shells export TF_VAR_region=$AWS_REGION."
  type        = string
  default     = "eu-west-1"
}

variable "image_uri" {
  description = "ECR image by digest (repo@sha256:...); CI passes it."
  type        = string
}

variable "schedules_enabled" {
  description = "Whether the daily runs fire (Phase 7 turns them on for demo)."
  type        = bool
  default     = false
}

variable "early_close_schedules" {
  description = "One-off runs for early-close days, from `python -m gtb.schedules` (UTC, no zone)."
  type = map(object({
    run = string
    at  = string
  }))
  default = {}
}
