#!/usr/bin/env bash
# Tear down SdeCrawlerStack. Empties + deletes the RETAIN'd S3 bucket first.
# Prereq: AWS creds exported.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
QUALIFIER="${CDK_BOOTSTRAP_QUALIFIER:-sde}"
export CDK_BOOTSTRAP_QUALIFIER="$QUALIFIER"

STACK="${STACK_NAME:-SdeCrawlerStack}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT"

echo "Account:    $ACCOUNT"
echo "Region:     $REGION"
echo "Qualifier:  $QUALIFIER"
echo "Stack:      $STACK"
echo

BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
  --output text 2>/dev/null || true)"

INSTANCE_ID="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" \
  --output text 2>/dev/null || true)"

echo "Will remove:"
echo "  InstanceId: ${INSTANCE_ID:-unknown}"
echo "  Bucket:     ${BUCKET:-unknown}"
echo "  + VPC, SG, IAM role/profile (all stack resources)"
echo
read -r -p "Type DESTROY to confirm: " CONFIRM
if [[ "$CONFIRM" != "DESTROY" ]]; then
  echo "Aborted."
  exit 1
fi

if [[ -n "$BUCKET" && "$BUCKET" != "None" ]]; then
  echo "Emptying s3://${BUCKET} ..."
  aws s3 rm "s3://${BUCKET}" --recursive || true
  # remove any leftover delete markers / versions if versioning ever enabled
  aws s3api delete-bucket --bucket "$BUCKET" 2>/dev/null \
    || echo "(bucket delete deferred to stack destroy / manual if still non-empty)"
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

CDK=(npx --yes aws-cdk@2)
echo "Destroying CloudFormation stack ${STACK}..."
"${CDK[@]}" destroy --force -c "@aws-cdk/core:bootstrapQualifier=${QUALIFIER}"

# If RETAIN left the bucket after destroy, remove it now
if [[ -n "$BUCKET" && "$BUCKET" != "None" ]]; then
  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "Bucket still exists (RETAIN). Emptying + deleting..."
    aws s3 rm "s3://${BUCKET}" --recursive || true
    aws s3api delete-bucket --bucket "$BUCKET"
  fi
fi

echo
echo "Stack gone. Note: CDK bootstrap (CDKToolkit) in this account/region is left alone."
echo "List remaining stack resources (should fail / empty):"
aws cloudformation describe-stacks --stack-name "$STACK" 2>&1 | head -5 || true
