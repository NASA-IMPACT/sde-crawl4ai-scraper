#!/usr/bin/env bash
# Package app → S3 → start install on EC2 in background → poll until done.
# Prereq: stack deployed; AWS creds exported.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$REGION"

STACK="${STACK_NAME:-SdeCrawlerStack}"
APP_DIR="/opt/sde-crawler"

echo "Resolving stack outputs from ${STACK}..."
INSTANCE_ID="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" \
  --output text)"
BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
  --output text)"

if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
  echo "InstanceId not found. Deploy infra first: cd infra && ./deploy.sh" >&2
  exit 1
fi
if [[ -z "$BUCKET" || "$BUCKET" == "None" ]]; then
  echo "BucketName not found." >&2
  exit 1
fi

echo "Instance: $INSTANCE_ID"
echo "Bucket:   $BUCKET"
echo "Region:   $REGION"
echo

echo "Waiting for SSM agent online..."
for _ in $(seq 1 60); do
  STATUS="$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
    --query 'InstanceInformationList[0].PingStatus' \
    --output text 2>/dev/null || true)"
  if [[ "$STATUS" == "Online" ]]; then
    echo "SSM Online."
    break
  fi
  sleep 10
done
if [[ "$STATUS" != "Online" ]]; then
  echo "SSM not Online (last=${STATUS})." >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TAR="/tmp/sde-crawler-${STAMP}.tgz"
REMOTE_SH="/tmp/sde-remote-install-${STAMP}.sh"
S3_TAR="bootstrap/sde-crawler-${STAMP}.tgz"
S3_SH="bootstrap/sde-remote-install-${STAMP}.sh"
STAGE="/tmp/sde-stage-${STAMP}"

echo "Packaging app (no vendor/) → ${TAR}"
rm -rf "$STAGE"
mkdir -p "$STAGE/jobs/incoming" "$STAGE/sde_crawler"
cp "$ROOT/run.py" "$ROOT/watch_inbox.sh" "$STAGE/"
cp -R "$ROOT/sde_crawler/." "$STAGE/sde_crawler/"
touch "$STAGE/jobs/incoming/.gitkeep"
cat > "$STAGE/requirements.txt" <<'REQ'
crawl4ai==0.9.2
httpx>=0.27.2
beautifulsoup4>=4.12
pypdf>=4.0
boto3>=1.34
REQ
export COPYFILE_DISABLE=1
tar -C "$STAGE" -czf "$TAR" .
rm -rf "$STAGE"
ls -lh "$TAR"

cat > "$REMOTE_SH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
STATUS_FILE=/var/log/sde-install.status
LOG=/var/log/sde-install.log
echo "RUNNING" > "\$STATUS_FILE"
exec > >(tee -a "\$LOG") 2>&1
echo "=== install \$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

fail() {
  echo "FAILED" > "\$STATUS_FILE"
  echo "ERROR: \$*" >&2
  exit 1
}

echo "[1/6] packages"
dnf install -y -q python3.11 python3.11-pip python3.11-devel git gcc inotify-tools \\
  at-spi2-atk libX11 libXcomposite libXcursor libXdamage libXext \\
  libXi libXrandr libXScrnSaver libXtst pango alsa-lib nss nspr \\
  cups-libs mesa-libgbm libdrm atk at-spi2-core libxkbcommon \\
  || fail "dnf install"

echo "[2/6] unpack"
mkdir -p ${APP_DIR}
cd ${APP_DIR}
rm -rf run.py watch_inbox.sh sde_crawler requirements.txt .venv
aws s3 cp "s3://${BUCKET}/${S3_TAR}" /tmp/sde-crawler.tgz || fail "s3 cp tarball"
tar --warning=no-unknown-keyword -xzf /tmp/sde-crawler.tgz -C ${APP_DIR}
rm -f /tmp/sde-crawler.tgz

echo "[3/6] venv + pip"
python3.11 -m venv .venv || fail "venv"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -U pip wheel || fail "pip upgrade"
pip install -q -r requirements.txt || fail "pip install"

echo "[4/6] dirs"
mkdir -p jobs/incoming jobs/done jobs/failed output/collections logs/collections logs/jobs ms-playwright
chmod +x watch_inbox.sh
mkdir -p /etc/sde
printf 'SDE_S3_BUCKET=${BUCKET}\\nPLAYWRIGHT_BROWSERS_PATH=${APP_DIR}/ms-playwright\\n' > /etc/sde/env
chmod 644 /etc/sde/env
chown -R ec2-user:ec2-user ${APP_DIR}

echo "[5/6] chromium (as ec2-user; Amazon Linux — no --with-deps)"
# Chromium may already be partially downloaded from a prior attempt.
sudo -u ec2-user bash -lc "cd ${APP_DIR} && source .venv/bin/activate && export PLAYWRIGHT_BROWSERS_PATH=${APP_DIR}/ms-playwright && playwright install chromium" \\
  || fail "playwright install"

echo "[6/6] watcher"
pkill -f '${APP_DIR}/watch_inbox.sh' || true
sleep 1
sudo -u ec2-user bash -lc 'cd ${APP_DIR} && set -a && source /etc/sde/env && set +a && nohup ./watch_inbox.sh >> logs/watch.log 2>&1 &'
sleep 2
pgrep -af watch_inbox.sh || fail "watcher did not start"
sudo -u ec2-user bash -lc "cd ${APP_DIR} && source .venv/bin/activate && python -c 'import crawl4ai, boto3; print(\"imports ok\")'" \\
  || fail "import check"

echo "=== install ok ==="
cat /etc/sde/env
ls -la ${APP_DIR}/jobs/incoming
echo "OK" > "\$STATUS_FILE"
EOF

echo "Uploading to s3://${BUCKET}/bootstrap/"
aws s3 cp "$TAR" "s3://${BUCKET}/${S3_TAR}"
aws s3 cp "$REMOTE_SH" "s3://${BUCKET}/${S3_SH}"
rm -f "$TAR" "$REMOTE_SH"

# Kick off install in background so SSM itself does not hit execution timeout.
echo "Starting background install on instance..."
START_ID="$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --comment "SDE crawler start install ${STAMP}" \
  --timeout-seconds 600 \
  --parameters commands="[
    \"aws s3 cp s3://${BUCKET}/${S3_SH} /tmp/sde-remote-install.sh\",
    \"chmod +x /tmp/sde-remote-install.sh\",
    \"pkill -f /tmp/sde-remote-install.sh || true\",
    \"echo RUNNING > /var/log/sde-install.status\",
    \"nohup bash /tmp/sde-remote-install.sh > /var/log/sde-install.nohup 2>&1 &\",
    \"echo started pid=\\\$!\",
    \"sleep 2\",
    \"test -f /var/log/sde-install.status\"
  ]" \
  --query 'Command.CommandId' \
  --output text)"

echo "Start CommandId: $START_ID"
for _ in $(seq 1 40); do
  ST="$(aws ssm get-command-invocation --command-id "$START_ID" --instance-id "$INSTANCE_ID" --query 'Status' --output text 2>/dev/null || echo Pending)"
  case "$ST" in
    Success) break ;;
    Failed|Cancelled|TimedOut)
      aws ssm get-command-invocation --command-id "$START_ID" --instance-id "$INSTANCE_ID" --query '[StandardErrorContent,StandardOutputContent]' --output text
      exit 1
      ;;
  esac
  sleep 5
done
echo "Install running on instance. Polling /var/log/sde-install.status ..."

# Poll status file via short SSM commands (avoids long-running SSM timeout).
for i in $(seq 1 120); do
  POLL_ID="$(aws ssm send-command \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --timeout-seconds 60 \
    --parameters commands='["cat /var/log/sde-install.status 2>/dev/null || echo MISSING","tail -n 8 /var/log/sde-install.log 2>/dev/null || true"]' \
    --query 'Command.CommandId' \
    --output text)"
  sleep 8
  OUT="$(aws ssm get-command-invocation \
    --command-id "$POLL_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'StandardOutputContent' \
    --output text 2>/dev/null || true)"
  STATE="$(printf '%s\n' "$OUT" | head -n 1 | tr -d '\r' | awk '{print $1}')"
  echo "  [${i}/120] status=${STATE}"
  printf '%s\n' "$OUT" | tail -n +2 | sed 's/^/    /' | tail -n 6

  case "$STATE" in
    OK)
      echo
      echo "Done."
      echo "  SSM shell:   aws ssm start-session --target ${INSTANCE_ID}"
      echo "  App dir:     ${APP_DIR}"
      echo "  Drop jobs:   ${APP_DIR}/jobs/incoming/"
      echo "  S3 outputs:  s3://${BUCKET}/scraped_collections/"
      exit 0
      ;;
    FAILED)
      echo "Install failed. Full log:" >&2
      aws ssm send-command \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --parameters commands='["tail -n 80 /var/log/sde-install.log"]' \
        --query 'Command.CommandId' --output text >/tmp/sde-fail-id.txt
      sleep 5
      aws ssm get-command-invocation \
        --command-id "$(cat /tmp/sde-fail-id.txt)" \
        --instance-id "$INSTANCE_ID" \
        --query 'StandardOutputContent' --output text >&2 || true
      exit 1
      ;;
  esac
  sleep 22
done

echo "Gave up after ~60 min of polling. Check:" >&2
echo "  aws ssm start-session --target ${INSTANCE_ID}" >&2
echo "  sudo tail -f /var/log/sde-install.log" >&2
exit 1
