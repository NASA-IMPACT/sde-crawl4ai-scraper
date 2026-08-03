# Deploy (AWS CDK + EC2)

Provisions an EC2 crawler host, private S3 bucket, VPC (public subnet), and an instance role with SSM + S3 access. Application code is installed separately onto the instance.

## Stack resources

| Resource | Notes |
|----------|--------|
| CloudFormation stack | `SdeCrawlerStack` |
| EC2 | `t3.xlarge`, Amazon Linux 2023, Name tag `sde-crawler`, 50 GB gp3 |
| S3 | Private crawl output bucket (`RemovalPolicy.RETAIN`) |
| IAM instance role | `AmazonSSMManagedInstanceCore` + read/write on the crawl bucket |
| Security group | Egress only (no inbound SSH; use SSM) |
| User data | OS packages + `/etc/sde/env` with `SDE_S3_BUCKET` |

This account uses CDK bootstrap qualifier **`sde`** (see `infra/cdk.json`).

## Prerequisites

- AWS credentials with permission to deploy the stack (CloudFormation / CDK bootstrap roles) and later use SSM + S3
- Node.js (for `npx aws-cdk`) and Python 3 for the CDK app
- Region, e.g. `export AWS_DEFAULT_REGION=us-east-1`

## Deploy infrastructure

```bash
cd infra
./deploy.sh
```

Outputs include `InstanceId` and `BucketName`.

## Install the application on EC2

```bash
./scripts/ec2_install.sh
```

Packages the app, uploads to the stack bucket, installs dependencies and Chromium via SSM, and starts `watch_inbox.sh` under `/opt/sde-crawler`.

## Operate

```bash
# interactive shell (requires Session Manager plugin)
aws ssm start-session --target <InstanceId>

# or drop a job without a shell
./scripts/drop_job.sh
# batch examples:
./scripts/submit_full_batch.sh
```

Verify:

```bash
aws s3 ls s3://<BucketName>/scraped_collections/
aws s3 ls s3://<BucketName>/failure_logs/
```

On the instance: `/opt/sde-crawler/logs/watch.log`, `logs/jobs/<id>.log`.

## Tear down

```bash
cd infra
./destroy.sh
```

Empties/deletes the retained bucket, then destroys `SdeCrawlerStack`. Leave the shared `CDKToolkit` bootstrap stack in place unless your organization removes it on purpose.
