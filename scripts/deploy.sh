#!/usr/bin/env bash
# Build, push and deploy one environment (IMPLEMENTATION_PLAN Phase 5).
#
#   scripts/deploy.sh demo            # plan + apply (Terraform asks before changing anything)
#   scripts/deploy.sh demo --plan     # stop after the plan
#
# The stack goes to $AWS_REGION (required; from the environment or .env).
#
# Steps: state bucket (once) -> terraform init -> ECR repo -> docker build/push (arm64, tagged
# with the git sha) -> early-close one-off schedules from data/early_close.yaml -> plan/apply
# with the image digest. Secrets are not touched here: scripts/put_secrets.py.
set -euo pipefail

ENV="${1:?usage: scripts/deploy.sh <demo|live> [--plan]}"
MODE="${2:-apply}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# AWS_REGION from the environment, else from .env (that one key only: the secrets stay out of
# this shell).
if [[ -z "${AWS_REGION:-}" && -f "$ROOT/.env" ]]; then
  AWS_REGION="$(sed -n 's/^AWS_REGION=["'\'']\{0,1\}\([^"'\'']*\)["'\'']\{0,1\}$/\1/p' "$ROOT/.env" | tail -1)"
fi
export AWS_REGION="${AWS_REGION:?set AWS_REGION (environment or .env) to the region to deploy to}"
REGION="$AWS_REGION"
TF="terraform -chdir=$ROOT/infra"

case "$ENV" in
  demo) ;;
  live)
    # CLAUDE.md: never deploy live without an explicit instruction in the current conversation.
    read -r -p "Deploying LIVE (real money). Type 'live' to continue: " answer
    [[ "$answer" == "live" ]] || { echo "aborted"; exit 1; } ;;
  *) echo "env must be demo or live" >&2; exit 1 ;;
esac

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="gtb-terraform-state-$ACCOUNT"

# The state bucket may live in another region than the stack (it was created in eu-central-1);
# the backend just needs to know where.
if ! aws s3api head-bucket --bucket "$STATE_BUCKET" 2>/dev/null; then
  echo "creating state bucket $STATE_BUCKET"
  aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" \
    --versioning-configuration Status=Enabled
  aws s3api put-public-access-block --bucket "$STATE_BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

STATE_REGION="$(aws s3api get-bucket-location --bucket "$STATE_BUCKET" \
  --query LocationConstraint --output text)"
[[ "$STATE_REGION" == "None" ]] && STATE_REGION="us-east-1"

$TF init -input=false -reconfigure \
  -backend-config="bucket=$STATE_BUCKET" -backend-config="key=gtb/$ENV.tfstate" \
  -backend-config="region=$STATE_REGION" >/dev/null

VARS=(-var-file="env/$ENV.tfvars" -var="region=$REGION")

# The functions need an image, the image needs the repository.
REPO="$($TF output -raw ecr_repository_url 2>/dev/null || true)"
if [[ -z "$REPO" || "$REPO" == *"No outputs"* ]]; then
  $TF apply -input=false "${VARS[@]}" -target=aws_ecr_repository.gtb \
    -target=aws_ecr_lifecycle_policy.gtb
  REPO="$($TF output -raw ecr_repository_url)"
fi

SHA="$(git -C "$ROOT" rev-parse HEAD)"
DIRTY=""
if [[ -n "$(git -C "$ROOT" status --porcelain -- src data pyproject.toml uv.lock Dockerfile)" ]]; then
  DIRTY="-dirty"
fi
TAG="${SHA:0:12}$DIRTY"
SHA="$SHA$DIRTY"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
docker build --platform linux/arm64 --provenance=false --build-arg GIT_SHA="$SHA" \
  -t "$REPO:$TAG" "$ROOT"
docker push "$REPO:$TAG" >/dev/null
DIGEST="$(aws ecr describe-images --region "$REGION" --repository-name "${REPO#*/}" \
  --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)"
echo "image $REPO@$DIGEST ($TAG)"

(cd "$ROOT" && uv run --quiet python -m gtb.schedules 2>/dev/null) \
  > "$ROOT/infra/early_close.auto.tfvars.json"

$TF plan -input=false "${VARS[@]}" -var="image_uri=$REPO@$DIGEST" -out=tfplan
[[ "$MODE" == "--plan" ]] && exit 0
$TF apply -input=false tfplan
