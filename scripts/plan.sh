#!/usr/bin/env bash
# Local `terraform plan` for one environment, against the same state CI uses. Applying happens
# only in GitHub Actions (.github/workflows/deploy.yml); never apply from here.
#
#   scripts/plan.sh dev                   # plan with the image that env runs now
#   scripts/plan.sh prod <repo@sha256:..> # plan with a given image (needed before prod's first deploy)
set -euo pipefail

ENV="${1:?usage: scripts/plan.sh <dev|prod> [image_uri]}"
case "$ENV" in dev|prod) ;; *) echo "env must be dev or prod" >&2; exit 1 ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_REGION="${AWS_REGION:-eu-west-1}"
export TF_VAR_region="$AWS_REGION"
TF="terraform -chdir=$ROOT/infra"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
IMAGE="${2:-$(aws lambda get-function --function-name "gtb-$ENV-morning" \
  --query Code.ImageUri --output text 2>/dev/null || true)}"
[[ -n "$IMAGE" ]] || { echo "gtb-$ENV-morning doesn't exist yet: pass an image_uri" >&2; exit 1; }

$TF init -input=false -reconfigure \
  -backend-config="bucket=tfstate-$ACCOUNT-$AWS_REGION" -backend-config="key=gtb/$ENV.tfstate" \
  -backend-config="region=$AWS_REGION" >/dev/null
$TF plan -input=false -var-file="env/$ENV.tfvars" -var="image_uri=$IMAGE"
