#!/usr/bin/env bash
# Drop a small smoke job on the EC2 crawler via SSM
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$REGION"
STACK="${STACK_NAME:-SdeCrawlerStack}"
SEED="${1:-https://aurorasaurus.org/}"
COLL="${2:-aurorasaurus}"
MAX="${3:-15}"

INSTANCE_ID="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" \
  --output text)"
BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
  --output text)"

JOB_JSON=$(printf '{"seed":"%s","collection_id":"%s","max_pages":%s}' "$SEED" "$COLL" "$MAX")
# shellcheck disable=SC2016
REMOTE=$(cat <<EOF
set -euo pipefail
cat > /opt/sde-crawler/jobs/incoming/${COLL}.json <<'JOB'
${JOB_JSON}
JOB
chown ec2-user:ec2-user /opt/sde-crawler/jobs/incoming/${COLL}.json
ls -la /opt/sde-crawler/jobs/incoming/
pgrep -af watch_inbox || echo NO_WATCHER
EOF
)

# encode as JSON array of one shell script via python for safety
PARAMS=$(JOB_BODY="$REMOTE" python3 - <<'PY'
import json, os
print(json.dumps({"commands": [os.environ["JOB_BODY"]]}))
PY
)

echo "Instance: $INSTANCE_ID"
echo "Job:      $JOB_JSON"
CMD_ID="$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --comment "SDE smoke ${COLL}" \
  --parameters "$PARAMS" \
  --query 'Command.CommandId' \
  --output text)"
echo "CommandId: $CMD_ID"
sleep 3
aws ssm get-command-invocation \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent]' \
  --output text

echo
echo "Wait 1–3 min, then:"
echo "  aws s3 ls s3://${BUCKET}/scraped_collections/"
echo "  ./scripts/check_smoke.sh"
