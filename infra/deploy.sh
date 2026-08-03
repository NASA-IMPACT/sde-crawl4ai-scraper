#!/usr/bin/env bash
# Deploy SDE crawler infra (EC2 + S3 + IAM) with CDK.
# Prereq: AWS creds exported (AWS_ACCESS_KEY_ID / SECRET / SESSION_TOKEN or profile).
#
# This account uses a custom CDK bootstrap qualifier: "sde"
# (SSM /cdk-bootstrap/sde/version, roles cdk-sde-*).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not found. Install it, then re-run:" >&2
  echo "  brew install awscli node" >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1 && ! command -v npx >/dev/null 2>&1; then
  echo "node/npx not found (needed for CDK). Install it, then re-run:" >&2
  echo "  brew install node" >&2
  exit 1
fi

REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1

# Match existing account bootstrap (do NOT use default hnb659fds)
QUALIFIER="${CDK_BOOTSTRAP_QUALIFIER:-sde}"
export CDK_BOOTSTRAP_QUALIFIER="$QUALIFIER"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
ARN="$(aws sts get-caller-identity --query Arn --output text)"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT"

echo "Account:    $ACCOUNT"
echo "Caller:     $ARN"
echo "Region:     $REGION"
echo "Qualifier:  $QUALIFIER"
echo

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

CDK=(npx --yes aws-cdk@2)

BOOT_VER="$(aws ssm get-parameter \
  --name "/cdk-bootstrap/${QUALIFIER}/version" \
  --query 'Parameter.Value' \
  --output text 2>/dev/null || true)"

if [[ -z "$BOOT_VER" || "$BOOT_VER" == "None" ]]; then
  echo "Missing /cdk-bootstrap/${QUALIFIER}/version — bootstrapping..."
  "${CDK[@]}" bootstrap "aws://${ACCOUNT}/${REGION}" --qualifier "$QUALIFIER"
  BOOT_VER="$(aws ssm get-parameter \
    --name "/cdk-bootstrap/${QUALIFIER}/version" \
    --query 'Parameter.Value' \
    --output text)"
fi
echo "Bootstrap OK (qualifier=${QUALIFIER} version=${BOOT_VER})"
echo

echo "Deploying SdeCrawlerStack..."
"${CDK[@]}" deploy --require-approval never \
  -c "@aws-cdk/core:bootstrapQualifier=${QUALIFIER}"

echo
echo "Outputs:"
aws cloudformation describe-stacks \
  --stack-name SdeCrawlerStack \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output table
