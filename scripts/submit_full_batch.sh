#!/usr/bin/env bash
# Submit jobs/examples/*.json to the deployed EC2 inbox.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$REGION"
STACK="${STACK_NAME:-SdeCrawlerStack}"
BATCH_DIR="${ROOT}/jobs/examples"

INSTANCE_ID="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" \
  --output text)"
BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
  --output text)"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TAR="/tmp/sde-full-batch-${STAMP}.tgz"
S3_KEY="bootstrap/full-batch-${STAMP}.tgz"

echo "Instance: $INSTANCE_ID"
echo "Bucket:   $BUCKET"
echo "Jobs:"
ls -1 "$BATCH_DIR"/*.json

export COPYFILE_DISABLE=1
tar -C "$BATCH_DIR" -czf "$TAR" .
aws s3 cp "$TAR" "s3://${BUCKET}/${S3_KEY}"
rm -f "$TAR"

CMD_ID="$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --comment "SDE full batch ${STAMP}" \
  --timeout-seconds 300 \
  --parameters commands="[
    \"set -euo pipefail\",
    \"aws s3 cp s3://${BUCKET}/${S3_KEY} /tmp/sde-full-batch.tgz\",
    \"mkdir -p /opt/sde-crawler/jobs/incoming\",
    \"tar --warning=no-unknown-keyword -xzf /tmp/sde-full-batch.tgz -C /opt/sde-crawler/jobs/incoming\",
    \"chown -R ec2-user:ec2-user /opt/sde-crawler/jobs/incoming\",
    \"rm -f /tmp/sde-full-batch.tgz\",
    \"ls -la /opt/sde-crawler/jobs/incoming/\",
    \"pgrep -af watch_inbox || echo NO_WATCHER\"
  ]" \
  --query 'Command.CommandId' \
  --output text)"

echo "CommandId: $CMD_ID"
sleep 5
aws ssm get-command-invocation \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' \
  --output text

echo
echo "Queued (up to 3 collections run at once)."
echo "  aws s3 ls s3://${BUCKET}/scraped_collections/"
