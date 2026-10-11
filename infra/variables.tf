variable "env" {
  description = "demo | live"
  type        = string

  validation {
    condition     = contains(["demo", "live"], var.env)
    error_message = "env must be demo or live."
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "image_uri" {
  description = "ECR image by digest (repo@sha256:...). Empty only for the first, ECR-only apply."
  type        = string
  default     = ""
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
